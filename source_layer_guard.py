"""H1 — a zero from a public layer is only an answer when the layer itself is alive.

Before any ArcGIS/WFS consultation tells the client "nenhum" / "0", three things
must hold, otherwise the result is a pending consultation:

1. the response is a valid, complete answer (HTTP 200, JSON object, no ``error``,
   a ``features`` list, no ``exceededTransferLimit`` / truncated page);
2. the layer has records: a global ``returnCountOnly`` (ArcGIS) or
   ``resultType=hits`` (WFS) at or above the floor registered below, cached for
   hours so the check costs one request per layer per process;
3. the base is not known to have stopped being fed (``stale_since``): a stale base
   can still show what it has, but its silence proves nothing.

Motivation (13/09/2026): the IBAMA layer used for embargoes
(``embargos_siscom_brasil/FeatureServer/2``) has 0 records in the whole country;
every query answered HTTP 200 with zero features and the site printed
"nenhum embargo" for every property.

Every layer used to state absence is registered in ``LAYERS``. The floors are well
below the totals measured live on 13/09/2026 (kept next to each entry), so normal
growth or pruning never trips them, while an emptied or swapped layer does.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable

import httpx

TTL_OK_SECONDS = float(os.getenv("RX_LAYER_GUARD_TTL_S", str(6 * 3600)))
TTL_FAIL_SECONDS = float(os.getenv("RX_LAYER_GUARD_FAIL_TTL_S", "90"))
COUNT_TIMEOUT_SECONDS = float(os.getenv("RX_LAYER_GUARD_TIMEOUT_S", "8"))
USER_AGENT = "Raio-X-Territorial/h1-layer-guard"

PAMGIA = "https://pamgia.ibama.gov.br/server/rest/services/"
TERRABRASILIS = "https://terrabrasilis.dpi.inpe.br/geoserver/ows"
IDE_SISEMA = "https://geoserver.meioambiente.mg.gov.br/ows"

# key -> registry entry. ``kind`` is "arcgis" (url = layer url, no /query) or
# "wfs" (url = service endpoint, ``type_name`` = feature type).
LAYERS: dict[str, dict[str, Any]] = {
    "ibama_embargos": {
        "kind": "arcgis", "min_total": 50_000,
        "url": PAMGIA + "01_Publicacoes_Bases/adm_embargos_ibama_a/FeatureServer/0",
        "measured": "91.197 registros; último embargo 12/09/2026 (13/09/2026)",
    },
    "ibama_autos": {
        "kind": "arcgis", "min_total": 300_000,
        "url": PAMGIA + "app_dadosabertos/adm_auto_infracao_p/FeatureServer/0",
        "measured": "710.298 registros; último auto 10/09/2026 (13/09/2026)",
    },
    "icmbio_embargos": {
        "kind": "arcgis", "min_total": 4_000,
        "url": PAMGIA + "01_Publicacoes_Bases/adm_embargo_icmbio_a/FeatureServer/0",
        "stale_since": "2022-02-08",
        "measured": "8.025 registros; último auto 08/02/2022 — base parada (13/09/2026)",
    },
    "terra_indigena": {
        "kind": "arcgis", "min_total": 400,
        "url": PAMGIA + "01_Publicacoes_Bases/lim_terra_indigena_a/FeatureServer/12",
        "measured": "627 registros; sem campo de data (13/09/2026)",
    },
    "unidade_conservacao": {
        "kind": "arcgis", "min_total": 2_500,
        "url": PAMGIA + "BasesSincronizadas/lim_unidades_conserva%C3%A7%C3%A3o_mma_a/FeatureServer/0",
        "measured": "3.471 registros; sincronizada 11/08/2026 (13/09/2026)",
    },
    "quilombola": {
        "kind": "arcgis", "min_total": 200,
        "url": PAMGIA + "BasesSincronizadas/lim_quilombos_incra_a/FeatureServer/0",
        "measured": "440 registros; dt_sync inválido 06/07/2050 (13/09/2026)",
    },
    "assentamento": {
        "kind": "arcgis", "min_total": 5_000,
        "url": PAMGIA + "01_Publicacoes_Bases/assentamentos_incra/FeatureServer/1",
        "measured": "8.377 registros; sem campo de data (13/09/2026)",
    },
    "floresta_publica": {
        "kind": "arcgis", "min_total": 3_000,
        "url": PAMGIA + "01_Publicacoes_Bases/lim_floresta_publica_a/FeatureServer/8",
        "stale_since": "2020-12-31",
        "measured": "4.763 registros, anos até 2020; CNFP 2025 do SFB tem 12.874 (13/09/2026)",
    },
    "sitio_arqueologico": {
        "kind": "arcgis", "min_total": 8_000,
        "url": PAMGIA + "BasesSincronizadas/lim_sitios_arqueologicos_iphan_a/FeatureServer/0",
        "stale_since": "2025-11-15",
        "measured": "13.267 polígonos, último cadastro 15/11/2025; IPHAN oficial tem 15.227 polígonos e 32.616 pontos (13/09/2026)",
    },
    "sigef_publico_espelho": {
        "kind": "arcgis", "min_total": 50_000,
        "url": PAMGIA + "01_Publicacoes_Bases/lim_imovel_sigef_publico_a/FeatureServer/10",
        "stale_since": "2022-04-26",
        "measured": "81.753 parcelas públicas; última aprovação 26/04/2022 (13/09/2026)",
    },
    "anm_sigmine": {
        "kind": "arcgis", "min_total": 100_000,
        "url": "https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/FeatureServer/0",
        "measured": "269.630 processos ativos (13/09/2026)",
    },
    "pivos_ana_2022": {
        "kind": "arcgis", "min_total": 20_000,
        "url": "https://portal1.snirh.gov.br/server/rest/services/SFI/PIVOS_2022_SNIRH/MapServer/0",
        "measured": "30.040 pivôs, mapeamento de 2022 (13/09/2026)",
    },
    "outorgas_igam_mg": {
        "kind": "wfs", "min_total": 20_000, "url": IDE_SISEMA,
        "type_name": "IDE:ide_2103_mg_outorgas_uso_recursos_hidricos_pto",
        "measured": "58.741 outorgas; última publicação 03/07/2025 (13/09/2026)",
    },
    "outorgas_ana_mg": {
        "kind": "wfs", "min_total": 5_000, "url": IDE_SISEMA,
        "type_name": "IDE:ide_2103_mg_federais_ana_outorgas_pto",
        "measured": "16.163 outorgas (13/09/2026)",
    },
}

# PRODES yearly layers are discovered from GetCapabilities; each one is checked by
# type name against this floor (smallest measured: Pantanal, 37.155 polygons).
PRODES_MIN_TOTAL = 10_000

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _default_get_json(url: str, params: dict[str, Any], timeout: float) -> Any:
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _default_get_text(url: str, params: dict[str, Any], timeout: float) -> str:
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.text


# Injection points (the offline gate replaces them; production never does).
http_get_json: Callable[[str, dict[str, Any], float], Any] = _default_get_json
http_get_text: Callable[[str, dict[str, Any], float], str] = _default_get_text


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_get(key: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        expires, state = item
        if _now() >= expires:
            _CACHE.pop(key, None)
            return None
        return {**state, "cached": True}


def _cache_put(key: str, state: dict[str, Any]) -> dict[str, Any]:
    ttl = TTL_OK_SECONDS if state.get("ok") else TTL_FAIL_SECONDS
    with _CACHE_LOCK:
        _CACHE[key] = (_now() + ttl, dict(state))
    return {**state, "cached": False}


def _layer_url(url: str) -> str:
    return re.sub(r"/query/?$", "", url.split("?", 1)[0].rstrip("/"))


def arcgis_layer_total(url: str, min_total: int = 1, *, key: str | None = None) -> dict[str, Any]:
    """Global record count of an ArcGIS layer, cached; ok only when >= min_total."""
    layer = _layer_url(url)
    cache_key = key or layer
    cached = _cache_get("arcgis:" + cache_key)
    if cached is not None:
        return cached
    checked = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        data = http_get_json(layer + "/query", {"f": "json", "where": "1=1", "returnCountOnly": "true"}, COUNT_TIMEOUT_SECONDS)
    except Exception as exc:
        return _cache_put("arcgis:" + cache_key, {"ok": False, "count": None, "reason": f"layer_count_failed:{type(exc).__name__}", "checked_at": checked})
    if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("count"), int):
        return _cache_put("arcgis:" + cache_key, {"ok": False, "count": None, "reason": "layer_count_invalid", "checked_at": checked})
    count = int(data["count"])
    if count < max(1, int(min_total)):
        return _cache_put("arcgis:" + cache_key, {"ok": False, "count": count, "reason": "layer_empty" if count == 0 else "layer_below_floor", "checked_at": checked})
    return _cache_put("arcgis:" + cache_key, {"ok": True, "count": count, "reason": "layer_alive", "checked_at": checked})


_HITS_RE = re.compile(r'number(?:Matched|OfFeatures)="(\d+)"')


def wfs_layer_total(url: str, type_name: str, min_total: int = 1) -> dict[str, Any]:
    """Global feature count of a WFS feature type (resultType=hits), cached."""
    cache_key = f"wfs:{url}|{type_name}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    checked = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": type_name, "resultType": "hits"}
    try:
        text = http_get_text(url, params, COUNT_TIMEOUT_SECONDS)
    except Exception as exc:
        return _cache_put(cache_key, {"ok": False, "count": None, "reason": f"layer_count_failed:{type(exc).__name__}", "checked_at": checked})
    match = _HITS_RE.search(text or "")
    if not match:
        return _cache_put(cache_key, {"ok": False, "count": None, "reason": "layer_count_invalid", "checked_at": checked})
    count = int(match.group(1))
    if count < max(1, int(min_total)):
        return _cache_put(cache_key, {"ok": False, "count": count, "reason": "layer_empty" if count == 0 else "layer_below_floor", "checked_at": checked})
    return _cache_put(cache_key, {"ok": True, "count": count, "reason": "layer_alive", "checked_at": checked})


def registered_layer_total(key: str) -> dict[str, Any]:
    entry = LAYERS[key]
    if entry["kind"] == "wfs":
        return wfs_layer_total(entry["url"], entry["type_name"], entry["min_total"])
    return arcgis_layer_total(entry["url"], entry["min_total"], key=key)


def arcgis_answer_problem(status: int | None, data: Any) -> str | None:
    """None when an ArcGIS query response is a complete answer; else the reason."""
    if status is not None and status != 200:
        return f"http_{status}"
    if not isinstance(data, dict):
        return "response_not_json_object"
    if data.get("error"):
        return "arcgis_error"
    if not isinstance(data.get("features"), list):
        return "features_missing"
    if data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit"):
        return "exceeded_transfer_limit"
    return None


def zero_verdict(key: str, *, zero: bool, answer_problem: str | None = None) -> dict[str, Any]:
    """Decide whether a result may be shown as an answer.

    ``zero`` is True when the consultation found nothing to show. A result with
    hits from a valid answer is always an answer (the layer evidently has data).
    A zero is an answer only when the response was complete, the layer total is at
    or above its floor, and the base is not registered as stale.
    """
    entry = LAYERS.get(key) or {}
    if answer_problem:
        return {"answer": False, "state": "pending", "reason": answer_problem}
    if not zero:
        return {"answer": True, "state": "answered_hit", "reason": "hits", "stale_since": entry.get("stale_since")}
    if entry.get("stale_since"):
        return {"answer": False, "state": "pending", "reason": "stale_base", "stale_since": entry["stale_since"]}
    layer = registered_layer_total(key)
    if not layer.get("ok"):
        return {"answer": False, "state": "pending", "reason": layer.get("reason"), "layer": layer}
    return {"answer": True, "state": "answered_clear", "reason": "layer_alive", "layer": layer}


async def zero_verdict_async(key: str, *, zero: bool, answer_problem: str | None = None) -> dict[str, Any]:
    """Same as zero_verdict, without blocking the event loop on an uncached count."""
    import asyncio

    if answer_problem or not zero or (LAYERS.get(key) or {}).get("stale_since"):
        return zero_verdict(key, zero=zero, answer_problem=answer_problem)
    return await asyncio.to_thread(zero_verdict, key, zero=zero, answer_problem=answer_problem)


def blank_counts(result: dict[str, Any]) -> dict[str, Any]:
    """A pending result carries no number: whoever skips ``ok`` must not read a zero.

    ``exact`` keeps its keys (callers index it) but every count and area becomes None
    and the occurrence lists are emptied.
    """
    exact = result.get("exact")
    if isinstance(exact, dict):
        for key in list(exact):
            if key.endswith(("_count", "_ha")):
                exact[key] = None
        for key in ("occurrences", "items"):
            if key in exact:
                exact[key] = []
        exact["available"] = False
    return result


# Reasons a second attempt a few seconds later cannot change: the base itself is empty,
# below its floor, stopped, or the CAR geometry is missing/unmeasurable.
NOT_TRANSIENT_REASONS = ("stale_base", "layer_empty", "layer_below_floor", "car_geometry_missing", "geometry_error",
                         "prodes_layer_empty", "prodes_layer_below_floor", "prodes_catalog_incomplete")


def worth_retry(result: Any) -> bool:
    """True when a quick second consultation can still turn this result into an answer."""
    if not isinstance(result, dict):
        return True
    if result.get("ok") is True and result.get("source_state") != "partial":
        return False
    reason = str((result.get("layer_guard") or {}).get("reason") or "")
    return not reason.startswith(NOT_TRANSIENT_REASONS)


def keep_better(old: Any, new: Any) -> Any:
    """After a retry: never trade an answer (even a partial one) for a worse reading."""
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    if old.get("ok") is True and not (new.get("ok") is True and new.get("source_state") != "partial"):
        return old
    return new


def apply_verdict(result: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Stamp the verdict on a source result; a non-answer can never keep ok=True."""
    result["source_state"] = verdict.get("state")
    guard = {k: v for k, v in verdict.items() if k in ("reason", "stale_since", "layer")}
    result["layer_guard"] = guard
    if not verdict.get("answer"):
        result["ok"] = False
        result.setdefault("detail", f"consulta_pendente:{verdict.get('reason')}")
        blank_counts(result)
    return result


def prodes_verdict(candidate_layers: list[str], failed_layers: list[Any], truncated_layers: list[str], has_hits: bool) -> dict[str, Any]:
    """PRODES reading for the whole catalog.

    With polygons found, the reading is an answer: complete (``answered_hit``) or,
    when a yearly layer failed, truncated or is missing, ``partial`` — the polygons
    found are real and must be shown, but they are not the whole count. Whether a
    partial reading still holds an occurrence inside the property is decided by
    ``deploy_app.finalize_prodes`` with the CAR geometry. Without polygons, the zero
    goes through ``prodes_zero_verdict``.
    """
    if not has_hits:
        return prodes_zero_verdict(candidate_layers, failed_layers, truncated_layers)
    yearly = [name for name in candidate_layers if "yearly_deforestation" in name]
    if failed_layers:
        return {"answer": True, "state": "partial", "reason": "prodes_layer_failed"}
    if truncated_layers:
        return {"answer": True, "state": "partial", "reason": "prodes_layer_truncated"}
    if len(yearly) < 6:
        return {"answer": True, "state": "partial", "reason": "prodes_catalog_incomplete"}
    return {"answer": True, "state": "answered_hit", "reason": "hits"}


def prodes_zero_verdict(candidate_layers: list[str], failed_layers: list[Any], truncated_layers: list[str]) -> dict[str, Any]:
    """PRODES answers "no intersection" only when every yearly layer answered."""
    yearly = [name for name in candidate_layers if "yearly_deforestation" in name]
    if len(yearly) < 6:
        return {"answer": False, "state": "pending", "reason": "prodes_catalog_incomplete"}
    if failed_layers:
        return {"answer": False, "state": "pending", "reason": "prodes_layer_failed"}
    if truncated_layers:
        return {"answer": False, "state": "pending", "reason": "prodes_layer_truncated"}
    from concurrent.futures import ThreadPoolExecutor

    from external_process_lifecycle import in_current_scope

    with ThreadPoolExecutor(max_workers=min(8, len(yearly))) as pool:
        states = list(pool.map(in_current_scope(
            lambda name: (name, wfs_layer_total(TERRABRASILIS, name, PRODES_MIN_TOTAL))), yearly))
    for name, layer in states:
        if not layer.get("ok"):
            return {"answer": False, "state": "pending", "reason": f"prodes_{layer.get('reason')}", "layer": {**layer, "type_name": name}}
    return {"answer": True, "state": "answered_clear", "reason": "layer_alive"}
