from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from google.cloud import bigquery

PROJECT = "metodo-afp-plataforma"
TABLE = "basedosdados.br_sfb_sicar.area_imovel"
SNAPSHOT = dt.date(2026, 8, 4)
CANDIDATES = ("SE", "ES", "AL", "PB")
MAX_BYTES = 50 * 1024**3


def main() -> None:
    project = (os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    if project != PROJECT:
        raise AssertionError(f"unexpected project:{project or '<missing>'}")
    client = bigquery.Client(project=PROJECT)
    sql = f"""
    WITH per_car AS (
      SELECT sigla_uf,
             id_imovel,
             ANY_VALUE(id_municipio) AS id_municipio,
             COUNT(*) AS source_rows,
             ST_UNION_AGG(geometria) AS geometria
      FROM `{TABLE}`
      WHERE data_extracao=@snapshot
        AND sigla_uf IN UNNEST(@ufs)
        AND geometria IS NOT NULL
      GROUP BY sigla_uf, id_imovel
    ), measured AS (
      SELECT sigla_uf,
             id_imovel,
             id_municipio,
             source_rows,
             ST_NUMPOINTS(geometria) AS num_points,
             ST_GEOMETRYTYPE(geometria) AS geometry_type,
             ST_AREA(geometria) / 10000.0 AS geometry_area_ha
      FROM per_car
    )
    SELECT sigla_uf,
           COUNT(*) AS distinct_cars,
           COUNT(DISTINCT id_municipio) AS municipalities,
           COUNTIF(source_rows > 1) AS duplicate_row_cars,
           MAX(source_rows) AS max_rows_per_car,
           APPROX_QUANTILES(num_points, 100)[OFFSET(50)] AS p50_points,
           APPROX_QUANTILES(num_points, 100)[OFFSET(90)] AS p90_points,
           APPROX_QUANTILES(num_points, 100)[OFFSET(95)] AS p95_points,
           APPROX_QUANTILES(num_points, 100)[OFFSET(99)] AS p99_points,
           MAX(num_points) AS max_points,
           APPROX_QUANTILES(geometry_area_ha, 100)[OFFSET(50)] AS p50_geometry_area_ha,
           APPROX_QUANTILES(geometry_area_ha, 100)[OFFSET(90)] AS p90_geometry_area_ha,
           COUNTIF(geometry_type='ST_Polygon') AS polygons,
           COUNTIF(geometry_type='ST_MultiPolygon') AS multipolygons,
           COUNTIF(geometry_type NOT IN ('ST_Polygon','ST_MultiPolygon')) AS other_geometry_types
    FROM measured
    GROUP BY sigla_uf
    ORDER BY distinct_cars
    """
    params = [
        bigquery.ScalarQueryParameter("snapshot", "DATE", SNAPSHOT),
        bigquery.ArrayQueryParameter("ufs", "STRING", list(CANDIDATES)),
    ]
    dry_cfg = bigquery.QueryJobConfig(query_parameters=params, dry_run=True, use_query_cache=False)
    dry_job = client.query(sql, job_config=dry_cfg)
    estimated = int(dry_job.total_bytes_processed or 0)
    print(f"RX_V48_CANDIDATE_COMPLEXITY_DRYRUN_BYTES={estimated}")
    if estimated > MAX_BYTES:
        raise AssertionError(f"candidate complexity query exceeds guard:{estimated}>{MAX_BYTES}")

    cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        use_legacy_sql=False,
        maximum_bytes_billed=MAX_BYTES,
    )
    rows = [dict(r.items()) for r in client.query(sql, job_config=cfg).result(timeout=180)]
    found = {str(r.get("sigla_uf") or ""): r for r in rows}
    if set(found) != set(CANDIDATES):
        raise AssertionError(f"candidate coverage mismatch:{sorted(found)}")

    serializable = []
    for uf in CANDIDATES:
        r = found[uf]
        item = {
            "uf": uf,
            "distinct_cars": int(r.get("distinct_cars") or 0),
            "municipalities": int(r.get("municipalities") or 0),
            "duplicate_row_cars": int(r.get("duplicate_row_cars") or 0),
            "max_rows_per_car": int(r.get("max_rows_per_car") or 0),
            "p50_points": int(r.get("p50_points") or 0),
            "p90_points": int(r.get("p90_points") or 0),
            "p95_points": int(r.get("p95_points") or 0),
            "p99_points": int(r.get("p99_points") or 0),
            "max_points": int(r.get("max_points") or 0),
            "p50_geometry_area_ha": float(r.get("p50_geometry_area_ha") or 0),
            "p90_geometry_area_ha": float(r.get("p90_geometry_area_ha") or 0),
            "polygons": int(r.get("polygons") or 0),
            "multipolygons": int(r.get("multipolygons") or 0),
            "other_geometry_types": int(r.get("other_geometry_types") or 0),
        }
        serializable.append(item)
        print("RX_V48_PILOT_CANDIDATE " + " ".join(f"{k}={v}" for k, v in item.items()))

    out = {
        "schema_version": "v48-pilot-candidate-audit-1",
        "snapshot": SNAPSHOT.isoformat(),
        "candidates": serializable,
        "dry_run_bytes": estimated,
    }
    path = Path("artifacts/v48_pilot_candidate_audit.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print("RX_V48_PILOT_CANDIDATE_AUDIT=PASS")


if __name__ == "__main__":
    main()
