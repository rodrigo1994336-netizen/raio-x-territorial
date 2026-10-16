from __future__ import annotations

import threading

import report_engine_v6 as v6
import report_ptbr_v50
import report_engine_v8 as v8
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer


report_ptbr_v50.install()
_RENDER_LOCK = threading.RLock()


def build_premium_property_report_v9(path, payload):
    """V8 plus the V47 CAR integrity/composition table.

    The insertion is scoped to the environmental section and delegates all
    existing rendering to V8/V7/V6, preserving prior pagination and visual
    behavior outside the new V47 block.
    """
    payload = report_ptbr_v50.client_payload(payload)
    env = payload.get("environment") or {}
    rows = env.get("car_integrity_table_rows") or []
    integrity = payload.get("car_integrity_v47") or {}
    original_section = v6._section

    scope_note = payload.get("sources_scope_note") or ""

    def section(title, subtitle):
        blocks = original_section(title, subtitle)
        # T2: o que o relatório NÃO cobre é dito uma vez, em linha de escopo, fora da tabela de situação
        # das fontes — nunca como uma fonte "NÃO CONSULTADA" no meio das que foram consultadas.
        if title == "Cobertura das fontes" and scope_note:
            blocks += [Spacer(1, 2 * mm), v6.P(scope_note, "small")]
            return blocks
        if title != "Ambiental e fiscalização":
            return blocks
        blocks += [Spacer(1, 3 * mm), Paragraph("Consistência da declaração ambiental do CAR", v6.S["h2"])]
        if rows:
            blocks.append(
                v6._info(
                    rows,
                    [37 * mm, 42 * mm, 47 * mm, 39 * mm],
                    ["Informação", "Declarado no CAR", "Dentro do perímetro", "Medido por nós"],
                )
            )
            ov = integrity.get("overlap") or {}
            if ov.get("state") == "checked":
                overlap_text = (
                    f"{int(ov.get('distinct_car_count') or 0)} outro(s) CAR(s) com interseção de área positiva; "
                    f"união das interseções {float(ov.get('union_area_ha') or 0):.4f} ha "
                    f"({float(ov.get('property_pct') or 0):.2f}% do imóvel)."
                )
            else:
                overlap_text = "A conferência de sobreposição com outros CARs ficou pendente agora; isso não quer dizer que não haja sobreposição."
            blocks += [Spacer(1, 3 * mm), v6._callout("SOBREPOSIÇÃO ENTRE CARs", overlap_text, "attention" if ov.get("distinct_car_count") else "info")]

            municipality = integrity.get("municipality_boundary") or {}
            uf = integrity.get("uf_boundary") or {}
            def boundary_text(label, item):
                if item.get("state") == "checked":
                    return f"{label}: {float(item.get('outside_ha') or 0):.4f} ha fora ({float(item.get('outside_pct') or 0):.2f}%)."
                if item.get("state") == "not_configured":
                    return f"{label}: limite oficial não disponível aqui; nada foi concluído a respeito."
                return f"{label}: conferência pendente nesta emissão."
            blocks += [
                Spacer(1, 2 * mm),
                v6.P(boundary_text("Município codificado no CAR", municipality) + " " + boundary_text("UF cadastrada", uf), "small"),
                v6.P("A linha de regeneração é o que sobra da conta entre as áreas declaradas; ela não prova que a vegetação voltou nem resolve questão legal.", "small"),
            ]
        else:
            # T2: o nome do espelho de dados e o estado técnico não dizem nada a quem compra terra.
            blocks.append(v6._callout(
                "COMPOSIÇÃO DO CAR — CONSULTA PENDENTE",
                "A base do SICAR não respondeu esta parte agora. Isso não quer dizer que as áreas sejam zero: "
                "a consulta é refeita na próxima emissão.",
                "attention",
            ))
        return blocks

    # V8/V7/V6 use scoped module-level renderer hooks. Serialize this narrow
    # critical section so concurrent PDF requests cannot cross-contaminate the
    # environmental table/captions of different properties.
    with _RENDER_LOCK:
        original_section = v6._section
        v6._section = section
        try:
            return v8.build_premium_property_report_v8(path, payload)
        finally:
            v6._section = original_section


print("RX_REPORT_ENGINE=V9_CAR_INTEGRITY_TABLE", flush=True)