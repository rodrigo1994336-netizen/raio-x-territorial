from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from google.cloud import bigquery

PROJECT = "metodo-afp-plataforma"
DATASET = "basedosdados.br_sfb_sicar"
EXPECTED_UFS = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
TABLES = (
    "area_imovel", "vegetacao_nativa", "reserva_legal", "app",
    "uso_restrito", "area_consolidada", "hidrografia", "area_pousio",
)
MAX_BYTES = 50 * 1024**3
SCHEMA_VERSION = "v48-sicar-canonical-snapshots-1"
SELECTION_RULE = "latest_date_all_8_tables_have_rows_and_usable_geometry_per_uf"


def fail(message: str) -> None:
    raise AssertionError(message)


def inventory_sql() -> str:
    parts = []
    for table in TABLES:
        parts.append(f"""
        SELECT '{table}' AS table_name,
               sigla_uf,
               data_extracao,
               COUNT(*) AS row_count,
               COUNTIF(geometria IS NOT NULL) AS geometry_count,
               COUNT(DISTINCT id_imovel) AS distinct_car_count
        FROM `{DATASET}.{table}`
        WHERE sigla_uf IN UNNEST(@ufs) AND data_extracao IS NOT NULL
        GROUP BY sigla_uf, data_extracao
        """)
    return "\nUNION ALL\n".join(parts)


def query_inventory(client: bigquery.Client) -> tuple[list[dict[str, Any]], int]:
    params = [bigquery.ArrayQueryParameter("ufs", "STRING", list(EXPECTED_UFS))]
    dry_cfg = bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False)
    dry = client.query(inventory_sql(), job_config=dry_cfg)
    estimated = int(dry.total_bytes_processed or 0)
    print(f"RX_V48_CANONICAL_SNAPSHOT_DRYRUN_BYTES={estimated}")
    if estimated > MAX_BYTES:
        fail(f"canonical snapshot audit exceeds guard:{estimated}>{MAX_BYTES}")
    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        use_legacy_sql=False,
        maximum_bytes_billed=MAX_BYTES,
    )
    rows = client.query(inventory_sql(), job_config=cfg).result(timeout=300)
    return [dict(row.items()) for row in rows], estimated


def choose(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_uf_date: dict[str, dict[str, dict[str, dict[str, int]]]] = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        uf = str(row.get("sigla_uf") or "").upper()
        table = str(row.get("table_name") or "")
        date = str(row.get("data_extracao") or "")
        if uf not in EXPECTED_UFS or table not in TABLES or not date:
            continue
        by_uf_date[uf][date][table] = {
            "row_count": int(row.get("row_count") or 0),
            "geometry_count": int(row.get("geometry_count") or 0),
            "distinct_car_count": int(row.get("distinct_car_count") or 0),
        }

    ufs: dict[str, Any] = {}
    for uf in EXPECTED_UFS:
        eligible: list[str] = []
        for date, tables in by_uf_date.get(uf, {}).items():
            if set(tables) != set(TABLES):
                continue
            if all(v["row_count"] > 0 and v["geometry_count"] > 0 for v in tables.values()):
                eligible.append(date)
        if not eligible:
            ufs[uf] = {
                "status": "unavailable",
                "snapshot": None,
                "reason": "no_date_has_all_8_required_tables_with_rows_and_usable_geometry",
            }
            print(f"RX_V48_CANONICAL_UF uf={uf} status=UNAVAILABLE")
            continue
        snapshot = max(eligible)
        tables = by_uf_date[uf][snapshot]
        area = tables["area_imovel"]
        ufs[uf] = {
            "status": "canonical",
            "snapshot": snapshot,
            "area_imovel_distinct_car_count": area["distinct_car_count"],
            "tables": tables,
        }
        print(
            "RX_V48_CANONICAL_UF",
            f"uf={uf}",
            f"snapshot={snapshot}",
            f"cars={area['distinct_car_count']}",
            "tables=8/8",
        )
    return ufs


def main() -> None:
    project = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    if project != PROJECT:
        fail(f"unexpected GCP project:{project or '<missing>'}")
    client = bigquery.Client(project=PROJECT)
    rows, dryrun_bytes = query_inventory(client)
    ufs = choose(rows)
    canonical_count = sum(1 for item in ufs.values() if item.get("status") == "canonical")
    unavailable = sorted(uf for uf, item in ufs.items() if item.get("status") != "canonical")
    national_cars = sum(int(item.get("area_imovel_distinct_car_count") or 0) for item in ufs.values())
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": DATASET,
        "selection_rule": SELECTION_RULE,
        "selection_rule_human": (
            "Para cada UF, escolher a data mais recente em que as oito tabelas necessárias possuem "
            "linhas e geometria utilizável. Sem data elegível, a UF fica indisponível; não há fallback."
        ),
        "required_tables": list(TABLES),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run_bytes": dryrun_bytes,
        "canonical_uf_count": canonical_count,
        "unavailable_ufs": unavailable,
        "national_distinct_cars_sum_by_canonical_uf": national_cars,
        "ufs": ufs,
    }
    canonical_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(canonical_bytes).hexdigest()
    payload["content_fingerprint_sha256"] = fingerprint
    out = Path("artifacts/v48_sicar_canonical_snapshot_manifest_candidate.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    immutable_path = f"car/manifests/sicar-canonical-snapshots-v1-{fingerprint}.json"
    print(f"RX_V48_CANONICAL_SNAPSHOT_UFS={canonical_count}/27")
    print("RX_V48_CANONICAL_SNAPSHOT_UNAVAILABLE=" + (",".join(unavailable) if unavailable else "NONE"))
    print(f"RX_V48_CANONICAL_SNAPSHOT_NATIONAL_CARS={national_cars}")
    print(f"RX_V48_CANONICAL_MANIFEST_FINGERPRINT={fingerprint}")
    print(f"RX_V48_CANONICAL_MANIFEST_PATH={immutable_path}")
    print("RX_V48_CANONICAL_SNAPSHOT_AUDIT=PASS")


if __name__ == "__main__":
    main()
