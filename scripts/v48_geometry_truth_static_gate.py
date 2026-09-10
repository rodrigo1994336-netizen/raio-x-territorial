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
from scripts import v48_national_finalize as finalizer_module


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
        "portal_car_snapshot_ui_v48.py",
        "portal_map_v46_anchor_state.py",
        "scripts/v48_national_worker.py",
        "scripts/v48_geometry_truth_real_gate.py",
    )
    for path in paths:
        compile_source(path)

    require(contract.NORMALIZATION_VERSION == "v48-polygonal-extraction-1", "normalization version drift")
    require(contract.AREA_ABSOLUTE_TOLERANCE_M2 == 0.01, "absolute area tolerance drift")
    require(contract.AREA_RELATIVE_TOLERANCE == 1e-9, "relative area tolerance drift")
    require(contract.CURVELO_POLYGON_REGRESSION_CAR == "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F", "Curvelo sentinel drift")
    require(contract.GEOMETRYCOLLECTION_REGRESSION_UF == "MG", "real GeometryCollection sentinel UF drift")
    require(contract.GEOMETRYCOLLECTION_REGRESSION_CAR == "MG-3100708-4B47889790D4418F8941396E87C461F0", "real GeometryCollection sentinel drift")
    require(contract.GEOMETRYCOLLECTION_REGRESSION_SNAPSHOT == "2026-08-04", "real GeometryCollection snapshot drift")
    require(contract.GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_RUN_ID == 34519865245, "sentinel source audit run drift")
    require(
        contract.GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_FINGERPRINT
        == "2c718fe0c6f5798430f65eb32a9bb9a9e0b51710ee6de34ce2dd72aac83830cd",
        "sentinel source audit fingerprint drift",
    )
    require(contract.GEOMETRYCOLLECTION_SENTINEL_DISCOVERY_RUN_ID == 34527676691, "sentinel discovery run drift")
    require(
        contract.GEOMETRYCOLLECTION_SENTINEL_DISCOVERY_FINGERPRINT
        == "3ab26311d90b56e0d57a40680d3d1c363431810c251c2f7bd99554edb62c2ed1",
        "sentinel discovery fingerprint drift",
    )
    provenance = contract.geometrycollection_sentinel_provenance()
    require(provenance.get("source_audit_run_id") == 34519865245, "sentinel provenance audit run missing")
    require("authenticated canonical BigQuery query" in str(provenance.get("identifier_source")), "sentinel query provenance missing")
    require("never copied from log output" in str(provenance.get("identifier_source")), "log identifier prohibition missing")
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

    snapshot_ui = text("portal_car_snapshot_ui_v48.py")
    require("if(d?.user_message)" in snapshot_ui, "analysis must privilege explicit customer state message")
    require("Fonte indisponível" in snapshot_ui, "generic unavailable fallback unexpectedly removed")

    compact = text("portal_map_v46_anchor_state.py")
    require("p?.geometry_notice" in compact, "compact property card does not display geometry notice")
    require("RX_MAP_V46_GEOMETRY_NOTICE" in compact, "compact geometry notice boot marker missing")

    v45 = text("portal_map_panel_v45.py")
    require("function geometry(){return activeData?.geometry||(window.current||{}).geometry||null}" in v45, "V45 export geometry source contract changed")
    require("function downloadPng(){const g=geometry()" in v45, "PNG no longer consumes panel geometry")

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

    zero_commit = {
        "analysis_geometry_normalization": contract.NORMALIZATION_VERSION,
        "map_geometry_normalization": contract.NORMALIZATION_VERSION,
        "source_geometry_set_fingerprint_sha256": "source",
        "render_geometry_set_fingerprint_sha256": "render",
        "normalization_applied_count": 0,
        "no_geometry_car_count": 0,
        "no_polygonal_car_count": 0,
        "distinct_car_count": 1,
        "feature_count": 1,
        "no_geometry_car_ids": [],
        "no_polygonal_car_ids": [],
        "run_metrics": {"source": {"normalization_applied_count": 9, "no_geometry_car_count": 7, "no_polygonal_car_count": 4, "distinct_car_count": 99, "feature_count": 88, "normalization_records": []}},
    }
    zero_evidence = finalizer_module._geometry_evidence(zero_commit)
    require(zero_evidence["normalization_applied_count"] == 0, "finalizer replaced legitimate zero normalization count")
    require(zero_evidence["no_geometry_car_count"] == 0, "finalizer replaced legitimate zero no-geometry count")
    require(zero_evidence["no_polygonal_car_count"] == 0, "finalizer replaced legitimate zero no-polygon count")
    require(zero_evidence["distinct_car_count"] == 1 and zero_evidence["feature_count"] == 1, "finalizer ignored explicit commit counters")

    recovery = text("scripts/v48_national_recovery_contract.py")
    require("recovery_requires_full_geometry_evidence_rebuild" in recovery, "evidence-poor recovery does not fail closed")

    finalizer = text("scripts/v48_national_finalize.py")
    require("geometry_evidence_valid" in finalizer, "national finalizer does not gate geometry evidence")
    require("_first_not_none" in finalizer, "national finalizer must preserve legitimate zero before fallback")
    require('commit.get("no_geometry_car_count") or source.get("no_geometry_car_count")' not in finalizer, "zero may not fall through to stale source count")
    require("normalized_polygon_area_before_m2" in finalizer and "normalized_polygon_area_after_m2" in finalizer, "national area normalization totals missing")
    require("discarded_line_length_m" in finalizer and "discarded_point_components" in finalizer, "national dimensional discard totals missing")

    real_gate = text("scripts/v48_geometry_truth_real_gate.py")
    require("geometrycollection_sentinel_provenance" in real_gate, "real gate does not persist sentinel provenance")
    require("RX_V48_REAL_MA_NO_GEOMETRY_DISCOVERED" in real_gate, "MA id must print before assertions")
    require("ma_discovered_pre_assertion" in real_gate, "MA evidence must persist before assertions")
    require("geometry_raw is None" in real_gate and "source_raw is None" in real_gate, "MA numeric classification must validate None explicitly")
    require("GEOMETRYCOLLECTION_REGRESSION_UF" in real_gate, "real gate hardcodes stale GeometryCollection UF")
    require("SE-2800209-07D88A428D8B4ED5B5647683275F103" not in real_gate, "invalid log-derived sentinel remains in real gate")

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
    feature, parts, state, audit = worker.row_to_feature(
        mixed,
        contract.GEOMETRYCOLLECTION_REGRESSION_UF,
        contract.GEOMETRYCOLLECTION_REGRESSION_SNAPSHOT,
    )
    require(feature is not None and state is None and audit is not None, "GeometryCollection normalized path regression")
    require(feature["properties"]["geometry_normalized"] is True, "normalized feature flag missing")
    require(audit.get("source_geometry_fingerprint") != audit.get("render_geometry_fingerprint"), "dual sentinel fingerprints collapsed")

    print("RX_V48_GEOMETRY_TRUTH_STATIC_GATE=PASS")
    print(f"RX_V48_POLYGON_SENTINEL={contract.CURVELO_POLYGON_REGRESSION_CAR}")
    print(f"RX_V48_GEOMETRYCOLLECTION_SENTINEL={contract.GEOMETRYCOLLECTION_REGRESSION_CAR}")
    print(f"RX_V48_GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_RUN={contract.GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_RUN_ID}")
    print(f"RX_V48_GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_FINGERPRINT={contract.GEOMETRYCOLLECTION_SENTINEL_SOURCE_AUDIT_FINGERPRINT}")
    print("RX_V48_NATIONAL_GENERATION_AUTHORIZED=NO")


if __name__ == "__main__":
    main()
