from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from google.cloud import bigquery

PROJECT = "metodo-afp-plataforma"
DATASET = "basedosdados.br_sfb_sicar"
TARGET_SNAPSHOT = dt.date(2026, 8, 4)
EXPECTED_UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}
TABLES = (
    "area_imovel",
    "vegetacao_nativa",
    "reserva_legal",
    "app",
    "uso_restrito",
    "area_consolidada",
    "hidrografia",
    "area_pousio",
)


def fail(message: str) -> None:
    raise AssertionError(message)


def query(client: bigquery.Client, sql: str, params: list[bigquery.ScalarQueryParameter]) -> tuple[list[dict[str, Any]], int]:
    dry = bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False)
    dry_job = client.query(sql, job_config=dry)
    estimated = int(dry_job.total_bytes_processed or 0)
    cfg = bigquery.QueryJobConfig(query_parameters=params, use_legacy_sql=False)
    rows = client.query(sql, job_config=cfg).result(timeout=120)
    return [dict(row.items()) for row in rows], estimated


def coverage_sql() -> str:
    parts = []
    for table in TABLES:
        parts.append(
            f"""
            SELECT '{table}' AS table_name,
                   sigla_uf,
                   COUNT(*) AS row_count,
                   COUNTIF(geometria IS NOT NULL) AS geometry_count,
                   COUNT(DISTINCT id_imovel) AS distinct_car_count
            FROM `{DATASET}.{table}`
            WHERE data_extracao=@snapshot
            GROUP BY sigla_uf
            """
        )
    return "\nUNION ALL\n".join(parts)


def area_stats_sql() -> str:
    return f"""
    SELECT sigla_uf,
           COUNT(*) AS row_count,
           COUNTIF(geometria IS NOT NULL) AS geometry_count,
           COUNT(DISTINCT id_imovel) AS distinct_car_count,
           APPROX_QUANTILES(SAFE_CAST(area AS FLOAT64), 100)[OFFSET(50)] AS p50_area_ha,
           APPROX_QUANTILES(SAFE_CAST(area AS FLOAT64), 100)[OFFSET(90)] AS p90_area_ha,
           MIN(SAFE_CAST(area AS FLOAT64)) AS min_area_ha,
           MAX(SAFE_CAST(area AS FLOAT64)) AS max_area_ha
    FROM `{DATASET}.area_imovel`
    WHERE data_extracao=@snapshot
    GROUP BY sigla_uf
    ORDER BY distinct_car_count ASC
    """


def sample_sql() -> str:
    return f"""
    SELECT sigla_uf, id_imovel, id_municipio, SAFE_CAST(area AS FLOAT64) AS area_ha
    FROM `{DATASET}.area_imovel`
    WHERE data_extracao=@snapshot
      AND geometria IS NOT NULL
      AND SAFE_CAST(area AS FLOAT64) BETWEEN 5 AND 500
      AND status IN ('AT','PE','SU')
    QUALIFY ROW_NUMBER() OVER (PARTITION BY sigla_uf ORDER BY id_imovel) = 1
    ORDER BY sigla_uf
    """


def main() -> None:
    project = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    if project != PROJECT:
        fail(f"unexpected GCP project: {project or '<missing>'}")

    client = bigquery.Client(project=PROJECT)
    params = [bigquery.ScalarQueryParameter("snapshot", "DATE", TARGET_SNAPSHOT)]

    coverage_rows, coverage_bytes = query(client, coverage_sql(), params)
    stats_rows, stats_bytes = query(client, area_stats_sql(), params)
    sample_rows, sample_bytes = query(client, sample_sql(), params)

    by_uf: dict[str, dict[str, Any]] = {uf: {"uf": uf, "tables": {}} for uf in sorted(EXPECTED_UFS)}
    for row in coverage_rows:
        uf = str(row.get("sigla_uf") or "").upper()
        table = str(row.get("table_name") or "")
        if uf not in by_uf or table not in TABLES:
            continue
        by_uf[uf]["tables"][table] = {
            "row_count": int(row.get("row_count") or 0),
            "geometry_count": int(row.get("geometry_count") or 0),
            "distinct_car_count": int(row.get("distinct_car_count") or 0),
        }

    for row in stats_rows:
        uf = str(row.get("sigla_uf") or "").upper()
        if uf not in by_uf:
            continue
        by_uf[uf]["area_stats"] = {
            "row_count": int(row.get("row_count") or 0),
            "geometry_count": int(row.get("geometry_count") or 0),
            "distinct_car_count": int(row.get("distinct_car_count") or 0),
            "p50_area_ha": float(row["p50_area_ha"]) if row.get("p50_area_ha") is not None else None,
            "p90_area_ha": float(row["p90_area_ha"]) if row.get("p90_area_ha") is not None else None,
            "min_area_ha": float(row["min_area_ha"]) if row.get("min_area_ha") is not None else None,
            "max_area_ha": float(row["max_area_ha"]) if row.get("max_area_ha") is not None else None,
        }

    for row in sample_rows:
        uf = str(row.get("sigla_uf") or "").upper()
        if uf in by_uf:
            by_uf[uf]["sample_car"] = {
                "id_imovel": str(row.get("id_imovel") or ""),
                "id_municipio": str(row.get("id_municipio") or ""),
                "area_ha": float(row["area_ha"]) if row.get("area_ha") is not None else None,
            }

    eligible = []
    for uf, item in by_uf.items():
        tables = item["tables"]
        missing = [t for t in TABLES if int((tables.get(t) or {}).get("row_count") or 0) <= 0]
        item["missing_tables"] = missing
        item["snapshot_complete"] = not missing
        area = item.get("area_stats") or {}
        if not missing and int(area.get("distinct_car_count") or 0) > 0 and item.get("sample_car"):
            eligible.append(item)

    eligible.sort(key=lambda x: int((x.get("area_stats") or {}).get("distinct_car_count") or 10**18))

    print(f"RX_V48_VECTOR_SNAPSHOT_TARGET={TARGET_SNAPSHOT.isoformat()}")
    print(f"RX_V48_VECTOR_SNAPSHOT_COVERAGE_DRYRUN_BYTES={coverage_bytes}")
    print(f"RX_V48_VECTOR_SNAPSHOT_STATS_DRYRUN_BYTES={stats_bytes}")
    print(f"RX_V48_VECTOR_SNAPSHOT_SAMPLE_DRYRUN_BYTES={sample_bytes}")
    print(f"RX_V48_VECTOR_SNAPSHOT_ELIGIBLE_UFS={','.join(x['uf'] for x in eligible)}")
    for item in eligible:
        area = item.get("area_stats") or {}
        sample = item.get("sample_car") or {}
        print(
            "RX_V48_VECTOR_UF",
            f"uf={item['uf']}",
            f"cars={int(area.get('distinct_car_count') or 0)}",
            f"rows={int(area.get('row_count') or 0)}",
            f"p50_area_ha={area.get('p50_area_ha')}",
            f"p90_area_ha={area.get('p90_area_ha')}",
            f"sample_car={sample.get('id_imovel')}",
        )

    payload = {
        "schema_version": "v48-vector-snapshot-audit-1",
        "project": PROJECT,
        "dataset": DATASET,
        "target_snapshot": TARGET_SNAPSHOT.isoformat(),
        "dry_run_bytes": {
            "coverage": coverage_bytes,
            "area_stats": stats_bytes,
            "sample": sample_bytes,
            "total": coverage_bytes + stats_bytes + sample_bytes,
        },
        "eligible_ufs_smallest_first": [x["uf"] for x in eligible],
        "ufs": by_uf,
    }
    out = Path("artifacts/v48_vector_snapshot_audit.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if not eligible:
        fail("no UF has the complete 2026-08-04 SICAR snapshot across required tables")
    print("RX_V48_VECTOR_SNAPSHOT_AUDIT=PASS")


if __name__ == "__main__":
    main()
