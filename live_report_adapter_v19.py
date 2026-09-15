from __future__ import annotations

import asyncio
from pathlib import Path

import incra_acervo_f2
import live_report_adapter_v18 as v18
import live_report_adapter_v17 as v17
import live_report_adapter_v13 as v13
from report_engine_v9 import build_premium_property_report_v9
import sicar_overlap_hardening_v47  # applies narrow CAR-overlap dedup patch
from sicar_integrity_v47 import query_car_integrity_v47
from sicar_integrity_display_v47 import format_number_ptbr, format_snapshot_ptbr, panel_table_rows


async def _extras_v47(result: dict, car_code: str, out_dir: Path):
    """V17 extras with the environmental WFS slot replaced by Base dos Dados."""
    car = result.get("car") or {}
    geom = car.get("geometry")
    props = car.get("properties") or {}
    return await asyncio.gather(
        v17.build_hybrid_property_imagery(geom, out_dir / "property_visual.jpg"),
        v13.query_groundwater(geom, 20.0),
        v13.query_safras(car_code),
        asyncio.to_thread(query_car_integrity_v47, car_code),
        asyncio.to_thread(v13.query_aerodromes_anac, geom, 50.0, 12),
        asyncio.to_thread(v13.query_soil_texture, geom),
        asyncio.to_thread(v13.query_climatology_nasa, geom),
        v13.query_sif_establishments(props.get("municipio"), props.get("uf"), 30),
        return_exceptions=True,
    )


def _patch_car_integrity_v47(payload: dict, integrity: dict):
    car = payload.setdefault("car", {})
    env = payload.setdefault("environment", {})

    car["fields"] = [
        row for row in list(car.get("fields") or [])
        if not str((row or [""])[0]).startswith("Composição CAR —")
    ]
    env["layer_rows"] = [
        row for row in list(env.get("layer_rows") or [])
        if not str((row or [""])[0]).startswith("CAR —")
    ]
    payload["sources"] = [
        src for src in list(payload.get("sources") or [])
        if "sicar — composição" not in str(src.get("name") or "").casefold()
    ]

    payload["car_integrity_v47"] = integrity
    table = panel_table_rows(integrity) if integrity.get("ok") else []
    env["car_integrity_table_rows"] = [
        [row["information"], row["declared"], row["inside"], row["measured"]]
        for row in table
    ]

    composition = []
    if integrity.get("ok"):
        for row in integrity.get("composition_rows") or []:
            composition.append({
                "label": row.get("label"),
                "declared_ha": row.get("declared_ha"),
                "inside_ha": row.get("inside_ha"),
                "inside_pct": row.get("inside_pct"),
                "measured_ha": row.get("measured_ha"),
                "state": row.get("state"),
                "discrepancy": row.get("discrepancy"),
            })
            if row.get("measured_ha") is not None:
                env.setdefault("layer_rows", []).append([
                    f"CAR — {row.get('label')}",
                    f"{format_number_ptbr(row.get('measured_ha'), 4)} ha medidos",
                    "Base dos Dados / SICAR · GRS80",
                ])
        car["environmental_composition"] = composition
        status = "CONSULTADA" if integrity.get("state") == "checked" else "PARCIAL"
        payload.setdefault("sources", []).append({
            "name": "Base dos Dados / SICAR — composição e consistência geométrica do CAR",
            "description": (
                f"Snapshot {format_snapshot_ptbr(integrity.get('snapshot'))}; áreas declaradas separadas de medição elipsoidal GRS80. "
                "Sobreposição com outros CARs usa união de interseções de área positiva. "
                "Residual de regeneração é apenas geométrico."
            ),
            "status": status,
            "level": "ok" if status == "CONSULTADA" else "attention",
        })
    else:
        car["environmental_composition"] = []
        payload.setdefault("sources", []).append({
            "name": "Base dos Dados / SICAR — composição e consistência geométrica do CAR",
            "description": f"Consulta indisponível nesta emissão: {integrity.get('detail') or 'sem detalhe'}. Nenhum zero foi inferido.",
            "status": "INDISPONÍVEL",
            "level": "attention",
        })
    return payload


v13._extras = _extras_v47
v13._patch_car_details = _patch_car_integrity_v47
v18.build_premium_property_report_v8 = build_premium_property_report_v9


def with_incra_acervo(result: dict) -> dict:
    """F2: every report asks the official INCRA base (SIGEF + SNCI) for this CAR before the payload is built.

    A shallow copy carries the answer to the report chain; the truth guard (report_truth_guard_v16) reads
    ``result['incra_acervo']``, and without it every line would be pending. Only a complete answer is written
    back to the (cached) analysis, so the analysis summary shown next to the PDF says the same thing and the next
    emission reuses it; an incomplete answer is never cached, so the next emission asks again.
    """
    working = dict(result or {})
    acervo = incra_acervo_f2.acervo_for_report(result)
    working["incra_acervo"] = acervo
    if isinstance(result, dict) and incra_acervo_f2.is_complete(acervo):
        result["incra_acervo"] = acervo
    return working


def generate_live_report(result: dict, car_code: str):
    meta = v18.generate_live_report(with_incra_acervo(result), car_code)
    meta["report_version"] = "V47-BLOCK2-CANDIDATE"
    return meta


print("RX_LIVE_REPORT_ADAPTER=V47_BASEDOSDADOS_CAR_INTEGRITY_NO_ENV_WFS", flush=True)
