from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sicar_geometry_normalization_v48 as contract
from scripts import v48_national_worker as worker


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def compile_source(path: str) -> None:
    ast.parse(text(path), filename=path)


def main() -> None:
    paths = (
        "sicar_geometry_normalization_v48.py",
        "sicar_canonical_snapshot_v48.py",
        "sicar_overlap_hardening_v47.py",
        "portal_geometry_truth_v48.py",
        "portal_car_integrity_v47.py",
        "scripts/v48_national_worker.py",
    )
    for path in paths:
        compile_source(path)

    require(contract.NORMALIZATION_VERSION == "v48-polygonal-extraction-1", "normalization version drift")
    require(contract.AREA_ABSOLUTE_TOLERANCE_M2 == 0.01, "absolute area tolerance drift")
    require(contract.AREA_RELATIVE_TOLERANCE == 1e-9, "relative area tolerance drift")
    require(contract.CURVELO_POLYGON_REGRESSION_CAR == "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F", "Curvelo sentinel drift")
    require(contract.GEOMETRYCOLLECTION_REGRESSION_CAR == "SE-2800209-07D88A428D8B4ED5B5647683275F103", "real GeometryCollection sentinel drift")
    require(
        contract.NORMALIZATION_USER_NOTICE
        == "A geometria publicada para este imóvel contém elementos que não são área (linhas ou pontos). O Raio-X considera apenas a parte poligonal.",
        "customer normalization notice drift",
    )
    require(
        contract.no_geometry_user_message("04/08/2026")
        == "este imóvel não possui geometria publicada na base do CAR em 04/08/2026",
        "no-geometry customer message drift",
    )
    normalized_sql = contract.normalization_ctes("per_car_source").upper()
    require("ST_DUMP(SOURCE_GEOMETRY, 2)" in normalized_sql, "ST_DUMP dimension-2 missing")
    require("SOURCE_GEOMETRY_FINGERPRINT" in normalized_sql and "RENDER_GEOMETRY_FINGERPRINT" in normalized_sql, "dual fingerprints missing")
    require("DISCARDED_LINE_LENGTH_M" in normalized_sql and "DISCARDED_POINT_COMPONENTS" in normalized_sql, "discard metrics missing")

    canonical = text("sicar_canonical_snapshot_v48.py")
    require("geometry_contract.normalization_ctes('per_car_source')" in canonical, "analysis does not use shared normalization")
    require("COUNTIF(geometria IS NOT NULL) AS geometry_row_count" in canonical, "explicit no-geometry classification missing")
    require("property_has_no_published_geometry_in_canonical_snapshot" in canonical, "no-geometry state missing")
    require("query_canonical_property_geometry" in canonical, "canonical geometry endpoint helper missing")
    source_block = canonical.split("WITH source_rows AS (", 1)[1].split("), per_car_source AS (", 1)[0]
    require("geometria IS NOT NULL" not in source_block, "analysis filters NULL geometry before classification")

    overlap = text("sicar_overlap_hardening_v47.py")
    require("geometry_contract.normalization_ctes('per_car_source')" in overlap, "overlap does not share normalization")
    require(overlap.count("render_geometry AS geometria") >= 2, "both overlap sides must use render geometry")

    portal = text("portal_geometry_truth_v48.py")
    require("query_canonical_property_geometry" in portal, "panel canonical geometry query missing")
    require('out["geometry"] = None' in portal, "WFS geometry fail-closed reset missing")
    require("/v1/exports/canonical-property/{car_code}/kml" in portal, "canonical KML route missing")
    require("geometry_available===false" in portal, "no-geometry export suppression missing")
    require("geometry_notice" in portal and "NORMALIZATION_USER_NOTICE" not in portal, "notice must flow from shared backend contract")
    require("stopImmediatePropagation" in portal, "legacy WFS KML action is not intercepted")

    national = text("scripts/v48_national_worker.py")
    source_block = national.split("WITH source_rows AS (", 1)[1].split("), per_car_source AS (", 1)[0]
    require("geometria IS NOT NULL" not in source_block, "national worker filters NULL geometry before classification")
    require("geometry_contract.normalization_ctes('per_car_source')" in national, "national worker does not use shared normalization")
    for token in (
        "no_geometry_car_ids",
        "no_polygonal_car_ids",
        "normalization_records",
        "discarded_line_length_m",
        "discarded_point_components",
        "source_geometry_set_fingerprint_sha256",
        "render_geometry_set_fingerprint_sha256",
        "analysis_geometry_normalization",
        "map_geometry_normalization",
    ):
        require(token in national, f"national geometry evidence missing:{token}")
    require("unexpected_geometry_type" not in national, "obsolete GeometryCollection reject path remains")

    config = json.loads(text("config/v48_uf_orchestration_contract.json"))
    require(config.get("geometryNormalizationVersion") == contract.NORMALIZATION_VERSION, "config normalization version mismatch")
    require(
        config.get("analysisMapGeometryRule")
        == "analysis_geometry_normalization == map_geometry_normalization == v48-polygonal-extraction-1",
        "analysis/map geometry invariant missing",
    )
    require(config.get("activeJsonMutationAllowed") is False, "active.json mutation became allowed")

    # Pure regression probes against worker classification. These do not claim
    # to replace the real BigQuery sentinel workflow; they protect state handling.
    no_geom = {
        "id_imovel": "MA-0000000-STATIC-NO-GEOMETRY",
        "source_row_count": 1,
        "geometry_row_count": 0,
    }
    feature, parts, state, audit = worker.row_to_feature(no_geom, "MA", "2026-08-02")
    require(feature is None and parts == 0 and state == "no_geometry_published", "no-geometry classification regression")
    require((audit or {}).get("state") == "no_geometry_published", "no-geometry evidence missing")

    polygon = {
        "id_imovel": contract.CURVELO_POLYGON_REGRESSION_CAR,
        "source_row_count": 1,
        "geometry_row_count": 1,
        "geometry_geojson": json.dumps({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}),
        "source_geometry_type": "ST_Polygon",
        "render_geometry_type": "ST_Polygon",
        "source_geometry_fingerprint": "a",
        "render_geometry_fingerprint": "a",
        "discarded_line_components": 0,
        "discarded_line_length_m": 0.0,
        "discarded_point_components": 0,
        "polygon_area_before_m2": 1.0,
        "polygon_area_after_m2": 1.0,
        "polygon_area_difference_m2": 0.0,
        "polygon_area_tolerance_m2": 0.01,
        "has_no_polygonal_component": False,
        "discarded_nonpolygon_components": False,
        "normalization_applied": False,
        "geometry_points": 4,
        "area_ha": 0.0001,
        "id_municipio": "3120904",
        "status": "AT",
    }
    feature, parts, state, audit = worker.row_to_feature(polygon, "MG", contract.CURVELO_POLYGON_REGRESSION_SNAPSHOT)
    require(feature is not None and parts == 1 and state is None and audit is None, "Polygon pass-through regression")
    require(feature["properties"]["geometry_normalized"] is False, "ordinary Polygon incorrectly marked normalized")

    mixed = dict(polygon)
    mixed.update({
        "id_imovel": contract.GEOMETRYCOLLECTION_REGRESSION_CAR,
        "source_geometry_type": "ST_GeometryCollection",
        "render_geometry_type": "ST_Polygon",
        "source_geometry_fingerprint": "source-real-sentinel",
        "render_geometry_fingerprint": "render-real-sentinel",
        "discarded_line_components": 1,
        "discarded_line_length_m": 12.5,
        "discarded_nonpolygon_components": True,
        "normalization_applied": True,
    })
    feature, parts, state, audit = worker.row_to_feature(mixed, "SE", contract.GEOMETRYCOLLECTION_REGRESSION_SNAPSHOT)
    require(feature is not None and state is None and audit is not None, "GeometryCollection normalized path regression")
    require(feature["properties"]["geometry_normalized"] is True, "normalized feature flag missing")
    require(audit.get("source_geometry_fingerprint") != audit.get("render_geometry_fingerprint"), "dual sentinel fingerprints collapsed")

    print("RX_V48_GEOMETRY_TRUTH_STATIC_GATE=PASS")
    print(f"RX_V48_POLYGON_SENTINEL={contract.CURVELO_POLYGON_REGRESSION_CAR}")
    print(f"RX_V48_GEOMETRYCOLLECTION_SENTINEL={contract.GEOMETRYCOLLECTION_REGRESSION_CAR}")
    print("RX_V48_NATIONAL_GENERATION_AUTHORIZED=NO")


if __name__ == "__main__":
    main()
