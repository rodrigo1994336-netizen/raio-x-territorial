from __future__ import annotations

"""V47 hardening for CAR x CAR overlap.

Keeps the Block 2 engine isolated while ensuring duplicate geometries for the
same CAR/snapshot are unioned before any spatial intersection is evaluated.
The patch is intentionally narrow: only ``sicar_integrity_v47._overlap_sql``
is replaced.
"""

import sicar_integrity_v47 as sicar


def _overlap_sql_hardened() -> str:
    return f"""
    WITH target AS (
      SELECT ST_UNION_AGG(geometria) AS geometria
      FROM `{sicar.DATASET}.area_imovel`
      WHERE sigla_uf=@uf AND data_extracao=@snapshot
        AND id_imovel=@car_code AND geometria IS NOT NULL
    ), others AS (
      SELECT id_imovel, ST_UNION_AGG(geometria) AS geometria
      FROM `{sicar.DATASET}.area_imovel`
      WHERE sigla_uf=@uf AND data_extracao=@snapshot
        AND id_imovel!=@car_code AND geometria IS NOT NULL
      GROUP BY id_imovel
    ), hits AS (
      SELECT other.id_imovel,
             ST_INTERSECTION(target.geometria, other.geometria) AS inter
      FROM target
      JOIN others AS other
        ON target.geometria IS NOT NULL
       AND ST_INTERSECTS(target.geometria, other.geometria)
      WHERE ST_AREA(ST_INTERSECTION(target.geometria, other.geometria)) > 0
    )
    SELECT COUNT(*) AS distinct_car_count,
           ST_ASGEOJSON(ST_UNION_AGG(inter)) AS geometry_geojson
    FROM hits
    """


def apply() -> None:
    sql = _overlap_sql_hardened()
    assert "SELECT ST_UNION_AGG(geometria) AS geometria" in sql
    assert "others AS" in sql and "GROUP BY id_imovel" in sql
    assert "id_imovel!=@car_code" in sql
    assert "ST_AREA(ST_INTERSECTION(target.geometria, other.geometria)) > 0" in sql
    sicar._overlap_sql = _overlap_sql_hardened


apply()
print("RX_SICAR_OVERLAP_HARDENING_V47=duplicate_geometries_unioned", flush=True)
