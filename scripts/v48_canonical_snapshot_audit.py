from __future__ import annotations

import datetime as dt
import hashlib
import itertools
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
MAX_BYTES = 2 * 1024**3
METADATA_MAX_BYTES = 100 * 1024**2
OLD_GEOMETRY_DRYRUN_BYTES = 275_904_855_982
SELECTION_RULE = "latest_date_all_8_tables_have_partition_and_rows_per_uf"
SCHEMA_VERSION = "v48-sicar-canonical-snapshots-1"


def fail(message: str) -> None:
    raise AssertionError(message)


def table_partition_contract(client: bigquery.Client) -> dict[str, dict[str, Any]]:
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
        print("RX_V48_PARTITION_CONTRACT", f"table={table}", f"field={field}", f"type={partition_type}")
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


def run_inventory(client: bigquery.Client, candidate_dates: list[dt.date]) -> tuple[list[dict[str, Any]], int, int, int]:
    params = [bigquery.ArrayQueryParameter("ufs", "STRING", list(EXPECTED_UFS))]
    sql = lightweight_inventory_sql(candidate_dates)
    dry_cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        dry_run=True,
        use_query_cache=False,
        use_legacy_sql=False,
    )
    dry = client.query(sql, job_config=dry_cfg)
    estimated = int(dry.total_bytes_processed or 0)
    print(f"RX_V48_CANONICAL_LIGHTWEIGHT_DRYRUN_BYTES={estimated}")
    print(f"RX_V48_CANONICAL_MAX_BYTES_BILLED={MAX_BYTES}")
    if estimated > MAX_BYTES:
        fail(f"lightweight canonical audit exceeds guard:{estimated}>{MAX_BYTES}")

    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        use_query_cache=False,
        use_legacy_sql=False,
        maximum_bytes_billed=MAX_BYTES,
    )
    job = client.query(sql, job_config=cfg)
    rows = [dict(row.items()) for row in job.result(timeout=300)]
    processed = int(job.total_bytes_processed or 0)
    billed = int(job.total_bytes_billed or 0)
    print(f"RX_V48_CANONICAL_REAL_QUERY_BYTES_PROCESSED={processed}")
    print(f"RX_V48_CANONICAL_REAL_QUERY_BYTES_BILLED={billed}")
    print("RX_V48_CANONICAL_REAL_QUERY_EXECUTED=YES")
    return rows, estimated, processed, billed


def build_matrix(rows: list[dict[str, Any]], candidate_dates: list[dt.date]) -> dict[str, Any]:
    counts: dict[str, dict[str, dict[str, int]]] = {
        uf: {d.isoformat(): {table: 0 for table in TABLES} for d in candidate_dates}
        for uf in EXPECTED_UFS
    }
    for row in rows:
        uf = str(row.get("sigla_uf") or "").upper()
        table = str(row.get("table_name") or "")
        raw_date = row.get("data_extracao")
        date = raw_date.isoformat() if hasattr(raw_date, "isoformat") else str(raw_date or "")
        if uf in counts and date in counts[uf] and table in TABLES:
            counts[uf][date][table] = int(row.get("row_count") or 0)

    coverage: dict[str, list[str]] = {}
    for d in candidate_dates:
        key = d.isoformat()
        eligible = [
            uf for uf in EXPECTED_UFS
            if all(counts[uf][key][table] > 0 for table in TABLES)
        ]
        coverage[key] = eligible
        print(f"RX_V48_DATE_COVERAGE date={key} ufs={len(eligible)}/27")

    audit_date = dt.datetime.now(dt.timezone.utc).date()
    ufs: dict[str, Any] = {}
    for uf in EXPECTED_UFS:
        eligible_dates = [d.isoformat() for d in candidate_dates if uf in coverage[d.isoformat()]]
        if not eligible_dates:
            ufs[uf] = {
                "status": "unavailable",
                "snapshot": None,
                "eligible_dates": [],
                "reason": "no_candidate_date_has_rows_in_all_8_required_tables_for_uf",
            }
            print(f"RX_V48_CANONICAL_UF uf={uf} status=UNAVAILABLE")
            continue
        snapshot = max(eligible_dates)
        age_days = (audit_date - dt.date.fromisoformat(snapshot)).days
        ufs[uf] = {
            "status": "canonical",
            "snapshot": snapshot,
            "age_days_at_audit": age_days,
            "eligible_dates": eligible_dates,
            "tables": {table: {"row_count": counts[uf][snapshot][table]} for table in TABLES},
        }
        print(f"RX_V48_CANONICAL_UF uf={uf} snapshot={snapshot} age_days={age_days} tables=8/8")

    unavailable = sorted(uf for uf, item in ufs.items() if item["status"] != "canonical")
    canonical_dates_used = sorted({item["snapshot"] for item in ufs.values() if item["status"] == "canonical"})
    all27_dates = sorted(date for date, eligible in coverage.items() if len(eligible) == 27)

    minimum_cover: list[str] | None = None
    if not unavailable:
        universe = set(EXPECTED_UFS)
        dates = [d.isoformat() for d in candidate_dates]
        for size in range(1, len(dates) + 1):
            for combo in itertools.combinations(dates, size):
                covered: set[str] = set()
                for date in combo:
                    covered.update(coverage[date])
                if covered == universe:
                    minimum_cover = list(combo)
                    break
            if minimum_cover is not None:
                break

    print("RX_V48_ALL_27_DATE=" + (all27_dates[-1] if all27_dates else "NONE"))
    print(f"RX_V48_CANONICAL_DISTINCT_DATES_USED={len(canonical_dates_used)}")
    print("RX_V48_CANONICAL_DATES_USED=" + (",".join(canonical_dates_used) if canonical_dates_used else "NONE"))
    print("RX_V48_MINIMUM_DATE_COVER=" + (",".join(minimum_cover) if minimum_cover else "UNAVAILABLE"))
    print("RX_V48_CANONICAL_UNAVAILABLE_UFS=" + (",".join(unavailable) if unavailable else "NONE"))

    return {
        "row_counts": counts,
        "coverage_by_date": coverage,
        "coverage_count_by_date": {date: len(ufs_) for date, ufs_ in coverage.items()},
        "ufs": ufs,
        "unavailable_ufs": unavailable,
        "all_27_dates": all27_dates,
        "canonical_dates_used": canonical_dates_used,
        "canonical_distinct_dates_used": len(canonical_dates_used),
        "minimum_date_cover": minimum_cover,
    }


def main() -> None:
    project = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    if project != PROJECT:
        fail(f"unexpected GCP project:{project or '<missing>'}")

    client = bigquery.Client(project=PROJECT)
    partition_contract = table_partition_contract(client)
    partitions, common_dates, metadata_bytes = partition_candidates(client)
    rows, dryrun_bytes, processed, billed = run_inventory(client, common_dates)
    matrix = build_matrix(rows, common_dates)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": "real_lightweight_canonical_matrix",
        "source": DATASET,
        "selection_rule": SELECTION_RULE,
        "selection_rule_human": (
            "Para cada UF, escolher a data mais recente em que as oito tabelas possuem a partição e "
            "pelo menos uma linha para a UF. Geometria não participa da seleção de data e permanece "
            "validada fail-closed no motor e no worker."
        ),
        "required_tables": list(TABLES),
        "partition_contract": partition_contract,
        "partitions_by_table": partitions,
        "common_partition_dates": [d.isoformat() for d in common_dates],
        "common_partition_dates_count": len(common_dates),
        "partitions_metadata_bytes_processed": metadata_bytes,
        "old_geometry_dryrun_bytes": OLD_GEOMETRY_DRYRUN_BYTES,
        "lightweight_dryrun_bytes": dryrun_bytes,
        "maximum_bytes_billed": MAX_BYTES,
        "real_query_bytes_processed": processed,
        "real_query_bytes_billed": billed,
        "real_data_query_executed": True,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        **matrix,
    }
    fingerprint_payload = dict(payload)
    canonical = json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    payload["content_fingerprint_sha256"] = fingerprint

    artifacts = Path("artifacts")
    artifacts.mkdir(parents=True, exist_ok=True)
    matrix_path = artifacts / "v48_canonical_snapshot_matrix.json"
    matrix_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    manifest_candidate = {
        "schema_version": SCHEMA_VERSION,
        "source": DATASET,
        "selection_rule": SELECTION_RULE,
        "generated_at_utc": payload["generated_at_utc"],
        "required_tables": list(TABLES),
        "ufs": matrix["ufs"],
        "evidence_fingerprint_sha256": fingerprint,
    }
    manifest_path = artifacts / "v48_sicar_canonical_snapshot_manifest_candidate.json"
    manifest_path.write_text(json.dumps(manifest_candidate, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(f"RX_V48_CANONICAL_MATRIX_FINGERPRINT={fingerprint}")
    print(f"RX_V48_CANONICAL_UFS={27 - len(matrix['unavailable_ufs'])}/27")
    print("RX_V48_CANONICAL_SNAPSHOT_AUDIT=PASS")


if __name__ == "__main__":
    main()
