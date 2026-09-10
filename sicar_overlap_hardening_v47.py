from __future__ import annotations

"""V47/V48 hardening for CAR x CAR overlap.

Every CAR is unioned at the canonical snapshot and then passes through the same
versioned polygonal normalization used by the map/export worker.  Neither the
target nor its neighbours may use an independent raw-geometry path.
"""

import sicar_integrity_v47 as sicar
import sicar_geometry_normalization_v48 as geometry_contract


def _overlap_sql_hardened() -> str:
    return f"""
    WITH source_rows AS (
      SELECT id_imovel, geometria
      FROM `{sicar.DATASET}.area_imovel`
      WHERE sigla_uf=@uf AND data_extracao=@snapshot
    ), per_car_source AS (
      SELECT id_imovel,
             COUNT(*) AS source_row_count,
             COUNTIF(geometria IS NOT NULL) AS geometry_row_count,
             ST_UNION_AGG(geometria) AS source_geometry
      FROM source_rows
      GROUP BY id_imovel
    )
    {geometry_contract.normalization_ctes('per_car_source')}
    , target AS (
      SELECT render_geometry AS geometria
      FROM normalized_geometry_metrics
      WHERE id_imovel=@car_code
        AND render_geometry IS NOT NULL
        AND polygon_area_difference_m2 <= polygon_area_tolerance_m2
    ), others AS (
      SELECT id_imovel, render_geometry AS geometria
      FROM normalized_geometry_metrics
      WHERE id_imovel!=@car_code
        AND render_geometry IS NOT NULL
        AND polygon_area_difference_m2 <= polygon_area_tolerance_m2
    ), hits AS (
      SELECT other.id_imovel,
             ST_INTERSECTION(target.geometria, other.geometria) AS inter
      FROM target
      JOIN others AS other
        ON ST_INTERSECTS(target.geometria, other.geometria)
      WHERE ST_AREA(ST_INTERSECTION(target.geometria, other.geometria)) > 0
    )
    SELECT COUNT(*) AS distinct_car_count,
           ST_ASGEOJSON(ST_UNION_AGG(inter)) AS geometry_geojson
    FROM hits
    """


def apply() -> None:
    sql = _overlap_sql_hardened()
    upper = sql.upper()
    assert "ST_UNION_AGG(GEOMETRIA) AS SOURCE_GEOMETRY" in upper
    assert "ST_DUMP(SOURCE_GEOMETRY, 2)" in upper
    assert "RENDER_GEOMETRY AS GEOMETRIA" in upper
    assert "ID_IMOVEL!=@CAR_CODE" in upper
    assert "ST_AREA(ST_INTERSECTION(TARGET.GEOMETRIA, OTHER.GEOMETRIA)) > 0" in upper
    sicar._overlap_sql = _overlap_sql_hardened


apply()
print("RX_SICAR_OVERLAP_HARDENING_V48=both_sides_canonical_normalized", flush=True)
