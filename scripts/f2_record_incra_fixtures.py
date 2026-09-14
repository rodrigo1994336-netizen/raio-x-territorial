"""Record F2 fixtures from live answers (INCRA Acervo Fundiário, SICAR, PAMGIA mirror).

Live, network required; never run by CI. Usage from the repository root:
  PYTHONPATH=. python scripts/f2_record_incra_fixtures.py

Personal and registry fields are replaced by a sentinel so the gate can
prove the parser never reads them; property names are pseudonymised (a registry name may contain a
person's name).
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import incra_acervo_f2 as acervo  # noqa: E402
import deploy_app  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "f2_incra_sigef_snci"
OUT.mkdir(parents=True, exist_ok=True)
SICAR = "https://geoserver.car.gov.br/geoserver/sicar/ows"
CARS = {
    "curvelo_teste": "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F",
    "vizinho_snci": "MG-3120904-528DEBA144FF4CEE994BA14D1ABD0E71",
    "vizinho_sigef2025": "MG-3120904-82A2876828AB43EE91CA8D1E33EE785F",
    "vizinho_sigef2021": "MG-3120904-DB822B19289143419DA829F1633B75EF",
}
SENTINEL = "SENTINELA-LGPD-REMOVIDO"
REDACT = ("rt", "art", "cod_profissional_credenciado", "num_processo", "registro_matricula", "registro_data")
NAME_FIELDS = ("nome_area", "nome_imovel")
_names: dict[str, str] = {}


def redact(body: bytes) -> bytes:
    text = body.decode("utf-8")
    for field in REDACT:
        text = re.sub(rf"<ms:{field}>[^<]*</ms:{field}>", f"<ms:{field}>{SENTINEL}</ms:{field}>", text)

    def pseudo(m):
        field, value = m.group(1), m.group(2)
        if not value.strip():
            return m.group(0)
        key = value.strip().upper()
        if key not in _names:
            _names[key] = f"IMOVEL DE REFERENCIA {len(_names) + 1:02d}"
        return f"<ms:{field}>{_names[key]}</ms:{field}>"

    text = re.sub(r"<ms:(nome_area|nome_imovel)>([^<]*)</ms:\1>", pseudo, text)
    return text.encode("utf-8")


def sicar_car(code: str):
    q = {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": f"sicar:sicar_imoveis_{code[:2].lower()}",
         "outputFormat": "application/json", "CQL_FILTER": f"cod_imovel IN ('{code}')"}
    p = subprocess.run(["curl", "-sS", "-m", "60", SICAR + "?" + urlencode(q)], capture_output=True, timeout=70)
    f = json.loads(p.stdout)["features"][0]
    props = f["properties"]
    return {"type": "Feature", "properties": {"cod_imovel": props.get("cod_imovel"), "uf": props.get("uf"),
            "municipio": props.get("municipio"), "area": props.get("area")}, "geometry": f["geometry"]}


def main():
    manifest = {"recorded_at": None, "cars": {}, "controls": {}}
    from datetime import datetime, timezone
    manifest["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for label, code in CARS.items():
        car = sicar_car(code)
        (OUT / f"car_{label}.geojson").write_text(json.dumps(car, ensure_ascii=False), encoding="utf-8", newline="\n")
        shape = acervo._car_shape(car["geometry"])
        x0, y0, x1, y1 = shape.bounds
        bbox = (x0 - acervo.BBOX_PAD_DEG, y0 - acervo.BBOX_PAD_DEG, x1 + acervo.BBOX_PAD_DEG, y1 + acervo.BBOX_PAD_DEG)
        entry = {"car": code, "bbox": [round(v, 6) for v in bbox], "layers": {}}
        for family, key, prefix in acervo.LAYERS:
            theme = f"{prefix}_mg"
            url = acervo.getfeature_url(theme, bbox)
            raw = acervo.curl_fetch(url)
            assert raw.get("ok") and raw.get("body"), (label, theme, raw.get("detail"))
            parsed = acervo.parse_wfs_gml(raw["body"], family, bbox=bbox)
            assert parsed["answered"] and not parsed["truncated"], (label, theme, parsed.get("detail"))
            (OUT / f"{label}__{theme}.gml").write_bytes(redact(raw["body"]))
            entry["layers"][theme] = {"url": url, "features_in_box": parsed["count"]}
        mirror = asyncio.run(deploy_app.query_sigef([x0, y0, x1, y1]))
        body = {"type": "FeatureCollection", "features": mirror.get("features") or [], "ok": mirror.get("ok")}
        assert mirror.get("ok"), mirror
        (OUT / f"{label}__pamgia_sigef_publico_10.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8", newline="\n")
        entry["pamgia_publico_10_feature_count"] = mirror.get("feature_count")
        live = acervo.query_incra_acervo(car["geometry"], "MG")
        entry["live_states"] = {f: live[f]["state"] for f in acervo.FAMILIES}
        entry["live_best"] = {f: [(r["layer"], r["car_share"]) for r in live[f]["rows"][:3]] for f in acervo.FAMILIES}
        manifest["cars"][label] = entry
        print(label, entry["live_states"], entry["live_best"], "pamgia10=", entry["pamgia_publico_10_feature_count"], flush=True)
    # Controls: RJ public SIGEF not enabled; JSON output refused; unknown theme -> empty body.
    rj_bbox = (-43.30, -22.95, -43.20, -22.85)
    ctl = {
        "rj_certificada_sigef_publico": acervo.getfeature_url("certificada_sigef_publico_rj", rj_bbox),
        "unknown_theme": acervo.getfeature_url("imoveiscertificados_privado_xx", rj_bbox),
        "json_output": acervo.getfeature_url("imoveiscertificados_privado_mg", manifest["cars"]["vizinho_snci"]["bbox"]).replace("outputFormat=GML2", "outputFormat=application%2Fjson"),
    }
    for name, url in ctl.items():
        raw = acervo.curl_fetch(url)
        body = raw.get("body") or b""
        (OUT / f"control__{name}.xml").write_bytes(body)
        parsed = acervo.parse_wfs_gml(body, "sigef")
        manifest["controls"][name] = {"url": url, "transport_ok": raw.get("ok"), "bytes": len(body), "parsed_answered": parsed["answered"], "detail": parsed.get("detail")}
        print(name, manifest["controls"][name], flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
