"""Relatório V10 (F2): V9 + as seções novas das frentes de verdade e lacunas.

Fino por construção: não redesenha nada do V9/V8/V7/V6. Usa o mesmo gancho de seção
(``report_engine_v6._section``) para inserir, em português simples:

* antes de "Mineração…": alertas recentes de desmatamento e patrimônio arqueológico (IPHAN);
* antes de "Conclusão…": outorgas de água (tabela de 7 colunas) e armazéns da CONAB;
* no começo de "Rastreabilidade e limitações" (a página final de fontes): os créditos de
  licença das bases combinadas. É o ÚNICO lugar onde esses créditos aparecem.

Campo vazio não aparece: uma seção sem dado da frente não é desenhada.
"""
from __future__ import annotations

import threading

import report_engine_v6 as v6
import report_engine_v9 as v9
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, Spacer

_RENDER_LOCK = threading.RLock()
BEFORE_MINING = "Mineração, minerais críticos e terras raras"
BEFORE_CONCLUSION = "Conclusão, próximos passos e monitoramento"
SOURCES_PAGE = "Rastreabilidade e limitações"
GRANT_WIDTHS = [21 * mm, 25 * mm, 19 * mm, 20 * mm, 31 * mm, 30 * mm, 19 * mm]
WAREHOUSE_WIDTHS = [68 * mm, 37 * mm, 32 * mm, 28 * mm]
SITE_WIDTHS = [62 * mm, 70 * mm, 33 * mm]


def _para(text, style="body"):
    return v6.P(str(text), style)


def _lines(items):
    return [Paragraph("• " + v6._s(x), v6.S["bullet"]) for x in items if str(x or "").strip()]


def alerts_blocks(payload):
    env = payload.get("environment") or {}
    alerts = env.get("deforestation_alerts") or {}
    iphan = env.get("iphan_f2") or {}
    blocks = []
    if alerts.get("summary"):
        blocks += [Paragraph(v6._s(alerts.get("title") or "Alertas recentes de desmatamento"), v6.S["h2"]), _para(alerts["summary"])]
        if alerts.get("items"):
            blocks += [Spacer(1, 2 * mm)] + _lines(alerts["items"])
        if alerts.get("other_lines"):
            blocks += [Spacer(1, 2 * mm), _para("Outras mudanças na vegetação (INPE):", "cellb")] + _lines(alerts["other_lines"])
        blocks += [Spacer(1, 1.5 * mm)] + [_para(n, "small") for n in alerts.get("notes") or [] if n]
    if iphan.get("headline"):
        blocks += [Spacer(1, 5 * mm), Paragraph(v6._s(iphan.get("title") or "Patrimônio arqueológico (IPHAN)"), v6.S["h2"]), _para(iphan["headline"])]
        inside = iphan.get("inside") or {}
        if inside.get("rows"):
            blocks += [Spacer(1, 2 * mm), v6._info(inside["rows"], SITE_WIDTHS, inside.get("headers") or ["Sítio", "Tipo", "Posição"])]
        near = iphan.get("near") or {}
        if iphan.get("near_text"):
            blocks += [Spacer(1, 2 * mm), _para(iphan["near_text"])]
        if near.get("rows"):
            blocks += [Spacer(1, 2 * mm), v6._info(near["rows"], SITE_WIDTHS, near.get("headers") or ["Sítio", "Tipo", "Distância da divisa"])]
        blocks += [Spacer(1, 1.5 * mm)] + [_para(n, "small") for n in iphan.get("notes") or [] if n]
    return blocks


def water_infra_blocks(payload):
    water = payload.get("water") or {}
    grants = water.get("grants_f2") or {}
    conab = (payload.get("infrastructure") or {}).get("warehouses_f2") or {}
    blocks = []
    if grants.get("headline"):
        blocks += [Paragraph(v6._s(grants.get("title") or "Outorgas de água dentro do imóvel"), v6.S["h2"]), _para(grants["headline"])]
        if grants.get("near_text"):
            blocks += [Spacer(1, 1.5 * mm), _para(grants["near_text"])]
        if grants.get("rows"):
            blocks += [Spacer(1, 2 * mm), v6._info(grants["rows"], GRANT_WIDTHS, grants.get("headers"))]
        blocks += [Spacer(1, 1.5 * mm)] + [_para(n, "small") for n in grants.get("notes") or [] if n]
    if conab.get("text"):
        blocks += [Spacer(1, 5 * mm), Paragraph("Armazéns cadastrados na CONAB (até 50 km)", v6.S["h2"]), _para(conab["text"])]
        if conab.get("state") == "found" and conab.get("table_rows"):
            blocks += [Spacer(1, 2 * mm), v6._info(conab["table_rows"], WAREHOUSE_WIDTHS, ["Armazém", "Município/UF", "Capacidade", "Distância"])]
        if conab.get("state") in ("found", "not_found") and conab.get("source_text"):
            blocks += [Spacer(1, 1.5 * mm), _para("Fonte: " + str(conab["source_text"]) + ". Distância até a divisa do imóvel.", "small")]
    return blocks


def credits_blocks(payload):
    credits = [c for c in payload.get("sources_page_credits") or [] if isinstance(c, dict) and c.get("text")]
    if not credits:
        return []
    return [Paragraph("Créditos das bases combinadas neste relatório", v6.S["h2"])] + [
        _para(f"{c['text']} ({c['license_url']})." if c.get("license_url") else f"{c['text']}.", "small") for c in credits
    ] + [Spacer(1, 4 * mm)]


def build_premium_property_report_v10(path, payload):
    alerts = alerts_blocks(payload)
    water = water_infra_blocks(payload)
    credits = credits_blocks(payload)

    with _RENDER_LOCK:
        original_section = v6._section

        def section(title, subtitle):
            if title == BEFORE_MINING and alerts:
                head = original_section("Alertas recentes e patrimônio arqueológico",
                                        "Alerta de satélite não é auto de infração; sítio próximo não é restrição dentro do imóvel.")
                return head + alerts + [PageBreak()] + original_section(title, subtitle)
            if title == BEFORE_CONCLUSION and water:
                head = original_section("Outorgas de água e armazéns próximos",
                                        "Vazão outorgada não é água garantida; armazém cadastrado não é contrato de armazenagem.")
                return head + water + [PageBreak()] + original_section(title, subtitle)
            if title == SOURCES_PAGE and credits:
                return original_section(title, subtitle) + credits
            return original_section(title, subtitle)

        v6._section = section
        try:
            return v9.build_premium_property_report_v9(path, payload)
        finally:
            v6._section = original_section


print("RX_REPORT_ENGINE=V10_F2_ALERTAS_IPHAN_OUTORGA_CONAB", flush=True)
