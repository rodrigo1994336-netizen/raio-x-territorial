"""Sistema visual do relatório-livro v10: fontes, grade, estilos, cores e selo.

Especificação: SPEC_RELATORIO_LIVRO seções 1.1 a 1.5, 1.10 e 9.5.

Regras que este módulo carrega em código:

- As 8 fontes estáticas da Adobe (OFL 1.1, Reserved Font Name "Source") moram em
  ``assets/fonts/`` e são registradas uma vez por processo. Faltou ou não abriu um
  arquivo: ``registrar_fontes()`` levanta ``BookAssetsMissing`` e o motor v10 não
  desenha. Queda silenciosa para Times/Helvetica é proibida (perde ``≥``, ``₂``, ``→``).
- ``Paragrafo`` é a única classe de texto do v10. Ela leva a marca
  ``__rx_ptbr_skip__``: o normalizador de ``report_ptbr_v50.install()`` não reescreve
  o texto dela, porque o formatador do livro já entrega pt-BR e o normalizador
  estraga o milhar ("1.500 ha" vira "1,50 ha").
- Não existe vermelho de alarme nem verde de aprovado. A cor do selo vem do estado
  e de ``atencao``; nunca de palavra-chave.
- Nenhum número é formatado aqui (portão AST do livro): só medidas de desenho.
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Frame, PageTemplate, Paragraph

from report_book_v10_contracts import BookAssetsMissing

# ---------------------------------------------------------------------------
# 1.2 Fontes
# ---------------------------------------------------------------------------
DIR_FONTES = Path(__file__).resolve().parent / "assets" / "fonts"

FONTES: dict[str, str] = {
    "Serif": "SourceSerif4SmText-Regular.ttf",
    "Serif-Sb": "SourceSerif4SmText-Semibold.ttf",
    "Serif-It": "SourceSerif4SmText-It.ttf",
    "Disp": "SourceSerif4Display-Regular.ttf",
    "Disp-Sb": "SourceSerif4Display-Semibold.ttf",
    "Disp-It": "SourceSerif4Display-It.ttf",
    "Sans": "SourceSans3-Regular.ttf",
    "Sans-Sb": "SourceSans3-Semibold.ttf",
}

# Família → (normal, negrito, itálico, negrito-itálico). Não há arquivo semibold-itálico
# nem Sans itálico no repositório: a combinação cai no desenho mais próximo da mesma família.
_FAMILIAS = {
    "Serif": ("Serif", "Serif-Sb", "Serif-It", "Serif-Sb"),
    "Disp": ("Disp", "Disp-Sb", "Disp-It", "Disp-Sb"),
    "Sans": ("Sans", "Sans-Sb", "Sans", "Sans-Sb"),
}

_LOCK = threading.Lock()
_FONTES_MS: float | None = None


def _mesmo_arquivo(nome: str, caminho: Path) -> bool | None:
    """None: nome livre. True: já registrado a partir deste arquivo. False: ocupado por outro."""
    if nome not in pdfmetrics.getRegisteredFontNames():
        return None
    atual = getattr(getattr(pdfmetrics.getFont(nome), "face", None), "filename", None)
    try:
        return atual is not None and Path(atual).resolve() == caminho.resolve()
    except OSError:
        return False


def registrar_fontes() -> float:
    """Registra as 8 fontes do livro uma vez por processo e devolve o custo em ms.

    Chamadas seguintes devolvem a medida da primeira sem registrar de novo. Levanta
    ``BookAssetsMissing`` se faltar arquivo, se um arquivo não abrir ou se o nome já
    estiver registrado com outra fonte (o livro sairia com a letra errada).
    """
    global _FONTES_MS
    if _FONTES_MS is not None:
        return _FONTES_MS
    with _LOCK:
        if _FONTES_MS is not None:
            return _FONTES_MS
        inicio = time.perf_counter()
        faltando = sorted(arq for arq in FONTES.values() if not (DIR_FONTES / arq).is_file())
        if faltando:
            raise BookAssetsMissing("fontes do livro ausentes: " + ", ".join(faltando))
        for nome, arq in FONTES.items():
            caminho = DIR_FONTES / arq
            situacao = _mesmo_arquivo(nome, caminho)
            if situacao is True:
                continue
            if situacao is False:
                raise BookAssetsMissing(f"nome de fonte {nome} já registrado com outro arquivo")
            try:
                pdfmetrics.registerFont(TTFont(nome, str(caminho)))
            except Exception as exc:  # arquivo truncado, convertido ou ilegível
                raise BookAssetsMissing(f"fonte do livro ilegível: {arq}") from exc
        for familia, (normal, negrito, italico, negrito_italico) in _FAMILIAS.items():
            addMapping(familia, 0, 0, normal)
            addMapping(familia, 1, 0, negrito)
            addMapping(familia, 0, 1, italico)
            addMapping(familia, 1, 1, negrito_italico)
        _FONTES_MS = (time.perf_counter() - inicio) * 1000
        print(f"RX_BOOK_FONTS_MS={int(_FONTES_MS)}", flush=True)
        return _FONTES_MS


def conferir_fontes() -> list[str]:
    """Confere os TTF contra ``assets/fonts/SHA256SUMS``. Devolve os problemas (vazio = ok)."""
    somas = DIR_FONTES / "SHA256SUMS"
    if not somas.is_file():
        return ["SHA256SUMS ausente"]
    esperado: dict[str, str] = {}
    for linha in somas.read_text(encoding="utf-8").splitlines():
        if linha.strip():
            soma, arq = linha.split(None, 1)
            esperado[arq.lstrip("*").strip()] = soma.lower()
    problemas = []
    if set(esperado) != set(FONTES.values()):
        problemas.append("SHA256SUMS não lista exatamente as 8 fontes do livro")
    for arq in sorted(FONTES.values()):
        caminho = DIR_FONTES / arq
        if not caminho.is_file():
            problemas.append(f"{arq}: ausente")
        elif hashlib.sha256(caminho.read_bytes()).hexdigest() != esperado.get(arq):
            problemas.append(f"{arq}: sha256 diferente")
    return problemas


# ---------------------------------------------------------------------------
# 1.1 Formato, margens e grade (valores em pt; o reportlab desenha em pt)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Grade:
    pagina_l: float = 210 * mm
    pagina_a: float = 297 * mm
    margem_sup: float = 25 * mm
    margem_inf: float = 24 * mm
    margem_esq: float = 22 * mm
    margem_dir: float = 18 * mm
    util_l: float = 170 * mm          # um único Frame, padding 0
    util_a: float = 248 * mm
    origem_x: float = 22 * mm
    origem_y: float = 24 * mm
    leitura: float = 116 * mm         # coluna de leitura (≈ 68 caracteres a 10 pt)
    calha: float = 6 * mm
    nota: float = 48 * mm             # coluna de nota à direita
    col2: float = 82 * mm             # 82 + 6 + 82
    col3: float = (170 - 12) / 3 * mm  # 52,67 + 6 + 52,67 + 6 + 52,67
    col4: float = 38 * mm             # 38 + 6 + 38 + 6 + 38 + 6 + 38
    u: float = 7.5                    # unidade vertical: todo espaço entre blocos é múltiplo dela
    cabecalho_base: float = 14 * mm   # do topo da folha
    cabecalho_fio: float = 17 * mm    # do topo da folha
    folio_base: float = 13 * mm       # da base da folha
    fio_fino: float = 0.35

    def us(self, n: float) -> float:
        """n unidades verticais em pt (1u = 7,5 pt = 2,646 mm)."""
        return n * self.u


GRADE = Grade()


# ---------------------------------------------------------------------------
# 1.4 Cores (sRGB)
# ---------------------------------------------------------------------------
_HEX = {
    "PAPER": "#FFFFFF",
    "INK": "#1E2320",
    "INK2": "#4A504B",
    "MUTED": "#767B74",
    "RULE": "#D8D2C4",
    "CREAM": "#F5F1E7",
    "CREAM_LINE": "#F6F1E4",
    "FOREST": "#1F4A36",
    "TERRA": "#9A5530",
    "TERRA_LIGHT": "#EAD9CB",
    "PENDING": "#8C9089",
    "PENDING_TEXT": "#6F736D",
    "WATER": "#4F7A8C",
    "WATER_LIGHT": "#E6EEF1",
    "PRODES_ATE_2008": "#D8B48F",
    "PRODES_ATE_2019": "#B5764A",
    "PRODES_DEPOIS_2019": "#7A3A1C",
    "SOLO_1": "#E9E1CF",
    "SOLO_2": "#D9CBB0",
    "SOLO_3": "#C4B08C",
    "USO_PASTO": "#D9C47E",
    "USO_LAVOURA": "#B8894F",
    "USO_AGUA": "#5E8FA3",
    "USO_NATIVA": "#6B8A5A",
    "USO_OUTROS": "#BFB8A8",
    "TEX_ARGILA": "#9C6B4E",
    "TEX_SILTE": "#C9A77F",
    "TEX_AREIA": "#E6D3AE",
}
HEX: dict[str, str] = dict(_HEX)
COR: dict[str, Color] = {nome: HexColor(valor) for nome, valor in _HEX.items()}


def cor_verdor(v: float) -> Color:
    """Rampa do verdor, a mesma fórmula de ``sentinel_cog._ndvi_image``."""
    x = min(1.0, max(0.0, (v + 0.15) / 0.95))
    return Color((180 * (1 - x) + 45 * x) / 255, (95 * (1 - x) + 175 * x) / 255, (55 * (1 - x) + 75 * x) / 255)


# ---------------------------------------------------------------------------
# 1.3 Estilos de texto
# ---------------------------------------------------------------------------
TAMANHO_MINIMO = 7.5  # nada do cliente abaixo de 7,5 pt

# token, fonte, pt, entrelinha, cor, extras
_TABELA_ESTILOS = (
    # 1.3 — tabela da especificação
    ("kicker", "Sans-Sb", 7.8, 10, "FOREST", {"textTransform": "uppercase", "charSpace": 1.4}),
    ("codigo_titulo", "Sans-Sb", 20, 23, "INK", {}),
    ("nome_titulo", "Disp-Sb", 26, 30, "INK", {}),
    ("codigo_sub", "Sans", 11, 14, "INK2", {}),
    ("h1", "Disp-Sb", 24, 28, "INK", {}),
    ("dek", "Disp-It", 12.5, 17, "INK2", {}),
    ("resposta", "Serif", 12.5, 18, "INK", {}),
    ("h2", "Sans-Sb", 12.5, 16, "INK", {}),
    ("corpo", "Serif", 10, 15, "INK", {}),
    ("corpo_sb", "Serif-Sb", 10, 15, "INK", {}),
    ("caixa", "Serif", 10.5, 15.5, "INK", {}),
    ("painel_pergunta", "Sans-Sb", 10.5, 13, "INK", {}),
    ("painel_resposta", "Serif", 9.8, 13.5, "INK", {}),
    ("fato_valor", "Disp", 18, 20, "INK", {}),
    ("destaque_valor", "Disp", 30, 32, "INK", {}),
    ("legenda", "Sans", 8.6, 12, "INK2", {}),
    ("tab_cab", "Sans-Sb", 7.8, 10, "MUTED", {"textTransform": "uppercase", "charSpace": 0.8}),
    ("tab_cel", "Sans", 9.6, 13, "INK", {}),
    ("tab_num", "Sans", 9.6, 13, "INK", {"alignment": TA_RIGHT}),
    ("selo_rotulo", "Sans-Sb", 7.6, 9, "INK", {"textTransform": "uppercase", "charSpace": 1.0}),
    ("corrente", "Sans", 8, 10, "INK2", {}),
    ("folio", "Sans-Sb", 8.5, 10, "INK", {}),
    ("rodape_id", "Sans", 7.5, 9, "MUTED", {}),
    ("glos_termo", "Sans-Sb", 9.6, 13, "FOREST", {}),
    ("glos_def", "Serif", 9.6, 13.5, "INK", {}),
    ("apend_cel", "Sans", 8.8, 12, "INK", {}),
    # 1.6 e 1.7 — medidas de texto citadas nos componentes e figuras
    ("destaque_unidade", "Sans", 11, 32, "INK", {}),
    ("limite_texto", "Serif", 9.8, 14, "INK", {}),
    ("quadro_frase", "Serif", 9.6, 13, "INK2", {}),
    ("conf_o_que", "Sans-Sb", 10.5, 13, "INK", {}),
    ("conf_por_que", "Serif", 9.6, 13.5, "INK", {}),
    ("conf_onde", "Sans", 8.6, 12, "INK2", {}),
    ("regra", "Serif", 9.6, 13.5, "INK", {}),
    ("edicao", "Sans", 8.6, 12, "INK2", {}),
    ("ref_pagina", "Sans-Sb", 8.6, 12, "FOREST", {}),
    ("doc_nome", "Disp-Sb", 15, 18, "INK", {}),
    ("icone_nome", "Sans-Sb", 10, 12.5, "INK", {}),
    ("rampa_valor", "Disp", 22, 24, "INK", {}),
    ("eixo", "Sans", 7.8, 10, "INK2", {}),
)


def _estilo(token: str, fonte: str, pt: float, entrelinha: float, cor: str, extras: dict) -> ParagraphStyle:
    base = dict(
        fontName=fonte, fontSize=pt, leading=entrelinha, textColor=COR[cor], alignment=TA_LEFT,
        allowWidows=0, allowOrphans=0, spaceBefore=0, spaceAfter=0, charSpace=0.0,
    )
    base.update(extras)
    return ParagraphStyle(token, **base)


ESTILO: dict[str, ParagraphStyle] = {linha[0]: _estilo(*linha) for linha in _TABELA_ESTILOS}


# ---------------------------------------------------------------------------
# Texto: Paragrafo e rótulo espacejado
# ---------------------------------------------------------------------------
class Paragrafo(Paragraph):
    """Única classe de texto do v10. Não passa pelo normalizador pt-BR do v9.

    O texto chega pronto: números pelo ``report_book_fmt`` e pedaços vindos de dado já
    escapados por ``reading_model_v51.esc``. ``estilo`` aceita o token de ``ESTILO``.
    """

    __rx_ptbr_skip__ = True

    def __init__(self, text, style=None, *args, **kwargs):
        if text is not None and not isinstance(text, str):
            raise TypeError("Paragrafo recebe texto já formatado (str), não " + type(text).__name__)
        if isinstance(style, str):
            style = ESTILO[style]
        super().__init__(text, style, *args, **kwargs)


def _texto_rotulo(texto: str, estilo: ParagraphStyle) -> str:
    return texto.upper() if getattr(estilo, "textTransform", None) == "uppercase" else texto


def largura_espacejada(texto: str, estilo: ParagraphStyle | str) -> float:
    """Largura em pt de um rótulo de uma linha desenhado com ``texto_espacejado``."""
    st = ESTILO[estilo] if isinstance(estilo, str) else estilo
    t = _texto_rotulo(texto, st)
    cs = getattr(st, "charSpace", 0.0)
    return pdfmetrics.stringWidth(t, st.fontName, st.fontSize) + cs * max(0, len(t) - 1)


def texto_espacejado(c, x: float, y: float, texto: str, estilo: ParagraphStyle | str,
                     cor: Color | None = None, alinhar: str = "esq") -> float:
    """Desenha um rótulo de uma linha (kicker, cabeçalho de tabela, selo) com ``charSpace``.

    ``y`` é a linha de base. Devolve a largura desenhada. ``Paragraph`` não faz espacejamento.
    """
    st = ESTILO[estilo] if isinstance(estilo, str) else estilo
    t = _texto_rotulo(texto, st)
    largura = largura_espacejada(t, st)
    if alinhar == "dir":
        x -= largura
    elif alinhar == "centro":
        x -= largura / 2
    c.saveState()
    c.setFillColor(cor if cor is not None else st.textColor)
    c.setFont(st.fontName, st.fontSize)
    c.drawString(x, y, t, charSpace=getattr(st, "charSpace", 0.0))
    c.restoreState()
    return largura


# ---------------------------------------------------------------------------
# 1.5 Selo de situação (desenhado no canvas; nunca por glifo)
# ---------------------------------------------------------------------------
SELO_DIAMETRO = 3.4 * mm
SELO_TRACO = 0.9
SELO_ESPACO = 1.8 * mm
SELO_ALTURA = 4.2 * mm
SELO_TRACEJADO = (1.2 * mm, 1.2 * mm)

# (estado, atencao) → (forma, cor do ícone, rótulo, cor do rótulo)
SELO: dict[tuple[str, bool], tuple[str, str, str, str]] = {
    ("encontrado", True): ("cheio", "TERRA", "Encontrado", "TERRA"),
    ("encontrado", False): ("cheio", "INK", "Encontrado", "INK"),
    ("nao_encontrado", False): ("vazado", "INK2", "Não encontrado", "INK2"),
    ("pendente", False): ("tracejado", "PENDING", "Consulta pendente", "PENDING_TEXT"),
}
ESTADOS_SEM_SELO = ("fora_desta_edicao", "nao_se_aplica")


def _selo_chave(estado, atencao) -> tuple[str, bool]:
    if not isinstance(estado, str):
        raise TypeError("selo recebe o estado da camada de leitura, nunca número ou contagem")
    if estado in ESTADOS_SEM_SELO:
        raise ValueError(f"o estado {estado} não tem selo desenhado")
    chave = (estado, bool(atencao) if estado == "encontrado" else False)
    if chave not in SELO:
        raise ValueError(f"estado desconhecido: {estado}")
    return chave


def largura_selo(estado: str, atencao: bool = False, diametro: float = SELO_DIAMETRO) -> float:
    _, _, rotulo, _ = SELO[_selo_chave(estado, atencao)]
    return diametro + SELO_ESPACO + largura_espacejada(rotulo, "selo_rotulo")


def desenhar_selo(c, x: float, y: float, estado: str, atencao: bool = False,
                  diametro: float = SELO_DIAMETRO, com_rotulo: bool = True) -> float:
    """Desenha o selo com a base da caixa de 4,2 mm em ``y``. Devolve a largura usada.

    ● cheio = encontrado (``TERRA`` quando pede conferência) · ○ vazado, sem ✓ = não
    encontrado · ◌ tracejado = consulta pendente. ``fora_desta_edicao`` e ``nao_se_aplica``
    não têm selo e levantam ``ValueError``; número levanta ``TypeError``.
    """
    forma, cor_icone, rotulo, cor_rotulo = SELO[_selo_chave(estado, atencao)]
    altura = max(SELO_ALTURA, diametro)
    r = diametro / 2
    cx, cy = x + r, y + altura / 2
    c.saveState()
    c.setLineWidth(SELO_TRACO)
    if forma == "cheio":
        c.setFillColor(COR[cor_icone])
        c.circle(cx, cy, r, stroke=0, fill=1)
    else:
        c.setStrokeColor(COR[cor_icone])
        if forma == "tracejado":
            c.setDash(*SELO_TRACEJADO)
        c.circle(cx, cy, r - SELO_TRACO / 2, stroke=1, fill=0)
    c.restoreState()
    largura = diametro
    if com_rotulo:
        st = ESTILO["selo_rotulo"]
        altura_maiuscula = pdfmetrics.getFont(st.fontName).face.capHeight / 1000 * st.fontSize
        largura += SELO_ESPACO + texto_espacejado(
            c, x + diametro + SELO_ESPACO, cy - altura_maiuscula / 2, rotulo, st, COR[cor_rotulo])
    return largura


# ---------------------------------------------------------------------------
# 1.10 Cabeçalho corrente, rodapé e modelo de página
# ---------------------------------------------------------------------------
MARCA = "Raio-X Territorial"


def desenhar_moldura(c, pagina: int, total: int, titulo_corrente: str | None = None,
                     rodape_id: str | None = None) -> None:
    """Cabeçalho corrente e rodapé de uma folha (1.10). O município nunca entra aqui.

    P. 1: só o fólio. Da p. 2 em diante: marca e título curto do capítulo no alto, fio
    fino, identificação do CAR e data à esquerda no pé, fólio "N de M" à direita.
    """
    g = GRADE
    direita = g.origem_x + g.util_l
    y_folio = g.folio_base
    c.saveState()
    if pagina > 1:
        y_cab = g.pagina_a - g.cabecalho_base
        texto_espacejado(c, g.origem_x, y_cab, MARCA, "kicker", COR["FOREST"])
        if titulo_corrente:
            st = ESTILO["corrente"]
            c.setFillColor(st.textColor)
            c.setFont(st.fontName, st.fontSize)
            c.drawRightString(direita, y_cab, titulo_corrente)
        y_fio = g.pagina_a - g.cabecalho_fio
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(g.fio_fino)
        c.line(g.origem_x, y_fio, direita, y_fio)
        if rodape_id:
            st = ESTILO["rodape_id"]
            c.setFillColor(st.textColor)
            c.setFont(st.fontName, st.fontSize)
            c.drawString(g.origem_x, y_folio, rodape_id)
    st = ESTILO["folio"]
    c.setFillColor(st.textColor)
    c.setFont(st.fontName, st.fontSize)
    c.drawRightString(direita, y_folio, f"{pagina} de {total}")
    c.restoreState()


def _titulo_da_folha(folha) -> str | None:
    for alvo in (folha, getattr(folha, "spec", None)):
        titulo = getattr(alvo, "titulo_corrente", None)
        if titulo:
            return titulo
    return None


def modelo_pagina(doc, plano, *, rodape_id: str | None = None, titulos=None) -> PageTemplate:
    """Instala no ``doc`` o modelo único de página: Frame 170 × 248 mm, padding 0, e moldura.

    ``plano.total`` dá o M de "N de M". Títulos correntes vêm de ``titulos`` (lista por
    página, a partir da 1) ou, na falta, de ``plano.folhas[i].titulo_corrente`` (ou
    ``.spec.titulo_corrente``). ``rodape_id`` vem do argumento ou de ``plano.rodape_id``.
    """
    registrar_fontes()
    g = GRADE
    total = int(plano.total)
    if titulos is None:
        titulos = [_titulo_da_folha(f) for f in (getattr(plano, "folhas", None) or [])]
    titulos = list(titulos)
    rodape = rodape_id if rodape_id is not None else getattr(plano, "rodape_id", None)

    def _na_pagina(c, _doc):
        pagina = c.getPageNumber()
        titulo = titulos[pagina - 1] if 0 < pagina <= len(titulos) else None
        desenhar_moldura(c, pagina, total, titulo, rodape)

    frame = Frame(g.origem_x, g.origem_y, g.util_l, g.util_a, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0, id="util", showBoundary=0)
    modelo = PageTemplate(id="livro", frames=[frame], onPageEnd=_na_pagina, pagesize=(g.pagina_l, g.pagina_a))
    # Sem isto o reportlab abre cada página com Helvetica no preâmbulo, e o PDF passa a
    # listar uma fonte que o livro não usa (o portão de fontes embutidas confere a lista).
    doc.initialFontName = "Sans"
    doc.addPageTemplates([modelo])
    return modelo


# Registro no import (1× por processo). Sem os arquivos o import não quebra: o motor
# chama ``registrar_fontes()`` antes de desenhar, recebe ``BookAssetsMissing`` e decide.
try:
    registrar_fontes()
except BookAssetsMissing as _erro:
    print("RX_BOOK_FONTS=ausentes", flush=True)
