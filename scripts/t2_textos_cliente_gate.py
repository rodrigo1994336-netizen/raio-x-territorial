"""T2 · textos do cliente: nada de jargão, sigla solta, nome interno ou estado técnico na tela e no PDF.

A régua é a do dono:

  * o sistema nunca transmite informação errada;
  * pode esconder a falha, nunca fingir o acerto — e sinceridade que parece defeito tira
    credibilidade, então o painel não anuncia o que ele deixa de fazer; campo vazio não aparece;
  * fonte que o produto NÃO consulta não é pendência nem ausência: é escopo, dito uma vez, em
    linha própria, fora da tabela de situação das fontes;
  * zero não é ausência, tentar não é responder.

O que o portão confere, no texto que o cliente realmente recebe:

  R1_ANUNCIA_AUSENCIA   nenhuma frase diz o que o sistema não faz ("não inventa denominação",
                        "não herda nomes OSM/SIGEF", "nenhum resultado foi presumido").
  R2_ESTADO_TECNICO     nenhum estado interno vira rótulo ("NÃO VERIFICADA", "REGISTRO ESPACIAL ·
                        VÍNCULO NÃO CONFIRMADO", "FONTE PARCIAL", "PREPARADO — OFF", "NÃO CONSULTADA").
  R3_JARGAO             nenhuma palavra de dentro do código ("backend", "endpoint", "payload",
                        "cache", "timeout", "WFS", "BBOX", "snapshot", "baseline", "gateway").
  R4_NOME_INTERNO       nenhum nome de serviço ou camada interna ("PAMGIA", "Base dos Dados",
                        "IDE:ide_…", "Meta Cloud", "geoserver").
  R5_SIGLA_SOLTA        sigla sem explicação no rótulo que o cliente lê ("ASV", "SINAFLOR", "MTE", "UAS").
  R6_ERRO_CRU           a mensagem técnica do erro não é impressa ("HTTPStatusError", "TimeoutExpired",
                        "Traceback", comando do curl, endereço do serviço).
  R7_ESCOPO             o PDF traz a linha de escopo do cartório e NÃO traz "Registro de imóveis"
                        como fonte não consultada, nem contador de "outras não consultadas".

Onde ele olha (nunca no código-fonte solto, sempre no texto final):

  * o HTML do portal composto pela cadeia real (sitecustomize -> módulos diferidos -> boot guard),
    de onde são extraídas as frases: nós de texto do HTML e literais de string do JS;
  * a tela nova de /novo (HTML + app.js servidos);
  * o PDF renderizado pelo motor que atende o cliente (report_engine_v9), a partir de um payload
    que carrega, de propósito, cada defeito que já chegou ao cliente em 15/09/2026.

Controle positivo: cada regra é medida contra o texto cru do defeito (RAW_DEFECTS) e, no PDF e no
portal, contra mutações que reinjetam a frase antiga — a regra TEM de reprovar. Regra que não
enxerga o próprio defeito não prova nada, e derruba o portão.

Rodar:
  RX_RELEASE=V8_OPERATIONAL_ZERO_COST PYTHONPATH=. python scripts/t2_textos_cliente_gate.py
  ... --pdf caminho.pdf     (audita um PDF já emitido)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "V8_OPERATIONAL_ZERO_COST"

# ---------------------------------------------------------------- regras

RULES: dict[str, re.Pattern[str]] = {
    "R1_ANUNCIA_AUSENCIA": re.compile(
        r"não inventa|nao inventa|não herda nomes|nunca é tratad[ao]|não é tratad[ao] como|"
        r"[Nn]enhum resultado (?:será|foi) presumido|[Nn]enhuma conclusão será inventada|"
        r"[Nn]enhuma ausência foi presumida|[Nn]enhum zero foi inferido|não foi inferido|"
        r"[Nn]ada foi presumido|O sistema (?:não|nunca)|O painel (?:não|nunca)|O Raio-X (?:não|nunca)"
    ),
    "R2_ESTADO_TECNICO": re.compile(
        r"NÃO VERIFICAD[AO]|REGISTRO ESPACIAL|VÍNCULO NÃO CONFIRMADO|FONTE PARCIAL|"
        r"PREPARADO\s*[—-]\s*OFF|INTEGRAÇÃO (?:PREPARADA|RESTRITA)|RISCO NÃO CLASSIFICADO|"
        r"NÃO CONSULTAD[AO]|SISTEMA LIVE|OPERACIONAL\s*[—-]\s*FREE|PERSISTENTE\s*[—-]\s*PRONTO|"
        r"AGUARDANDO VÍNCULO DO BANCO|BACKEND DE ALERTAS|NÃO EXECUTAD[AO]"
    ),
    "R3_JARGAO": re.compile(
        r"\b(?:backend|endpoint|payload|timeout|cache|snapshot|baseline|gateway|"
        r"WFS|BBOX|bbox|viewport|polling|deploy|commit|fallback)\b"
    ),
    "R4_NOME_INTERNO": re.compile(
        r"PAMGIA|Base dos Dados|IDE:ide_|Meta Cloud|geoserver|arcgisonline|BigQuery|GeoSGB/WMS"
    ),
    "R5_SIGLA_SOLTA": re.compile(r"\b(?:ASV|SINAFLOR|MTE|UAS|SNCR|CNARH)\b"),
    "R6_ERRO_CRU": re.compile(
        r"[A-Za-z]*(?:Error|Exception|Timeout)[A-Za-z]*\b|Traceback|Command\s*'?\[|"
        r"(?<![\w\"'=/])https?://"
    ),
}

# Cada regra medida contra o texto exato que chegou ao cliente em 15/09/2026 (ou equivalente).
RAW_DEFECTS: dict[str, str] = {
    "R1_ANUNCIA_AUSENCIA": "O painel não inventa denominação e não herda nomes OSM/SIGEF.",
    "R2_ESTADO_TECNICO": "Registro de imóveis — NÃO CONSULTADA",
    "R3_JARGAO": "Cadastro Ambiental Rural consultado via WFS público.",
    "R4_NOME_INTERNO": "Consulta territorial por polígono e interseção exata. Fonte: FUNAI / IBAMA-PAMGIA.",
    "R5_SIGLA_SOLTA": "ASV 20319201908550: intersecta espacialmente o CAR.",
    "R6_ERRO_CRU": "Camada IDE-Sisema. TimeoutExpired:Command '['curl', '-sS']' timed out",
}

# Frase limpa: nenhuma regra pode reprovar o que já está em português de quem compra terra.
CLEAN_SAMPLES = (
    "Este relatório não inclui matrícula, ônus nem titularidade do cartório de registro de imóveis.",
    "A lista do Ministério do Trabalho é de empregadores, não de imóveis.",
    "Consulta pendente. O SICAR não respondeu nesta consulta.",
    "Área do imóvel: 1.981,20 ha · Curvelo/MG · Consulta feita em 15/09/2026.",
)

# ------------------------------------------------- extração das frases do cliente

# Nó de texto do HTML e literal de string do JS. O objetivo é olhar TEXTO, nunca identificador:
# "cache", "timeout" e "worker" são nomes de variável no código e não podem reprovar o portão.
_TEXT_NODE = re.compile(r">([^<>{}]{3,400})<")
_STRING = re.compile(r"'([^'\\\n]{3,400})'|\"([^\"\\\n]{3,400})\"|`([^`\\]{3,400})`")
# Sintaxe de código, seletor CSS, atributo HTML e chave=valor nunca são frase de cliente.
_CODEISH = re.compile(
    r"=>|\bfunction\b|\bconst \b|\bvar \b|\blet \b|\breturn \b|[{}();\[\]<>=]|\|\||&&|"
    r"--[a-z]|\.\w+\(|^[\w.\-/#?&%:]+$|^[A-Za-z_][\w.]*$|^https?://\S*$"
)
_WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")
_PROSE_SPLIT = re.compile(r"[`'\"<>{}$\\\n]+")
# Comentário do código (em inglês, no repositório inteiro) não é texto de cliente. O "//" de um
# endereço é poupado pelo ":" que vem antes.
_JS_COMMENT = re.compile(r"(?m)(?<![:\w])//[^\n]*|/\*.*?\*/", re.S)
# Seletor de CSS, par chave:número e palavra de comentário em inglês.
_NOT_PROSE = re.compile(
    r"^[.#][\w-]+|\w+\s*:\s*\d|//|\b(?:the|when|with|that|this|from|into|does|must|never|"
    r"answer|source|engine|response|cache|hit)\b"
)


def frases_do_cliente(raw: str) -> list[str]:
    """Só o que tem cara de frase/rótulo lido por gente: >=2 palavras, sem sintaxe de código."""
    raw = _JS_COMMENT.sub(' ', raw)
    candidates: list[str] = []
    for m in _TEXT_NODE.finditer(raw):
        candidates.append(m.group(1))
    for m in _STRING.finditer(raw):
        candidates.append(next(g for g in m.groups() if g is not None))
    # Um literal de template com interpolação (`Fonte: MTE${d.data}`) não é pego pelas duas varreduras
    # acima — e é texto de cliente igual. Aqui o texto cru é quebrado nos delimitadores de código,
    # de modo que o pedaço em português sobreviva sozinho.
    candidates.extend(_PROSE_SPLIT.split(raw))
    out = []
    for item in candidates:
        text = item.strip()
        if len(_WORD.findall(text)) < 2:
            continue
        if _CODEISH.search(text) or _NOT_PROSE.search(text):
            continue
        out.append(text)
    return out


def frases_do_pdf(text: str) -> list[str]:
    """No PDF nao existe codigo: cada linha impressa e texto de cliente, sem filtro nenhum.

    Filtrar "o que parece codigo" aqui seria o pior dos dois mundos: e justamente o rabo tecnico
    (comando do curl, endereco do servico) que precisa ser visto pelo portao.
    """
    return [line.strip() for line in text.splitlines() if line.strip()]


def lint(phrases) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for phrase in phrases:
        for name, rx in RULES.items():
            m = rx.search(phrase)
            if m:
                hits.setdefault(name, []).append(phrase.strip()[:160])
    return hits


# ---------------------------------------------------------------- testes

FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print("FAIL", msg, flush=True)


def ok(msg: str) -> None:
    print("ok", msg, flush=True)


def test_regras_veem_o_defeito_cru() -> None:
    for name, sample in RAW_DEFECTS.items():
        if not RULES[name].search(sample):
            fail(f"controle positivo: {name} não enxerga {sample!r}")
    for sample in CLEAN_SAMPLES:
        hits = lint([sample])
        if hits:
            fail(f"falso positivo em texto limpo: {sample!r} -> {hits}")
    ok("cada regra enxerga o próprio defeito e não reprova texto limpo")


def test_extrator_separa_texto_de_codigo() -> None:
    codigo = "const cache=new Map();let timeout=null;fetch(url).then(r=>r.json())"
    if frases_do_cliente(codigo):
        fail(f"o extrator devolveu código como frase: {frases_do_cliente(codigo)}")
    texto = "<span>Consulta pendente. O SICAR não respondeu.</span>"
    if "Consulta pendente. O SICAR não respondeu." not in frases_do_cliente(texto):
        fail("o extrator perdeu um nó de texto do HTML")
    if "O painel não inventa denominação." not in frases_do_cliente("x='O painel não inventa denominação.'"):
        fail("o extrator perdeu um literal de string do JS")
    ok("o extrator separa frase de cliente de identificador de código")


def rotulos_de_fonte() -> dict[str, str]:
    """Rótulos que a leitura completa e o PDF imprimem, vindos do servidor (não do HTML).

    A varredura do HTML não alcança este texto: ele nasce em Python e chega ao cartão por JSON.
    Sem esta checagem, "FUNAI / IBAMA-PAMGIA" continuaria na tela com o portão verde.
    """
    sys.path.insert(0, str(ROOT))
    import iphan_sicg
    import parity_public_layers  # noqa: F401 - injeta SFB/IPHAN antes de ler o catálogo
    import sinaflor_authorization_v48
    import source_audit_registry_v49
    import territorial_constraints

    out: dict[str, str] = {}
    for key, meta in territorial_constraints.SERVICES.items():
        out[f"territorial_constraints.SERVICES[{key}]"] = str(meta[1])
    for source_id, label in source_audit_registry_v49._SOURCE_CATALOG:
        out[f"source_audit_registry_v49[{source_id}]"] = str(label)
    out["iphan_sicg.SOURCE_LABEL"] = str(iphan_sicg.SOURCE_LABEL)
    out["sinaflor_authorization_v48.SOURCE_NAME"] = str(sinaflor_authorization_v48.SOURCE_NAME)
    return out


def test_rotulos_de_fonte_do_servidor(rotulos: dict[str, str]) -> None:
    for onde, label in rotulos.items():
        hits = lint([label])
        if hits:
            fail(f"rótulo de fonte servido ao cliente: {onde} = {label!r} -> {sorted(hits)}")
    # Controle positivo: o rótulo antigo tem de reprovar.
    if not lint(["FUNAI / IBAMA-PAMGIA"]):
        fail("controle positivo: o rótulo antigo 'FUNAI / IBAMA-PAMGIA' deveria reprovar")
    if not FAILS:
        ok(f"rótulos de fonte do servidor limpos ({len(rotulos)} conferidos)")


def boot_portal() -> tuple[str, str, str]:
    sys.path.insert(0, str(ROOT))
    import sitecustomize  # noqa: F401
    import portal_api  # noqa: F401
    import portal_boot_guard_v26 as guard

    deadline = time.time() + 60
    while time.time() < deadline and not guard.STATE.get("ready") and not guard.STATE.get("error"):
        time.sleep(0.1)
    assert guard.STATE.get("ready") is True, ("portal boot failed", guard.STATE)
    import portal_v8
    from fastapi.testclient import TestClient

    client = TestClient(portal_api.app)
    novo = client.get("/novo")
    assert novo.status_code == 200, ("/novo não respondeu", novo.status_code)
    app_js = ""
    for name in re.findall(r'src="(/novo/a/[^"]+)"', novo.text):
        part = client.get(name)
        if part.status_code == 200 and "leaflet" not in name:
            app_js += part.text
    return portal_v8.PORTAL_HTML, novo.text, app_js


def test_portal_servido_esta_limpo(portal_html: str, novo_html: str, novo_js: str) -> None:
    for label, raw in (("portal", portal_html), ("/novo", novo_html + novo_js)):
        hits = lint(frases_do_cliente(raw))
        if hits:
            for name, found in hits.items():
                fail(f"{label}: {name} -> {found[:4]}")
        else:
            ok(f"{label}: nenhum jargão, sigla solta, nome interno ou estado técnico no texto servido")


def test_mutacao_no_portal_reprova(portal_html: str) -> None:
    """Controle positivo do caminho real: reinjeta a frase antiga no HTML servido e exige reprovação."""
    mutants = {
        "R1_ANUNCIA_AUSENCIA": "<div class=\"rx45-name-note\">O painel não inventa denominação e não herda nomes OSM/SIGEF.</div>",
        "R2_ESTADO_TECNICO": "<span class=\"rx48-check-status\">NÃO VERIFICADA</span>",
        "R3_JARGAO": "<span>Cadastro consultado via WFS público no geoportal.</span>",
        "R4_NOME_INTERNO": "<span>Fonte: FUNAI / IBAMA-PAMGIA para esta camada.</span>",
        "R5_SIGLA_SOLTA": "<span>ASV localizada sobre o imóvel.</span>",
        "R6_ERRO_CRU": "<span>Falhou aqui: HTTPStatusError ao consultar a base.</span>",
    }
    for name, mutant in mutants.items():
        hits = lint(frases_do_cliente(portal_html + mutant))
        if name not in hits:
            fail(f"mutante sobreviveu no portal: {name} não reprovou {mutant!r}")
    ok("cada regra reprova a frase antiga reinjetada no HTML servido")


# ---------------------------------------------------------------- PDF

def _payload_com_defeitos() -> dict:
    return {
        "report_id": "RX-T2-GATE",
        "generated_at": "2026-09-15T23:04:27.150790+00:00",
        "property": {"name": "", "area_ha": 14.795, "municipality": "Curvelo", "uf": "MG",
                     "car_code": "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"},
        "car": {"status": "AT", "fields": [["Fonte", "SICAR / WFS público"], ["Status do imóvel", "AT"]]},
        "land": {
            "certifications": [["SIGEF", "CONSULTA PENDENTE", "—", "Consulta ao INCRA pendente."]],
            "matrix": [
                ["CAR", "AT", "14.795 ha", "Cadastro ambiental consultado"],
                ["Matrícula", "NÃO CONSULTADA", "-", "Exige fonte registral adequada"],
                ["Detentor/titular", "NÃO CONSULTADO", "-", "Não inferido a partir do CAR"],
            ],
            "evidence": {"score": "LIMITADA", "text": "Matrícula e cadeia dominial não foram consultadas neste ciclo."},
        },
        "environment": {"layer_rows": [["Terra Indígena", "Nenhuma", "FUNAI / IBAMA-PAMGIA"]]},
        "productive": {"soil_rows": [["Solo", "Camada IDE:ide_1502_mg_mapa_solos_pol; 0 interseção(ões). "
                                              "TimeoutExpired:Command '['curl', '-sS']' timed out"]]},
        "enforcement": {}, "mining": {}, "monitoring": {}, "water": {}, "agropecuaria": {},
        "satellite_imagery": {}, "conclusion": {},
        "narrative": {"one_sentence": "Imóvel de 14.795 ha em Curvelo/MG.",
                      "what_we_found": ["Algumas bases não entregaram resposta completa nesta emissão: Registro de imóveis."]},
        "sources": [
            {"name": "SICAR", "description": "Cadastro Ambiental Rural consultado via WFS público.", "status": "CONSULTADA", "level": "ok"},
            {"name": "IBAMA / PAMGIA", "description": "Base oficial de áreas embargadas do IBAMA, com cruzamento exato pela geometria do CAR.", "status": "CONSULTADA", "level": "ok"},
            {"name": "Registro de imóveis", "description": "Matrícula e titularidade não consultadas nesta emissão.", "status": "NÃO CONSULTADA", "level": "neutral"},
            {"name": "IDE-Sisema / Mapa de Solos", "description": "Camada IDE:ide_1502_mg_mapa_solos_pol; 0 interseção(ões) exata(s). TimeoutExpired:Command '['curl', '-sS']' timed out", "status": "INDISPONÍVEL", "level": "attention"},
        ],
        "interpretation_rules": ["NÃO CONSULTADO nunca é tratado como ausência de ocorrência."],
    }


def _render(payload: dict) -> str:
    from PIL import Image
    from pypdf import PdfReader
    from report_engine_v9 import build_premium_property_report_v9

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        cover = folder / "cover.jpg"
        Image.new("RGB", (32, 32), (240, 240, 240)).save(cover, format="JPEG")
        payload = {**payload, "satellite_image_path": str(cover)}
        pdf = folder / "t2.pdf"
        build_premium_property_report_v9(pdf, payload)
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(pdf)).pages)


def test_pdf_do_cliente_esta_limpo() -> None:
    import report_ptbr_v50

    text = _render(_payload_com_defeitos())
    hits = lint(frases_do_pdf(text))
    if hits:
        for name, found in hits.items():
            fail(f"PDF: {name} -> {found[:4]}")
    else:
        ok("PDF: nenhum jargão, nome interno, estado técnico ou erro cru no texto impresso")

    flat = " ".join(text.split())
    # R7: o escopo é dito uma vez, em linha própria — e nunca como fonte "não consultada".
    if "não inclui matrícula" not in flat:
        fail("PDF: falta a linha de escopo do cartório (SCOPE_NOTE) na cobertura das fontes")
    if "Registro de imóveis" in flat:
        fail("PDF: 'Registro de imóveis' ainda aparece como fonte")
    if "não consultadas nesta emissão" in flat:
        fail("PDF: ainda existe contador de fontes 'não consultadas nesta emissão'")
    if "Matrícula" in flat and "Detentor" in flat and "NÃO CONSULT" in flat:
        fail("PDF: a matriz de vínculo ainda lista matrícula/detentor como não consultados")
    ok("PDF: escopo do cartório dito uma vez, fora da tabela de situação das fontes")

    # Controle positivo do PDF: desligados o filtro de escopo (client_payload) e a reescrita de
    # texto (normalize_text), o MESMO payload tem de voltar a reprovar — nas duas coisas.
    original_payload = report_ptbr_v50.client_payload
    original_text = report_ptbr_v50.normalize_text
    try:
        report_ptbr_v50.client_payload = lambda payload: payload
        report_ptbr_v50.normalize_text = lambda text: text
        cru = _render(_payload_com_defeitos())
        cru_flat = " ".join(cru.split())
        if "Registro de imóveis" not in cru_flat:
            fail("controle positivo do PDF: sem o filtro, 'Registro de imóveis' deveria voltar a aparecer")
        cru_hits = lint(frases_do_pdf(cru))
        for name in ("R2_ESTADO_TECNICO", "R3_JARGAO", "R4_NOME_INTERNO", "R6_ERRO_CRU"):
            if name not in cru_hits:
                fail(f"controle positivo do PDF: sem a reescrita, {name} deveria reprovar o texto impresso")
    finally:
        report_ptbr_v50.client_payload = original_payload
        report_ptbr_v50.normalize_text = original_text
    ok("controle positivo: com filtro e reescrita desligados, o mesmo payload reprova")


def lint_pdf(path: str) -> int:
    from pypdf import PdfReader

    total = 0
    for number, page in enumerate(PdfReader(path).pages, 1):
        raw = page.extract_text() or ""
        for name, found in lint(frases_do_pdf(raw)).items():
            total += len(found)
            print(f"p{number} {name}: {found[:4]}")
    print(f"RX_T2_TEXTOS_CLIENTE_PDF_TOTAL={total}")
    return 1 if total else 0


# ---------------------------------------------------------------- execução

def reexec_with_portal_env() -> None:
    if os.environ.get("RX_RELEASE") == RELEASE and os.environ.get("T2_GATE_CHILD") == "1":
        return
    env = dict(os.environ, RX_RELEASE=RELEASE, T2_GATE_CHILD="1", PYTHONUTF8="1")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    sys.exit(subprocess.call([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], env=env, cwd=str(ROOT)))


def main() -> int:
    if "--pdf" in sys.argv:
        return lint_pdf(sys.argv[sys.argv.index("--pdf") + 1])
    test_regras_veem_o_defeito_cru()
    test_extrator_separa_texto_de_codigo()
    test_rotulos_de_fonte_do_servidor(rotulos_de_fonte())
    portal_html, novo_html, novo_js = boot_portal()
    test_portal_servido_esta_limpo(portal_html, novo_html, novo_js)
    test_mutacao_no_portal_reprova(portal_html)
    test_pdf_do_cliente_esta_limpo()
    if FAILS:
        print(f"RX_T2_TEXTOS_CLIENTE_GATE=FAIL falhas={len(FAILS)}")
        return 1
    print("RX_T2_TEXTOS_CLIENTE_GATE=PASS")
    return 0


if __name__ == "__main__":
    reexec_with_portal_env()
    raise SystemExit(main())
