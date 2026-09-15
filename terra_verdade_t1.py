"""T1 · terra-verdade: última palavra do relatório sobre relevo, solo, erosão e chuva comparada.

O estudo F1 (seção 0) provou informação errada ou enganosa chegando ao cliente. Este módulo é aplicado
no fim da cadeia do relatório (``live_report_adapter_v20.render_v20``), depois de todas as camadas de
remendo, para que nenhuma delas devolva o texto antigo:

* E1/E4 relevo: inclinação em %, classes da Embrapa (plano 0–3 %, suave ondulado 3–8 %, ondulado 8–20 %,
  forte ondulado 20–45 %, montanhoso 45–75 %, escarpado acima de 75 %); 25° só como marco legal quando
  existe área acima dele; nenhum "máximo" (o valor antigo era o percentil 99,5);
* E2/E3/E6 solo: base nacional (IBGE + Embrapa + MapBiomas Solo) em qualquer UF, com a escala dita;
  IDE-Sisema, SoilGrids e "risco potencial de erosão: muito baixo" sozinho saem do relatório;
* E5 chuva: a chuva recente é da NASA POWER (grade de ~50 km). Em Curvelo, 12 meses da POWER deram
  839 mm contra ~1.280 mm do CHIRPS (5 km) no mesmo ponto: com a POWER o ano parece seco, com o CHIRPS
  não. Enquanto a chuva recente não vier da mesma base de 5 km do normal, o adjetivo da comparação
  ("acima/abaixo do normal") não é impresso; os milímetros medidos continuam, com a fonte.
"""
from __future__ import annotations

from typing import Any

import solo_nacional_t1 as solo_t1

num = solo_t1.num
RAIN_COMPARISON_PREFIX = "Chuva recente comparada"
RAIN_WITHHELD_REASON = "fonte_recente_grade_grossa"
RELIEF_SOURCE_NAME = "SRTM 1 arc-second — altitude e inclinação do terreno"
LEGAL_ROW_LABEL = "Inclinação acima de 25° (Lei 12.651/2012, art. 11: uso restrito)"
_OLD_SOURCE_MARKERS = (
    "ide-sisema / mapa de solos", "ide-sisema / aptidão", "ide-sisema / risco potencial de erosão",
    "ide-sisema / declividade", "isric soilgrids", "srtm 1 arc-second — altitude e declividade",
)
_OLD_CHECK_FACTORS = {"Solo", "Aptidão agrícola", "Declividade", "Declividade SRTM", "Relevo (SRTM)"}
_OLD_NEXT_STEP_MARKERS = ("composição físico-química", "soilgrids")  # só o pedido antigo de solo; achados de outras fontes ficam


# ------------------------------------------------------------------ relevo
def relief(terrain: dict[str, Any] | None) -> dict[str, Any] | None:
    t = terrain if isinstance(terrain, dict) else {}
    classes = t.get("slope_classes") or []
    if not t.get("ok") or t.get("slope_unit") != "%" or not classes:
        return None
    shares = {str(r.get("class")): float(r.get("share_pct") or 0.0) for r in classes}
    return {
        "median_pct": t.get("slope_median_pct"), "p90_pct": t.get("slope_p90_pct"),
        "flat_gentle_pct": round(shares.get("plano", 0.0) + shares.get("suave ondulado", 0.0), 2),
        "classes": [(str(r.get("class")), str(r.get("range") or ""), float(r.get("share_pct") or 0.0)) for r in classes],
        "legal_ge25_pct": t.get("slope_ge_25deg_share_pct"),
        "elevation": (t.get("elevation_min_m"), t.get("elevation_median_m"), t.get("elevation_max_m")),
    }


def relief_kpis(terrain: dict[str, Any] | None) -> list[dict[str, Any]]:
    r = relief(terrain)
    if not r:
        return [{"label": "Relevo", "value": "CONSULTA PENDENTE", "note": "modelo de elevação não respondeu nesta emissão", "status": "CONSULTA PENDENTE", "level": "attention"}]
    lo, med, hi = r["elevation"]
    return [
        {"label": "Altitude", "value": f"{num(med)} m", "note": f"de {num(lo)} a {num(hi)} m · SRTM ~30 m", "status": "CONSULTADA", "level": "ok"},
        {"label": "Inclinação", "value": f"{num(r['median_pct'], 1)}% mediana", "note": f"9 de cada 10 pontos até {num(r['p90_pct'], 1)}%", "status": "CONSULTADA", "level": "ok"},
        {"label": "Plano ou suave ondulado", "value": f"{num(r['flat_gentle_pct'], 1)}%", "note": "até 8% de inclinação (classes de relevo da Embrapa); não é laudo de mecanização", "status": "CONSULTADA", "level": "info"},
    ]


def relief_rows(terrain: dict[str, Any] | None) -> list[list[str]]:
    r = relief(terrain)
    if not r:
        return [["Relevo medido (classes da Embrapa)", solo_t1.PENDING_TEXT]] if isinstance(terrain, dict) else []
    parts = [f"{name} ({rng}) {num(share, 1)}%" for name, rng, share in r["classes"] if share >= 0.05]
    rows = [["Relevo medido no imóvel (classes da Embrapa, inclinação em %)", " · ".join(parts) + " do imóvel. Modelo de elevação SRTM ~30 m; não substitui levantamento topográfico."]]
    legal = r.get("legal_ge25_pct")
    if legal is not None and float(legal) >= 0.05:
        rows.append([LEGAL_ROW_LABEL, f"{num(legal, 1)}% do imóvel no modelo de elevação (~30 m); conferir em campo antes de qualquer conclusão."])
    return rows


def relief_check(terrain: dict[str, Any] | None) -> dict[str, Any] | None:
    r = relief(terrain)
    if not r:
        return None
    return {"factor": "Relevo (SRTM)", "scope": "modelo de elevação ~30 m dentro do CAR", "status": "consultada",
            "value": f"inclinação mediana {num(r['median_pct'], 1)}% · plano ou suave ondulado (até 8%): {num(r['flat_gentle_pct'], 1)}% do imóvel"}


def relief_source(terrain: dict[str, Any] | None) -> dict[str, Any] | None:
    r = relief(terrain)
    if not r:
        return None
    return {"name": RELIEF_SOURCE_NAME, "status": "CONSULTADA", "level": "ok",
            "description": (f"Modelo de elevação ~30 m recortado ao CAR: altitude mediana {num(r['elevation'][1])} m; inclinação mediana "
                            f"{num(r['median_pct'], 1)}%. Classes de relevo da Embrapa em porcentagem de inclinação. "
                            "Não substitui levantamento topográfico de campo.")}


# ------------------------------------------------------------------ chuva comparada
def withhold_rain_comparison(drought: dict[str, Any] | None) -> dict[str, Any]:
    """Estado que nunca vira adjetivo: a chuva recente (POWER, ~50 km) não é comparada ao normal."""
    base = {k: (drought or {}).get(k) for k in ("rain_sum_mm", "days", "period_start", "period_end") if isinstance(drought, dict)}
    return {**base, "status": "not_found", "state": None, "state_code": None, "alert": False, "summary": "",
            "reason": RAIN_WITHHELD_REASON, "method": None}


# ------------------------------------------------------------------ payload final
def _drop_sources(payload: dict[str, Any]) -> None:
    kept = []
    for src in payload.get("sources") or []:
        name = str((src or {}).get("name") or "").casefold() if isinstance(src, dict) else ""
        if any(marker in name for marker in _OLD_SOURCE_MARKERS):
            continue
        if name in {solo_t1.SOURCE_PEDOLOGIA.casefold(), solo_t1.SOURCE_APTIDAO.casefold(), solo_t1.SOURCE_ERODIBILIDADE.casefold(),
                    solo_t1.SOURCE_TEXTURA.casefold(), RELIEF_SOURCE_NAME.casefold()}:
            continue
        kept.append(src)
    payload["sources"] = kept


def _state_layer_only_elsewhere(result: dict[str, Any]) -> bool:
    """IDE-Sisema é de MG: fora de MG todas as camadas vêm "não aplicável" e não são consulta pendente."""
    ide = (result or {}).get("ide_layers")
    if not isinstance(ide, dict) or not ide:
        return False
    states = [v.get("state") for v in ide.values() if isinstance(v, dict) and v.get("state") != "superseded"]
    return bool(states) and all(s == "not_applicable" for s in states)


def _state(layer: dict[str, Any] | None) -> str | None:
    return (layer or {}).get("state") if isinstance(layer, dict) else None


def apply_payload(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    prod = payload.setdefault("productive", {})
    terrain = prod.get("terrain_srtm") if isinstance(prod.get("terrain_srtm"), dict) else (result or {}).get("terrain_srtm")
    solo = (result or {}).get("terra_nacional")
    texture = prod.get("solo_textura_t1")
    rel = relief(terrain)
    reading = solo_t1.soil_reading(solo, texture, rel)

    # E1/E2/E3/E4/E6 - quadro de solo e relevo
    prod["soil_rows"] = relief_rows(terrain) + reading["soil_rows"]
    prod["aptitude_rows"] = reading["aptitude_rows"]
    prod["erosion_rows"] = []  # o conceito certo (erodibilidade) está no quadro de solo, com o que ele não é
    landcover = prod.get("landcover_rows")
    if isinstance(landcover, list) and any(isinstance(r, (list, tuple)) and r and str(r[0]).startswith("MapBiomas") for r in landcover):
        # a camada regional de MG que o MapBiomas substituiu não vira "INDISPONÍVEL -" ao lado da resposta
        prod["landcover_rows"] = [r for r in landcover if isinstance(r, (list, tuple)) and r and str(r[0]).startswith("MapBiomas")]
    kpis = relief_kpis(terrain)
    apt_groups = [r for r in reading["aptitude_rows"] if r and r[0] not in ("Escala", "Aptidão agrícola")]
    if apt_groups:
        kpis.append({"label": "Aptidão (mapa regional)", "value": f"{len(apt_groups)} {'classe' if len(apt_groups) == 1 else 'classes'}",
                     "note": "Embrapa, unidades 1:250.000; ver quadro abaixo", "status": "CONSULTADA", "level": "info"})
    elif _state((solo or {}).get("aptidao") if isinstance(solo, dict) else None) == "pending":
        kpis.append({"label": "Aptidão (mapa regional)", "value": "CONSULTA PENDENTE", "note": "refeita na próxima emissão", "status": "CONSULTA PENDENTE", "level": "attention"})
    elif reading["screen"].get("textura"):
        dominant = max((texture.get("group_shares_pct") or {}).items(), key=lambda kv: kv[1])[0]
        kpis.append({"label": "Textura (0–30 cm)", "value": dominant, "note": "MapBiomas Solo, estimativa no imóvel", "status": "CONSULTADA", "level": "info"})
    prod["terrain_kpis"] = kpis[:4]

    checks_holder = payload.setdefault("agropecuaria", {}).setdefault("property_screening", {})
    checks = [c for c in checks_holder.get("checks") or [] if not (isinstance(c, dict) and c.get("factor") in _OLD_CHECK_FACTORS)]
    rc = relief_check(terrain)
    checks += ([rc] if rc else []) + reading["checks"]
    checks_holder["checks"] = checks

    _drop_sources(payload)
    if _state_layer_only_elsewhere(result):
        payload["sources"] = [s for s in payload["sources"] if not (isinstance(s, dict) and str(s.get("name") or "").startswith("IDE-Sisema"))]
    rs = relief_source(terrain)
    payload["sources"] += ([rs] if rs else []) + reading["sources"]

    solo_answered = _state((solo or {}).get("pedologia") if isinstance(solo, dict) else None) in ("found", "not_found")
    apt_answered = _state((solo or {}).get("aptidao") if isinstance(solo, dict) else None) in ("found", "not_found")
    relief_ok = rel is not None
    for item in payload.get("compliance") or []:
        if isinstance(item, dict) and item.get("label") == "Solo / aptidão":
            item["text"] = " • ".join((
                "Solo: " + ("consultado no mapa nacional" if solo_answered else "consulta pendente"),
                "Aptidão: " + ("consultada no mapa nacional" if apt_answered else "consulta pendente"),
                "Relevo: " + ("medido no imóvel" if relief_ok else "consulta pendente"),
            ))
            item["badge"] = "CONSULTADO" if (solo_answered and relief_ok) else "PARCIAL"
            item["level"] = "ok" if (solo_answered and relief_ok) else "attention"
    con = payload.get("conclusion")
    if isinstance(con, dict):
        for cat in con.get("categories") or []:
            if isinstance(cat, dict) and cat.get("label") == "Produtivo":
                said = []
                if reading["screen"].get("solo"):
                    said.append("Solo no mapa oficial: " + reading["screen"]["solo"])
                if rel:
                    said.append(f"Relevo medido: {num(rel['flat_gentle_pct'], 1)}% plano ou suave ondulado (até 8% de inclinação).")
                cat["text"] = " ".join(said) or "Solo e relevo: consulta pendente."

    # E5 - comparação da chuva recente com o normal
    water = payload.get("water")
    if isinstance(water, dict):
        water["rain_rows"] = [r for r in water.get("rain_rows") or [] if not (isinstance(r, (list, tuple)) and r and str(r[0]).startswith(RAIN_COMPARISON_PREFIX))]
        if "drought_screening" in water:
            water["drought_screening"] = withhold_rain_comparison(water.get("drought_screening"))

    nar = payload.get("narrative")
    if isinstance(nar, dict):
        for key in ("next_steps", "what_we_found", "attention", "why"):
            if isinstance(nar.get(key), list):
                nar[key] = [x for x in nar[key] if not any(m in str(x).casefold() for m in _OLD_NEXT_STEP_MARKERS)]
    payload["terra_t1"] = {"version": "T1", "relief": bool(rel), "soil_states": {k: _state((solo or {}).get(k)) for k in solo_t1.LAYERS} if isinstance(solo, dict) else None,
                           "texture_state": (texture or {}).get("state") if isinstance(texture, dict) else None, "rain_comparison": "withheld"}
    return payload


print("RX_TERRA_VERDADE_T1=relevo_pct_solo_nacional_chuva_sem_adjetivo", flush=True)
