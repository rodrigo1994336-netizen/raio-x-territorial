"""T1 · terra-verdade: última palavra do relatório sobre relevo, solo, erosão e chuva comparada.

O estudo F1 (seção 0) provou informação errada ou enganosa chegando ao cliente. Este módulo é aplicado
no fim da cadeia do relatório (``live_report_adapter_v20.render_v20``), depois de todas as camadas de
remendo, para que nenhuma delas devolva o texto antigo:

* E1/E4 relevo: inclinação em %, classes da Embrapa (plano 0–3 %, suave ondulado 3–8 %, ondulado 8–20 %,
  forte ondulado 20–45 %, montanhoso 45–75 %, escarpado acima de 75 %), medida pelo método de Horn num
  mosaico das folhas SRTM; percentuais inteiros (o modelo de ~30 m não sustenta casa decimal). Marco legal
  em graus só quando existe área nele: 25° a 45° é uso restrito (Lei 12.651/2012, art. 11); acima de 45° é
  área de preservação permanente (art. 4º, V). Nenhum "máximo" (o valor antigo era o percentil 99,5);
* E2/E3/E6 solo: base nacional (IBGE + Embrapa + MapBiomas Solo) em qualquer UF, com a escala dita;
  IDE-Sisema, SoilGrids e "risco potencial de erosão: muito baixo" sozinho saem do relatório;
* E5 chuva: a chuva recente e a climatologia são da NASA POWER (grade de ~50 km). Em Curvelo, 12 meses
  da POWER deram 839 mm contra ~1.280 mm do CHIRPS (5 km) no mesmo ponto. Os milímetros saem inteiros e
  rotulados como estimativa regional, e a chuva recente não é comparada com o normal da época.
"""
from __future__ import annotations

import re
from typing import Any

import solo_nacional_t1 as solo_t1

num = solo_t1.num
pct_text = solo_t1.pct_text
RAIN_COMPARISON_PREFIX = "Chuva recente comparada"
RAIN_WITHHELD_REASON = "fonte_recente_grade_grossa"
RAIN_REGIONAL_TAG = "estimativa regional NASA POWER, grade de ~50 km"
RAIN_BASIS_LABEL = "Base da chuva e da climatologia"
RAIN_BASIS_TEXT = ("Estimativa regional da NASA POWER (grade de ~50 km), no centro do imóvel. Não é medição no imóvel: "
                   "a chuva local pode ser bem diferente. Por isso a chuva recente não é comparada com o normal da época.")
RELIEF_SOURCE_NAME = "SRTM 1 arc-second — altitude e inclinação do terreno"
LEGAL_RESTRICTED_LABEL = "Inclinação entre 25° e 45° (Lei 12.651/2012, art. 11: uso restrito)"
LEGAL_APP_LABEL = "Inclinação acima de 45° (Lei 12.651/2012, art. 4º, V: área de preservação permanente)"
LEGAL_SCREENING_TEXT = "do imóvel no modelo de elevação (~30 m); é triagem, conferir em campo antes de qualquer conclusão."
_OLD_SOURCE_MARKERS = (
    "ide-sisema / mapa de solos", "ide-sisema / aptidão", "ide-sisema / risco potencial de erosão",
    "ide-sisema / declividade", "isric soilgrids", "srtm 1 arc-second — altitude e declividade",
)
_OLD_CHECK_FACTORS = {"Solo", "Aptidão agrícola", "Declividade", "Declividade SRTM", "Relevo (SRTM)"}
_OLD_NEXT_STEP_MARKERS = ("composição físico-química", "soilgrids")  # só o pedido antigo de solo; achados de outras fontes ficam


# ------------------------------------------------------------------ relevo
def relief_state(terrain: dict[str, Any] | None) -> str | None:
    """found · not_found (limite do método: a linha some) · pending (falha: tenta de novo) · None (não rodou)."""
    if not isinstance(terrain, dict):
        return None
    if terrain.get("ok") and terrain.get("slope_unit") == "%" and terrain.get("slope_classes"):
        return "found"
    return "not_found" if terrain.get("state") == "not_found" else "pending"


def relief(terrain: dict[str, Any] | None) -> dict[str, Any] | None:
    t = terrain if isinstance(terrain, dict) else {}
    if relief_state(t) != "found":
        return None
    classes = t.get("slope_classes") or []
    shares = {str(r.get("class")): float(r.get("share_pct") or 0.0) for r in classes}
    legal_25_45 = t.get("slope_25_45deg_share_pct")
    legal_gt_45 = t.get("slope_gt_45deg_share_pct")
    return {
        "median_pct": t.get("slope_median_pct"), "p90_pct": t.get("slope_p90_pct"),
        "flat_gentle_pct": round(shares.get("plano", 0.0) + shares.get("suave ondulado", 0.0), 2),
        "classes": [(str(r.get("class")), str(r.get("range") or ""), float(r.get("share_pct") or 0.0)) for r in classes],
        "legal_25_45_pct": legal_25_45, "legal_gt_45_pct": legal_gt_45,
        "elevation": (t.get("elevation_min_m"), t.get("elevation_median_m"), t.get("elevation_max_m")),
    }


def relief_kpis(terrain: dict[str, Any] | None) -> list[dict[str, Any]]:
    r = relief(terrain)
    if not r:
        if relief_state(terrain) == "not_found":
            return []  # limite do método (imóvel pequeno ou grande demais para o modelo): campo vazio não aparece
        return [{"label": "Relevo", "value": "CONSULTA PENDENTE", "note": "modelo de elevação não respondeu nesta emissão", "status": "CONSULTA PENDENTE", "level": "attention"}]
    lo, med, hi = r["elevation"]
    return [
        {"label": "Altitude", "value": f"{num(med)} m", "note": f"de {num(lo)} a {num(hi)} m · SRTM ~30 m", "status": "CONSULTADA", "level": "ok"},
        {"label": "Inclinação", "value": f"{pct_text(r['median_pct'])} mediana", "note": f"9 de cada 10 pontos até {pct_text(r['p90_pct'])}", "status": "CONSULTADA", "level": "ok"},
        {"label": "Plano ou suave ondulado", "value": pct_text(r["flat_gentle_pct"]), "note": "até 8% de inclinação (classes de relevo da Embrapa); não é laudo de mecanização", "status": "CONSULTADA", "level": "info"},
    ]


def relief_rows(terrain: dict[str, Any] | None) -> list[list[str]]:
    r = relief(terrain)
    if not r:
        return [["Relevo medido (classes da Embrapa)", solo_t1.PENDING_TEXT]] if relief_state(terrain) == "pending" else []
    parts = [f"{name} ({rng}) {pct_text(share)}" for name, rng, share in r["classes"] if share > 0]
    rows = [["Relevo medido no imóvel (classes da Embrapa, inclinação em %)",
             " · ".join(parts) + " do imóvel. Modelo de elevação SRTM ~30 m, método de Horn; não substitui levantamento topográfico."]]
    for label, value in ((LEGAL_RESTRICTED_LABEL, r.get("legal_25_45_pct")), (LEGAL_APP_LABEL, r.get("legal_gt_45_pct"))):
        if value is not None and float(value) > 0:
            rows.append([label, f"{pct_text(value)} {LEGAL_SCREENING_TEXT}"])
    return rows


def relief_check(terrain: dict[str, Any] | None) -> dict[str, Any] | None:
    r = relief(terrain)
    if not r:
        return None
    return {"factor": "Relevo (SRTM)", "scope": "modelo de elevação ~30 m dentro do CAR", "status": "consultada",
            "value": f"inclinação mediana {pct_text(r['median_pct'])} · plano ou suave ondulado (até 8%): {pct_text(r['flat_gentle_pct'])} do imóvel"}


def relief_source(terrain: dict[str, Any] | None) -> dict[str, Any] | None:
    r = relief(terrain)
    if not r:
        return None
    return {"name": RELIEF_SOURCE_NAME, "status": "CONSULTADA", "level": "ok",
            "description": (f"Modelo de elevação ~30 m recortado ao CAR: altitude mediana {num(r['elevation'][1])} m; inclinação mediana "
                            f"{pct_text(r['median_pct'])}. Inclinação pelo método de Horn em mosaico das folhas; classes de relevo da Embrapa "
                            "em porcentagem de inclinação. Não substitui levantamento topográfico de campo.")}


# ------------------------------------------------------------------ chuva
def withhold_rain_comparison(drought: dict[str, Any] | None) -> dict[str, Any]:
    """Estado que nunca vira adjetivo: a chuva recente (POWER, ~50 km) não é comparada ao normal."""
    base = {k: (drought or {}).get(k) for k in ("rain_sum_mm", "days", "period_start", "period_end") if isinstance(drought, dict)}
    return {**base, "status": "not_found", "state": None, "state_code": None, "alert": False, "summary": "",
            "reason": RAIN_WITHHELD_REASON, "method": None}


_MM_DAY = re.compile(r"\s*\(\s*[\d.,]+\s*mm/dia\s*\)")


def _regional_rain_rows(water: dict[str, Any], climate: dict[str, Any]) -> None:
    """Milímetros da POWER inteiros e rotulados como estimativa regional; a base é dita uma vez."""
    rows = []
    has_rain = False
    for row in water.get("rain_rows") or []:
        if not (isinstance(row, (list, tuple)) and row):
            rows.append(row)
            continue
        label = str(row[0])
        if label.startswith(RAIN_COMPARISON_PREFIX) or label == RAIN_BASIS_LABEL:
            continue
        if label.startswith("Precipitação acumulada"):
            total = climate.get("rain_sum_mm") if climate.get("ok") else None
            if total is None:
                continue
            rows.append([label, f"{num(total)} mm ({RAIN_REGIONAL_TAG})"])
            has_rain = True
            continue
        if label.startswith("Precipitação média diária"):
            daily = climate.get("rain_daily_avg_mm") if climate.get("ok") else None
            if daily is None:
                continue
            rows.append([label, f"{num(daily, 1)} mm/dia (mesma estimativa regional)"])
            continue
        if label.startswith("Climatologia"):
            rows.append([label, _MM_DAY.sub("", str(row[1]) if len(row) > 1 else "")])
            has_rain = True
            continue
        rows.append(list(row))
    if has_rain:
        rows.insert(0, [RAIN_BASIS_LABEL, RAIN_BASIS_TEXT])
    water["rain_rows"] = rows
    if climate.get("ok") and climate.get("rain_sum_mm") is not None:
        water["rain_30d"] = f"{num(climate.get('rain_sum_mm'))} mm"
        period = str(water.get("rain_period") or "").strip()
        if RAIN_REGIONAL_TAG not in period:
            water["rain_period"] = (period + " · " if period else "") + RAIN_REGIONAL_TAG


def _regional_rain_check(payload: dict[str, Any], climate: dict[str, Any]) -> None:
    checks = ((payload.get("agropecuaria") or {}).get("property_screening") or {}).get("checks")
    if not isinstance(checks, list):
        return
    for check in checks:
        if isinstance(check, dict) and check.get("factor") == "Clima recente" and climate.get("ok"):
            said = []
            if climate.get("rain_sum_mm") is not None:
                said.append(f"chuva recente {num(climate.get('rain_sum_mm'))} mm")
            if climate.get("temp_avg_c") is not None:
                said.append(f"temperatura média {num(climate.get('temp_avg_c'), 1)} °C")
            if said:
                check["value"] = " · ".join(said) + f" ({RAIN_REGIONAL_TAG})"


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


def layer_state(solo: dict[str, Any] | None, key: str) -> str:
    """Estado de uma camada da base nacional; ausente ou sem estado conhecido é pendente, nunca respondido."""
    state = _state((solo or {}).get(key) if isinstance(solo, dict) else None)
    return state if state in ("found", "not_found") else "pending"


def apply_payload(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    prod = payload.setdefault("productive", {})
    terrain = prod.get("terrain_srtm") if isinstance(prod.get("terrain_srtm"), dict) else (result or {}).get("terrain_srtm")
    solo = (result or {}).get("terra_nacional")
    texture = prod.get("solo_textura_t1")
    rel = relief(terrain)
    rel_state = relief_state(terrain)
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
    apt_classes = reading["aptitude_classes"]
    if apt_classes:
        kpis.append({"label": "Aptidão (mapa regional)", "value": f"{apt_classes} {'classe' if apt_classes == 1 else 'classes'}",
                     "note": "Embrapa, unidades 1:250.000; ver quadro abaixo", "status": "CONSULTADA", "level": "info"})
    elif layer_state(solo, "aptidao") == "pending":
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

    solo_state = layer_state(solo, "pedologia")
    apt_state = layer_state(solo, "aptidao")
    parts = [
        ("Solo", "consultado no mapa nacional" if solo_state != "pending" else "consulta pendente", solo_state == "pending"),
        ("Aptidão", "consultada no mapa nacional" if apt_state != "pending" else "consulta pendente", apt_state == "pending"),
    ]
    if rel_state == "found":
        parts.append(("Relevo", "medido no imóvel", False))
    elif rel_state == "pending" or rel_state is None:
        parts.append(("Relevo", "consulta pendente", True))
    # limite do método (not_found): o relevo não é citado, nem como medido nem como pendente
    all_answered = not any(p[2] for p in parts)
    for item in payload.get("compliance") or []:
        if isinstance(item, dict) and item.get("label") == "Solo / aptidão":
            item["text"] = " • ".join(f"{name}: {text}" for name, text, _p in parts)
            item["badge"] = "CONSULTADO" if all_answered else "PARCIAL"
            item["level"] = "ok" if all_answered else "attention"
    con = payload.get("conclusion")
    if isinstance(con, dict):
        for cat in con.get("categories") or []:
            if isinstance(cat, dict) and cat.get("label") == "Produtivo":
                said = []
                if reading["screen"].get("solo"):
                    said.append("Solo no mapa oficial: " + reading["screen"]["solo"])
                if rel:
                    said.append(f"Relevo medido: {pct_text(rel['flat_gentle_pct'])} plano ou suave ondulado (até 8% de inclinação).")
                pending = [name.lower() for name, _t, p in parts if p]
                if pending and said:
                    said.append("Consulta pendente: " + ", ".join(pending) + ".")
                cat["text"] = " ".join(said) or "Solo e relevo: consulta pendente."
                # risco e nível vêm dos estados T1, não do texto antigo ("Solo indisponível..." dava ATENÇÃO)
                cat["risk"] = "TRIAGEM DISPONÍVEL" if all_answered else "CONSULTA PENDENTE"
                cat["level"] = "info" if all_answered else "neutral"

    # E5 - chuva da POWER: estimativa regional, inteira, sem comparação com o normal
    climate = (result or {}).get("climate_nasa") if isinstance((result or {}).get("climate_nasa"), dict) else {}
    water = payload.get("water")
    if isinstance(water, dict):
        _regional_rain_rows(water, climate)
        if "drought_screening" in water:
            water["drought_screening"] = withhold_rain_comparison(water.get("drought_screening"))
    _regional_rain_check(payload, climate)

    nar = payload.get("narrative")
    if isinstance(nar, dict):
        for key in ("next_steps", "what_we_found", "attention", "why", "why_it_matters"):
            if isinstance(nar.get(key), list):
                nar[key] = [x for x in nar[key] if not any(m in str(x).casefold() for m in _OLD_NEXT_STEP_MARKERS)]
    payload["terra_t1"] = {"version": "T1", "relief": rel_state, "soil_states": {k: _state((solo or {}).get(k)) for k in solo_t1.LAYERS} if isinstance(solo, dict) else None,
                           "texture_state": (texture or {}).get("state") if isinstance(texture, dict) else None, "rain_comparison": "withheld"}
    return payload


print("RX_TERRA_VERDADE_T1=relevo_pct_horn_solo_nacional_chuva_regional", flush=True)
