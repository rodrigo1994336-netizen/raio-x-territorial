from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from google.cloud import bigquery

PROJECT = "metodo-afp-plataforma"
DATASET_PROJECT = "basedosdados"
DATASET_ID = "br_sfb_sicar"
DATASET = f"{DATASET_PROJECT}.{DATASET_ID}"
EXPECTED_UFS = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
TABLES = (
    "area_imovel", "vegetacao_nativa", "reserva_legal", "app",
    "uso_restrito", "area_consolidada", "hidrografia", "area_pousio",
)
MAX_BYTES = 50 * 1024**3
METADATA_MAX_BYTES = 100 * 1024**2
OLD_GEOMETRY_DRYRUN_BYTES = 275_904_855_982
SELECTION_RULE = "latest_date_all_8_tables_have_partition_and_rows_per_uf"


def fail(message: str) -> None:
    raise AssertionError(message)


def table_partition_contract(client: bigquery.Client) -> dict[str, dict[str, Any]]:
    """Read table metadata via tables.get; this is not a BigQuery data query."""
    out: dict[str, dict[str, Any]] = {}
    for table in TABLES:
        meta = client.get_table(f"{DATASET}.{table}")
        tp = meta.time_partitioning
        field = tp.field if tp else None
        partition_type = str(tp.type_ if tp else "")
        if field != "data_extracao":
            fail(f"{table}: expected partition field data_extracao; got {field or '<none>'}")
        out[table] = {
            "partition_field": field,
            "partition_type": partition_type,
            "num_rows_table_metadata": int(meta.num_rows or 0),
        }
        print(
            "RX_V48_PARTITION_CONTRACT",
            f"table={table}",
            f"field={field}",
            f"type={partition_type}",
        )
    return out


def partitions_metadata_sql() -> str:
    return f"""
    SELECT table_name, partition_id, total_rows
    FROM `{DATASET_PROJECT}.{DATASET_ID}.INFORMATION_SCHEMA.PARTITIONS`
    WHERE table_name IN UNNEST(@tables)
      AND partition_id IS NOT NULL
      AND partition_id NOT IN ('__NULL__', '__UNPARTITIONED__')
      AND total_rows > 0
    ORDER BY table_name, partition_id
    """


def parse_partition_id(raw: str) -> dt.date:
    text = str(raw or "").strip()
    if len(text) != 8 or not text.isdigit():
        fail(f"unexpected daily partition_id:{text or '<empty>'}")
    return dt.datetime.strptime(text, "%Y%m%d").date()


def partition_candidates(client: bigquery.Client) -> tuple[dict[str, list[str]], list[dt.date], int]:
    params = [bigquery.ArrayQueryParameter("tables", "STRING", list(TABLES))]
    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        use_legacy_sql=False,
        maximum_bytes_billed=METADATA_MAX_BYTES,
    )
    job = client.query(partitions_metadata_sql(), job_config=cfg)
    rows = list(job.result(timeout=120))
    metadata_bytes = int(job.total_bytes_processed or 0)
    print(f"RX_V48_PARTITIONS_METADATA_BYTES_PROCESSED={metadata_bytes}")

    by_table: dict[str, set[dt.date]] = {table: set() for table in TABLES}
    for row in rows:
        table = str(row.get("table_name") or "")
        if table not in by_table:
            continue
        date = parse_partition_id(str(row.get("partition_id") or ""))
        if int(row.get("total_rows") or 0) > 0:
            by_table[table].add(date)

    missing = [table for table, dates in by_table.items() if not dates]
    if missing:
        fail(f"partition metadata returned no non-empty partitions for:{','.join(missing)}")

    common = set.intersection(*(dates for dates in by_table.values()))
    if not common:
        fail("no partition date exists in all eight tables")
    common_dates = sorted(common)
    print(
        "RX_V48_COMMON_PARTITION_DATES",
        f"count={len(common_dates)}",
        f"oldest={common_dates[0].isoformat()}",
        f"newest={common_dates[-1].isoformat()}",
    )
    serialized = {table: [d.isoformat() for d in sorted(dates)] for table, dates in by_table.items()}
    return serialized, common_dates, metadata_bytes


def lightweight_inventory_sql(candidate_dates: list[dt.date]) -> str:
    if not candidate_dates:
        fail("candidate_dates_empty")
    literals = ",".join(f"DATE '{date.isoformat()}'" for date in candidate_dates)
    parts: list[str] = []
    for table in TABLES:
        parts.append(f"""
        SELECT '{table}' AS table_name,
               sigla_uf,
               data_extracao,
               COUNT(*) AS row_count
        FROM `{DATASET}.{table}`
        WHERE data_extracao IN ({literals})
          AND sigla_uf IN UNNEST(@ufs)
        GROUP BY sigla_uf, data_extracao
        """)
    return "\nUNION ALL\n".join(parts)


def lightweight_dry_run(client: bigquery.Client, candidate_dates: list[dt.date]) -> int:
    params = [bigquery.ArrayQueryParameter("ufs", "STRING", list(EXPECTED_UFS))]
    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        dry_run=True,
        use_query_cache=False,
        use_legacy_sql=False,
    )
    job = client.query(lightweight_inventory_sql(candidate_dates), job_config=cfg)
    estimated = int(job.total_bytes_processed or 0)
    print(f"RX_V48_CANONICAL_LIGHTWEIGHT_DRYRUN_BYTES={estimated}")
    if estimated > MAX_BYTES:
        fail(f"lightweight canonical audit exceeds guard:{estimated}>{MAX_BYTES}")
    return estimated


def main() -> None:
    project = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    if project != PROJECT:
        fail(f"unexpected GCP project:{project or '<missing>'}")

    client = bigquery.Client(project=PROJECT)
    partition_contract = table_partition_contract(client)
    partitions, common_dates, metadata_bytes = partition_candidates(client)
    dryrun_bytes = lightweight_dry_run(client, common_dates)

    reduction = None
    if OLD_GEOMETRY_DRYRUN_BYTES > 0:
        reduction = 1.0 - (dryrun_bytes / OLD_GEOMETRY_DRYRUN_BYTES)

    payload = {
        "mode": "probe_only_no_real_data_query",
        "source": DATASET,
        "selection_rule": SELECTION_RULE,
        "selection_rule_human": (
            "Para cada UF, a data canônica candidata será a mais recente em que as oito tabelas "
            "possuem a partição e pelo menos uma linha para a UF. Geometria não participa da seleção "
            "de data; permanece validada fail-closed no motor e no worker."
        ),
        "required_tables": list(TABLES),
        "partition_contract": partition_contract,
        "partitions_by_table": partitions,
        "common_partition_dates": [d.isoformat() for d in common_dates],
        "common_partition_dates_count": len(common_dates),
        "partitions_metadata_bytes_processed": metadata_bytes,
        "old_geometry_dryrun_bytes": OLD_GEOMETRY_DRYRUN_BYTES,
        "lightweight_dryrun_bytes": dryrun_bytes,
        "estimated_byte_reduction_fraction": reduction,
        "real_data_query_executed": False,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    out = Path("artifacts/v48_canonical_snapshot_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("RX_V48_CANONICAL_PROBE_REAL_QUERY_EXECUTED=NO")
    print("RX_V48_CANONICAL_SNAPSHOT_PROBE=PASS")


if __name__ == "__main__":
    main()
