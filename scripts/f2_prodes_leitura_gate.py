"""F2 gate: leitura correta do PRODES, a mesma no portal e no relatório.

Uso (offline, sem rede):
  PYTHONPATH=. python scripts/f2_prodes_leitura_gate.py

Fixtures reais em tests/fixtures/f2_prodes_leitura/ (gravadas do caminho de produção
deploy_app.fetch_car_live + prodes_fast_v24.query_prodes_fast em 13/09/2026):
  * curvelo_mg.json        — 1 desmatamento dentro (PRODES 2006) + 3 faixas de divisa
  * sao_desiderio_ba.json  — desmatamento interno grande (2022–2024), faixa de divisa 2021
                              e máscara acumulada até 2000 cruzando o imóvel

Parte A roda o caminho que o cliente vê (analyze_car offline → resumo do portal →
renderAnalysis no Node; build_live_payload → patch_prodes_lens → narrativa do relatório)
e por isso reprova o código antigo pelo defeito, não por falta de módulo.
Parte B confere o contrato do módulo prodes_reading_f2 (estados, critério, formatação).
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "f2_prodes_leitura"
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []
PASSES = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"PASS {name}")
    else:
        FAILS.append(name)
        print(f"FAIL {name} :: {str(detail)[:400]}")


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# Imprecisão falsa sobre dado de 30 m: 3+ casas decimais em ha (formato Python ou pt-BR).
FALSE_PRECISION = re.compile(r"\d[.,]\d{3,}\s?ha\b")
LEGACY_LABELS = {"Histórico PRODES completo", "Recorte pós-31/07/2019", "Ocorrências exatas", "Área única intersectada",
                 "Percentual do CAR", "Anos identificados", "Ano", "Área intersectada", "Imagem", "Método de área PRODES"}


def texts_of(obj) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out += texts_of(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += texts_of(v)
    return out


# ------------------------------------------------------------------ caminho do portal

def run_analyze_car(fixture: dict) -> dict:
    """deploy_app.analyze_car de verdade, com as fontes de rede trocadas pela fixture."""
    import deploy_app

    fx = copy.deepcopy(fixture)
    saved = {k: getattr(deploy_app, k) for k in ("fetch_car_live", "query_sigef", "query_embargos", "query_anm", "query_prodes")}

    async def empty(_bbox):
        return {"ok": True, "features": [], "feature_count": 0}

    async def prodes(_bbox):
        return fx["prodes"]

    deploy_app.fetch_car_live = lambda _code: fx["car"]
    deploy_app.query_sigef = deploy_app.query_embargos = deploy_app.query_anm = empty
    deploy_app.query_prodes = prodes
    try:
        return asyncio.run(deploy_app.analyze_car(fx["car"]["properties"]["cod_imovel"]))
    finally:
        for k, v in saved.items():
            setattr(deploy_app, k, v)


def render_portal(summary: dict) -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    src = (ROOT / "portal_api.py").read_text(encoding="utf-8").splitlines()
    source_card = next(x for x in src if x.startswith("function sourceCard("))
    render = next(x for x in src if x.startswith("function renderAnalysis("))
    harness = (
        "const box={};const $=s=>box[s]||(box[s]={innerHTML:'',textContent:'',classList:{add(){},remove(){}}});"
        "let current={municipality:'M',uf:'UF'};\n" + source_card + "\n" + render + "\n"
        "const d=JSON.parse(require('fs').readFileSync(0,'utf8'));renderAnalysis({analysis:d});"
        "process.stdout.write($('#pbody').innerHTML);"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "portal_render.js"
        path.write_text(harness, encoding="utf-8")
        res = subprocess.run([node, str(path)], input=json.dumps(summary, ensure_ascii=False), capture_output=True, text=True, encoding="utf-8", timeout=60)
    if res.returncode != 0:
        raise RuntimeError(res.stderr[:400])
    return res.stdout


# ------------------------------------------------------------------ caminho do relatório

def run_report_payload(fixture: dict, tmpdir: Path) -> tuple[dict, dict]:
    import live_report_adapter
    import prodes_truth_v44
    import report_narrative

    result = run_analyze_car(fixture)
    payload = live_report_adapter.build_live_payload(result, "RX-GATE-F2", "2026-09-13T21:00:00+00:00", str(tmpdir / "map.png"))
    payload = prodes_truth_v44.patch_prodes_lens(payload, result)
    payload["narrative"] = report_narrative.build_narrative(payload)
    return payload, result


def prodes_payload_texts(payload: dict) -> list[str]:
    env = payload.get("environment") or {}
    pd = env.get("prodes") or {}
    parts = [pd.get("summary"), pd.get("rows"), pd.get("meaning"), payload.get("narrative"), payload.get("attention_points"),
             [r for r in env.get("layer_rows") or [] if r and r[0] == "PRODES"],
             [r for r in payload.get("executive_summary_rows") or [] if r and "PRODES" in str(r[0])],
             [c for c in payload.get("compliance") or [] if isinstance(c, dict) and c.get("label") == "PRODES"],
             [(payload.get("conclusion") or {}).get(k) for k in ("categories", "risks", "diligence", "main_attention")],
             payload.get("credit_screening")]
    # A área declarada do CAR ("O imóvel de 14.795 ha") não é número do PRODES; o R1 a formata no PDF.
    return [re.sub(r"O imóvel de [\d.,]+ ha", "O imóvel de <área do CAR>", t) for t in texts_of(parts) if t]


def part_a() -> None:
    print("== A · o que o cliente vê (portal e relatório)")
    curvelo, sd = load("curvelo_mg.json"), load("sao_desiderio_ba.json")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for label, fx, expect in (
            ("curvelo", curvelo, {"inside": 1, "post": 0, "years": [2006], "area": "14,28", "boundary_years": ("2004", "2014", "2021")}),
            ("sao_desiderio", sd, {"inside": 9, "post": 8, "years": [2004, 2022, 2023, 2024], "area": "2.529,77", "boundary_years": ("2021",)}),
        ):
            payload, result = run_report_payload(fx, tmpdir)
            env = payload.get("environment") or {}
            pd = env.get("prodes") or {}
            lens = pd.get("lens") or {}
            hist, post = lens.get("historical") or {}, lens.get("post_2019_07_31") or {}
            rows = pd.get("rows") or []
            row_text = " | ".join(" ".join(str(c) for c in r) for r in rows)
            texts = prodes_payload_texts(payload)
            joined = "\n".join(texts)

            check(f"A.{label}.relatorio_conta_so_dentro", pd.get("count") == expect["inside"] and hist.get("occurrence_count") == expect["inside"], (pd.get("count"), hist.get("occurrence_count")))
            check(f"A.{label}.relatorio_area_so_dentro_2_casas", pd.get("area_ha") == expect["area"], pd.get("area_ha"))
            check(f"A.{label}.anos_so_dentro_sem_mascara_2000", hist.get("years") == expect["years"], hist.get("years"))
            check(f"A.{label}.recorte_pos_2019_preservado", post.get("occurrence_count") == expect["post"] and post.get("cutoff") == "2019-07-31", post)
            check(f"A.{label}.faixa_de_divisa_em_linha_propria", any(r and r[0] == "Na divisa (não conta)" and all(y in str(r[1]) for y in expect["boundary_years"]) for r in rows), row_text[:400])
            credit_row = next((str(r[1]) for r in rows if r and r[0] == "Triagem para crédito rural"), "")
            border_row = next((str(r[1]) for r in rows if r and r[0] == "Na divisa (não conta)"), "")
            said = [x for x in (credit_row, border_row) if "2021" in x and "posterior" in x]
            check(f"A.{label}.faixa_pos_2019_nao_some", len(said) == 1, (credit_row, border_row))
            check(f"A.{label}.sem_linhas_antigas", not any(r and str(r[0]) in LEGACY_LABELS for r in rows), [r[0] for r in rows])
            check(f"A.{label}.sem_satelite_do_atributo_errado", "Landsat" not in joined and "OLI" not in joined, [t for t in texts if "Landsat" in t][:2])
            check(f"A.{label}.sem_falsa_precisao", not any(FALSE_PRECISION.search(t) for t in texts), [t for t in texts if FALSE_PRECISION.search(t)][:3])
            check(f"A.{label}.frase_de_capa_sem_contagem_bruta", "ocorrência(s) PRODES" not in str((payload.get("narrative") or {}).get("one_sentence")), (payload.get("narrative") or {}).get("one_sentence"))
            check(f"A.{label}.risco_ambiental_por_dentro", ((payload.get("conclusion") or {}).get("categories") or [{}])[1].get("text", "").startswith("Dentro do imóvel"), (payload.get("conclusion") or {}).get("categories"))

            # Portal: mesmo resultado de analyze_car, resumo que o portal recebe e HTML do painel.
            import deploy_app

            summary = deploy_app._safe_summary(result)
            sp = summary.get("prodes") or {}
            reading = sp.get("reading") or {}
            check(f"A.{label}.portal_conta_igual_relatorio", (sp.get("exact") or {}).get("occurrence_count") == pd.get("count"), (sp.get("exact"), pd.get("count")))
            check(f"A.{label}.portal_mesma_leitura_do_relatorio", bool(reading) and reading.get("headline") == (pd.get("reading") or {}).get("headline") and reading.get("inside_text") == pd.get("summary"), (reading.get("headline"), pd.get("summary")))
            try:
                html = render_portal(summary)
            except Exception as exc:  # pragma: no cover - falha do harness é falha do gate
                html = f"ERRO {exc}"
            if html is None:
                check(f"A.{label}.portal_painel_node_disponivel", False, "node não encontrado: o painel do portal precisa ser conferido")
            else:
                check(f"A.{label}.portal_painel_mostra_leitura", "Dentro do imóvel:" in html and "Na divisa:" in html, html[:300])
                check(f"A.{label}.portal_painel_sem_contagem_bruta", "ocorrência(s) •" not in html.split("PRODES", 1)[-1][:400] and not FALSE_PRECISION.search(html), html[:600])

        # Curvelo: a máscara acumulada do bbox não cruza o imóvel; em São Desidério cruza 49,61 ha.
        payload, _ = run_report_payload(sd, tmpdir)
        rows = (payload.get("environment") or {}).get("prodes", {}).get("rows") or []
        check("A.sao_desiderio.mascara_2000_nunca_ocorrencia", not any(str(r[0]) in ("PRODES 2000", "Ano") for r in rows) and any(r[0] == "Já desmatado antes" and "até 2000" in r[1] for r in rows), [r[0] for r in rows])
        detail = [r for r in rows if str(r[0]).startswith("PRODES 20")]
        # Fonte fora do ar: consulta pendente discreta, nunca "nenhum" nem risco baixo.
        failed = copy.deepcopy(curvelo)
        failed["prodes"] = {"ok": False, "error": "ReadTimeout", "source": "INPE/TerraBrasilis WFS", "elapsed_ms": 16000}
        payload_p, result_p = run_report_payload(failed, tmpdir)
        pd_p = (payload_p.get("environment") or {}).get("prodes") or {}
        texts_p = [x.lower() for x in prodes_payload_texts(payload_p)]
        check("A.pendente.relatorio_consulta_pendente", pd_p.get("count") == "Pendente" and pd_p.get("status") == "CONSULTA PENDENTE", (pd_p.get("count"), pd_p.get("status"), pd_p.get("summary")))
        check("A.pendente.relatorio_nunca_diz_nenhum", not any("nenhuma interseção prodes" in x or "nenhum desmatamento" in x or "nenhuma ocorrência intersectante" in x for x in texts_p), [x for x in texts_p if "nenhum" in x][:3])
        check("A.pendente.risco_geral_nao_baixo", (payload_p.get("conclusion") or {}).get("overall_risk") != "BAIXO", (payload_p.get("conclusion") or {}).get("overall_risk"))
        import deploy_app

        summary_p = deploy_app._safe_summary(result_p)
        html_p = render_portal(summary_p) or ""
        check("A.pendente.portal_sem_zero", (summary_p["prodes"].get("exact") or {}).get("occurrence_count") is None and "Consulta ao PRODES pendente" in html_p, html_p[-500:])
        check("A.sao_desiderio.detalhe_sem_corte_pos_2019_primeiro", len(detail) == 9 and all("posterior a 31/07/2019" in r[1] for r in detail[:8]), [r[0] for r in detail])


# ------------------------------------------------------------------ contrato do módulo

def part_b() -> None:
    print("== B · contrato do módulo prodes_reading_f2")
    try:
        import prodes_reading_f2 as f2
        import shapely
        from shapely.geometry import shape
        from shapely.ops import transform
        from pyproj import Transformer
    except Exception as exc:
        check("B.modulo_importa", False, f"{type(exc).__name__}: {exc}")
        return
    check("B.modulo_importa", True)

    curvelo, sd = load("curvelo_mg.json"), load("sao_desiderio_ba.json")

    # B1 · classificação por feição (ids reais) e o critério medido de novo, de forma independente.
    expected = {
        "curvelo": ({"yearly_deforestation.2ce0e5b9-c371-4e92-bf1b-97a25ad72602": "boundary"}, curvelo),
        "sao_desiderio": ({}, sd),
    }
    for label, (known, fx) in expected.items():
        r = f2.classify_prodes(fx["prodes"], fx["car"]["geometry"], fx["car"]["properties"]["area"])
        items = r["inside"]["occurrences"] + r["boundary"]["occurrences"]
        for fid, placement in known.items():
            got = next((x["placement"] for x in items if x["id"] == fid), None)
            check(f"B.{label}.faixa_de_4m_nunca_dentro", got == placement, got)
        car = shape(fx["car"]["geometry"])
        c = car.centroid
        to_m = Transformer.from_crs("EPSG:4674", f"+proj=tmerc +lat_0={c.y} +lon_0={c.x} +k=1 +ellps=GRS80 +units=m", always_xy=True).transform
        feats = {f["id"]: f for h in fx["prodes"]["hits"] for f in h["features"]}
        widths = {}
        for x in items:
            inter = car.intersection(shapely.make_valid(shape(feats[x["id"]]["geometry"])))
            polys = [g for g in shapely.get_parts(inter) if g.geom_type == "Polygon"]
            widths[x["id"]] = (x["placement"], x["reason"], 2 * shapely.maximum_inscribed_circle(transform(to_m, shapely.union_all(polys)), tolerance=0.25).length)
        bmax = max([w for p, _, w in widths.values() if p == "boundary"] or [0])
        imin = min([w for p, reason, w in widths.values() if p == "inside" and reason != "mancha inteira dentro do CAR"] or [math.inf])
        check(f"B.{label}.divisa_abaixo_de_um_pixel", bmax < f2.PIXEL_M, bmax)
        check(f"B.{label}.dentro_comporta_um_pixel", imin >= f2.PIXEL_M, imin)
        print(f"     medido {label}: maior faixa de divisa {bmax:.1f} m · menor parte interna (não inteira) {imin:.1f} m")

    rc = f2.prodes_reading_payload(curvelo)
    check("B.curvelo.estado_found", rc["state"] == "found" and rc["inside"]["state"] == "found" and rc["boundary"]["state"] == "found" and rc["accumulated"]["state"] == "not_found", (rc["state"], rc["accumulated"]["state"]))
    check("B.curvelo.contagens", (rc["inside"]["count"], rc["boundary"]["count"], rc["post_cutoff_inside"]["count"], rc["boundary"]["post_cutoff_count"]) == (1, 3, 0, 1), (rc["inside"]["count"], rc["boundary"]["count"]))
    check("B.curvelo.periodo_bienal", rc["inside"]["occurrences"][0]["period_label"] == "entre 2004 e a imagem de 21/07/2006", rc["inside"]["occurrences"][0]["period_label"])
    check("B.curvelo.faixa_2021_area_honesta", "0,03 ha" in rc["boundary_text"] and "0.027909" not in json.dumps(rc["rows"]), rc["boundary_text"])
    check("B.curvelo.credito_cita_faixa_pos_2019", "faixa de divisa de 2021" in rc["credit_text"] and rc["lens"]["credit_screening"]["automatic_check_may_flag"] and not rc["lens"]["credit_screening"]["mcr_check_required"], rc["credit_text"])

    rs = f2.prodes_reading_payload(sd)
    check("B.sao_desiderio.contagens", (rs["inside"]["count"], rs["post_cutoff_inside"]["count"], rs["boundary"]["count"], rs["accumulated"]["count"]) == (9, 8, 1, 1), (rs["inside"]["count"], rs["post_cutoff_inside"]["count"], rs["boundary"]["count"], rs["accumulated"]["count"]))
    check("B.sao_desiderio.mascara_fora_da_area_dentro", 2000 not in rs["inside"]["years"] and abs(rs["accumulated"]["area_ha"] - 49.611) < 0.01, (rs["inside"]["years"], rs["accumulated"]["area_ha"]))
    check("B.sao_desiderio.risco_pos_2019", rs["risk"]["status"] == "ATENÇÃO" and rs["lens"]["credit_screening"]["mcr_check_required"], rs["risk"])

    # B2 · estados explícitos: fonte que não respondeu nunca vira "nenhum".
    failed = copy.deepcopy(curvelo)
    failed["prodes"] = {"ok": False, "error": "ReadTimeout", "source": "INPE/TerraBrasilis WFS"}
    rp = f2.prodes_reading_payload(failed)
    check("B.fonte_fora_do_ar_pendente", rp["state"] == "pending" and rp["risk"]["status"] == "CONSULTA PENDENTE" and "nenhum" not in rp["inside_text"].lower(), rp["inside_text"])
    partial = copy.deepcopy(curvelo)
    partial["prodes"]["hits"] = [h for h in partial["prodes"]["hits"] if "yearly" not in h["layer"]]
    partial["prodes"]["failed_layers"] = [{"layer": "prodes-cerrado-nb:yearly_deforestation", "error": "ReadTimeout"}]
    rp = f2.prodes_reading_payload(partial)
    check("B.camada_anual_falhou_sem_achado_pendente", rp["state"] == "pending" and rp["inside"]["state"] == "pending", rp["state"])
    result = copy.deepcopy(partial)
    f2.apply_reading_to_result(result)
    check("B.pendente_nao_vira_zero_no_portal", result["prodes"]["exact"]["occurrence_count"] is None, result["prodes"]["exact"])
    found_partial = copy.deepcopy(sd)
    found_partial["prodes"]["failed_layers"] = [{"layer": "prodes-caatinga-nb:yearly_deforestation", "error": "ReadTimeout"}]
    rp = f2.prodes_reading_payload(found_partial)
    check("B.achado_com_camada_falha_nao_fecha_total", rp["state"] == "found" and rp["complete"] is False and "pode haver mais" in rp["inside_text"], rp["inside_text"])
    clear = copy.deepcopy(curvelo)
    clear["prodes"]["hits"] = [h for h in clear["prodes"]["hits"] if "yearly" not in h["layer"]]
    rp = f2.prodes_reading_payload(clear)
    check("B.respondeu_sem_achado_not_found", rp["state"] == "not_found" and rp["panel"]["audit_state"] == "ANSWERED_CLEAR", rp["state"])

    # B3 · classe que não é desmatamento (medido: RESERVATORIO/QUEIMADA em Caatinga, Mata Atlântica, Pampa).
    reservoir = copy.deepcopy(sd)
    for h in reservoir["prodes"]["hits"]:
        for f in h["features"]:
            if f["properties"].get("year") == 2022:
                f["properties"]["main_class"] = "RESERVATORIO"
    rp = f2.prodes_reading_payload(reservoir)
    check("B.reservatorio_nao_conta_como_desmatamento", rp["inside"]["count"] == 8 and rp["other_classes"]["count"] == 1 and "reservatório" in rp["other_classes_text"], (rp["inside"]["count"], rp["other_classes_text"]))

    # B4 · precisão honesta e pt-BR.
    check("B.formato_area", (f2.format_ha(0.027909), f2.format_ha(0.004), f2.format_ha(1981.2), f2.format_ha(14.278854)) == ("0,03 ha", "< 0,01 ha", "1.981,20 ha", "14,28 ha"), (f2.format_ha(0.027909), f2.format_ha(0.004)))
    check("B.formato_percentual", (f2.format_pct(14.278854, 14.795), f2.format_pct(0.027909, 14.795), f2.format_pct(15, 14.795)) == ("97%", "< 1%", "100%"), f2.format_pct(14.278854, 14.795))

    # B5 · idempotência e a lente antiga (a mesma do quality-gate) seguem válidas.
    twice = copy.deepcopy(sd)
    f2.apply_reading_to_result(twice)
    first = copy.deepcopy(twice["prodes"]["exact"])
    f2.apply_reading_to_result(twice)
    check("B.idempotente", twice["prodes"]["exact"] == first and "exact_raw" not in first, twice["prodes"]["exact"].get("occurrence_count"))
    from prodes_lens import derive_prodes_lens

    geom = {"type": "Polygon", "coordinates": [[[0, 0], [0.02, 0], [0.02, 0.02], [0, 0.02], [0, 0]]]}
    old = {"type": "Polygon", "coordinates": [[[0, 0], [0.02, 0], [0.02, 0.018], [0, 0.018], [0, 0]]]}
    r1 = {"type": "Polygon", "coordinates": [[[0.001, 0.001], [0.006, 0.001], [0.006, 0.006], [0.001, 0.006], [0.001, 0.001]]]}
    r2 = {"type": "Polygon", "coordinates": [[[0.003, 0.003], [0.008, 0.003], [0.008, 0.008], [0.003, 0.008], [0.003, 0.003]]]}
    legacy = {"hits": [{"layer": "proof:yearly_deforestation", "features": [
        {"id": "old", "geometry": old, "properties": {"year": 2006, "image_date": "2006-07-21"}},
        {"id": "r1", "geometry": r1, "properties": {"year": 2021, "image_date": "2021-09-16"}},
        {"id": "r2", "geometry": r2, "properties": {"year": 2022, "image_date": "2022-09-16"}},
    ]}]}
    lens = derive_prodes_lens(legacy, 0.5, car_geometry=geom, car_area_ha=49.236)
    check("B.lente_do_quality_gate_continua", lens["calculation_method"] == "exact_geometry_union_after_intersection" and lens["post_2019_07_31"]["occurrence_count"] == 2, lens["post_2019_07_31"])
    real = derive_prodes_lens(curvelo["prodes"], 0.37, car_geometry=curvelo["car"]["geometry"], car_area_ha=14.795)
    check("B.prodes_lens_usa_a_mesma_leitura", real["historical"]["occurrence_count"] == 1 and real["boundary_touch"]["occurrence_count"] == 3, real["historical"])

    # B6 · LGPD: nada de pessoa nas fixtures nem na leitura.
    personal = re.compile(r'"[^"]*(cpf|cnpj|nome|propriet|respons|titular)[^"]*"\s*:', re.I)
    blob = (FIX / "curvelo_mg.json").read_text(encoding="utf-8") + (FIX / "sao_desiderio_ba.json").read_text(encoding="utf-8") + json.dumps(rs, ensure_ascii=False)
    check("B.lgpd_sem_dado_pessoal", not personal.search(blob), personal.search(blob))


def main() -> int:
    part_a()
    part_b()
    print(f"RESULT pass={PASSES} fail={len(FAILS)}")
    if FAILS:
        print("RX_F2_PRODES_LEITURA_GATE=failed:" + ",".join(FAILS[:12]))
        return 1
    print("RX_F2_PRODES_LEITURA_GATE=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
