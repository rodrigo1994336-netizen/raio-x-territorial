"""F2 · verdade e lacunas: liga as frentes novas ao relatório, encadeando o V19.

O que entra aqui (o resto das frentes já vem pela cadeia):

* armazéns cadastrados na CONAB até 50 km (``conab_armazens``);
* alertas recentes de desmatamento: DETER do INPE e alertas validados por código do CAR,
  combinados numa seção só (``deter_alertas`` + ``mapbiomas_alerta``). Decisão do dono
  (13/09): o cliente lê só "alerta de desmatamento validado"; o nome da fonte de alertas
  validados, laudo e número do alerta nunca entram no corpo. O crédito da licença vai só
  para a página final de fontes (``sources_page_credits``);
* outorgas por tipo de uso, com vazão, regime e validade (``outorga_vazao``);
* satélite da imagem PRODES só com prova de órbita (``prodes_image_platform_f2``).

Já ligados antes deste arquivo: INCRA (V19), leitura do PRODES (``prodes_truth_v44``),
IPHAN (``live_report_adapter_v11``), chuva comparada à média da época (V13).

Regra comum: fonte que não respondeu vira "Consulta pendente." e nunca "nenhum". Cada
módulo tem o próprio prazo; este arquivo não cria prazo novo (timeout só com medição).
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import conab_armazens
import deter_alertas
import incra_acervo_f2
import live_report_adapter as base_payload
import live_report_adapter_v18 as v18
import live_report_adapter_v19 as v19
import mapbiomas_alerta
import outorga_vazao
import prodes_image_platform_f2
from report_engine_v10 import build_premium_property_report_v10

REPORT_VERSION = "F2-VERDADE-E-LACUNAS"
PENDING_TEXT = "Consulta pendente."
ALERTS_SOURCE_NAME = "Alertas de desmatamento validados — consulta pública por código do CAR"
_ANSWERED = ("found", "not_found")

_CTX = threading.local()
_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="rx-f2-extra")


# ------------------------------------------------------------------ consultas
def _first(props: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if props.get(key) not in (None, ""):
            return props.get(key)
    return None


def _centroid_lonlat(geometry: Any) -> tuple[float, float] | None:
    try:
        from shapely.geometry import shape

        c = shape(geometry).centroid
        return (round(c.x, 6), round(c.y, 6)) if not c.is_empty else None
    except Exception:
        return None


def _pending_conab() -> dict[str, Any]:
    return conab_armazens.build_warehouses_payload(None, None, pendente=True)


def _pending_deter() -> dict[str, Any]:
    return {"payload": {"source": "inpe_deter", "state": "pending", "complete": False, "title": "Alertas recentes de desmatamento",
                        "text": PENDING_TEXT, "notes": [], "events": [], "other_events": [], "source_text": deter_alertas.FONTE_TEXTO},
            "combiner": {"state": "pending", "features": []}}


def _pending_alerts() -> dict[str, Any]:
    return {"state": "pending", "needs_retry": True, "text": {"summary": mapbiomas_alerta.PENDING_TEXT, "items": []}}


def _outcome(future, fallback: Callable[[], dict[str, Any]], label: str) -> dict[str, Any]:
    """Resultado da consulta; exceção ou formato estranho viram pendência, nunca "nenhum"."""
    try:
        value = future.result()
    except Exception as exc:
        print(f"RX_F2_REPORT_EXTRA={label}:error={type(exc).__name__}", flush=True)
        return fallback()
    if not isinstance(value, dict):
        print(f"RX_F2_REPORT_EXTRA={label}:unexpected_answer", flush=True)
        return fallback()
    return value


def start_queries(result: dict[str, Any], car_code: str) -> dict[str, Any]:
    """Dispara as consultas de rede das frentes em paralelo com a cadeia do relatório."""
    car = (result or {}).get("car") or {}
    geometry = car.get("geometry")
    props = car.get("properties") or {}
    code = props.get("cod_imovel") or car_code
    updated = _first(props, "dat_atuali", "data_atualizacao", "dt_atualizacao")
    return {
        "conab": _POOL.submit(conab_armazens.conab_warehouses_payload, geometry),
        "deter": _POOL.submit(deter_alertas.deter_alerts_bundle, geometry, (result or {}).get("prodes")),
        "alerts": _POOL.submit(mapbiomas_alerta.query_mapbiomas_alerta, code, geometry, car_updated_at=updated),
    }


def prodes_image_lookups(result: dict[str, Any]) -> dict[str, Any]:
    """Satélite de cada imagem PRODES dentro do imóvel, só com órbita WRS-2 e centróide.

    Sem prova (órbita, centróide ou catálogo), nada é consultado ou mostrado além da data.
    """
    try:
        occurrences = base_payload._extract_prodes_occurrences(result)
        if not occurrences:
            return {}
        lonlat = _centroid_lonlat(((result or {}).get("car") or {}).get("geometry"))
        return prodes_image_platform_f2.query_platforms_for_occurrences(occurrences, lonlat=lonlat)
    except Exception as exc:
        print(f"RX_F2_REPORT_EXTRA=prodes_image:error={type(exc).__name__}", flush=True)
        return {}


def collect(ctx: dict[str, Any]) -> dict[str, Any]:
    futures = ctx.get("futures") or {}
    out = {}
    for key, fallback in (("conab", _pending_conab), ("deter", _pending_deter), ("alerts", _pending_alerts)):
        out[key] = _outcome(futures[key], fallback, key) if key in futures else fallback()
    bundle = out["deter"]
    if not isinstance(bundle.get("payload"), dict) or not isinstance(bundle.get("combiner"), dict):
        out["deter"] = _pending_deter()
    return out


# ------------------------------------------------------------------ payload
def _source(name: str, description: str, answered: bool) -> dict[str, Any]:
    return {"name": name, "description": description, "status": "CONSULTADA" if answered else "INDISPONÍVEL",
            "level": "ok" if answered else "attention"}


def _replace_source(payload: dict[str, Any], match: Callable[[str], bool], row: dict[str, Any] | None) -> None:
    kept = [s for s in payload.get("sources") or [] if not (isinstance(s, dict) and match(str(s.get("name") or "")))]
    if row:
        kept.append(row)
    payload["sources"] = kept


def alerts_section(combined: dict[str, Any], deter_payload: dict[str, Any]) -> dict[str, Any]:
    """Uma seção de alertas recentes, sem nome da fonte de alertas validados e sem geometria."""
    text = combined.get("text") if isinstance(combined.get("text"), dict) else {}
    state = combined.get("state") or "pending"
    summary = str(text.get("summary") or mapbiomas_alerta.COMBINED_PENDING_TEXT)
    sources = combined.get("sources") if isinstance(combined.get("sources"), dict) else {}
    notes = [n for n in (deter_payload.get("notes") or []) if n == deter_alertas.NOTA_COBERTURA_PARCIAL]
    if notes and sources.get("validated_alerts") != "answered" and state in ("not_found", "partial"):
        # Só o INPE respondeu e ele monitora só parte do imóvel: "nenhum" vale para essa parte.
        summary = summary.replace("sobre o imóvel", "sobre a parte monitorada do imóvel", 1)
    if text.get("disclaimer"):
        notes.append(str(text["disclaimer"]))
    if combined.get("credit_note"):
        notes.append(str(combined["credit_note"]))
    other = []
    if deter_payload.get("state") in _ANSWERED:
        other = [str(e.get("line")) for e in deter_payload.get("other_events") or [] if isinstance(e, dict) and e.get("line")]
    title = "Alertas recentes de desmatamento" + (" e de outras mudanças na vegetação" if other else "")
    return {
        "state": state,
        "title": title,
        "summary": summary,
        "items": [str(i.get("text")) for i in text.get("items") or [] if isinstance(i, dict) and i.get("text")],
        "other_lines": other,
        "notes": notes,
        "recent_after": combined.get("recent_after"),
        "sources": {k: v for k, v in sources.items()},
        "event_count": combined.get("event_count"),
        "area_union_ha": combined.get("area_union_ha"),
    }


def _apply_alerts(payload: dict[str, Any], result: dict[str, Any], bundle: dict[str, Any], alerts: dict[str, Any]) -> None:
    car = (result or {}).get("car") or {}
    combiner = bundle.get("combiner") or {}
    combined = mapbiomas_alerta.combine_deforestation_alerts(car.get("geometry"), alerts, combiner, (result or {}).get("prodes"),
                                                             combiner.get("cutoff"))
    section = alerts_section(combined, bundle.get("payload") or {})
    payload.setdefault("environment", {})["deforestation_alerts"] = section
    credits = [c for c in combined.get("sources_page_credits") or [] if isinstance(c, dict) and c.get("text")]
    if credits:
        payload["sources_page_credits"] = credits
    deter = bundle.get("payload") or {}
    deter_ok = deter.get("state") in _ANSWERED + ("not_covered",)
    _replace_source(payload, lambda n: "DETER" in n, _source(str(deter.get("source_text") or deter_alertas.FONTE_TEXTO),
                                                             str(deter.get("text") or PENDING_TEXT), deter_ok))
    alerts_ok = alerts.get("state") in _ANSWERED
    alerts_text = ((alerts.get("text") or {}).get("summary") if isinstance(alerts.get("text"), dict) else None) or PENDING_TEXT
    _replace_source(payload, lambda n: n == ALERTS_SOURCE_NAME, _source(ALERTS_SOURCE_NAME, str(alerts_text), alerts_ok))


def _apply_conab(payload: dict[str, Any], conab: dict[str, Any]) -> None:
    infra = payload.setdefault("infrastructure", {})
    infra["warehouses_f2"] = conab
    infra["warehouses"] = list(conab.get("table_rows") or []) if conab.get("state") == "found" else []
    _replace_source(payload, lambda n: n.startswith("CONAB — Cadastro Nacional de Unidades Armazenadoras"),
                    _source(str(conab.get("source_text") or conab_armazens.FONTE), str(conab.get("text") or PENDING_TEXT),
                            conab.get("state") in _ANSWERED))
    nar = payload.get("narrative")
    if isinstance(nar, dict) and isinstance(nar.get("next_steps"), list):
        # Item de roteiro interno ("completar armazéns CONAB"): a seção de armazéns já responde.
        nar["next_steps"] = [x for x in nar["next_steps"] if "armazéns CONAB" not in str(x)]


def _apply_grants(payload: dict[str, Any], result: dict[str, Any]) -> None:
    props = (((result or {}).get("car") or {}).get("properties") or {})
    grants = outorga_vazao.water_grants_payload((result or {}).get("water_mg"), uf=props.get("uf"), ana=(result or {}).get("water_ana"))
    water = payload.setdefault("water", {})
    water["grants_f2"] = grants
    state = grants.get("state")
    old_source = lambda n: n.startswith("IGAM + ANA / IDE-Sisema - Outorgas") or n.startswith("Outorgas de água —")
    compliance = [c for c in payload.get("compliance") or [] if not (isinstance(c, dict) and c.get("label") == "Outorgas de uso de água")]
    attention = [x for x in payload.get("attention_points") or [] if not str(x).startswith("Outorgas:")]
    if state == outorga_vazao.STATE_NOT_COVERED:
        # Fora de MG e sem a camada nacional: a seção não aparece (campo vazio não aparece).
        water["grants"] = []
        water.pop("meaning", None)
        _replace_source(payload, old_source, None)
    else:
        water["grants"] = []  # a tabela antiga de 5 colunas dá lugar à de 7 (report_engine_v10)
        if state == outorga_vazao.STATE_PENDING:
            water["grant_count"] = "NÃO CONSULTADO"
            water["meaning"] = "Outorgas de água: consulta pendente."
        else:
            water["grant_count"] = grants.get("count")
            water["meaning"] = " ".join(x for x in (grants.get("headline"), grants.get("near_text")) if x)
            if grants.get("count"):
                attention.append("Outorgas: " + str((grants.get("compliance_row") or {}).get("text") or ""))
        if grants.get("compliance_row"):
            compliance.append(grants["compliance_row"])
        _replace_source(payload, old_source, grants.get("source_row"))
    payload["compliance"] = compliance
    if "attention_points" in payload or attention:
        payload["attention_points"] = attention
    con = payload.get("conclusion")
    if isinstance(con, dict) and state in (outorga_vazao.STATE_FOUND, outorga_vazao.STATE_NOT_FOUND):
        for cat in con.get("categories") or []:
            if isinstance(cat, dict) and cat.get("label") == "Hídrico" and grants.get("compliance_row"):
                cat["text"] = grants["compliance_row"]["text"]
    nar = payload.get("narrative")
    if isinstance(nar, dict) and isinstance(nar.get("what_we_found"), list):
        found = [x for x in nar["what_we_found"] if not str(x).startswith("Água:")]
        if state in (outorga_vazao.STATE_FOUND, outorga_vazao.STATE_NOT_FOUND) and grants.get("compliance_row"):
            found.append("Água: " + grants["compliance_row"]["text"][:1].lower() + grants["compliance_row"]["text"][1:])
        nar["what_we_found"] = found


def _apply_prodes_images(payload: dict[str, Any], result: dict[str, Any]) -> None:
    """Acrescenta o satélite (com prova) logo depois da linha de cada desmatamento dentro do imóvel."""
    pd = (payload.get("environment") or {}).get("prodes")
    if not isinstance(pd, dict) or not isinstance(pd.get("rows"), list):
        return
    try:
        occurrences = base_payload._extract_prodes_occurrences(result)
    except Exception:
        return
    extra: dict[str, list[list[str]]] = {}
    for occ in occurrences:
        row = prodes_image_platform_f2.image_row(occ, occ.get("image_lookup"))
        if row and " · " in row[1] and occ.get("year"):
            extra.setdefault(f"PRODES {occ['year']}", []).append([f"Imagem do PRODES {occ['year']}", row[1]])
    if not extra:
        return
    rows = []
    for row in pd["rows"]:
        rows.append(row)
        label = str(row[0]) if isinstance(row, (list, tuple)) and row else ""
        rows.extend(extra.pop(label, []))
    pd["rows"] = rows


def apply_f2_payload(payload: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    result = ctx.get("result") or {}
    answers = collect(ctx)
    _apply_alerts(payload, result, answers["deter"], answers["alerts"])
    _apply_conab(payload, answers["conab"])
    _apply_grants(payload, result)
    _apply_prodes_images(payload, result)
    payload["source_version"] = f"{payload.get('source_version') or ''} F2 · verdade e lacunas.".strip()
    return payload


# ------------------------------------------------------------------ render e cadeia
def render_v20(path, payload):
    ctx = getattr(_CTX, "value", None)
    if ctx:
        t0 = time.monotonic()
        payload = apply_f2_payload(payload, ctx)
        Path(path).parent.joinpath("payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"RX_REPORT_STAGE=f2_payload:{round((time.monotonic() - t0) * 1000)}ms", flush=True)
    return build_premium_property_report_v10(path, payload)


def generate_live_report(result: dict, car_code: str):
    working = dict(result or {})
    futures = start_queries(working, car_code)
    working["prodes_image_lookups"] = prodes_image_lookups(working)
    _CTX.value = {"futures": futures, "result": working}
    try:
        meta = v19.generate_live_report(working, car_code)
    finally:
        _CTX.value = None
    # O V19 guarda a resposta completa do INCRA no dicionário que recebeu: repassa para a análise em cache.
    if isinstance(result, dict) and incra_acervo_f2.is_complete(working.get("incra_acervo")):
        result["incra_acervo"] = working["incra_acervo"]
    meta["report_version"] = REPORT_VERSION
    return meta


v18.build_premium_property_report_v8 = render_v20

print("RX_LIVE_REPORT_ADAPTER=F2_VERDADE_E_LACUNAS_CONAB_ALERTAS_OUTORGA_LANDSAT", flush=True)
