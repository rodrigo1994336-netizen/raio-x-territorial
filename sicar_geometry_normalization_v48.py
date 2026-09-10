from __future__ import annotations

"""Single geometry-normalization contract shared by V47 analysis and V48 map build.

This module is deliberately pure: it defines the frozen SQL normalization semantics
and customer-facing truth messages without creating clients or executing queries.
"""

NORMALIZATION_VERSION = "v48-polygonal-extraction-1"
AREA_ABSOLUTE_TOLERANCE_M2 = 0.01
AREA_RELATIVE_TOLERANCE = 1e-9

CURVELO_POLYGON_REGRESSION_CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
CURVELO_POLYGON_REGRESSION_SNAPSHOT = "2026-08-04"

# Permanent real GeometryCollection regression sentinel.
# Audit #6 established that MG has 202 GeometryCollection CARs at the canonical
# 2026-08-04 snapshot. The audit artifact intentionally stored counts, not IDs.
# Discovery run 34527676691 then selected the lexicographically first matching
# id_imovel with the same post-ST_UNION_AGG semantics and validated the full CAR
# format. Identifiers copied from logs are not admissible as regression data.
GEOMETRYCOLLECTION_REGRESSION_UF = "MG"
GEOMETRYCOLLECTION_REGRESSION_CAR = "MG-3100708-4B47889790D4418F8941396E87C461F0"
GEOMETRYCOLLECTION_REGRESSION_SNAPSHOT = "2026-08-04"
GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_RUN_ID = 34519865245
GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_FINGERPRINT = (
    "2c718fe0c6f5798430f65eb32a9bb9a9e0b51710ee6de34ce2dd72aac83830cd"
)
GEOMETRYCOLLECTION_SENTINEL_DISCOVERY_RUN_ID = 34527676691
GEOMETRYCOLLECTION_SENTINEL_DISCOVERY_FINGERPRINT = (
    "3ab26311d90b56e0d57a40680d3d1c363431810c251c2f7bd99554edb62c2ed1"
)
GEOMETRYCOLLECTION_SENTINEL_SELECTION_RULE = (
    "lexicographically first canonical MG GeometryCollection after ST_UNION_AGG"
)

NORMALIZATION_USER_NOTICE = (
    "A geometria publicada para este imóvel contém elementos que não são área "
    "(linhas ou pontos). O Raio-X considera apenas a parte poligonal."
)


def no_geometry_user_message(date_pt: str) -> str:
    return f"este imóvel não possui geometria publicada na base do CAR em {date_pt}"


def geometrycollection_sentinel_provenance() -> dict[str, object]:
    return {
        "source_audit_run_id": GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_RUN_ID,
        "source_audit_content_fingerprint_sha256": GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_FINGERPRINT,
        "source_audit_fact": "MG has 202 ST_GeometryCollection CARs after ST_UNION_AGG at canonical snapshot 2026-08-04",
        "discovery_run_id": GEOMETRYCOLLECTION_SENTINEL_DISCOVERY_RUN_ID,
        "discovery_content_fingerprint_sha256": GEOMETRYCOLLECTION_SENTINEL_DISCOVERY_FINGERPRINT,
        "selection_rule": GEOMETRYCOLLECTION_SENTINEL_SELECTION_RULE,
        "identifier_source": "authenticated canonical BigQuery query; never copied from log output",
    }


def normalization_ctes(source_cte: str = "per_car_source") -> str:
    """Return the canonical dimensional-normalization CTEs.

    The input CTE must expose ``source_geometry``.  ``ST_DUMP(..., 2)`` is the
    only operation allowed to derive render/analysis geometry from that source.
    Polygon/MultiPolygon therefore pass through the same path as mixed
    collections; non-polygon components never become area.
    """
    return f"""
    , normalized_geometry AS (
      SELECT
        s.*,
        ST_GEOMETRYTYPE(source_geometry) AS source_geometry_type,
        CASE
          WHEN source_geometry IS NULL THEN NULL
          ELSE (
            SELECT ST_UNION_AGG(part)
            FROM UNNEST(ST_DUMP(source_geometry, 2)) AS part
          )
        END AS render_geometry,
        COALESCE(ARRAY_LENGTH(ST_DUMP(source_geometry, 1)), 0) AS discarded_line_components,
        COALESCE((
          SELECT SUM(ST_LENGTH(part))
          FROM UNNEST(ST_DUMP(source_geometry, 1)) AS part
        ), 0.0) AS discarded_line_length_m,
        COALESCE(ARRAY_LENGTH(ST_DUMP(source_geometry, 0)), 0) AS discarded_point_components,
        COALESCE((
          SELECT SUM(ST_AREA(part))
          FROM UNNEST(ST_DUMP(source_geometry, 2)) AS part
        ), 0.0) AS polygon_area_before_m2,
        IF(
          source_geometry IS NULL,
          NULL,
          TO_HEX(SHA256(ST_ASBINARY(source_geometry)))
        ) AS source_geometry_fingerprint
      FROM {source_cte} AS s
    ), normalized_geometry_metrics AS (
      SELECT
        n.*,
        ST_GEOMETRYTYPE(render_geometry) AS render_geometry_type,
        COALESCE(ST_AREA(render_geometry), 0.0) AS polygon_area_after_m2,
        ABS(COALESCE(ST_AREA(render_geometry), 0.0) - polygon_area_before_m2) AS polygon_area_difference_m2,
        GREATEST(
          {AREA_ABSOLUTE_TOLERANCE_M2},
          polygon_area_before_m2 * {AREA_RELATIVE_TOLERANCE}
        ) AS polygon_area_tolerance_m2,
        IF(
          render_geometry IS NULL,
          NULL,
          TO_HEX(SHA256(ST_ASBINARY(render_geometry)))
        ) AS render_geometry_fingerprint,
        source_geometry IS NOT NULL AND render_geometry IS NULL AS has_no_polygonal_component,
        source_geometry IS NOT NULL
          AND (discarded_line_components > 0 OR discarded_point_components > 0)
          AS discarded_nonpolygon_components,
        source_geometry IS NOT NULL
          AND ST_GEOMETRYTYPE(source_geometry) = 'ST_GeometryCollection'
          AND render_geometry IS NOT NULL
          AS normalization_applied
      FROM normalized_geometry AS n
    )
    """


def normalization_contract_fields() -> dict[str, object]:
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "area_absolute_tolerance_m2": AREA_ABSOLUTE_TOLERANCE_M2,
        "area_relative_tolerance": AREA_RELATIVE_TOLERANCE,
        "invariant": "analysis_geometry_normalization == map_geometry_normalization",
        "operation": "ST_DUMP(source_geometry, 2) then ST_UNION_AGG polygonal parts",
    }


print("RX_V48_GEOMETRY_NORMALIZATION_CONTRACT=polygonal_extraction_single_source", flush=True)
