from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import sicar_integrity_v47 as sicar

MANIFEST_PATH = Path(os.getenv("RX_SICAR_CANONICAL_MANIFEST") or "car/manifests/sicar-canonical-snapshots-v1.json")
EXPECTED_SCHEMA = "v48-sicar-canonical-snapshots-1"
UF_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}
_ORIGINAL_QUERY = sicar.query_car_integrity_v47


def _load_manifest() -> dict[str, Any]:
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("canonical_snapshot_manifest_missing") from exc
    except Exception as exc:
        raise RuntimeError("canonical_snapshot_manifest_invalid_json") from exc
    if data.get("schema_version") != EXPECTED_SCHEMA:
        raise RuntimeError("canonical_snapshot_manifest_schema_mismatch")
    if not isinstance(data.get("ufs"), dict):
        raise RuntimeError("canonical_snapshot_manifest_ufs_missing")
    return data


def canonical_snapshot_for_uf(uf: str) -> dt.date | None:
    code = str(uf or "").upper().strip()
    entry = (_load_manifest().get("ufs") or {}).get(code) or {}
    if entry.get("status") != "canonical":
        return None
    raw = str(entry.get("snapshot") or "")
    try:
        return dt.date.fromisoformat(raw)
    except Exception as exc:
        raise RuntimeError(f"canonical_snapshot_invalid_date:{code}") from exc


def _date_pt(value: dt.date) -> str:
    return value.strftime("%d/%m/%Y")


def _base_label(uf: str, snapshot: dt.date) -> str:
    return f"Base do CAR de {UF_NAMES.get(uf, uf)}: {_date_pt(snapshot)}"


def _fetch_property_canonical(client: Any, car_code: str, uf: str) -> dict[str, Any] | None:
    snapshot = canonical_snapshot_for_uf(uf)
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


def query_car_integrity_v48(car_code: str, *, client: Any | None = None) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not sicar.CAR_RE.match(code):
        return _ORIGINAL_QUERY(code, client=client)
    uf = code[:2]
    try:
        snapshot = canonical_snapshot_for_uf(uf)
    except Exception as exc:
        return {"ok": False, "state": "unavailable", "car_code": code, "detail": str(exc), "source": sicar.DATASET}
    if snapshot is None:
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "canonical_snapshot_unavailable", "source": sicar.DATASET,
            "user_message": f"Base do CAR de {UF_NAMES.get(uf, uf)}: indisponível.",
            "snapshot_scope": "uf_canonical",
        }
    out = _ORIGINAL_QUERY(code, client=client)
    if not out.get("ok") and out.get("detail") == "car_not_found_in_basedosdados":
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "car_not_present_in_canonical_snapshot", "source": sicar.DATASET,
            "snapshot": snapshot.isoformat(), "snapshot_scope": "uf_canonical",
            "user_message": f"Este imóvel não consta na base de {UF_NAMES.get(uf, uf)} de {_date_pt(snapshot)}.",
        }
    if out.get("ok") and str(out.get("snapshot") or "") != snapshot.isoformat():
        return {
            "ok": False, "state": "unavailable", "car_code": code,
            "detail": "canonical_snapshot_runtime_mismatch", "source": sicar.DATASET,
        }
    if out.get("ok"):
        out = dict(out)
        out["snapshot_scope"] = "uf_canonical"
        out["snapshot_label"] = _base_label(uf, snapshot)
    return out


sicar._fetch_property = _fetch_property_canonical
sicar.query_car_integrity_v47 = query_car_integrity_v48
print("RX_SICAR_CANONICAL_SNAPSHOT_V48=uf_manifest_fail_closed", flush=True)
