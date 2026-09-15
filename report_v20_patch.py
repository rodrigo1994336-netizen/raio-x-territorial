from __future__ import annotations

import asyncio
import os

import incra_acervo_f2
import report_api as base
import report_v13_patch as v13patch
import report_v19_patch  # noqa: F401  (a cadeia V19 continua embaixo do V20)
from live_report_adapter_v20 import generate_live_report

base.generate_live_report = generate_live_report
v13patch.generate_live_report = generate_live_report
base.APP_VERSION = "0.50.0-f2-verdade-e-lacunas"


async def _build_v20(car_code: str, property_name: str | None = None):
    """O mesmo _build_v13, devolvendo à análise em cache a resposta completa do INCRA.

    O V13 entrega ao relatório uma cópia rasa da análise; sem esta volta, o resumo "analysis"
    ao lado do PDF dizia SIGEF/SNCI pendente enquanto o PDF trazia a resposta oficial.
    """
    code = car_code.upper()
    result = await base._analyze_with_live_addons(code)
    working = dict(result)
    if property_name and str(property_name).strip():
        working["_requested_property_name"] = str(property_name).strip()[:120]
    async with base._REPORT_SEMAPHORE:
        try:
            meta = await asyncio.to_thread(v13patch.generate_live_report, working, code)
        finally:
            base._release_memory()
    if incra_acervo_f2.is_complete(working.get("incra_acervo")) and not incra_acervo_f2.is_complete(result.get("incra_acervo")):
        result["incra_acervo"] = working["incra_acervo"]
    return result, meta


_build_v13_previous = v13patch._build_v13  # o gate F2 usa como controle positivo (sem a volta do INCRA)
v13patch._build_v13 = _build_v20

if os.getenv("RX_RELEASE", "") == "":
    # Serviço do relatório: a base da CONAB baixa em segundo plano, fora do primeiro relatório.
    import conab_armazens

    conab_armazens.aquecer_em_segundo_plano()
    # T1: abre os arquivos do MapBiomas Solo (textura) fora do primeiro relatório.
    import solo_nacional_t1

    solo_nacional_t1.warm_texture_in_background()

print("RX_REPORT_F2_RUNTIME=conab_alertas_outorga_landsat_connected", flush=True)
