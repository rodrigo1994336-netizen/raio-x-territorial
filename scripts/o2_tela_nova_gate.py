"""O2 gate: tela nova em /novo (sem rede).

Confere, no módulo real ``tela_nova_o2`` montado num app FastAPI vazio (sem o portal):

  R_ROTAS    /novo, /novo/, /novo/imovel/{CAR}, /novo/prospeccao, /novo/precos, /novo/entrar respondem 200;
             CAR minúsculo redireciona para o maiúsculo; código inválido e arquivo inexistente dão 404; HEAD responde.
  R_HASH     o HTML só referencia /novo/a/<nome>.<16 hex>.<ext>; cada arquivo responde 200, cache de um ano
             ``immutable`` e o hash do nome é o sha256 do conteúdo; HTML com no-cache, ETag e CSP.
  R_INLINE   nenhum <script> ou <style> embutido nem atributo style= (a CSP só aceita arquivos do próprio site);
             nenhum script, folha de estilo ou fonte de outro domínio.
  R_ISOLADO  o módulo não importa módulo do portal nem lê/remenda PORTAL_HTML (a raiz / fica intocada).
  R_MAPA     nada escrito sobre o mapa: sem tooltip, popup, divIcon nem evento de passar o mouse.
  R_M2       nenhuma área em m².
  R_PESSOA   nenhum CPF/CNPJ, nome de autuado ou de titular; o JS não lê ``sample_properties`` nem ``nearest``.
  R_INTERNOS nenhum texto interno ("RISCO NÃO CLASSIFICADO", "SISTEMA LIVE", "NÃO CONSULTADA", ...) e o JS
             não lê a classificação interna ``risk``.
  R_CORES    nenhum verde de aprovado nem vermelho de alarme do portal antigo.
  R_TOQUE    alvos de toque de 44 px (variável --toque e nenhum botão/aba/sugestão menor).
  R_FOCO     foco visível.
  R_FONTES   as três fontes OFL são os arquivos oficiais (SHA256SUMS) e as licenças estão junto.
  R_ENDPOINTS o JS usa os endpoints do portal, e cada um existe no repositório.
  R_SICAR    o cartão tem o link "Consultar no SICAR (site oficial)" para a Consulta Pública oficial.
  R_CSS      o app.css fecha cada chave que abre (uma chave solta faz o navegador jogar fora a regra seguinte inteira).
  N_*        regras puras do app.js rodadas no node com respostas montadas aqui: "Sim"/"Não" só com resposta,
             corte no teto com zero é pendente, achado vale mesmo com outra fonte pendente, pendente + nada nunca
             vira "Não", IDE-Sisema só em MG, título só com nome validado, números pt-BR, busca, junção; base que não veio
             na resposta é pendente (nunca some da conta do "Não"); código do CAR copiado de PDF e coordenada em graus;
             "nenhum registro" de água diz na própria resposta que não é falta de água.

O comportamento na tela (leitura de outro imóvel, pendente que tenta sozinha, Voltar, celular deitado, medida) fica no
scripts/o2_tela_nova_browser_gate.py, que roda num Chromium de verdade.

Controle positivo: cada regra recebe um mutante que ela TEM de pegar (e o motivo da falha é conferido pelo id
da regra). Mutante que passa derruba o gate.

Rodar: PYTHONPATH=. python scripts/o2_tela_nova_gate.py   (precisa de node no PATH)
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "static" / "novo"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print("FAIL", msg, flush=True)


# ------------------------------------------------------------------ regras estáticas
TOOLTIP = re.compile(r"bindTooltip|openTooltip|L\.tooltip|L\.Tooltip|bindPopup|openPopup|L\.popup|divIcon|mouseover|mouseenter|leaflet-tooltip")
M2 = re.compile(r"m²|m&sup2;|\bm2\b|area_m2|metros quadrados", re.I)
PESSOA = re.compile(r"\bCPF\b|\bCNPJ\b|cpf_cnpj|autuad|titular|propriet[aá]ri|sample_properties|\bnearest\b|\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", re.I)
INTERNOS = re.compile(r"RISCO NÃO CLASSIFICADO|SISTEMA LIVE|NÃO CONSULTADA|INDISPONÍVEL|PREPARADO|BACKEND|not_classified|probable_impediment|diligence|sem pendências|Em acordo|\.risk\b")
CORES = re.compile(r"#63e6a5|#48d995|#ff756f|#ff927a|#f5c96a|\bred\b|#f00\b|#ff0000", re.I)
INTERACTIVE_RULES = (".icone{", ".ferramentas .icone{", ".botao{", ".sugestoes li{", ".pergunta-botao{", ".abas a{", ".link{", ".menu a{", ".marca{", ".oficial{")
ENDPOINTS = {
    "/v1/live/sicar/viewport-v46": "portal_map_v46.py",
    "/v1/live/car/": "deploy_app.py",
    "/v1/live/map-panel/": "portal_map_panel_v45.py",
    "/v1/live/cities": "portal_v8.py",
    "/v1/live/resolve": "portal_api.py",
    "/v1/live/quick/": "portal_pdf_v21.py",
    "/v1/live/progressive/status/": "portal_pdf_v21.py",
    "/v1/exports/property/": "portal_api.py",
    "/v1/live/report-engine/wake": "portal_report_wake_f1b.py",
    "/v1/bootstrap/state": "portal_boot_guard_v26.py",
}


def static_rules(files: dict[str, str]) -> dict[str, list[str]]:
    """files: {'app.js', 'app.css', 'index.html', 'module'} -> {rule: [problems]} (empty list = passes)."""
    js, css, html, mod = files["app.js"], files["app.css"], files["index.html"], files["module"]
    out: dict[str, list[str]] = {k: [] for k in ("R_MAPA", "R_M2", "R_PESSOA", "R_INTERNOS", "R_CORES", "R_TOQUE", "R_FOCO", "R_INLINE", "R_ISOLADO", "R_ENDPOINTS", "R_SICAR", "R_CSS")}
    depth = 0
    for n, line in enumerate(re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), css, flags=re.S).split("\n"), 1):
        for ch in line:
            depth += (ch == "{") - (ch == "}")
            if depth < 0:
                out["R_CSS"].append(f"app.css line {n}: closing brace without an opening one")
                depth = 0
    if depth:
        out["R_CSS"].append(f"app.css ends with {depth} unclosed brace(s)")
    if not re.search(r'href="https://consulta\.car\.gov\.br/"[^>]*>Consultar no SICAR \(site oficial\)</a>', js):
        out["R_SICAR"].append("card without the official SICAR link")
    if ".oficial{" in css and not re.search(r"\.oficial\{[^}]*min-height:var\(--toque\)", css):
        out["R_TOQUE"].append(".oficial{ without var(--toque)")
    for name, text in (("app.js", js), ("app.css", css), ("index.html", html)):
        out["R_MAPA"] += [f"{name}: {m.group(0)}" for m in TOOLTIP.finditer(text)]
        out["R_M2"] += [f"{name}: {m.group(0)}" for m in M2.finditer(text)]
        out["R_PESSOA"] += [f"{name}: {m.group(0)}" for m in PESSOA.finditer(text)]
        out["R_INTERNOS"] += [f"{name}: {m.group(0)}" for m in INTERNOS.finditer(text)]
    out["R_CORES"] += [m.group(0) for m in CORES.finditer(css + js)]
    if not re.search(r"--toque:\s*44px", css):
        out["R_TOQUE"].append("--toque is not 44px")
    for sel in INTERACTIVE_RULES:
        for m in re.finditer(re.escape(sel) + r"([^}]*)\}", css):
            for prop, val in re.findall(r"(?<![-\w])(width|height|min-height|min-width)\s*:\s*(\d+(?:\.\d+)?)px", m.group(1)):
                if float(val) < 44 and not (prop in ("width", "height") and "min-" + prop in m.group(1)):
                    out["R_TOQUE"].append(f"{sel} {prop}:{val}px")
        if sel in (".icone{", ".botao{", ".pergunta-botao{", ".sugestoes li{") and not re.search(re.escape(sel) + r"[^}]*var\(--toque\)", css):
            out["R_TOQUE"].append(f"{sel} without var(--toque)")
    if not re.search(r":focus-visible\{outline:\s*3px solid", css):
        out["R_FOCO"].append("no visible focus outline")
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
        if m.group(2).strip() or "src=" not in m.group(1):
            out["R_INLINE"].append("inline script")
    if re.search(r"<style\b", html, re.I):
        out["R_INLINE"].append("inline style block")
    if re.search(r"\sstyle\s*=", html, re.I) or re.search(r"style=\\?[\"']", js):
        out["R_INLINE"].append("style attribute in markup")
    if re.search(r"<(?:script|link)\b[^>]*(?:src|href)=\"https?://(?!server\.arcgisonline\.com)", html, re.I):
        out["R_INLINE"].append("external script/style host")
    if re.search(r"@import|url\(\s*['\"]?https?:", css, re.I):
        out["R_INLINE"].append("external css resource")
    try:
        tree = ast.parse(mod)
    except SyntaxError as exc:
        out["R_ISOLADO"].append(f"module does not parse: {exc}")
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                if n.startswith(("portal_", "report_", "sitecustomize", "deploy_app")):
                    out["R_ISOLADO"].append(f"imports {n}")
            if isinstance(node, (ast.Name, ast.Attribute)) and (getattr(node, "id", None) == "PORTAL_HTML" or getattr(node, "attr", None) == "PORTAL_HTML"):
                out["R_ISOLADO"].append("touches PORTAL_HTML")
    for path, owner in ENDPOINTS.items():
        if path not in js:
            out["R_ENDPOINTS"].append(f"app.js does not use {path}")
        src = (ROOT / owner).read_text(encoding="utf-8") if (ROOT / owner).exists() else ""
        if path.rstrip("/") not in src:
            out["R_ENDPOINTS"].append(f"{path} not found in {owner}")
    return out


def fonts_rule(files: dict[str, bytes]) -> list[str]:
    problems = []
    sums = (SRC / "fonts" / "SHA256SUMS").read_text(encoding="utf-8").split("\n")
    expected = {line.split()[1]: line.split()[0] for line in sums if line.strip()}
    if len(expected) != 3:
        problems.append("SHA256SUMS must list the three fonts")
    for name, digest in expected.items():
        data = files.get(name)
        if data is None or hashlib.sha256(data).hexdigest() != digest:
            problems.append(f"{name} differs from the official file")
    for lic in ("OFL-SourceSans3.txt", "OFL-SourceSerif4-Adobe-LICENSE.md"):
        if not (SRC / "fonts" / lic).is_file():
            problems.append(f"missing {lic}")
    return problems


def served_rules(html: str, headers: dict, fetch) -> list[str]:
    """R_HASH over a served page: fetch(path) -> (status, headers, body)."""
    problems = []
    if "no-cache" not in headers.get("cache-control", "") or not headers.get("etag") or "script-src 'self'" not in headers.get("content-security-policy", ""):
        problems.append("html headers (no-cache, etag, csp)")
    refs = re.findall(r"(?:src|href)=\"(/[^\"]+)\"", html)
    assets = [r for r in refs if not r.startswith("/novo") or r.startswith("/novo/a/")]
    if not any(r.endswith(".js") for r in assets) or not any(r.endswith(".css") for r in assets):
        problems.append("no js/css referenced")
    for ref in assets:
        m = re.fullmatch(r"/novo/a/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9-]+)*\.([0-9a-f]{16})\.(js|css|ttf)", ref)
        if not m:
            problems.append(f"not a hashed asset: {ref}")
            continue
        status, h, body = fetch(ref)
        if status != 200 or "immutable" not in h.get("cache-control", ""):
            problems.append(f"{ref} status {status} cache {h.get('cache-control')}")
        elif hashlib.sha256(body).hexdigest()[:16] != m.group(1):
            problems.append(f"{ref} hash does not match content")
        if m.group(2) == "css":
            for font in re.findall(r"url\((/novo/a/[^)]+)\)", body.decode("utf-8")):
                fs, fh, fb = fetch(font)
                fm = re.search(r"\.([0-9a-f]{16})\.ttf$", font)
                if fs != 200 or not fm or hashlib.sha256(fb).hexdigest()[:16] != fm.group(1):
                    problems.append(f"font {font} not served by hash")
    return problems


# ------------------------------------------------------------------ regras puras no node
HARNESS = r"""
const path = process.argv[2];
require(path);
const R = globalThis.RXO2;
const out = {};
const t = (id, fn) => { try { const r = fn(); out[id] = r === true ? true : String(r); } catch (e) { out[id] = 'throw: ' + e.message; } };
const car = { cod_imovel: 'MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F', area: 14.795, m_fiscal: 0.3699, status_imovel: 'AT', tipo_imovel: 'IRU', condicao: 'Aguardando análise', municipio: 'Curvelo', uf: 'MG', dat_criacao: '2018-11-28T12:04:45.439Z', data_atualizacao: '2025-03-07T16:10:06.509Z' };
const svc = (n, ok = true) => ({ ok, occurrence_count: n, area_unique_ha: n ? 3.5 : 0, source: 'x' });
const clean = () => ({
  car: { properties: car },
  prodes: { reading: { state: 'not_found', complete: true, inside: { state: 'not_found', count: 0 }, post_cutoff_inside: { count: 0 } } },
  embargos_ibama: { ok: true, exact: { available: true, occurrence_count: 0, area_unique_ha: 0 } },
  autos_ibama: { ok: true, occurrence_count: 0, feature_count_bbox: 3 },
  anm: { ok: true, exact: { available: true, occurrence_count: 0, area_unique_ha: 0 } },
  territorial_constraints: { ok: true, services: { terra_indigena: svc(0), unidade_conservacao: svc(0), quilombola: svc(0), assentamento: svc(0), embargo_icmbio: svc(0), floresta_publica: svc(0) } },
  water_mg: { ok: true, inside_count: 0 },
  pivots_ana: { ok: true, intersection_count: 0, reference_year: 2022, feature_count_bbox: 0, parsed_feature_count: 0 },
  climate_nasa: { ok: true, rain_sum_mm: 33.01, temp_avg_c: 25.86, available_days: 30, period_start: '20260815', period_end: '20260913' },
  ide_layers: { soil: { ok: true, exact_count: 1, samples: [{ properties: { legenda: 'Cambissolo háplico' }, intersection_pct_car: 100 }] }, slope: { ok: false, detail: null } }
});
const f = R.facts(car);
const row = (a, id, fact) => R.glance(a, fact || f, 1757900000000).find(r => r.id === id);
const WORDS = new Set(['Sim', 'Não', 'Consulta pendente']);
t('N1_clean_answers_nao', () => ['desmatamento', 'embargo', 'protegida'].every(id => row(clean(), id).seal === 'nao') || 'clean fixture is not all "nao"');
t('N2_embargo_without_answer_is_pending', () => { const a = clean(); a.embargos_ibama = { ok: false, exact: { available: true, occurrence_count: 0 } }; return row(a, 'embargo').seal === 'pendente' || row(a, 'embargo').seal; });
t('N3_cap_zero_is_pending', () => { const a = clean(); a.autos_ibama = { ok: true, occurrence_count: 0, feature_count_bbox: 2000 }; return row(a, 'embargo').seal === 'pendente' || row(a, 'embargo').seal; });
t('N4_incomplete_found_is_floor', () => { const a = clean(); a.prodes.reading = { state: 'found', complete: false, inside: { count: 2, area_ha: 1.5 }, post_cutoff_inside: { count: 1 } }; const r = row(a, 'desmatamento'); return (r.seal === 'sim' && /pelo menos/.test(r.lead)) || r.lead; });
t('N5_incomplete_not_found_is_pending', () => { const a = clean(); a.prodes.reading = { state: 'not_found', complete: false, inside: { count: 0 } }; return row(a, 'desmatamento').seal === 'pendente' || row(a, 'desmatamento').seal; });
t('N6_found_with_pending_still_sim', () => { const a = clean(); a.embargos_ibama.exact.occurrence_count = 2; a.embargos_ibama.exact.area_unique_ha = 20.9339; a.territorial_constraints.services.embargo_icmbio = svc(0, false); const r = row(a, 'embargo'); return (r.seal === 'sim' && /20,93 ha/.test(r.lead) && r.details.some(d => /Ainda sem resposta/.test(d))) || JSON.stringify(r); });
t('N7_no_services_is_pending', () => { const a = clean(); delete a.territorial_constraints; return row(a, 'protegida').seal === 'pendente' || row(a, 'protegida').seal; });
t('N8_null_count_is_not_zero', () => { const a = clean(); a.anm = { ok: true, exact: { available: true, occurrence_count: null } }; return row(a, 'protegida').seal === 'pendente' || row(a, 'protegida').seal; });
t('N9_water_outside_coverage_hidden', () => { const a = clean(); a.water_mg = { ok: false, detail: 'consulta_pendente:outside_source_coverage' }; const r = row(a, 'agua'); return (!/[Oo]utorga/.test(r.lead + r.details.join(' ')) && r.seal === null) || JSON.stringify(r); });
// T1: solo pela base nacional (IBGE + Embrapa) em qualquer UF; a camada estadual de MG e o "risco de erosão" sozinho não voltam.
t('N10_soil_national_not_state_layer', () => {
  const a = clean(); a.ide_layers.erosion = { ok: true, exact_count: 1, samples: [{ properties: { indicador: 'Muito baixo' }, intersection_pct_car: 100 }] };
  const mg = row(a, 'terra');
  const pa = R.facts(Object.assign({}, car, { cod_imovel: 'PA-1500602-8465DA50596B4D829325D2591083D5DB', uf: 'PA' }));
  const b = clean(); b.terra_nacional = { version: 'T1', states: { pedologia: 'found', aptidao: 'not_found', erodibilidade: 'found' }, texts: { solo: 'Cambissolo Háplico Tb Distrófico em cerca de 5 de cada 10 partes do imóvel.', erodibilidade: 'Erodibilidade média em todo o imóvel.' } };
  const rp = row(b, 'terra', pa);
  const mgText = mg.lead + ' ' + mg.details.join(' '), paText = rp.lead + ' ' + rp.details.join(' ');
  return (!/Solo|erosão|Declividade|IDE-Sisema/.test(mgText) && /Solo no mapa oficial/.test(paText) && /fragilidade do próprio solo/.test(paText) && !/Risco potencial/.test(paText)) || JSON.stringify([mgText, paText]);
});
t('N11_title_only_validated', () => {
  const code = car.cod_imovel;
  const a = R.identity(code, { ok: true, validated_name: 'Fazenda X', validated_name_state: 'unresolved', panel_name_eligible: true });
  const b = R.identity(code, { ok: true, validated_name: 'Fazenda X', validated_name_state: 'validated', panel_name_eligible: false });
  const c = R.identity(code, { ok: true, validated_name: 'Fazenda X', validated_name_state: 'validated', panel_name_eligible: true });
  const d = R.identity(code, null);
  return (!a.named && !b.named && c.named && c.title === 'Fazenda X' && !d.named && d.lines.join('') === code) || JSON.stringify([a, b, c, d]);
});
t('N12_facts_never_m2', () => { const x = R.facts(car, { ok: true, car_code: car.cod_imovel, area_ha: 14.795, area_m2: 147950 }); return (!/m²|m2|147/.test(JSON.stringify(x)) && x.area === '14,80 ha') || JSON.stringify(x); });
t('N13_ptbr_numbers', () => (R.ha(1981.2) === '1.981,20 ha' && R.num('14.795', 2) === '14,80' && R.modulos(0.3699) === '0,37 módulo fiscal' && R.modulos(5.7642) === '5,76 módulos fiscais' && R.ha(null) === '' && R.ha('') === '') || [R.ha(1981.2), R.num('14.795', 2), R.modulos(0.3699)].join('|'));
t('N14_search', () => {
  const a = R.parseQuery('mg-3120904-dfb380becd7a4323ad8aa68fa14d011f'), b = R.parseQuery('MG-3120904-'), c = R.parseQuery('-44.1819, -18.8912'), d = R.parseQuery('-18,8912 -44,1819'), e = R.parseQuery('Curvelo'), g = R.parseQuery('40.7, -74.0');
  return (a.kind === 'car' && a.code === car.cod_imovel && b.kind === 'car-partial' && c.kind === 'coord' && c.lat === -18.8912 && d.kind === 'coord' && d.lon === -44.1819 && e.kind === 'city' && g.kind === 'coord-outside') || JSON.stringify([a, b, c, d, e, g]);
});
t('N15_answer_words_and_seals', () => { const rows = R.glance(clean(), f, 1757900000000); const ok = rows.length === 6 && rows.every(r => r.seal === null || WORDS.has(R.ANSWER[r.seal])) && ['car', 'agua', 'terra'].every(id => rows.find(r => r.id === id).seal === null) && rows.every(r => r.source && r.lead); return ok || JSON.stringify(rows.map(r => [r.id, r.seal, r.source])); });
t('N16_merge_keeps_answers', () => { const old = [{ id: 'a', seal: 'sim' }, { id: 'b', seal: 'pendente' }], fresh = [{ id: 'a', seal: 'pendente' }, { id: 'b', seal: 'nao' }]; const m = R.mergeRows(old, fresh); return (m.rows[0].seal === 'sim' && m.rows[1].seal === 'nao' && m.changed) || JSON.stringify(m); });
t('N17_pending_plus_none_never_nao', () => { const a = clean(); a.territorial_constraints.services.floresta_publica = svc(0, false); return row(a, 'protegida').seal === 'pendente' || row(a, 'protegida').seal; });
t('N18_all_water_pending_is_pending', () => { const a = clean(); a.water_mg = { ok: false }; a.pivots_ana = { ok: false }; return row(a, 'agua').seal === 'pendente' || row(a, 'agua').seal; });
t('N19_view_param', () => { const v = R.parseView('-18.89126,-44.18186,16'); return (v && v.z === 16 && R.parseView('x') === null && R.parseView('-91,0,5') === null) || JSON.stringify(v); });
t('N20_cells_match_server_grid', () => { const k = R.cellKeys(0.04, -44.2, -18.92, -44.16, -18.88); return (k.length === 1 && k[0] === '0.04:-1105:-473' && R.stepFor(-44.3, -19, -44.1, -18.8, 0) === 0.08) || JSON.stringify([k, R.stepFor(-44.3, -19, -44.1, -18.8, 0)]); });
t('N22_missing_base_is_pending', () => {
  const a = clean(); delete a.territorial_constraints.services.floresta_publica;
  const b = clean(); delete b.territorial_constraints.services.embargo_icmbio;
  const ra = row(a, 'protegida'), rb = row(b, 'embargo');
  return (ra.seal === 'pendente' && !/^Não/.test(ra.lead) && rb.seal === 'pendente' && !/^Não/.test(rb.lead)) || JSON.stringify([ra.seal, ra.lead, rb.seal, rb.lead]);
});
t('N23_search_pdf_code_and_dms', () => {
  const a = R.parseQuery('MG 3120904 DFB380BECD7A4323AD8AA68FA14D011F'), b = R.parseQuery('mg3120904dfb380becd7a4323ad8aa68fa14d011f'), c = R.parseQuery("18°53'28\"S 44°10'54\"W"), d = R.parseQuery('Três Marias');
  return (a.kind === 'car' && a.code === car.cod_imovel && b.kind === 'car' && b.code === car.cod_imovel && c.kind === 'coord-dms' && d.kind === 'city') || JSON.stringify([a, b, c, d]);
});
t('N24_water_none_says_not_lack_of_water', () => {
  const r = row(clean(), 'agua'); const a = clean(); a.water_mg.inside_count = 2; const r2 = row(a, 'agua');
  return (r.q === 'Tem uso de água registrado?' && /não quer dizer que falta água/.test(r.lead) && !/não quer dizer que falta água/.test(r2.lead) && r2.details.some(x => /não quer dizer que falta água/.test(x))) || JSON.stringify([r, r2]);
});
t('N21_measure_area', () => { const sq = [{ lat: 0, lng: 0 }, { lat: 0, lng: 0.01 }, { lat: 0.01, lng: 0.01 }, { lat: 0.01, lng: 0 }]; const a = R.geodesicArea(sq) / 10000; return (a > 123 && a < 124) || a; });
process.stdout.write(JSON.stringify(out));
"""


def node_rules(js: str) -> dict[str, str | bool]:
    node = shutil.which("node")
    if not node:
        return {"N_NODE": "node not found on PATH"}
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "app.js"
        app.write_text(js, encoding="utf-8")
        harness = Path(tmp) / "harness.js"
        harness.write_text(HARNESS, encoding="utf-8")
        res = subprocess.run([node, str(harness), str(app)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if res.returncode != 0:
        return {"N_RUN": res.stderr[-600:]}
    return json.loads(res.stdout)


def failing(static: dict, node: dict) -> set[str]:
    bad = {k for k, v in static.items() if v}
    bad |= {k for k, v in node.items() if v is not True}
    return bad


# ------------------------------------------------------------------ servidor real (sem portal)
def server_checks() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import tela_nova_o2

    app = FastAPI()
    tela_nova_o2.install(app)
    tela_nova_o2.install(app)  # idempotent
    paths = [getattr(r, "path", "") for r in app.router.routes]
    if paths.count("/novo") != 1:
        fail(f"R_ROTAS install is not idempotent: {paths}")
    client = TestClient(app)

    def fetch(path):
        r = client.get(path)
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content

    for path in ("/novo", "/novo/", f"/novo/imovel/{CAR}", "/novo/prospeccao", "/novo/precos", "/novo/entrar"):
        r = client.get(path)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            fail(f"R_ROTAS {path} -> {r.status_code}")
            continue
        problems = served_rules(r.text, {k.lower(): v for k, v in r.headers.items()}, fetch)
        if problems:
            fail(f"R_HASH {path}: {problems[:4]}")
        if path.startswith("/novo/imovel/") and (f'data-car="{CAR}"' not in r.text or CAR not in r.text.split("</title>")[0]):
            fail("R_ROTAS property page does not carry the CAR")
    r = client.get(f"/novo/imovel/{CAR.lower()}", follow_redirects=False)
    if r.status_code != 308 or r.headers.get("location") != f"/novo/imovel/{CAR}":
        fail(f"R_ROTAS lowercase CAR -> {r.status_code} {r.headers.get('location')}")
    for path in ("/novo/imovel/MG-123", "/novo/imovel/%3Cscript%3E", "/novo/a/app.0000000000000000.js", "/novo/a/../tela_nova_o2.py"):
        r = client.get(path)
        if r.status_code != 404:
            fail(f"R_ROTAS {path} -> {r.status_code}")
        if "<script>" in r.text:
            fail(f"R_ROTAS {path} echoes markup")
    if client.head("/novo").status_code != 200:
        fail("R_ROTAS HEAD /novo")
    etag = client.get("/novo").headers.get("etag")
    if client.get("/novo", headers={"If-None-Match": etag}).status_code != 304:
        fail("R_HASH ETag does not answer 304")
    gz = client.get("/novo", headers={"Accept-Encoding": "gzip"})
    if gz.headers.get("content-encoding") != "gzip":
        fail("R_HASH html not gzip")

    # R_HASH positive control: a served page that references an unhashed file must fail.
    page = client.get("/novo").text
    bad = re.sub(r"/novo/a/app\.[0-9a-f]{16}\.js", "/novo/a/app.js", page)
    good_headers = {k.lower(): v for k, v in client.get("/novo").headers.items()}
    if not served_rules(bad, good_headers, fetch):
        fail("CONTROL R_HASH: unhashed asset reference was not caught")
    swapped = page.replace(re.search(r"/novo/a/app\.([0-9a-f]{16})\.js", page).group(1), "0123456789abcdef")
    if not served_rules(swapped, good_headers, fetch):
        fail("CONTROL R_HASH: wrong hash was not caught")


# ------------------------------------------------------------------ principal
def main() -> int:
    files = {
        "app.js": (SRC / "app.js").read_text(encoding="utf-8"),
        "app.css": (SRC / "app.css").read_text(encoding="utf-8"),
        "index.html": (SRC / "index.html").read_text(encoding="utf-8"),
        "module": (ROOT / "tela_nova_o2.py").read_text(encoding="utf-8"),
    }
    font_bytes = {p.name: p.read_bytes() for p in (SRC / "fonts").glob("*.ttf")}

    static = static_rules(files)
    node = node_rules(files["app.js"])
    base_bad = failing(static, node)
    for rule in sorted(base_bad):
        fail(f"{rule}: {static.get(rule) or node.get(rule)}")
    fonts = fonts_rule(font_bytes)
    if fonts:
        fail(f"R_FONTES {fonts}")
    server_checks()

    # Positive controls: each mutant must fail exactly through the rule it targets.
    js, css, html, mod = files["app.js"], files["app.css"], files["index.html"], files["module"]

    def sub(text: str, old: str, new: str) -> str:
        if text.count(old) != 1:
            raise AssertionError(f"mutation anchor not found once: {old[:80]}")
        return text.replace(old, new)

    mutants = [
        ("R_MAPA", "tooltip on parcel", {"app.js": sub(js, "g.on('click', function (e) { onParcelClick(e, f); });", "g.on('click', function (e) { onParcelClick(e, f); }); g.bindTooltip(String(p.municipio));")}),
        ("R_MAPA", "hover text", {"app.js": sub(js, "g.on('click', function (e) { onParcelClick(e, f); });", "g.on('mouseover', function () {});")}),
        ("R_M2", "area in m²", {"app.js": sub(js, "area: ha(area),", "area: ha(area) + ' (' + int(area * 10000) + ' m²)',")}),
        ("R_PESSOA", "CPF shown", {"app.js": sub(js, "if (f.tipo) row.details.push('Tipo: ' + f.tipo + '.');", "if (f.tipo) row.details.push('Tipo: ' + f.tipo + '. CPF do titular');")}),
        ("R_PESSOA", "company name read", {"app.js": sub(js, "exactPart(a.anm, 'processo de mineração na ANM')", "exactPart(a.anm, String((a.anm || {}).sample_properties))")}),
        ("R_INTERNOS", "internal risk label", {"app.js": sub(js, "'Consulta pendente. O SICAR não respondeu nesta consulta.'", "'RISCO NÃO CLASSIFICADO'")}),
        ("R_CORES", "old approved green", {"app.css": sub(css, ".selo.sim{background:var(--terra)}", ".selo.sim{background:#63e6a5}")}),
        ("R_TOQUE", "small icon button", {"app.css": sub(css, "width:var(--toque);height:var(--toque);flex:none;padding:0;border:0;border-radius:10px", "width:32px;height:32px;flex:none;padding:0;border:0;border-radius:10px")}),
        ("R_TOQUE", "small tabs", {"app.css": sub(css, "  .abas a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-height:var(--toque);", "  .abas a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-height:36px;")}),
        ("R_FOCO", "focus removed", {"app.css": sub(css, ":focus-visible{outline:3px solid var(--verde);outline-offset:2px}", ":focus-visible{outline:0}")}),
        ("R_INLINE", "inline script", {"index.html": sub(html, "</body>", "<script>window.x=1</script></body>")}),
        ("R_INLINE", "external script", {"index.html": sub(html, '<script src="__LEAFLET_JS__" defer></script>', '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" defer></script>')}),
        ("R_ISOLADO", "portal import", {"module": sub(mod, "from fastapi import Request\n", "from fastapi import Request\nimport portal_v8\n")}),
        ("R_ISOLADO", "portal html patch", {"module": sub(mod, "BUILD = build()\n", "BUILD = build()\nPORTAL_HTML = ''\n")}),
        ("R_ENDPOINTS", "own viewport endpoint", {"app.js": js.replace("/v1/live/sicar/viewport-v46", "/v1/novo/viewport")}),
        ("N2_embargo_without_answer_is_pending", "answer without ok", {"app.js": sub(js, "if (obj.ok === true && ex.available === true && isNum(n) && n >= 0) return", "if (isNum(n) && n >= 0) return")}),
        ("N3_cap_zero_is_pending", "cap ignored", {"app.js": sub(js, "if (obj.ok === true && isNum(n) && n >= 0 && !(capped && n === 0)) return", "if (obj.ok === true && isNum(n) && n >= 0) return")}),
        ("N4_incomplete_found_is_floor", "floor dropped", {"app.js": sub(js, "var bits = [(full ? '' : 'pelo menos ') +", "var bits = [(full ? '' : '') +")}),
        ("N5_incomplete_not_found_is_pending", "incomplete zero as Não", {"app.js": sub(js, "} else if (r.state === 'not_found' && full) {", "} else if (r.state === 'not_found') {")}),
        ("N6_found_with_pending_still_sim", "pending hides a finding", {"app.js": sub(js, "    if (list.some(function (p) { return p.state === SIM; })) return SIM;\n    if (list.some(function (p) { return p.state === PEND; })) return PEND;", "    if (list.some(function (p) { return p.state === PEND; })) return PEND;\n    if (list.some(function (p) { return p.state === SIM; })) return SIM;")}),
        ("N17_pending_plus_none_never_nao", "pending ignored", {"app.js": sub(js, "    if (list.some(function (p) { return p.state === PEND; })) return PEND;\n    return NAO;", "    return NAO;")}),
        ("N8_null_count_is_not_zero", "null read as zero", {"app.js": sub(js, "if (obj.ok === true && ex.available === true && isNum(n) && n >= 0) return { label: label, state: n > 0 ? SIM : NAO", "if (obj.ok === true && ex.available === true) return { label: label, state: n > 0 ? SIM : NAO")}),
        ("N9_water_outside_coverage_hidden", "coverage ignored", {"app.js": sub(js, "if (w && typeof w === 'object' && !outside) {", "if (w && typeof w === 'object') {")}),
        ("N10_soil_national_not_state_layer", "national soil ignored", {"app.js": sub(js, "var t1 = a.terra_nacional && typeof a.terra_nacional === 'object' ? a.terra_nacional : null;", "var t1 = null;")}),
        ("N11_title_only_validated", "unvalidated name as title", {"app.js": sub(js, "p.validated_name_state === 'validated' && p.panel_name_eligible === true ?", "p.validated_name ?")}),
        ("N13_ptbr_numbers", "en-US numbers", {"app.js": sub(js, "return n.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits });", "return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });")}),
        ("N16_merge_keeps_answers", "later pending replaces answer", {"app.js": sub(js, "if (r.seal === PEND && n && n.seal !== PEND) { changed = true; return n; }", "if (n) { changed = true; return n; }")}),
        ("R_CSS", "stray closing brace", {"app.css": sub(css, ".oficial{display:flex;", "}\n.oficial{display:flex;")}),
        ("R_SICAR", "official link removed", {"app.js": sub(js, ">Consultar no SICAR (site oficial)</a>", ">SICAR</a>")}),
        ("R_TOQUE", "small official link", {"app.css": sub(css, ".oficial{display:flex;align-items:center;justify-content:center;min-height:var(--toque);", ".oficial{display:flex;align-items:center;justify-content:center;min-height:30px;")}),
        ("N22_missing_base_is_pending", "missing base left out", {"app.js": sub(js, "    if (!s || typeof s !== 'object') return { label: label, state: PEND };", "    if (!s || typeof s !== 'object') return null;")}),
        ("N23_search_pdf_code_and_dms", "PDF code and DMS not understood", {"app.js": sub(sub(js, "    if (bare) return { kind: 'car', code: bare[1] + '-' + bare[2] + '-' + bare[3] };\n", ""), "    if (/[°º'\"′″]/.test(raw) && /\\d/.test(raw)) return { kind: 'coord-dms' };\n", "")}),
        ("N24_water_none_says_not_lack_of_water", "caveat hidden in the detail", {"app.js": sub(js, "row.lead = said.join(' ') + (found ? '' : ' ' + caveat);", "row.lead = said.join(' ');")}),
        ("N15_answer_words_and_seals", "seal on declaration", {"app.js": sub(js, "var f = fact || {}, row = { id: 'car', q: 'O que o CAR declara?', seal: null,", "var f = fact || {}, row = { id: 'car', q: 'O que o CAR declara?', seal: 'sim',")}),
    ]
    for rule, label, change in mutants:
        mf = dict(files)
        mf.update(change)
        s = static_rules(mf)
        n = node_rules(mf["app.js"]) if "app.js" in change else node
        bad = failing(s, n) - base_bad
        if rule not in bad:
            fail(f"CONTROL {rule} ({label}) was not caught; failing now: {sorted(bad)}")
        else:
            print(f"control ok: {rule} ({label})", flush=True)
    tampered = dict(font_bytes)
    first = sorted(tampered)[0]
    tampered[first] = tampered[first][:-1] + bytes([tampered[first][-1] ^ 1])
    if not fonts_rule(tampered):
        fail("CONTROL R_FONTES: altered font was not caught")
    else:
        print("control ok: R_FONTES (altered font)", flush=True)

    if FAILS:
        print(f"O2_TELA_NOVA_GATE=FAIL {len(FAILS)}")
        return 1
    print(f"O2_TELA_NOVA_GATE=OK static:{len(static)} node:{len(node)} controls:{len(mutants) + 3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
