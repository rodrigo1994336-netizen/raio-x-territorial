from __future__ import annotations

import datetime as dt

import sinaflor_authorization_v48 as sf
import sinaflor_authorization_hardening_v48  # noqa: F401 — patches sf deliberately


CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
CAR_GEOM = {
    "type": "Polygon",
    "coordinates": [[[-44.20, -18.90], [-44.18, -18.90], [-44.18, -18.88], [-44.20, -18.88], [-44.20, -18.90]]],
}


def feature(*, activity: str, geometry: dict, number: str = "ASV-001", status: str = "ATIVA", start="2026-01-01", end="2026-12-31", reported_car=CAR, municipality="Curvelo"):
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "nu_autorizacao": number,
            "tipo_atividade": activity,
            "nm_orgao": "IBAMA TESTE",
            "status_autorizacao": status,
            "dt_valid_inicio": start,
            "dt_valid_fim": end,
            "nu_car_imovel": reported_car,
            "municipio": municipality,
            "uf": "MG",
            "area_pamgia_ha": 10.0,
            "dt_atualizacao": "2026-09-01T12:00:00Z",
        },
    }


def deterministic() -> None:
    overlap = {
        "type": "Polygon",
        "coordinates": [[[-44.195, -18.895], [-44.185, -18.895], [-44.185, -18.885], [-44.195, -18.885], [-44.195, -18.895]]],
    }
    outside = {
        "type": "Polygon",
        "coordinates": [[[-44.30, -19.00], [-44.29, -19.00], [-44.29, -18.99], [-44.30, -18.99], [-44.30, -19.00]]],
    }
    touching_only = {
        "type": "Polygon",
        "coordinates": [[[-44.18, -18.90], [-44.17, -18.90], [-44.17, -18.89], [-44.18, -18.89], [-44.18, -18.90]]],
    }
    broad = {
        "type": "Polygon",
        "coordinates": [[[-46.0, -20.0], [-42.0, -20.0], [-42.0, -17.0], [-46.0, -17.0], [-46.0, -20.0]]],
    }
    irrelevant = feature(activity="Plano de Manejo Florestal Sustentável - PMFS", geometry=overlap, number="PMFS-1")

    result = sf.evaluate_features(
        CAR_GEOM,
        [
            feature(activity="Autorização de Supressão de Vegetação - ASV", geometry=overlap),
            feature(activity="Uso Alternativo do Solo", geometry=outside, number="UAS-OUT"),
            feature(activity="Autorização de Supressão de Vegetação - ASV", geometry=touching_only, number="ASV-TOUCH"),
            irrelevant,
        ],
        car_code=CAR,
        query_date=dt.date(2026, 9, 8),
        car_municipality="Curvelo",
        car_uf="MG",
    )
    assert result["ok"] is True, result
    assert result["candidate_count"] == 4, result
    assert result["relevant_candidate_count"] == 3, result
    assert result["match_count"] == 1 and result["confirmed_match_count"] == 1, result
    match = result["matches"][0]
    assert match["authorization_number"] == "ASV-001", match
    assert match["overlap_ha"] > 0, match
    assert match["reported_car_matches"] is True, match
    assert match["property_binding_confirmed"] is True, match
    assert match["currently_confirmed"] is True, match

    broad_result = sf.evaluate_features(
        CAR_GEOM,
        [feature(activity="Autorização de Supressão de Vegetação - ASV", geometry=broad, number="ASV-BROAD", status="Autorização Emitida", start="2019-10-22", end=None, reported_car=None, municipality="Sete Lagoas")],
        car_code=CAR,
        query_date=dt.date(2026, 9, 8),
        car_municipality="Curvelo",
        car_uf="MG",
    )
    assert broad_result["match_count"] == 1 and broad_result["confirmed_match_count"] == 0, broad_result
    broad_match = broad_result["matches"][0]
    assert broad_match["broad_territorial_scope"] is True, broad_match
    assert broad_match["property_binding"] == "broad_territorial_geometry_unconfirmed", broad_match
    assert broad_match["currently_confirmed"] is None, broad_match
    assert broad_match["municipality_matches_car"] is False, broad_match

    expired = sf.evaluate_features(
        CAR_GEOM,
        [feature(activity="Uso Alternativo do Solo", geometry=overlap, number="UAS-OLD", status="VENCIDA", start="2024-01-01", end="2024-12-31")],
        car_code=CAR,
        query_date=dt.date(2026, 9, 8),
    )
    assert expired["match_count"] == 1
    assert expired["matches"][0]["currently_confirmed"] is False

    clear = sf.evaluate_features(
        CAR_GEOM,
        [feature(activity="Autorização de Supressão de Vegetação - ASV", geometry=outside)],
        car_code=CAR,
        query_date=dt.date(2026, 9, 8),
    )
    assert clear["match_count"] == 0, clear

    bad = sf.evaluate_features({}, [], car_code=CAR)
    assert bad["ok"] is False and bad["detail"] == "car_geometry_invalid", bad

    print("RX_V48_SINAFLOR_DETERMINISTIC=PASS exact_positive_area touching_not_match activity_scope broad_scope_not_promoted temporal_truth")


def real() -> None:
    sf._METADATA_CACHE = None
    result = sf.query_sinaflor_authorization(CAR)
    assert result.get("ok") is True, result
    assert result.get("answered") is True, result
    assert result.get("state") in {"checked_clear", "checked_authorization_overlap", "checked_authorization_overlap_unconfirmed", "checked_spatial_record_unconfirmed"}, result
    assert result.get("queried_at"), result
    assert result.get("data_date") or result.get("data_date_status") == "not_published_by_layer", result
    assert isinstance(result.get("candidate_count"), int), result
    assert isinstance(result.get("match_count"), int), result
    assert isinstance(result.get("confirmed_match_count"), int), result
    assert result.get("method") and "broad-scope binding guard" in result["method"], result

    for match in result.get("matches") or []:
        assert match.get("overlap_ha", 0) > 0, match
        assert match.get("activity_type"), match
        assert "authorization_number" in match, match
        assert "currently_confirmed" in match, match
        if match.get("broad_territorial_scope") and not match.get("reported_car_matches"):
            assert match.get("property_binding_confirmed") is False, match

    # Curvelo benchmark currently sees the known broad CEMIG/URFBio geometry.
    # It must NEVER be promoted to property authorization merely because it covers the CAR.
    known = [m for m in result.get("matches") or [] if str(m.get("authorization_number") or "") == "20319201908550"]
    if known:
        m = known[0]
        assert m.get("authorization_geometry_ha", 0) >= 100000, m
        assert m.get("reported_car_matches") is False, m
        assert m.get("property_binding_confirmed") is False, m
        if result.get("confirmed_match_count") == 0:
            assert result.get("state") == "checked_spatial_record_unconfirmed", result

    print(
        "RX_V48_SINAFLOR_REAL=PASS "
        f"state={result['state']} candidates={result['candidate_count']} matches={result['match_count']} "
        f"confirmed={result['confirmed_match_count']} data_date={result.get('data_date') or result.get('data_date_status')}"
    )
    print("RX_V48_SINAFLOR_REAL_RESULT", {k: result.get(k) for k in ("state", "candidate_count", "relevant_candidate_count", "match_count", "confirmed_match_count", "unconfirmed_match_count", "data_date", "data_date_status")})


if __name__ == "__main__":
    deterministic()
    real()
    print("RX_V48_SINAFLOR_CONFORMITY_GATE=PASS")
