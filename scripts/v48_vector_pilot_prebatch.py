from __future__ import annotations

import json
from pathlib import Path

from google.cloud import bigquery

import v48_vector_pilot_worker as worker

PROJECT = worker.PROJECT
UF = worker.UF
SNAPSHOT = worker.SNAPSHOT
EXPECTED_FEATURES = worker.EXPECTED_FEATURES
MAX_BQ_BYTES = worker.MAX_BQ_BYTES


def main() -> None:
    worker.assert_static_contract()
    assert UF == "ES"
    assert SNAPSHOT.isoformat() == "2026-08-04"

    client = bigquery.Client(project=PROJECT)
    params = [
        bigquery.ScalarQueryParameter("uf", "STRING", UF),
        bigquery.ScalarQueryParameter("snapshot", "DATE", SNAPSHOT),
    ]

    dry_cfg = bigquery.QueryJobConfig(
        query_parameters=params,
        dry_run=True,
        use_query_cache=False,
    )
    dry_job = client.query(worker.extraction_sql(), job_config=dry_cfg)
    dry_bytes = int(dry_job.total_bytes_processed or 0)
    if dry_bytes > MAX_BQ_BYTES:
        raise AssertionError(f"worker dry-run exceeds guard: {dry_bytes}>{MAX_BQ_BYTES}")

    audit_sql = f"""
    WITH source_rows AS (
      SELECT id_imovel, geometria
      FROM `{worker.SOURCE_TABLE}`
      WHERE sigla_uf=@uf
        AND data_extracao=@snapshot
        AND geometria IS NOT NULL
    ), per_car AS (
      SELECT id_imovel, COUNT(*) AS source_row_count, ST_UNION_AGG(geometria) AS geometria
      FROM source_rows
      GROUP BY id_imovel
    )
    SELECT
      COUNT(*) AS feature_count,
      COUNTIF(ST_GEOMETRYTYPE(geometria) = 'ST_Polygon') AS polygon_count,
      COUNTIF(ST_GEOMETRYTYPE(geometria) = 'ST_MultiPolygon') AS multipolygon_count,
      COUNTIF(ST_GEOMETRYTYPE(geometria) NOT IN ('ST_Polygon','ST_MultiPolygon')) AS other_count,
      COUNTIF(source_row_count > 1) AS duplicate_car_count
    FROM per_car
    """
    row = next(iter(client.query(
        audit_sql,
        job_config=bigquery.QueryJobConfig(query_parameters=params, maximum_bytes_billed=MAX_BQ_BYTES),
    ).result()))

    feature_count = int(row["feature_count"])
    if feature_count != EXPECTED_FEATURES:
        raise AssertionError(f"feature count changed: {feature_count}!={EXPECTED_FEATURES}")

    other_sql = f"""
    WITH source_rows AS (
      SELECT id_imovel, geometria
      FROM `{worker.SOURCE_TABLE}`
      WHERE sigla_uf=@uf
        AND data_extracao=@snapshot
        AND geometria IS NOT NULL
    ), per_car AS (
      SELECT id_imovel, COUNT(*) AS source_row_count, ST_UNION_AGG(geometria) AS geometria
      FROM source_rows
      GROUP BY id_imovel
    )
    SELECT
      id_imovel,
      source_row_count,
      ST_GEOMETRYTYPE(geometria) AS geometry_type,
      ST_ISEMPTY(geometria) AS is_empty,
      ST_NUMPOINTS(geometria) AS geometry_points,
      ST_AREA(geometria) AS area_m2,
      ST_ASGEOJSON(geometria) AS geometry_geojson
    FROM per_car
    WHERE ST_GEOMETRYTYPE(geometria) NOT IN ('ST_Polygon','ST_MultiPolygon')
    ORDER BY id_imovel
    LIMIT 20
    """
    other_rows = list(client.query(
        other_sql,
        job_config=bigquery.QueryJobConfig(query_parameters=params, maximum_bytes_billed=MAX_BQ_BYTES),
    ).result())

    others = []
    for r in other_rows:
        raw = str(r["geometry_geojson"] or "")
        others.append({
            "id_imovel": str(r["id_imovel"]),
            "source_row_count": int(r["source_row_count"] or 0),
            "geometry_type": str(r["geometry_type"] or ""),
            "is_empty": bool(r["is_empty"]),
            "geometry_points": int(r["geometry_points"] or 0),
            "area_m2": float(r["area_m2"] or 0.0),
            "geometry_geojson": raw[:4000],
        })

    payload = {
        "schema_version": "v48-vector-prebatch-1",
        "project": PROJECT,
        "uf": UF,
        "snapshot": SNAPSHOT.isoformat(),
        "worker_dry_run_bytes": dry_bytes,
        "expected_features": EXPECTED_FEATURES,
        "feature_count": feature_count,
        "polygon_count": int(row["polygon_count"]),
        "multipolygon_count": int(row["multipolygon_count"]),
        "other_count": int(row["other_count"]),
        "duplicate_car_count": int(row["duplicate_car_count"]),
        "other_geometries": others,
        "national_generation_allowed": False,
    }
    out = Path("artifacts/v48_vector_pilot_prebatch.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(f"RX_V48_PREBATCH_WORKER_DRYRUN_BYTES={dry_bytes}")
    print(f"RX_V48_PREBATCH_FEATURES={feature_count}")
    print(f"RX_V48_PREBATCH_POLYGON={payload['polygon_count']}")
    print(f"RX_V48_PREBATCH_MULTIPOLYGON={payload['multipolygon_count']}")
    print(f"RX_V48_PREBATCH_OTHER={payload['other_count']}")
    print(f"RX_V48_PREBATCH_DUPLICATE_CARS={payload['duplicate_car_count']}")
    for item in others:
        print(
            "RX_V48_PREBATCH_OTHER_DETAIL="
            f"{item['id_imovel']}|{item['geometry_type']}|empty={item['is_empty']}|"
            f"points={item['geometry_points']}|area_m2={item['area_m2']}|rows={item['source_row_count']}"
        )
    print("RX_V48_PREBATCH_NATIONAL_GENERATION=BLOCKED")
    print("RX_V48_VECTOR_PREBATCH=PASS_AUDIT_COMPLETED")


if __name__ == "__main__":
    main()
