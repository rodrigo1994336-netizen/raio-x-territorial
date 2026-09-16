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
    # T2 (correção): sem re.I, o estado técnico escapava toda vez que o motor o imprimia em minúscula —
    # é o caso da coluna "escopo · estado" do relatório ("raio 50 km · indisponível").
    "R2_ESTADO_TECNICO": re.compile(
        r"NÃO VERIFICAD[AO]|REGISTRO ESPACIAL|VÍNCULO NÃO CONFIRMADO|FONTE PARCIAL|"
        r"PREPARADO\s*[—-]\s*OFF|INTEGRAÇÃO (?:PREPARADA|RESTRITA)|RISCO NÃO CLASSIFICADO|"
        r"NÃO\s+CONSULTAD[AO]|NAO\s+CONSULTAD[AO]|SISTEMA LIVE|OPERACIONAL\s*[—-]\s*FREE|"
        r"PERSISTENTE\s*[—-]\s*PRONTO|AGUARDANDO VÍNCULO DO BANCO|BACKEND DE ALERTAS|"
        r"NÃO\s+EXECUTAD[AO]|(?<![\w])INDISPONÍVEL(?![\w])|(?<![\w])INDISPONIVEL(?![\w])",
        re.I,
    ),
    "R3_JARGAO": re.compile(
        r"\b(?:backend|endpoint|payload|timeout|cache|snapshot|baseline|gateway|"
        r"WFS|BBOX|viewport|polling|deploy|commit|fallback)\b",
        re.I,
    ),
    # T2 (correção): IDE-Sisema (nome do sistema de dados de MG) e GeoSGB/SIAGAS (sistemas do Serviço
    # Geológico) apareciam no texto do cliente sem que nenhuma regra os visse.
    "R4_NOME_INTERNO": re.compile(
        r"PAMGIA|Base dos Dados|IDE:ide_|IDE-Sisema|Meta Cloud|geoserver|arcgisonline|BigQuery|"
        r"GeoSGB|SGB\s*/\s*SIAGAS|SGB\s*/\s*GeoSGB",
        re.I,
    ),
    "R5_SIGLA_SOLTA": re.compile(r"\b(?:ASV|SINAFLOR|MTE|UAS|SNCR|CNARH)\b"),
    # Completada em _carregar_regra_de_erro() com a MESMA expressão de endereço técnico que o relatório usa.
    "R6_ERRO_CRU": re.compile(r"[A-Za-z]*(?:Error|Exception|Timeout)[A-Za-z]*\b|Traceback|Command\s*'?\["),
}

_R6_BASE = r"[A-Za-z]*(?:Error|Exception|Timeout)[A-Za-z]*\b|Traceback|Command\s*'?\["


def _carregar_regra_de_erro() -> None:
    """R6 usa a MESMA expressão de endereço técnico que o relatório usa para cortar.

    T2 (correção): a alternativa `https?://` solta cortava — e o portão EXIGIA que cortasse — a atribuição
    obrigatória da licença Creative Commons, única prosa do relatório com endereço. Duas cópias da regra em
    arquivos diferentes foi o que congelou o defeito; agora existe uma só, importada do módulo que corta.
    """
    sys.path.insert(0, str(ROOT))
    import report_ptbr_v50

    RULES["R6_ERRO_CRU"] = re.compile(_R6_BASE + r"|(?<![\w\"'=/])" + report_ptbr_v50._SERVICE_URL)

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
    # A atribuição da licença é obrigatória e traz endereço em prosa: nenhuma regra pode reprová-la,
    # e nenhuma reescrita pode cortá-la (era o que acontecia, em todo relatório emitido).
    "INPE/TerraBrasilis (DETER) — CC BY-SA 4.0; dados recortados e combinados pelo Raio-X; "
    "material adaptado sob a mesma licença (https://creativecommons.org/licenses/by-sa/4.0/).",
    "MapBiomas Alerta — CC BY-SA 3.0 BR; dados recortados e combinados pelo Raio-X; "
    "material adaptado sob a mesma licença (https://creativecommons.org/licenses/by-sa/3.0/br/).",
    # Rótulo com sigla explicada entre parênteses: a forma canônica do produto (o filtro descartava tudo
    # que tivesse parêntese, e por isso nunca lintava nenhuma destas).
    "Trabalho escravo (Ministério do Trabalho)",
    "Autorização para cortar vegetação (IBAMA)",
    "Fonte: Ministério do Trabalho · lista de 04/09/2026 · consulta em 15/09/2026",
)

# T2: título que o comprador lê nunca afirma ausência a partir de um estado "não confirmado" nem de um
# "limpo" que a própria razão relativiza logo abaixo. Zero não é ausência; não confirmado não é "não existe".
_AFIRMA_AUSENCIA = re.compile(
    r"SEM (?:LIGAÇÃO|VÍNCULO|RELAÇÃO)|NÃO EXISTE|INEXISTENTE|NENHUM[AO]?\s+\w+\s+SOBRE O IMÓVEL|"
    r"SEM (?:AUTORIZAÇÃO|OCORRÊNCIA|REGISTRO)$"
)

# ------------------------------------------------- extração das frases do cliente

# Nó de texto do HTML e literal de string do JS. O objetivo é olhar TEXTO, nunca identificador:
# "cache", "timeout" e "worker" são nomes de variável no código e não podem reprovar o portão.
_TEXT_NODE = re.compile(r">([^<>{}]{3,400})<")
_STRING = re.compile(r"'([^'\\\n]{3,400})'|\"([^\"\\\n]{3,400})\"|`([^`\\]{3,400})`")
# Sintaxe de código, seletor CSS, atributo HTML e chave=valor nunca são frase de cliente.
#
# T2 (correção): o parêntese estava nesta classe, então QUALQUER frase de cliente com parêntese era
# descartada antes de qualquer regra rodar — e "Trabalho escravo (Ministério do Trabalho)" é justamente
# a forma canônica do rótulo com sigla explicada. Um mutante com parêntese sobrevivia ao portão inteiro.
# O que separa código de prosa não é o parêntese: é a chamada, `nome(` colado, sem espaço.
#
# O ponto-e-vírgula também saiu da classe: em português ele é pontuação comum ("consultado; não prova"),
# e enquanto esteve aqui descartou dezenas de frases inteiras de cliente — entre elas uma que anunciava
# ausência ("nada foi presumido") e nunca foi lintada.
#
# O que sobrou como marca de código, e não existe em prosa: camelCase (enableHighAccuracy, clearTimeout),
# identificador pontuado sem espaço (ctrl.signal), chave:valor colado (timeout:9000) e a chamada `nome(`.
# A chamada abre exceção para o plural do português, que não leva espaço: "interseção(ões)", "0 imóvel(is)".
_CODEISH = re.compile(
    r"=>|\bfunction\b|\bconst \b|\bvar \b|\blet \b|\breturn \b|[{}\[\]<>=]|\|\||&&|"
    r"--[a-z]|\.\w+\(|\b[a-z][a-z0-9_]*[A-Z]\w*\b|(?<=[a-z])\.(?=[a-z])|\b\w+:\d|"
    r"\b\w+\s*:\s*\d+\s*,\s*\w+\s*:|"
    r"[A-Za-z_]\w*\((?!(?:ões|õe|s|es|as|is|ão|a|o)\)|\s)|"
    r"^[\w.\-/#?&%:]+$|^[A-Za-z_][\w.]*$|^https?://\S*$"
)
_WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")
_PROSE_SPLIT = re.compile(r"[`'\"<>{}$\\\n]+")
# Comentário do código (em inglês, no repositório inteiro) não é texto de cliente. O "//" de um
# endereço é poupado pelo ":" que vem antes.
_JS_COMMENT = re.compile(r"(?m)(?<![:\w])//[^\n]*|/\*.*?\*/", re.S)
# Seletor de CSS e comentário de código.
# T2 (correção): saíram daqui duas listas que descartavam texto de cliente inteiro —
#  * `\w+\s*:\s*\d`, que matava "Fonte: MTE · dado: 04/09/2026", a forma exata do rodapé do cartão;
#  * a lista de palavras em inglês uma a uma, que incluía "cache", "source" e "response": QUALQUER frase
#    de cliente com uma delas era descartada, e a palavra "cache" da regra R3 nunca podia disparar.
# Comentário em inglês agora é reconhecido pelo conjunto (duas ou mais marcas inglesas e nenhuma portuguesa).
_NOT_PROSE = re.compile(r"^[.#][\w-]+|//")
_EN_MARKER = re.compile(r"\b(?:the|when|with|that|this|from|into|does|must|never|only|its|it|was|are|"
                        r"which|while|there|their|would|should|because)\b", re.I)
# Uma frase descartada que tem cara de português é sinal de buraco no filtro, não de código: o portão
# conta quantas foram, mostra amostra e reprova acima do teto. Foi assim que o parêntese passou batido.
_PT_MARKER = re.compile(r"\b(?:de|da|do|não|nao|para|com|que|em|no|na|uma?|os|pelo|pela|sem|nesta|ao|à|é)\b", re.I)
TETO_PROSA_DESCARTADA = 0
ULTIMO_DESCARTE: dict[str, list[str]] = {}


def _comentario_em_ingles(text: str) -> bool:
    return len(_EN_MARKER.findall(text)) >= 2 and not _PT_MARKER.search(text)


_CODE_CHARS = re.compile(r"[(){}\[\]=<>|&/\\]")


def _parece_prosa(text: str) -> bool:
    """Frase de gente, não pedaço de código que por acaso tem palavra em português dentro.

    Uma expressão regular do JS (`/clique em um polígono|escolha o município/i.test(t)`) tem palavras
    portuguesas e não é texto de cliente: o que a separa é a densidade de sintaxe.
    """
    if len(_WORD.findall(text)) < 4 or len(_PT_MARKER.findall(text)) < 2:
        return False
    return len(_CODE_CHARS.findall(text)) * 20 <= len(text)


def _coberta(text: str, lidas: str) -> bool:
    """O trecho em português deste descarte já foi lido em outra frase?

    Um pedaço de HTML com atributo (`<span class="x">Texto</span>`) é código E carrega prosa: o texto
    de dentro entra pelo nó de texto. Contar esse descarte como buraco seria alarme falso. Só contam os
    pedaços que são prosa de verdade — o pedaço de código do mesmo trecho não precisa ter sido lido.
    """
    pedacos = [p.strip() for p in re.split(r"[<>{}$`'\"\n]+", text) if len(p.strip()) >= 25]
    prosa = [p for p in pedacos if _parece_prosa(p)]
    return all(p in lidas for p in prosa)


def frases_do_cliente(raw: str, rotulo: str = "") -> list[str]:
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
    out: list[str] = []
    suspeitas: list[str] = []
    for item in candidates:
        text = item.strip()
        if len(_WORD.findall(text)) < 2:
            continue
        if _CODEISH.search(text) or _NOT_PROSE.search(text) or _comentario_em_ingles(text):
            if _parece_prosa(text):
                suspeitas.append(text)
            continue
        out.append(text)
    lidas = "\n".join(out)
    descartadas = [x for x in suspeitas if not _coberta(x, lidas)]
    if rotulo:
        ULTIMO_DESCARTE[rotulo] = descartadas
        print(f"extrator[{rotulo}]: {len(out)} frases lidas · {len(suspeitas)} com cara de prosa filtradas · "
              f"{len(descartadas)} nunca lidas de outro jeito", flush=True)
        for sample in descartadas[:5]:
            print(f"   nunca lida: {sample[:140]!r}", flush=True)
    return out


def frases_do_pdf(text: str) -> list[str]:
    """No PDF nao existe codigo: cada linha impressa e texto de cliente, sem filtro nenhum.

    Filtrar "o que parece codigo" aqui seria o pior dos dois mundos: e justamente o rabo tecnico
    (comando do curl, endereco do servico) que precisa ser visto pelo portao.

    T2 (correcao): a coluna de status do relatorio e estreita e quebra o rotulo em duas linhas
    ("CONSULTA\\nPENDENTE", "NAO\\nCONSULTADA"). Lendo so linha a linha, o estado tecnico escapava
    justamente onde ele aparece. Cada pagina tambem entra junta, num texto so.
    """
    saida: list[str] = []
    for pagina in str(text).split("\f"):
        linhas = [line.strip() for line in pagina.splitlines() if line.strip()]
        saida.extend(linhas)
        if linhas:
            saida.append(" ".join(linhas))
    return saida


def lint(phrases) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for phrase in phrases:
        for name, rx in RULES.items():
            m = rx.search(phrase)
            if m:
                # O trecho em volta do acerto: numa página inteira junta, a frase cortada em 160 não diz nada.
                janela = phrase[max(0, m.start() - 70):m.end() + 70].strip()
                hits.setdefault(name, []).append(janela)
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
    # T2 (correção): a amostra antiga não tinha parêntese nenhum, então o controle não cobria o buraco
    # pelo qual TODA frase com parêntese saía do portão — inclusive os três defeitos de 15/09.
    com_parenteses = (
        "<div>O painel não inventa denominação (e não herda nomes OSM/SIGEF).</div>"
        "<span>Fonte: FUNAI / IBAMA-PAMGIA (camada pública)</span>"
        "<b>NÃO VERIFICADA (sem documento do dono)</b>"
    )
    lidas = frases_do_cliente(com_parenteses)
    for esperada in ("não inventa denominação", "IBAMA-PAMGIA", "NÃO VERIFICADA"):
        if not any(esperada in x for x in lidas):
            fail(f"o extrator descartou frase de cliente com parêntese: {esperada!r} sumiu de {lidas}")
    if not {"R1_ANUNCIA_AUSENCIA", "R2_ESTADO_TECNICO", "R4_NOME_INTERNO"} <= set(lint(lidas)):
        fail(f"frase com parêntese entrou mas nenhuma regra reprovou: {sorted(lint(lidas))}")
    ok("o extrator separa frase de cliente de identificador de código, e não perde frase com parêntese")


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


# Módulos cujo `reason` é impresso no cartão palavra por palavra (portal_conformity_*.view/mtePaint).
MODULOS_DE_CONFORMIDADE = (
    "sinaflor_authorization_v48.py",
    "sinaflor_authorization_hardening_v48.py",
    "mte_slave_labor_v48.py",
    "portal_panel_sources_f2.py",
)


def _texto_literal(node) -> str:
    """Texto de um literal, inclusive f-string: a parte interpolada vira um número de exemplo."""
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        partes = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                partes.append(item.value)
            else:
                partes.append("9")
        return "".join(partes)
    return ""


def textos_de_reason() -> dict[str, str]:
    """Cada `reason`/`status` escrito nos módulos de conformidade — é isso que o cartão imprime.

    O portão olhava SOURCE_NAME, SERVICES e o catálogo, mas nenhum campo `reason`: por isso "ASV",
    "UAS", "SINAFLOR", "polígono" e "intersectando" seguiam na tela do cliente com o portão verde.
    """
    import ast

    out: dict[str, str] = {}
    for nome in MODULOS_DE_CONFORMIDADE:
        caminho = ROOT / nome
        tree = ast.parse(caminho.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for chave, valor in zip(node.keys, node.values):
                    if isinstance(chave, ast.Constant) and chave.value in ("reason", "status"):
                        texto = _texto_literal(valor)
                        if texto:
                            out[f"{nome}:{getattr(valor, 'lineno', 0)}:{chave.value}"] = texto
            elif isinstance(node, ast.Assign):
                if any(isinstance(alvo, ast.Name) and alvo.id in ("reason", "status", "PENDING_REASON", "PENDING_STATUS") for alvo in node.targets):
                    texto = _texto_literal(node.value)
                    if texto:
                        out[f"{nome}:{node.lineno}:reason"] = texto
    return out


def test_reason_dos_modulos_de_conformidade(reasons: dict[str, str]) -> None:
    if len(reasons) < 10:
        fail(f"varredura de reason quebrada: só {len(reasons)} textos lidos de {len(MODULOS_DE_CONFORMIDADE)} módulos")
    for onde, texto in reasons.items():
        hits = lint([texto])
        if hits:
            fail(f"reason impresso no cartão: {onde} = {texto[:110]!r} -> {sorted(hits)}")
    # Controle positivo: o texto antigo TEM de reprovar.
    antigo = "Nenhum polígono público de ASV/UAS do SINAFLOR foi localizado intersectando o imóvel."
    if "R5_SIGLA_SOLTA" not in lint([antigo]):
        fail("controle positivo: o reason antigo do SINAFLOR deveria reprovar por sigla solta")
    if not FAILS:
        ok(f"reason dos módulos de conformidade em português de comprador ({len(reasons)} conferidos)")


def test_titulo_nao_afirma_ausencia() -> None:
    """Estado interno não confirmado nunca vira, no título, a afirmação de que não existe."""
    sys.path.insert(0, str(ROOT))
    import portal_panel_sources_f2 as f2

    for source_id in (f2.MTE_ID, f2.SINAFLOR_ID):
        for estado, titulo in f2.status_labels(source_id).items():
            if estado.endswith("_unconfirmed") or estado.endswith("_clear"):
                achado = _AFIRMA_AUSENCIA.search(titulo)
                if achado:
                    fail(f"título afirma ausência num estado {estado}: {titulo!r} (trecho {achado.group(0)!r})")
    # Controle positivo: os dois títulos de 15/09 têm de reprovar nesta regra.
    for titulo in ("REGISTRO NA REGIÃO, SEM LIGAÇÃO COM O IMÓVEL", "NENHUMA AUTORIZAÇÃO SOBRE O IMÓVEL"):
        if not _AFIRMA_AUSENCIA.search(titulo):
            fail(f"controle positivo: o título antigo {titulo!r} deveria reprovar")
    ok("nenhum título afirma ausência a partir de estado não confirmado ou limpo")


def test_titulos_do_cartao_vem_de_um_lugar_so(portal_html: str) -> None:
    """O título escrito duas vezes (Python e JS) divergiu. Agora o JS servido carrega o texto do servidor."""
    sys.path.insert(0, str(ROOT))
    import portal_panel_sources_f2 as f2

    for source_id in (f2.MTE_ID, f2.SINAFLOR_ID):
        for estado, titulo in f2.status_labels(source_id).items():
            if titulo not in portal_html:
                fail(f"título do servidor não chegou ao HTML servido: {source_id}/{estado} = {titulo!r}")
    ok("os títulos do cartão vêm do servidor e chegam iguais ao HTML servido")


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
        hits = lint(frases_do_cliente(raw, rotulo=label))
        if hits:
            for name, found in hits.items():
                fail(f"{label}: {name} -> {found[:4]}")
        else:
            ok(f"{label}: nenhum jargão, sigla solta, nome interno ou estado técnico no texto servido")
        descartadas = ULTIMO_DESCARTE.get(label) or []
        if len(descartadas) > TETO_PROSA_DESCARTADA:
            fail(f"{label}: {len(descartadas)} frase(s) com cara de prosa foram descartadas pelo filtro "
                 f"(teto {TETO_PROSA_DESCARTADA}); o portão não olhou nenhuma delas: {descartadas[:4]}")


def test_mutacao_no_portal_reprova(portal_html: str) -> None:
    """Controle positivo do caminho real: reinjeta a frase antiga no HTML servido e exige reprovação."""
    mutants = {
        "R1_ANUNCIA_AUSENCIA": "<div class=\"rx45-name-note\">O painel não inventa denominação e não herda nomes OSM/SIGEF.</div>",
        "R2_ESTADO_TECNICO": "<span class=\"rx48-check-status\">NÃO VERIFICADA</span>",
        "R3_JARGAO": "<span>Cadastro consultado via WFS público no geoportal.</span>",
        "R4_NOME_INTERNO": "<span>Fonte: FUNAI / IBAMA-PAMGIA para esta camada.</span>",
        "R5_SIGLA_SOLTA": "<span>ASV localizada sobre o imóvel.</span>",
        "R6_ERRO_CRU": "<span>Falhou aqui: HTTPStatusError ao consultar a base.</span>",
        # T2 (correção): os três defeitos de 15/09 na forma EXATA em que estavam na tela — com parêntese.
        # Nesta forma eles sobreviviam ao portão inteiro: só o parêntese os separava dos mutantes acima.
        "R1_ANUNCIA_AUSENCIA_PARENTESE": "<div>O painel não inventa denominação (e não herda nomes OSM/SIGEF)</div>",
        "R4_NOME_INTERNO_PARENTESE": "<span>Fonte: FUNAI / IBAMA-PAMGIA (camada pública)</span>",
        "R2_ESTADO_TECNICO_PARENTESE": "<span>NÃO VERIFICADA (sem documento do dono)</span>",
    }
    for name, mutant in mutants.items():
        regra = name.replace("_PARENTESE", "")
        hits = lint(frases_do_cliente(portal_html + mutant))
        if regra not in hits:
            fail(f"mutante sobreviveu no portal: {regra} não reprovou {mutant!r}")
    ok("cada regra reprova a frase antiga reinjetada no HTML servido, com e sem parêntese")


def test_mutacao_sobre_frase_real(portal_html: str) -> None:
    """Mutação sobre frases sorteadas do HTML SERVIDO, não sobre exemplos inventados.

    O mutante inventado prova que a regra funciona no exemplo do autor; este prova que ela funciona na
    forma que o texto tem de verdade nesta tela — que é onde o filtro do extrator falhava.
    """
    reais = [x for x in frases_do_cliente(portal_html) if len(x) > 30][:400]
    if len(reais) < 20:
        fail(f"mutação sobre texto real: só {len(reais)} frases servidas para sortear")
        return
    passo = max(1, len(reais) // 8)
    amostra = reais[::passo][:8]
    sujeiras = {
        "R3_JARGAO": " O backend respondeu por cache.",
        "R4_NOME_INTERNO": " Fonte: IBAMA-PAMGIA.",
        "R5_SIGLA_SOLTA": " Consulta ASV no SINAFLOR.",
    }
    for frase in amostra:
        for regra, sujeira in sujeiras.items():
            mutante = f"<span>{frase}{sujeira}</span>"
            if regra not in lint(frases_do_cliente(portal_html + mutante)):
                fail(f"mutante sobre frase real sobreviveu: {regra} não reprovou {mutante[:120]!r}")
    ok(f"mutação sobre {len(amostra)} frases sorteadas do HTML servido: toda regra reprova")


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
        # A atribuição obrigatória da licença, com o endereço em prosa: tem de sair INTEIRA no PDF.
        "sources_page_credits": [
            {"text": "INPE/TerraBrasilis (DETER) — CC BY-SA 4.0; dados recortados e combinados pelo Raio-X; "
                     "material adaptado sob a mesma licença", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"},
            {"text": "MapBiomas Alerta — CC BY-SA 3.0 BR; dados recortados e combinados pelo Raio-X; "
                     "material adaptado sob a mesma licença", "license_url": "https://creativecommons.org/licenses/by-sa/3.0/br/"},
        ],
    }


def _payload_sem_bloco_de_fontes() -> dict:
    """Payload sem "sources": a linha de escopo tem de aparecer do mesmo jeito.

    A remoção da matrícula/detentor da matriz de vínculo roda sempre; a nota de escopo só era gravada
    dentro de `if "sources" in out`. Sem o bloco de fontes, o limite do cartório sumia sem substituto.
    """
    payload = _payload_com_defeitos()
    payload.pop("sources", None)
    return payload


def _render(payload: dict) -> str:
    from PIL import Image
    from pypdf import PdfReader
    # V10 é o motor que atende o cliente (live_report_adapter_v20). Gerar por V9 deixava de fora a
    # página de créditos — justamente onde mora a atribuição obrigatória de licença que a reescrita cortava.
    from report_engine_v10 import build_premium_property_report_v10

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        cover = folder / "cover.jpg"
        Image.new("RGB", (32, 32), (240, 240, 240)).save(cover, format="JPEG")
        payload = {**payload, "satellite_image_path": str(cover)}
        pdf = folder / "t2.pdf"
        build_premium_property_report_v10(pdf, payload)
        # "\f" separa as páginas: a junção por página não pode grudar o fim de uma no começo da outra.
        return "\f".join((page.extract_text() or "") for page in PdfReader(str(pdf)).pages)


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

    # T2 (correção): a atribuição de licença é obrigação legal. Ela tem de sair INTEIRA, com endereço —
    # e não terminando em "(." como terminava em todo relatório emitido, nos dois CARs.
    creditos = flat.count("creativecommons.org/licenses")
    if creditos != 2:
        fail(f"PDF: a atribuição de licença saiu {creditos} vez(es) com endereço completo; deveria ser 2")
    if "mesma licença (." in flat or "mesma licença ." in flat:
        fail("PDF: a atribuição de licença saiu cortada ('mesma licença (.')")
    ok("PDF: a atribuição das licenças Creative Commons sai inteira, com o endereço")

    # Sem bloco de fontes, a linha de escopo tem de aparecer do mesmo jeito.
    sem_fontes = " ".join(_render(_payload_sem_bloco_de_fontes()).split())
    if "não inclui matrícula" not in sem_fontes:
        fail("PDF sem bloco de fontes: a matrícula saiu da matriz de vínculo e nenhuma linha de escopo ficou no lugar")
    else:
        ok("PDF sem bloco de fontes: a linha de escopo do cartório continua no relatório")

    # Controle positivo da leitura por linha: a coluna estreita quebra o rótulo em duas linhas.
    if "R2_ESTADO_TECNICO" not in lint(frases_do_pdf("Registro de imóveis\nNÃO\nCONSULTADA\noutra linha")):
        fail("controle positivo: 'NÃO\\nCONSULTADA' quebrado em duas linhas deveria reprovar")
    if "R4_NOME_INTERNO" not in lint(frases_do_pdf("Fonte\nBase dos\nDados / SICAR")):
        fail("controle positivo: 'Base dos\\nDados' quebrado em duas linhas deveria reprovar")
    ok("controle positivo: estado técnico quebrado entre duas linhas do PDF reprova")

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
    _carregar_regra_de_erro()
    if "--pdf" in sys.argv:
        return lint_pdf(sys.argv[sys.argv.index("--pdf") + 1])
    test_regras_veem_o_defeito_cru()
    test_extrator_separa_texto_de_codigo()
    test_rotulos_de_fonte_do_servidor(rotulos_de_fonte())
    test_reason_dos_modulos_de_conformidade(textos_de_reason())
    test_titulo_nao_afirma_ausencia()
    portal_html, novo_html, novo_js = boot_portal()
    test_titulos_do_cartao_vem_de_um_lugar_so(portal_html)
    test_portal_servido_esta_limpo(portal_html, novo_html, novo_js)
    test_mutacao_no_portal_reprova(portal_html)
    test_mutacao_sobre_frase_real(portal_html)
    test_pdf_do_cliente_esta_limpo()
    if FAILS:
        print(f"RX_T2_TEXTOS_CLIENTE_GATE=FAIL falhas={len(FAILS)}")
        return 1
    print("RX_T2_TEXTOS_CLIENTE_GATE=PASS")
    return 0


if __name__ == "__main__":
    reexec_with_portal_env()
    raise SystemExit(main())
