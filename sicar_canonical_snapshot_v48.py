from __future__ import annotations

from typing import Any

import sicar_integrity_v47 as sicar
import sicar_canonical_manifest_v48 as canonical

_ORIGINAL_QUERY = sicar.query_car_integrity_v47


def _fetch_property_canonical(client: Any, car_code: str, uf: str) -> dict[str, Any] | None:
    snapshot = canonical.canonical_snapshot_for_uf(uf)
    if snapshot is None:
        raise RuntimeError(f"canonical_snapshot_unavailable:{uf}")
    sql = f"""
    SELECT data_extracao, sigla_uf, id_municipio, id_imovel, area,
           ST_ASGEOJSON(geometria) AS geometry_geojson
    FROM `{sicar.DATASET}.area_imovel`
    WHERE sigla_uf=@uf AND id_imovel=@car_code
      AND data_extracao=@snapshot AND geometria IS NOT NULL
    ORDER BY data_atualizacao DESC NULLS LAST
    LIMIT 1
    """
    rows = sicar._query(client, sql, {"uf": uf, "car_code": car_code, "snapshot": snapshot})
    return rows[0] if rows else None


def _freshness_fields(uf: str, snapshot) -> dict[str, Any]:
    note = canonical.staleness_note(snapshot)
    return {
        "snapshot_label": canonical.base_label(uf, snapshot),
        "snapshot_age_days": canonical.age_days(snapshot),
        "snapshot_staleness_note": note,
    }


def query_car_integrity_v48(car_code: str, *, client: Any | None = None) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not sicar.CAR_RE.match(code):
        return _ORIGINAL_QUERY(code, client=client)
    uf = code[:2]
    try:
        snapshot = canonical.canonical_snapshot_for_uf(uf)
    except Exception as exc:
        return {"ok": False, "state": "unavailable", "car_code": code, "detail": str(exc), "source": sicar.DATASET}
    if snapshot is None:
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "canonical_snapshot_unavailable", "source": sicar.DATASET,
            "user_message": f"Base do CAR de {canonical.UF_NAMES.get(uf, uf)}: indisponível.",
            "snapshot_scope": "uf_canonical",
        }
    freshness = _freshness_fields(uf, snapshot)
    out = _ORIGINAL_QUERY(code, client=client)
    if not out.get("ok") and out.get("detail") == "car_not_found_in_basedosdados":
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "car_not_present_in_canonical_snapshot", "source": sicar.DATASET,
            "snapshot": snapshot.isoformat(), "snapshot_scope": "uf_canonical",
            "user_message": f"Este imóvel não consta na base de {canonical.UF_NAMES.get(uf, uf)} de {canonical.date_pt(snapshot)}.",
            **freshness,
        }
    if out.get("ok") and str(out.get("snapshot") or "") != snapshot.isoformat():
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "canonical_snapshot_runtime_mismatch", "source": sicar.DATASET,
        }
    if out.get("ok"):
        out = dict(out)
        out["snapshot_scope"] = "uf_canonical"
        out.update(freshness)
    return out


sicar._fetch_property = _fetch_property_canonical
sicar.query_car_integrity_v47 = query_car_integrity_v48
print("RX_SICAR_CANONICAL_SNAPSHOT_V48=uf_content_addressed_manifest_fail_closed_freshness", flush=True)
