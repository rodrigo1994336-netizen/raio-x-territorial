from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from shapely.geometry import box, mapping
from PIL import Image

import sicar_integrity_v47 as sicar
from report_engine_v9 import build_premium_property_report_v9


CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"


def geo(g):
    return json.dumps(mapping(g), separators=(",", ":"))


def record(*, geom=None, declared=None, rows=0, snapshot=True):
    return {
        "snapshot_available": snapshot,
        "row_count": rows,
        "geometry_count": rows if geom is not None else 0,
        "declared_area_ha": declared,
        "geometry_geojson": geo(geom) if geom is not None else None,
    }


def synthetic_integrity(*, boundaries: bool = True):
    prop = box(-44.20, -18.90, -44.19, -18.89)
    # Consolidated geometry deliberately leaks east of the CAR. This proves
    # that "Dentro do perímetro" and "Medido por nós" are distinct facts.
    consolidated = box(-44.196, -18.90, -44.186, -18.89)
    native = box(-44.20, -18.90, -44.198, -18.89)
    hydro = box(-44.198, -18.90, -44.1975, -18.89)

    prop_area = sicar.area_ha_grs80(prop)
    inside_consolidated = sicar.area_ha_grs80(consolidated.intersection(prop))
    assert prop_area and inside_consolidated

    property_record = {
        "geometry_geojson": geo(prop),
        "area": round(prop_area, 6),
        "id_municipio": "3120904",
        "sigla_uf": "MG",
        "data_extracao": "2026-08-04",
    }
    themes = {
        "vegetacao_nativa": record(geom=native, declared=sicar.area_ha_grs80(native), rows=1),
        "reserva_legal": record(rows=0),
        "app": record(rows=0),
        "uso_restrito": record(rows=0),
        # Deliberately greater than the clipped area to force an explicit
        # discrepancy label without relying on floating-point noise.
        "area_consolidada": record(geom=consolidated, declared=inside_consolidated + 1.0, rows=1),
        # Even if a bogus declared value is injected in a synthetic record, the
        # V47 contract must refuse to present hydrology as officially declared.
        "hidrografia": record(geom=hydro, declared=999.0, rows=1),
        "area_pousio": record(rows=0),
    }
    overlap_geom = box(-44.1995, -18.8995, -44.1985, -18.8985)
    overlap = {"query_ok": True, "distinct_car_count": 2, "geometry_geojson": geo(overlap_geom)}

    if boundaries:
        municipality = {
            "configured": True,
            "query_ok": True,
            "geometry_geojson": geo(box(-44.20, -18.90, -44.191, -18.89)),
        }
        uf = {"configured": True, "query_ok": True, "geometry_geojson": geo(box(-45, -20, -43, -17))}
    else:
        municipality = {"configured": False}
        uf = {"configured": False}

    return sicar.build_integrity_from_records(
        CAR, property_record, themes, overlap, municipality, uf
    )


def test_geometry_truth():
    out = synthetic_integrity(boundaries=True)
    assert out["ok"] is True, out
    assert out["state"] == "checked", out["state"]
    rows = {r["key"]: r for r in out["composition_rows"]}

    con = rows["area_consolidada"]
    assert con["inside_ha"] < con["measured_ha"], con
    assert str(con["discrepancy"]).startswith("Déficit:"), con

    hydro = rows["hidrografia"]
    assert hydro["declared_ha"] is None, hydro
    table = {r["key"]: r for r in sicar.panel_table_rows(out)}
    assert "Déficit:" in table["area_consolidada"]["inside"], table["area_consolidada"]
    assert "Déficit:" not in table["area_consolidada"]["measured"], table["area_consolidada"]
    assert table["hidrografia"]["declared"] == "Campo não publicado", table["hidrografia"]
    assert table["regeneracao"]["declared"] == "Não é campo declarado", table["regeneracao"]
    assert "0.0000 ha" not in table["reserva_legal"]["declared"], table["reserva_legal"]
    assert table["reserva_legal"]["declared"] == "Não declarado/localizado", table["reserva_legal"]

    regen = rows["regeneracao"]
    assert regen["state"] == "geometric_residual" and regen["measured_ha"] is not None, regen
    assert "não comprova regeneração" in regen["note"].lower(), regen

    ov = out["overlap"]
    assert ov["state"] == "checked" and ov["distinct_car_count"] == 2 and ov["union_area_ha"] > 0, ov
    assert out["municipality_boundary"]["outside_ha"] > 0, out["municipality_boundary"]
    assert out["uf_boundary"]["outside_ha"] == 0.0, out["uf_boundary"]


def test_fail_closed_states():
    out = synthetic_integrity(boundaries=False)
    assert out["ok"] is True and out["state"] == "partial", out
    assert out["municipality_boundary"]["state"] == "not_configured", out
    assert out["uf_boundary"]["state"] == "not_configured", out

    # One missing exclusive snapshot must block the residual rather than treating
    # the missing class as an empty/zero set.
    prop = box(-44.20, -18.90, -44.19, -18.89)
    property_record = {
        "geometry_geojson": geo(prop), "area": 1.0, "id_municipio": "3120904",
        "sigla_uf": "MG", "data_extracao": "2026-08-04",
    }
    themes = {key: record(rows=0) for key in sicar.THEMES}
    themes["hidrografia"] = record(rows=0, snapshot=False)
    broken = sicar.build_integrity_from_records(
        CAR, property_record, themes,
        {"query_ok": True, "distinct_car_count": 0, "geometry_geojson": None},
        {"configured": False}, {"configured": False},
    )
    regen = next(r for r in broken["composition_rows"] if r["key"] == "regeneracao")
    assert regen["state"] == "not_computable" and regen["measured_ha"] is None, regen
    assert broken["state"] == "partial", broken

    # Runtime with no project must be explicit and must not attempt to turn the
    # configuration failure into a successful zero result.
    saved = {k: os.environ.pop(k, None) for k in ("RX_BIGQUERY_PROJECT", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")}
    try:
        unavailable = sicar.query_car_integrity_v47(CAR)
        assert unavailable["ok"] is False and unavailable["state"] == "unavailable", unavailable
        assert unavailable["runtime"]["project_id_configured"] is False, unavailable
        assert "bigquery_project_not_configured" in unavailable["detail"], unavailable
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_source_contract():
    src = Path("sicar_integrity_v47.py").read_text(encoding="utf-8")
    adapter = Path("live_report_adapter_v19.py").read_text(encoding="utf-8")
    panel = Path("portal_map_panel_v45.py").read_text(encoding="utf-8")
    ui = Path("portal_car_integrity_ui_v47.py").read_text(encoding="utf-8")
    portal_route = Path("portal_car_integrity_v47.py").read_text(encoding="utf-8")
    engine = Path("report_engine_v9.py").read_text(encoding="utf-8")
    site = Path("sitecustomize.py").read_text(encoding="utf-8")

    assert "basedosdados.br_sfb_sicar" in src
    assert "Geod(ellps=\"GRS80\")" in src
    assert "0.99692794" not in src
    assert "data_extracao AS STRING" not in src
    assert src.count("data_extracao=@snapshot") >= 4
    assert "ST_AREA(ST_INTERSECTION(target.geometria,other.geometria))>0" in src
    assert "per_car AS" in src and "ST_UNION_AGG(inter)" in src and "COUNT(*) AS distinct_car_count" in src
    assert "other.id_imovel!=@car_code" in src
    assert "APP_TOTAL" in src
    assert 'EXCLUSIVE_FOR_RESIDUAL = ("area_consolidada", "vegetacao_nativa", "hidrografia", "area_pousio")' in src
    assert "servidao_administrativa" not in src
    municipality_sql = sicar._boundary_sql("basedosdados.br_geobr_mapas.municipio", "id_municipio")
    uf_sql = sicar._boundary_sql("basedosdados.br_geobr_mapas.estado", "sigla_uf")
    assert "sigla_uf=@uf" in municipality_sql and "id_municipio=@boundary_key" in municipality_sql
    assert "sigla_uf=@uf" in uf_sql and "CAST(" not in municipality_sql + uf_sql
    real_gate = Path("scripts/v47_block2_real_bigquery_gate.py").read_text(encoding="utf-8")
    assert "basedosdados.br_geobr_mapas" in real_gate
    assert "INFORMATION_SCHEMA.TABLES" in real_gate and "INFORMATION_SCHEMA.COLUMNS" in real_gate
    assert 'EXPECTED_STATE_TABLE = "estado"' in real_gate
    assert 'EXPECTED_MUNICIPALITY_TABLE = "municipio"' in real_gate
    assert "nenhuma fonte/coluna alternativa" in real_gate.lower()
    assert "_dry_run_bytes" in real_gate and "RX_V47_COST_ESTIMATES_READY=PASS" in real_gate

    assert "query_car_integrity_v47" in adapter
    assert "query_sicar_details_v2" not in adapter
    assert "panel_table_rows" in adapter
    assert all(h in engine for h in ("Informação", "Declarado no CAR", "Dentro do perímetro", "Medido por nós"))
    # Block 1 is frozen: V47 UI must live outside the V45 renderer.
    assert "RX_CAR_INTEGRITY_V47" not in panel and "/v1/live/car-integrity/" not in panel
    assert "RX_CAR_INTEGRITY_V47" in ui and "/v1/live/car-integrity/" in ui
    assert "MutationObserver" not in ui and "setInterval(" not in ui
    assert "_lock_for(code)" in portal_route and "with _lock_for(code):" in portal_route
    assert "portal_car_integrity_v47" in site and "portal_car_integrity_ui_v47" in site and "report_v19_patch" in site


def test_report_render_and_shared_rows():
    out = synthetic_integrity(boundaries=True)
    table = sicar.panel_table_rows(out)
    payload = {
        "report_id": "RX-V47-GATE",
        "generated_at": "2026-09-07T18:00:00-03:00",
        "source_version": "V47 gate",
        "property": {"name": "Imóvel sintético V47", "area_ha": out["property"]["measured_area_ha"], "municipality": "Curvelo", "uf": "MG"},
        "car": {"status": "ATIVO", "analysis_status": "Aguardando análise", "fields": []},
        "land": {}, "enforcement": {}, "mining": {}, "productive": {}, "water": {}, "monitoring": {},
        "conclusion": {}, "narrative": {}, "sources": [], "agropecuaria": {}, "satellite_imagery": {},
        "environment": {"car_integrity_table_rows": [[r["information"], r["declared"], r["inside"], r["measured"]] for r in table]},
        "car_integrity_v47": out,
    }
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        image_path = td / "cover.jpg"
        Image.new("RGB", (32, 32), (240, 240, 240)).save(image_path, format="JPEG")
        payload["satellite_image_path"] = str(image_path)
        pdf = td / "v47_gate.pdf"
        digest = build_premium_property_report_v9(pdf, payload)
        assert pdf.exists() and pdf.stat().st_size > 10_000, pdf.stat().st_size if pdf.exists() else None
        assert len(digest) == 64, digest


def main():
    test_geometry_truth()
    test_fail_closed_states()
    test_source_contract()
    test_report_render_and_shared_rows()
    print("RX_V47_BLOCK2_DETERMINISTIC_GATE=PASS")


if __name__ == "__main__":
    main()