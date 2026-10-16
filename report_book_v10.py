"""Motor do relatório-livro v10: o livro inteiro, do payload real ao PDF.

SPEC_RELATORIO_LIVRO seções 1 (sistema visual), 2 (o livro página a página), 3 (glossário) e 4
(apêndice). Cresce a partir da amostra de 3 páginas que o dono aprovou (p. 1, 3 e 7): as três
continuam desenhadas pelo mesmo código, e as outras dez seguem a mesma grade, a mesma letra e as
mesmas regras.

**De onde vem cada coisa**

- O que o livro AFIRMA sai dos Fatos (``report_book_v10_fatos``): envelope `_v51` quando a faixa
  dona já grava, senão a regra de transição sobre o payload que o relatório de hoje produz.
- O que o livro DESENHA sai de ``payload["book_v10"]`` (``report_book_v10_dados``): geometria em
  metros, imagem limpa e recorte das manchas.
- O sistema visual (grade, letra, cor, selo) sai de ``report_book_v10_style``.

**As regras do dono, em código**

1. Página que depende de fato ``pendente`` não inventa: entra a variante ◌ da seção 2.0.5, sem
   caixa colorida, sem número e sem figura.
2. Campo vazio não aparece: nada de "—", "N/D" ou "não informado".
3. Zero só aparece com o alcance escrito ("Nenhum embargo com mapa na base oficial").
4. Nome de outro cadastro é referência, num bloco próprio, nunca o nome do CAR.
5. Nada escrito sobre o mapa: a imagem não leva rótulo, e o nome do imóvel não é desenhado nela.

O livro **não lê** as chaves que a seção 2.0.2 proíbe (``compliance``, ``conclusion``, ``narrative``,
``quick_read``, ``monitoring``, ``priorities``…): elas são a leitura do gerador antigo, e duas
leituras do mesmo dado foi o defeito que esta especificação veio resolver.

Uso no serviço: ``gerar_livro(caminho_pdf, payload, result)`` devolve o sha256 do PDF, a mesma
assinatura que o gerador atual entrega.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import report_ptbr_v50  # noqa: E402

report_ptbr_v50.install()  # como em produção: o texto do livro não pode passar pelo normalizador

import report_book_v10_style as st  # noqa: E402
from report_book_v10_contracts import FATO_CHAVES, LayoutError  # noqa: E402
from report_book_v10_fatos import atencao as fato_atencao  # noqa: E402
from report_book_v10_fatos import ler_fatos  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.colors import HexColor  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfgen.canvas import Canvas  # noqa: E402

G = st.GRADE
COR = st.COR
X0 = G.origem_x
TOPO = G.origem_y + G.util_a
LARG = G.util_l
U = G.u

# Endereço do relatório (D4). Variável só para o ambiente de teste; o padrão é o site no ar.
SITE_PADRAO = os.getenv("RX_LIVRO_SITE", "raioxterritorial.com.br")

# Piso de leitura da revisão da amostra (13/09): nenhuma anotação, rótulo, selo ou rodapé abaixo de
# 8,5 pt, e o cabeçalho de tabela em INK2 (o MUTED some no celular).
PISO_PT = 8.5
ANOT = PISO_PT

# Rampa de inclinação, do plano (claro) ao forte ondulado (escuro): a mesma na barra, nos ícones e
# no mapa de células.
TONS_RELEVO = {"plano": "#EFE7D6", "suave ondulado": "#D8C3A1", "ondulado": "#B48A5C",
               "forte ondulado": "#6E4A2C", "montanhoso": "#4A3020", "escarpado": "#2E1E14"}

# Limite de soma da lista oficial do MMA para o MCR 2-9-17 (plano/F4_checagem_mcr.md)
LIMITE_LISTA_MMA_HA = {"Cerrado": Decimal("5"), "Pantanal": Decimal("5"), "Amazônia": Decimal("6.25"),
                       "Amazônia Legal": Decimal("6.25")}

_UF_MALHA = ROOT / "data" / "uf_br_ibge_v3.json.gz"
_CONTORNO_CACHE: dict[str, list[tuple[float, float]]] = {}


def contorno_uf(uf: str) -> list[tuple[float, float]] | None:
    """Contorno simplificado do estado para o encarte de localização, da malha do IBGE no repositório.

    Devolve None quando o estado não está na malha: o encarte some, e nada mais muda na página.
    """
    uf = (uf or "").upper()
    if uf in _CONTORNO_CACHE:
        return _CONTORNO_CACHE[uf] or None
    if not _UF_MALHA.is_file():
        _CONTORNO_CACHE[uf] = []
        return None
    try:
        bruto = json.loads(gzip.decompress(_UF_MALHA.read_bytes()).decode("utf-8"))
        g = (bruto.get("ufs") or {}).get(uf)
        if not g:
            _CONTORNO_CACHE[uf] = []
            return None
        aneis = []
        if g["type"] == "Polygon":
            aneis = [g["coordinates"][0]]
        else:
            aneis = [poligono[0] for poligono in g["coordinates"]]
        maior = max(aneis, key=len)
        # 1 ponto a cada N, para o encarte de 19 mm não carregar milhares de vértices
        passo = max(1, len(maior) // 220)
        contorno = [(float(x), float(y)) for x, y in maior[::passo]]
    except Exception:
        contorno = []
    _CONTORNO_CACHE[uf] = contorno
    return contorno or None


class Leitura:
    """Acesso único do livro ao dado: Fatos pela chave, ``book_v10`` e caminhos de imagem.

    ``fatos`` vem pronto de ``report_book_v10_fatos.ler_fatos`` (envelope `_v51` ou transição). A
    página nunca procura um campo do payload por conta própria.
    """

    def __init__(self, payload: dict, pasta: Path, fatos: dict | None = None,
                 result: dict | None = None):
        self.p = payload
        self.pasta = Path(pasta)
        self.caminhos = dict(FATO_CHAVES)
        fixture = payload.get("_fixture")
        if isinstance(fixture, dict) and isinstance(fixture.get("fatos"), dict):
            self.caminhos.update(fixture["fatos"])   # a F1 congelada guarda os caminhos dela
        self.fatos = fatos if fatos is not None else ler_fatos(payload, result)
        self.site = SITE_PADRAO

    def fato(self, chave: str) -> dict:
        e = self.fatos.get(chave)
        if e is None:
            return {"estado": "fora_desta_edicao", "valor": None, "limites": [], "fonte": {}}
        return e

    def estado(self, chave: str) -> str:
        return str(self.fato(chave).get("estado") or "fora_desta_edicao")

    def atencao(self, chave: str) -> bool:
        return fato_atencao(chave, self.fatos)

    def selo(self, chave: str) -> tuple[str, bool] | None:
        """(estado, atenção) do selo desenhado, ou None quando o estado não tem selo."""
        e = self.estado(chave)
        if e in st.ESTADOS_SEM_SELO:
            return None
        return (e, self.atencao(chave))

    def valor(self, chave: str):
        """Valor do fato respondido. Levanta se a página pedir valor de fato não respondido."""
        f = self.fato(chave)
        if f["estado"] not in ("encontrado", "nao_encontrado"):
            raise LayoutError(f"o livro pediu o valor de {chave} com estado {f['estado']}")
        return f["valor"]

    def tem(self, chave: str) -> bool:
        return self.estado(chave) == "encontrado"

    @property
    def book(self) -> dict:
        b = self.p.get("book_v10")
        return b if isinstance(b, dict) else {}

    @property
    def geometria(self) -> dict | None:
        g = self.book.get("geometry")
        return g if isinstance(g, dict) and g.get("car_m") else None

    def imagem_base(self) -> dict | None:
        b = self.book.get("base_image")
        return b if isinstance(b, dict) and b.get("ok") and b.get("path") else None

    def imagem_media(self) -> dict | None:
        b = self.book.get("sentinel_image")
        return b if isinstance(b, dict) and b.get("ok") and b.get("path") else None

    def imagem(self, rel: str) -> Path:
        caminho = Path(rel)
        return caminho if caminho.is_absolute() else self.pasta / rel


def aplicar_piso_de_leitura() -> None:
    for token in ("kicker", "tab_cab", "selo_rotulo", "rodape_id", "corrente", "eixo"):
        s = st.ESTILO[token]
        extras = {"fontSize": max(s.fontSize, PISO_PT), "leading": max(s.leading, PISO_PT + 2)}
        if token == "tab_cab":
            extras["textColor"] = COR["INK2"]
        st.ESTILO[token] = ParagraphStyle(token, parent=s, **extras)
    # "Não encontrado" com círculo vazado, para notícia boa, se lê como "não achamos o dado" (revisão, item 11).
    forma, cor, _, cor_rot = st.SELO[("nao_encontrado", False)]
    st.SELO[("nao_encontrado", False)] = (forma, cor, "Nenhum registro", cor_rot)


# Formatador provisório (regras da seção 1.9; a P1 entrega o definitivo)
# ===========================================================================
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
         "outubro", "novembro", "dezembro")
MESES_CURTOS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
BRASILIA = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019


def _q(x, casas: int) -> Decimal:
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)


def _ptbr(d: Decimal) -> str:
    texto = format(d, "f")
    sinal = "-" if texto.startswith("-") else ""
    inteiro, _, fracao = texto.lstrip("-").partition(".")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return sinal + ".".join(grupos) + ("," + fracao if fracao else "")


def f_ha(x) -> str:
    if Decimal(str(x)) < Decimal("0.005"):
        return "menos de 0,01 ha"
    return _ptbr(_q(x, 2)) + " ha"


def f_pct(x) -> str:
    v = Decimal(str(x))
    if v >= 10:
        return _ptbr(_q(v, 0)) + "%"
    if v >= Decimal("0.1"):
        t = _ptbr(_q(v, 1))
        return (t[:-2] if t.endswith(",0") else t) + "%"
    return "menos de 0,1%"


def f_pct_composicao(valores) -> list[int]:
    brutos = [Decimal(str(v)) for v in valores]
    pisos = [int(b.to_integral_value(rounding=ROUND_FLOOR)) for b in brutos]
    falta = 100 - sum(pisos)
    ordem = sorted(range(len(brutos)), key=lambda i: (brutos[i] - pisos[i], brutos[i]), reverse=True)
    for i in ordem[:max(0, falta)]:
        pisos[i] += 1
    return pisos


def f_inteiro(x) -> str:
    return _ptbr(_q(x, 0))


def f_metros(x) -> str:
    return f_inteiro(x) + " m"


def f_data(iso: str) -> str:
    a, m, d = iso[:10].split("-")
    return f"{d}/{m}/{a}"


def f_data_extenso(iso: str) -> str:
    a, m, d = iso[:10].split("-")
    return f"{int(d)} de {MESES[int(m) - 1]} de {a}"


def f_mes_ano_curto(iso: str) -> str:
    a, m, _ = iso[:10].split("-")
    return f"{MESES_CURTOS[int(m) - 1]}/{a}"


def f_mes_ano(iso: str) -> str:
    a, m, _ = iso[:10].split("-")
    return f"{MESES[int(m) - 1]} de {a}"


def _instante(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BRASILIA)


def f_hora_brasilia(iso: str) -> str:
    t = _instante(iso)
    return str(t.hour).zfill(2) + "h" + str(t.minute).zfill(2)


def f_data_brasilia(iso: str) -> str:
    return _instante(iso).strftime("%d/%m/%Y")


def f_ha_extenso(x) -> str:
    v = Decimal(str(x))
    inteiro = int(v.to_integral_value(rounding=ROUND_FLOOR))
    fracao = v - inteiro
    if 1 <= v < 100 and fracao >= Decimal("0.5"):
        return f"pouco menos de {inteiro + 1} hectares"
    if 1 <= v < 100 and Decimal("0.05") < fracao < Decimal("0.5"):
        return f"pouco mais de {inteiro} hectares"
    return f"cerca de {f_inteiro(v)} hectares"


def f_comparar_area(ha) -> str | None:
    v = Decimal(str(ha))
    if v >= 5:
        return None
    if v < Decimal("0.1"):
        n, um, varios = v * 10000 / Decimal("260.76"), "uma quadra de tênis", "quadras de tênis"
    else:
        n, um, varios = v / Decimal("0.714"), "um campo de futebol", "campos de futebol"
    if n < Decimal("0.75"):
        return "menor que " + um
    if n < Decimal("1.25"):
        return "mais ou menos o tamanho de " + um
    return f"cerca de {f_inteiro(n)} {varios}"


def f_faixa_parte(share) -> str:
    v = Decimal(str(share))
    for piso, texto in ((90, "quase toda a área"), (60, "a maior parte"), (50, "pouco mais da metade"),
                        (40, "pouco menos da metade"), (10, "uma parte")):
        if v >= piso:
            return texto
    return "uma pequena parte"


# ===========================================================================
# Texto: parágrafos medidos, registro para o autoteste de redação
# ===========================================================================
REGISTRO: list[tuple[int, str]] = []
_PAGINA_ATUAL = [0]
_ESTILOS_DERIVADOS: dict[tuple, ParagraphStyle] = {}


def estilo(token: str, **extras) -> ParagraphStyle:
    if not extras:
        return st.ESTILO[token]
    chave = (token, tuple(sorted((k, str(v)) for k, v in extras.items())))
    if chave not in _ESTILOS_DERIVADOS:
        _ESTILOS_DERIVADOS[chave] = ParagraphStyle(f"{token}_{len(_ESTILOS_DERIVADOS)}", parent=st.ESTILO[token],
                                                   **extras)
    return _ESTILOS_DERIVADOS[chave]


@dataclass
class Texto:
    par: st.Paragrafo
    largura: float
    altura: float

    def desenhar(self, c, x: float, y_topo: float) -> float:
        self.par.drawOn(c, x, y_topo - self.altura)
        return self.altura

    @property
    def linhas(self) -> int:
        return len(getattr(self.par, "blPara").lines)


def texto(t: str, token: str, largura: float, registrar: bool = True, **extras) -> Texto:
    par = st.Paragrafo(t, estilo(token, **extras))
    _, altura = par.wrap(largura, 10000)
    if registrar:
        REGISTRO.append((_PAGINA_ATUAL[0], t))
    return Texto(par, largura, altura)


def rotulo(c, x, y_base, t: str, token: str = "kicker", cor=None, alinhar="esq") -> float:
    REGISTRO.append((_PAGINA_ATUAL[0], t))
    return st.texto_espacejado(c, x, y_base, t, token, cor, alinhar)


def linha_simples(c, x, y_base, t: str, fonte: str, pt: float, cor, alinhar="esq") -> float:
    REGISTRO.append((_PAGINA_ATUAL[0], t))
    c.saveState()
    c.setFillColor(cor)
    c.setFont(fonte, pt)
    largura = pdfmetrics.stringWidth(t, fonte, pt)
    if alinhar == "dir":
        c.drawRightString(x, y_base, t)
    elif alinhar == "centro":
        c.drawCentredString(x, y_base, t)
    else:
        c.drawString(x, y_base, t)
    c.restoreState()
    return largura


def altura_maiuscula(fonte: str, pt: float) -> float:
    return pdfmetrics.getFont(fonte).face.capHeight / 1000 * pt


# ===========================================================================
# Ícones de linha (caixa de 24 × 24 unidades, pontas redondas; seção 1.6)
# ===========================================================================
def icone(c, nome: str, x: float, y: float, tamanho: float, cor=None, traco: float = 1.1) -> None:
    k = tamanho / 24
    c.saveState()
    c.translate(x, y)
    c.scale(k, k)
    c.setStrokeColor(cor if cor is not None else COR["INK"])
    c.setLineWidth(traco / k)
    c.setLineCap(1)
    c.setLineJoin(1)
    p = c.beginPath()
    if nome == "folha":
        p.moveTo(5, 5)
        p.curveTo(4, 15, 10, 20, 20, 20)
        p.curveTo(20.5, 10, 15, 4.5, 5, 5)
        p.moveTo(5, 5)
        p.lineTo(14.5, 14.5)
        p.moveTo(5, 5)
        p.lineTo(3, 3)
    elif nome == "broto":
        p.moveTo(12, 3.5)
        p.lineTo(12, 13)
        p.moveTo(12, 10.5)
        p.curveTo(9.5, 10.5, 5, 12, 4.5, 17.5)
        p.curveTo(9, 17.8, 11.8, 15, 12, 10.5)
        p.moveTo(12, 13)
        p.curveTo(12.5, 17.5, 16, 20.5, 20.5, 20.5)
        p.curveTo(20.8, 16, 17, 12.8, 12, 13)
        p.moveTo(6, 3.5)
        p.lineTo(18, 3.5)
    elif nome == "carimbo":
        p.circle(12, 18.5, 3)
        p.moveTo(10.3, 15.8)
        p.lineTo(10.3, 11.5)
        p.moveTo(13.7, 15.8)
        p.lineTo(13.7, 11.5)
        p.roundRect(4, 7.5, 16, 4, 1)
        p.moveTo(4, 4)
        p.lineTo(20, 4)
    elif nome == "escudo":
        p.moveTo(12, 21)
        p.lineTo(19, 18.2)
        p.lineTo(19, 11.5)
        p.curveTo(19, 6.8, 15.8, 4.2, 12, 3)
        p.curveTo(8.2, 4.2, 5, 6.8, 5, 11.5)
        p.lineTo(5, 18.2)
        p.close()
        p.moveTo(8.8, 12)
        p.lineTo(11.2, 9.6)
        p.lineTo(15.4, 14)
    elif nome == "documento":
        p.moveTo(6, 3)
        p.lineTo(6, 21)
        p.lineTo(14, 21)
        p.lineTo(18, 17)
        p.lineTo(18, 3)
        p.close()
        p.moveTo(14, 21)
        p.lineTo(14, 17)
        p.lineTo(18, 17)
        for yy, xx in ((13, 15), (10, 15), (7, 12.5)):
            p.moveTo(9, yy)
            p.lineTo(xx, yy)
    elif nome == "gota":
        p.moveTo(12, 21)
        p.curveTo(12, 21, 5, 13.5, 5, 9.6)
        p.curveTo(5, 5.6, 8.2, 3, 12, 3)
        p.curveTo(15.8, 3, 19, 5.6, 19, 9.6)
        p.curveTo(19, 13.5, 12, 21, 12, 21)
    elif nome == "trator":
        p.circle(7.5, 7.5, 4.5)
        p.circle(18.5, 6, 3)
        p.moveTo(4.5, 12.5)
        p.lineTo(4.5, 20)
        p.lineTo(11.5, 20)
        p.lineTo(12.5, 12.5)
        p.moveTo(12, 9)
        p.lineTo(21, 9)
        p.lineTo(21, 12.5)
        p.lineTo(4.5, 12.5)
        p.moveTo(16.5, 12.5)
        p.lineTo(16.5, 16)
    elif nome == "camadas":
        for base in (4, 9, 14):
            p.moveTo(3, base + 3)
            p.lineTo(12, base + 6)
            p.lineTo(21, base + 3)
            p.lineTo(12, base)
            p.close()
    elif nome == "lupa":
        p.circle(10, 14, 6.5)
        p.moveTo(14.8, 9.2)
        p.lineTo(20.5, 3.5)
    elif nome == "pino":
        p.moveTo(12, 3)
        p.curveTo(12, 3, 5, 10.5, 5, 14.5)
        p.curveTo(5, 18.5, 8.2, 21, 12, 21)
        p.curveTo(15.8, 21, 19, 18.5, 19, 14.5)
        p.curveTo(19, 10.5, 12, 3, 12, 3)
        p.circle(12, 14.5, 2.6)
    else:
        raise KeyError(nome)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()


def kicker(c, x, y_topo, t: str, cor, nome_icone: str | None = None) -> float:
    """Rótulo de capítulo ou seção com ícone de 5 mm. Devolve a altura usada (5 mm)."""
    base = y_topo - 5 * mm / 2 - altura_maiuscula("Sans-Sb", st.ESTILO["kicker"].fontSize) / 2
    xx = x
    if nome_icone:
        icone(c, nome_icone, x, y_topo - 5 * mm, 5 * mm, cor, 1.0)
        xx += 5 * mm + 1.8 * mm
    rotulo(c, xx, base, t, "kicker", cor)
    return 5 * mm


# ===========================================================================
# Pilha vertical com mola, no lugar do compositor da seção 1.8
# ===========================================================================
@dataclass
class Bloco:
    nome: str
    altura: float
    desenhar: Callable  # (c, x, y_topo, altura)
    mola: tuple[float, float] | None = None
    espaco: float = 2  # em unidades de 7,5 pt, antes do bloco
    ancorar: bool = False  # só o último bloco: desenhado colado ao pé da área útil (FolhaSpec.ancorar_rodape)
    prioridade: int = 0    # 0 obrigatório; 1..9 sai primeiro quando a folha não fecha (§1.8)


def _folga(blocos: list[Bloco]) -> float:
    """Quanto sobra (ou falta) na área útil com esta pilha, usando a altura mínima de cada bloco."""
    espacos = sum(G.us(b.espaco) for b in blocos[1:])
    fixos = sum(b.altura for b in blocos if b.mola is None)
    minimos = sum(b.mola[0] for b in blocos if b.mola)
    return G.util_a - espacos - fixos - minimos


def compor(c, blocos: list[Bloco], pagina: int, grade: bool = False) -> float:
    # A folha não fechou: sai o bloco opcional de maior prioridade, um por vez, antes de desistir.
    # É a regra da seção 1.8 — capítulo com muito achado perde o bloco acessório, nunca o essencial.
    saiu: list[str] = []
    while _folga(blocos) < -0.01:
        opcionais = [b for b in blocos if b.prioridade > 0]
        if not opcionais:
            break
        fora = max(opcionais, key=lambda b: b.prioridade)
        blocos.remove(fora)
        saiu.append(f"{fora.nome}({_ptbr(_q((fora.mola[0] if fora.mola else fora.altura) / mm, 1))}mm,"
                    f"faltavam {_ptbr(_q(-_folga(blocos + [fora]) / mm, 1))}mm)")
    if saiu:
        ficou = ",".join(f"{b.nome}={_ptbr(_q((b.mola[0] if b.mola else b.altura) / mm, 1))}" for b in blocos)
        print(f"RX_BOOK_BLOCO_FORA=p{pagina}:{','.join(saiu)}|ficou:{ficou}", flush=True)
    espacos = sum(G.us(b.espaco) for b in blocos[1:])
    fixos = sum(b.altura for b in blocos if b.mola is None)
    minimos = sum(b.mola[0] for b in blocos if b.mola)
    livre = G.util_a - fixos - espacos
    medidas = ", ".join(f"{b.nome}={_ptbr(_q((b.mola[0] if b.mola else b.altura) / mm, 1))}" for b in blocos)
    if livre < minimos - 0.01 and os.environ.get("RX_AMOSTRA_SOLTO") == "1":  # só para olhar enquanto ajusta
        print(f"AVISO p. {pagina}: faltam {_ptbr(_q((minimos - livre) / mm, 1))} mm ({medidas})")
        livre = minimos
    if livre < minimos - 0.01:
        raise LayoutError(f"p. {pagina}: faltam {_ptbr(_q((minimos - livre) / mm, 1))} mm ({medidas})")
    molas = [b for b in blocos if b.mola]
    alturas = {id(b): b.altura for b in blocos}
    resto = livre - minimos
    folga_total = sum(b.mola[1] - b.mola[0] for b in molas) or 1
    for b in molas:
        ganho = min(b.mola[1] - b.mola[0], resto * (b.mola[1] - b.mola[0]) / folga_total)
        alturas[id(b)] = b.mola[0] + ganho
    sobra = livre - sum(alturas[id(b)] for b in molas)
    # A sobra abre os espaços entre blocos. Numa folha sem figura elástica (capítulo curto), ela abre
    # mais: o conteúdo ocupa a folha inteira em vez de ficar amontoado no alto, com um buraco no pé.
    teto_extra = G.us(3) if molas else G.us(9)
    extra = min(teto_extra, sobra / max(1, len(blocos) - 1)) if sobra > 0 else 0
    y = TOPO
    ocupado = 0.0
    for i, b in enumerate(blocos):
        if i:
            y -= G.us(b.espaco) + extra
            ocupado += G.us(b.espaco) + extra
        h = alturas[id(b)]
        if b.ancorar and i == len(blocos) - 1:
            y = G.origem_y + h
        if grade:
            c.saveState()
            c.setStrokeColorRGB(0.85, 0.2, 0.6)
            c.setLineWidth(0.3)
            c.rect(X0, y - h, LARG, h)
            c.restoreState()
        b.desenhar(c, X0, y, h)
        y -= h
        ocupado += h
    if y < G.origem_y - 0.5 and os.environ.get("RX_AMOSTRA_SOLTO") != "1":
        raise LayoutError(f"p. {pagina}: passou da área útil")
    return ocupado / G.util_a


# ===========================================================================
# Geometria e imagem
# ===========================================================================
class Mapa:
    """Converte metros (UTM) em pontos da página, com a mesma escala nos dois eixos."""

    def __init__(self, x: float, y: float, largura: float, altura: float, caixa_m: tuple[float, float, float, float]):
        self.x, self.y, self.w, self.h = x, y, largura, altura
        self.caixa = caixa_m
        self.s = largura / (caixa_m[2] - caixa_m[0])

    def pt(self, e: float, n: float) -> tuple[float, float]:
        return self.x + (e - self.caixa[0]) * self.s, self.y + (n - self.caixa[1]) * self.s

    def imagem(self, c, caminho: Path, limites_img) -> None:
        x1, y1 = self.pt(limites_img[0], limites_img[1])
        x2, y2 = self.pt(limites_img[2], limites_img[3])
        c.drawImage(ImageReader(str(caminho)), x1, y1, x2 - x1, y2 - y1)


def caixa_na_proporcao(centro, largura_m: float, proporcao: float, limites_img) -> tuple[float, float, float, float]:
    """Caixa de largura dada e altura = largura × proporção, deslocada para dentro da imagem."""
    altura_m = largura_m * proporcao
    lx = max(limites_img[0], min(centro[0] - largura_m / 2, limites_img[2] - largura_m))
    ly = max(limites_img[1], min(centro[1] - altura_m / 2, limites_img[3] - altura_m))
    return lx, ly, lx + largura_m, ly + altura_m


def caminho_geom(c, geom: dict, mapa: Mapa):
    p = c.beginPath()
    poligonos = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poligono in poligonos:
        for anel in poligono:
            for i, (e, n) in enumerate(anel):
                xx, yy = mapa.pt(e, n)
                if i == 0:
                    p.moveTo(xx, yy)
                else:
                    p.lineTo(xx, yy)
            p.close()
    return p


def limite_car(c, geom: dict, mapa: Mapa, grosso: float = 2.8, fino: float = 1.5, halo: float = 0.45,
               preencher: float = 0.0) -> None:
    """Limite do CAR com traço duplo (halo escuro por baixo, linha clara por cima) e, se pedido, véu branco."""
    c.saveState()
    c.setLineJoin(1)
    if preencher:
        c.setFillColor(COR["PAPER"])
        c.setFillAlpha(preencher)
        c.drawPath(caminho_geom(c, geom, mapa), stroke=0, fill=1)
        c.setFillAlpha(1)
    c.setStrokeColor(COR["INK"])
    c.setStrokeAlpha(halo)
    c.setLineWidth(grosso)
    c.drawPath(caminho_geom(c, geom, mapa), stroke=1, fill=0)
    c.setStrokeAlpha(1)
    c.setStrokeColor(COR["CREAM_LINE"])
    c.setLineWidth(fino)
    c.drawPath(caminho_geom(c, geom, mapa), stroke=1, fill=0)
    c.restoreState()


def pilula(c, x, y, w, h, alpha: float = 0.85) -> None:
    c.saveState()
    c.setFillColor(COR["CREAM"])
    c.setFillAlpha(alpha)
    c.roundRect(x, y, w, h, 1.4 * mm, stroke=0, fill=1)
    c.restoreState()


def escala_e_norte(c, mapa: Mapa, x, y) -> tuple[float, float]:
    """Pílula justa com barra de escala e seta norte. A barra é a opção que dá 18–30 mm; se nenhuma der, a mais perto
    dessa faixa. Devolve (largura, altura) da pílula."""
    opcoes = (20, 50, 100, 200, 500, 1000)
    dentro = [m for m in opcoes if 18 * mm <= m * mapa.s <= 30 * mm]
    escolha = dentro[0] if dentro else min(opcoes, key=lambda m: min(abs(m * mapa.s - 18 * mm), abs(m * mapa.s - 30 * mm)))
    barra = escolha * mapa.s
    pad, h = 2.4 * mm, 8.2 * mm
    w = pad + barra + 3.4 * mm + 3.2 * mm + pad
    pilula(c, x, y, w, h)
    bx, by = x + pad, y + 4.1 * mm
    c.saveState()
    c.setStrokeColor(COR["INK"])
    c.setLineWidth(0.8)
    c.line(bx, by, bx + barra, by)
    for t in (0, 0.5, 1):
        c.line(bx + barra * t, by, bx + barra * t, by + (1.3 if t != 0.5 else 0.8) * mm)
    c.restoreState()
    linha_simples(c, bx + barra / 2, by - 3 * mm, _ptbr(Decimal(escolha)) + " m", "Sans", ANOT, COR["INK"], "centro")
    nx, ny = bx + barra + 3.4 * mm + 1.6 * mm, y + 3.4 * mm
    c.saveState()
    c.setFillColor(COR["INK"])
    p = c.beginPath()
    p.moveTo(nx, ny + 3.8 * mm)
    p.lineTo(nx - 1.5 * mm, ny + 0.4 * mm)
    p.lineTo(nx, ny + 1.1 * mm)
    p.lineTo(nx + 1.5 * mm, ny + 0.4 * mm)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.restoreState()
    linha_simples(c, nx, y + 0.9 * mm, "N", "Sans-Sb", ANOT, COR["INK"], "centro")
    return w, h


def encarte_uf(c, uf: str, lonlat, x, y, w=19 * mm, h=16 * mm) -> bool:
    """Encarte do estado com o ponto do imóvel. Devolve False (e não desenha) sem o contorno."""
    contorno = contorno_uf(uf)
    if not contorno:
        return False
    lons = [p[0] for p in contorno]
    lats = [p[1] for p in contorno]
    k = math.cos(math.radians(sum(lats) / len(lats)))
    lx, ly = (max(lons) - min(lons)) * k, max(lats) - min(lats)
    pilula(c, x, y, w, h)
    s = min((w - 3.4 * mm) / lx, (h - 3.4 * mm) / ly)
    ox = x + (w - lx * s) / 2
    oy = y + (h - ly * s) / 2

    def pt(lon, lat):
        return ox + (lon - min(lons)) * k * s, oy + (lat - min(lats)) * s

    c.saveState()
    c.setStrokeColor(COR["FOREST"])
    c.setLineWidth(0.6)
    c.setLineJoin(1)
    p = c.beginPath()
    for i, (lon, lat) in enumerate(contorno):
        (p.moveTo if i == 0 else p.lineTo)(*pt(lon, lat))
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.setFillColor(COR["TERRA"])
    c.circle(*pt(*lonlat), 0.8 * mm, stroke=0, fill=1)
    c.restoreState()
    return True


def marcador_numero(c, x: float, y: float, n: int, cor=None, raio: float = 2.1 * mm) -> None:
    """Chamada numerada desenhada (círculo branco, traço na cor do período e algarismo): nunca por glifo ①."""
    c.saveState()
    c.setFillColor(COR["PAPER"])
    c.setStrokeColor(cor if cor is not None else COR["INK"])
    c.setLineWidth(1.1)
    c.circle(x, y, raio, stroke=1, fill=1)
    c.restoreState()
    linha_simples(c, x, y - altura_maiuscula("Sans-Sb", ANOT) / 2, str(n), "Sans-Sb", ANOT, COR["INK"], "centro")


def recorte_imagem(caminho: Path, lim, caixa, largura_px: int, natural: bool = False):
    """Recorta a caixa (metros) da imagem georreferenciada e amplia com filtro bicúbico. ``natural`` tira a cara de
    falsa-cor do Sentinel na seca: menos ganho no vermelho, gama mais suave e saturação −20%."""
    from PIL import Image, ImageEnhance

    img = Image.open(caminho).convert("RGB")
    sx = img.width / (lim[2] - lim[0])
    sy = img.height / (lim[3] - lim[1])
    janela = ((caixa[0] - lim[0]) * sx, (lim[3] - caixa[3]) * sy, (caixa[2] - lim[0]) * sx, (lim[3] - caixa[1]) * sy)
    altura_px = round(largura_px * (caixa[3] - caixa[1]) / (caixa[2] - caixa[0]))
    rec = img.resize((largura_px, altura_px), Image.BICUBIC, box=janela)
    if natural:
        r, g, b = rec.split()
        rec = Image.merge("RGB", (r.point(lambda v: v * 0.9), g, b.point(lambda v: min(255, v * 1.04))))
        rec = rec.point(lambda v: round(255 * (v / 255) ** 0.88))
        rec = ImageEnhance.Contrast(rec).enhance(0.92)
        rec = ImageEnhance.Color(rec).enhance(0.8)
    return rec


# ===========================================================================
# Componentes de página (versões de amostra dos da seção 1.6)
# ===========================================================================
KICKER_SIGNIFICADO = "O que isso significa para você"
COLUNA_ROTULO = 31 * mm  # rótulo pendurado da caixa de significado, em 3 linhas curtas


def linhas_de_rotulo(t: str, largura: float, token: str = "kicker") -> list[str]:
    """Quebra um rótulo espacejado em linhas que cabem na largura (por palavra)."""
    linhas, atual = [], ""
    for palavra in t.split():
        tentativa = f"{atual} {palavra}".strip()
        if atual and st.largura_espacejada(tentativa, token) > largura:
            linhas.append(atual)
            atual = palavra
        else:
            atual = tentativa
    return linhas + ([atual] if atual else [])


def caixa_significado(itens: list[tuple[str | None, str]], largura: float = LARG, colunas: bool = False):
    """Fundo CREAM, barra FOREST de 2,6 pt, rótulo pendurado numa coluna à esquerda e texto numa medida de ~65
    caracteres. ``colunas=True``: os itens com perfil ("Se vai comprar:") ficam lado a lado; os sem perfil correm
    por cima, na largura toda do texto. Devolve (altura, desenhar)."""
    pad_esq, pad = 6 * mm, 4 * mm
    x_txt = pad_esq + COLUNA_ROTULO
    medida = largura - x_txt - pad
    gap_col = 6 * mm
    rot_linhas = linhas_de_rotulo(KICKER_SIGNIFICADO, COLUNA_ROTULO - 5 * mm)
    if colunas:
        corridos = [texto(t, "caixa", medida) for perfil, t in itens if not perfil]
        perfis = [(perfil, t) for perfil, t in itens if perfil]
    else:
        corridos = [texto((f"<b>{perfil}</b> " if perfil else "") + t, "caixa", medida) for perfil, t in itens]
        perfis = []
    larguras = [medida]
    lado = []
    if perfis:
        util = medida - gap_col * (len(perfis) - 1)
        partes = [[Decimal(1) / len(perfis)] * len(perfis)]
        if len(perfis) == 2:
            partes = [[Decimal(k) / 100, 1 - Decimal(k) / 100] for k in range(36, 65, 2)]

        def alturas(fr):
            return [texto(f"<b>{pf}</b> {t}", "caixa", util * float(f), registrar=False).altura
                    for (pf, t), f in zip(perfis, fr)]

        melhor = min(partes, key=lambda fr: (max(alturas(fr)), abs(fr[0] - Decimal("0.5"))))
        larguras = [util * float(f) for f in melhor]
        lado = [texto(f"<b>{pf}</b> {t}", "caixa", w) for (pf, t), w in zip(perfis, larguras)]
    entre = 1.6 * mm
    alt_txt = sum(p.altura for p in corridos) + entre * max(0, len(corridos) - 1)
    if lado:
        alt_txt += (entre if corridos else 0) + max(p.altura for p in lado)
    alt_rot = len(rot_linhas) * st.ESTILO["kicker"].leading
    altura = pad + max(alt_txt, alt_rot) + pad

    def desenhar(c, x, y_topo, h):
        c.saveState()
        c.setFillColor(COR["CREAM"])
        c.rect(x, y_topo - h, largura, h, stroke=0, fill=1)
        c.setStrokeColor(COR["FOREST"])
        c.setLineWidth(2.6)
        c.line(x + 1.3, y_topo - h, x + 1.3, y_topo)
        c.restoreState()
        y = y_topo - pad - (h - altura) / 2
        base = y - st.ESTILO["caixa"].fontSize + 1
        for i, linha in enumerate(rot_linhas):
            rotulo(c, x + pad_esq, base - i * st.ESTILO["kicker"].leading, linha, "kicker", COR["FOREST"])
        for par in corridos:
            y -= par.desenhar(c, x + x_txt, y) + entre
        xc = x + x_txt
        for k, par in enumerate(lado):
            if k:
                c.saveState()
                c.setStrokeColor(COR["RULE"])
                c.setLineWidth(G.fio_fino)
                c.line(xc - gap_col / 2, y, xc - gap_col / 2, y - max(q.altura for q in lado))
                c.restoreState()
            par.desenhar(c, xc, y)
            xc += larguras[k] + gap_col

    return altura, desenhar


def cabeca_capitulo(n: int, rotulo_cap: str, pergunta: str, resposta: str | tuple[str, ...], nota: str,
                    nome_icone: str, selo: tuple[str, bool] | None):
    """Cabeça de capítulo (1.6). ``resposta`` pode trazer o modelo longo e os curtos: entra o primeiro que
    cabe em 4 linhas de 116 mm e deixa a cabeça com até 55 mm."""
    h1 = texto(pergunta, "h1", LARG)
    nota_t = texto(nota, "legenda", G.nota - 3 * mm)
    modelos = (resposta,) if isinstance(resposta, str) else resposta
    for modelo in modelos:
        resp = texto(modelo, "resposta", G.leitura, registrar=False)
        corpo = max(resp.altura, nota_t.altura)
        altura = 5 * mm + 2.5 * mm + h1.altura + 2 * mm + 0.8 + 3.2 * mm + corpo
        if resp.linhas <= 4 and altura <= 55 * mm:
            REGISTRO.append((_PAGINA_ATUAL[0], modelo))
            break
    else:
        raise LayoutError(f"cabeça do capítulo {n} com {_ptbr(_q(altura / mm, 1))} mm e {resp.linhas} linhas "
                          f"(máximo 55 mm e 4 linhas; nota com {_ptbr(_q(nota_t.altura / mm, 1))} mm)")

    def desenhar(c, x, y_topo, h):
        kicker(c, x, y_topo, f"Capítulo {n} · {rotulo_cap}", COR["TERRA"], nome_icone)
        if selo:
            largura = st.largura_selo(*selo)
            st.desenhar_selo(c, x + LARG - largura, y_topo - 5 * mm + 0.4 * mm, *selo)
        y = y_topo - 5 * mm - 2.5 * mm
        y -= h1.desenhar(c, x, y)
        y -= 2 * mm
        c.saveState()
        c.setStrokeColor(COR["TERRA"])
        c.setLineWidth(0.8)
        c.line(x, y, x + 40 * mm, y)
        c.restoreState()
        y -= 3.2 * mm
        resp.desenhar(c, x, y)
        xn = x + G.leitura + G.calha
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        c.line(xn, y, xn, y - corpo)
        c.restoreState()
        nota_t.desenhar(c, xn + 3 * mm, y)

    return altura, desenhar


def cabeca_secao(pergunta: str, nome_icone: str | None = None):
    t = texto(pergunta, "h2", LARG - 8 * mm)
    altura = t.altura

    def desenhar(c, x, y_topo, h):
        xx = x
        if nome_icone:
            icone(c, nome_icone, x, y_topo - 5.2 * mm, 5.2 * mm, COR["INK"], 1.0)
            xx += 7.4 * mm
        t.desenhar(c, xx, y_topo)

    return altura, desenhar


# ===========================================================================
# PÁGINA 1 — ficha e resposta rápida (seção 2.1, com a revisão de 13/09)
# ===========================================================================
def site_do_relatorio(L: Leitura) -> str:
    """D4: o contato é só o site, sem o esquema, como se escreve num livro."""
    fixture = L.p.get("_fixture")
    if isinstance(fixture, dict):
        achado = re.search(r"https?://([\w.\-]+)", str((fixture.get("decisoes_do_dono") or {}).get("D4", "")))
        if achado:
            return achado.group(1)
    return L.site


def complemento(L: Leitura) -> dict:
    """Pedaços que a F1 congelada traz à mão e o payload real ainda não (ficam vazios em produção)."""
    fixture = L.p.get("_fixture")
    if isinstance(fixture, dict) and isinstance(fixture.get("complemento"), dict):
        return fixture["complemento"]
    return COMPLEMENTO_F1.get(str((L.p.get("property") or {}).get("car_code") or ""), {})


# O que a F1 escrita à mão traz e o payload real ainda não entrega. Em produção fica vazio, e as
# frases que dependem disso saem sem o pedaço (nunca com um valor inventado no lugar).
COMPLEMENTO_F1 = {
    "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F": {
        # fontes/01_car_composicao_datas.md, linha 54: APP_TOTAL 1,4547 = APP_LAGO_NATURAL 1,4547
        "app_em_volta_de": "da lagoa",
        # fontes/03_alertas_desmatamento.md, linha 90: a camada de alertas atuais começa em 01/08/2025
        "alertas_desde": "2025-08-01",
    },
}


def codigo_curto(report_id: str) -> str:
    """RX-20260913T215201Z-A14D011F → RX-A14D011F: o carimbo em UTC ao lado da hora de Brasília parecia erro."""
    return "RX-" + report_id.rsplit("-", 1)[-1]


def f_gms(valor: float, positivo: str, negativo: str) -> str:
    v = abs(Decimal(str(valor)))
    graus = int(v)
    minutos_d = (v - graus) * 60
    minutos = int(minutos_d)
    segundos = int(_q((minutos_d - minutos) * 60, 0))
    if segundos == 60:
        minutos, segundos = minutos + 1, 0
    return f"{graus}°{minutos:02d}'{segundos:02d}\" {negativo if valor < 0 else positivo}"


def f_comparar_area_ficha(ha) -> str | None:
    """D2 na ficha: abaixo de 5 ha, a comparação da seção 1.9; até 100 ha, em campos de futebol oficiais."""
    v = Decimal(str(ha))
    if v < 5:
        return f_comparar_area(v)
    if v < 100:
        return f"cerca de {f_inteiro(v / Decimal('0.714'))} campos de futebol"
    return None
# ===========================================================================
# Constantes e ajudantes compartilhados pelas páginas de capítulo
# ===========================================================================
PERIODO_COR = {"ate_2008_07_22": "PRODES_ATE_2008", "ate_2019_07_31": "PRODES_ATE_2019",
               "depois_2019_07_31": "PRODES_DEPOIS_2019"}
PERIODO_ORDEM = {"depois_2019_07_31": 3, "ate_2019_07_31": 2, "ate_2008_07_22": 1}
PERIODO_ROTULO = {"ate_2008_07_22": "até 22/07/2008", "ate_2019_07_31": "de 23/07/2008 a 31/07/2019",
                  "depois_2019_07_31": "depois de 31/07/2019"}
RESOLUCAO_SATELITE_M = {"Landsat": 30, "Sentinel": 10, "CBERS": 20}




def desenhar_manchas(c, mapa: Mapa, occ, figuras, geom, fino: bool = False) -> None:
    c.saveState()
    c.setLineJoin(1)
    for o in occ:
        fig = figuras.get(o["id"])
        if not fig:
            continue
        c.setStrokeColor(COR["INK2"])
        c.setLineWidth(0.4 if not fino else 0.7)
        c.setDash(1.6, 1.2)
        c.drawPath(caminho_geom(c, fig["feature_geom_m"], mapa), stroke=1, fill=0)
    c.setDash()
    for o in sorted(occ, key=lambda o: o["area_inside_ha"], reverse=True):
        fig = figuras.get(o["id"])
        if not fig:
            continue
        c.setFillColor(COR[PERIODO_COR[o["periodo_legal"]]])
        c.setFillAlpha(0.8)
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.5 if o["periodo_legal"] != "depois_2019_07_31" else 0.6)
        c.drawPath(caminho_geom(c, fig["inside_geom_m"], mapa), stroke=1, fill=1, fillMode=0)
    c.setFillAlpha(1)
    c.restoreState()
    limite_car(c, geom["car_m"], mapa, 2.4 if not fino else 3.2, 1.3 if not fino else 1.8)



# ===========================================================================
# PÁGINA 7 — capítulo 5, a terra: relevo, solo, textura, aptidão (leitura corrigida)
# ===========================================================================
CLASSE_SOLO = {
    "Argissolo": "solo com mais argila embaixo",
    "Cambissolo": "solo pouco desenvolvido",
    "Latossolo": "solo profundo e bem drenado",
    "Neossolo": "solo raso ou arenoso",
    "Gleissolo": "solo encharcado",
}
APTIDAO = {
    "1": "boa para lavoura", "2": "regular para lavoura", "3": "restrita para lavoura",
    "4": "apta só para pasto plantado", "4(p)": "apta, com restrições, só para pasto plantado",
    "5": "apta só para floresta plantada ou pasto nativo", "6": "sem aptidão agrícola, indicada para preservação",
}
APTIDAO_GRUPO_LAVOURA = {"1": "boa", "2": "regular", "3": "restrita"}
SOLO_ADJETIVO = {"muito argilosa": "muito argiloso", "argilosa": "argiloso", "média": "de textura média",
                 "siltosa": "siltoso", "arenosa": "arenoso"}
FAIXAS_RELEVO = {"plano": (0, 3), "suave ondulado": (3, 8), "ondulado": (8, 20), "forte ondulado": (20, 45),
                 "montanhoso": (45, 75), "escarpado": (75, 100)}


def f_pct_exato(x) -> str:
    """Percentual com a casa decimal da fonte (24,5%), sem arredondar para fechar 100 (revisão, item 13)."""
    t = _ptbr(_q(x, 1))
    return (t[:-2] if t.endswith(",0") else t) + "%"


def classe_textural(argila, areia) -> str:
    """Grupamento textural do SiBCS pela argila e pela areia (%)."""
    argila, areia = Decimal(str(argila)), Decimal(str(areia))
    if argila > 60:
        return "muito argilosa"
    if argila >= 35:
        return "argilosa"
    if areia - argila > 70:
        return "arenosa"
    if areia < 15:
        return "siltosa"
    return "média"


def nome_solo(classe: str) -> tuple[str, str]:
    """Nome curto (ordem + subordem + fertilidade) e explicação do dicionário."""
    palavras = classe.split()
    ordem = palavras[0]
    fert = next((w for w in palavras if w.lower() in ("eutrófico", "distrófico")), None)
    sub = " ".join(w for w in palavras[1:3] if w not in ("Tb", "Ta") and w.lower() not in ("eutrófico", "distrófico"))
    curto = " ".join(x for x in (ordem, sub, fert) if x)
    explica = CLASSE_SOLO.get(ordem, "")
    if fert:
        explica += "; fertilidade natural " + ("alta" if fert.lower() == "eutrófico" else "baixa")
    return curto, explica


def componentes_lavoura(componentes: list[str]) -> list[tuple[str, str]]:
    """'Latossolo Vermelho 2(ab)c' → ('Latossolo Vermelho', 'regular'): só os componentes com aptidão para lavoura."""
    saida = []
    for comp in componentes:
        achado = re.match(r"(.+?)\s+([123])\(?[abcABC]*\)?[abcABC]*$", comp.strip())
        if achado:
            saida.append((achado.group(1), APTIDAO_GRUPO_LAVOURA[achado.group(2)]))
    return saida





# ===========================================================================
# Autoteste de redação (o que dá para conferir por máquina no texto desenhado)
# ===========================================================================
PROIBIDAS = (
    (r"\bregular(?!\s+para\s+lavoura)\b", "regular"), (r"\bregulariza", "regularizado/regularização"),
    (r"\blimp[oa]\b", "limpo"), (r"\baprovad[oa]\b", "aprovado"), (r"sem pendências", "sem pendências"),
    (r"m²", "m²"), (r"\((s|is|ões)\)", "plural entre parênteses"), (r"\bN/D\b", "N/D"),
    (r"Landsat\s*8", "satélite errado"), (r"\bOLI\b", "satélite errado"), (r"\bpequena propriedade\b", "categoria"),
    (r"\bminifúndio\b", "categoria"), (r"área consolidada", "área consolidada"), (r"\b\d+\.\d{1,2}\b(?!\d)",
                                                                                 "ponto decimal"),
)
ABREVIACOES = ("nº", "pág.", "art.", "Res.")




def frases(t: str) -> list[str]:
    limpo = re.sub(r"<[^>]+>", " ", t).replace("&nbsp;", " ")
    for abrev in ABREVIACOES:
        limpo = limpo.replace(abrev, abrev.replace(".", "§"))
    partes = re.split(r"(?<=[.?!;:])\s+", limpo)
    return [x.strip() for x in partes if x.strip()]


def autoteste_redacao() -> list[str]:
    problemas = []
    maior = 0
    por_pagina: dict[int, list[int]] = {}
    for pagina, t in REGISTRO:
        limpo = re.sub(r"<[^>]+>", " ", t)
        for padrao, nome in PROIBIDAS:
            if re.search(padrao, limpo, flags=re.IGNORECASE):
                problemas.append(f"p. {pagina}: '{nome}' em: {limpo[:80]}")
        if pagina == 1 and re.search(r"\bmulta", limpo, flags=re.IGNORECASE):
            problemas.append(f"p. 1: 'multa' proibida em: {limpo[:80]}")
        for frase in frases(t):
            palavras = len(re.findall(r"[\wÀ-ÿ§/\-,°%]+", frase))
            maior = max(maior, palavras)
            if palavras >= 4:  # rótulos e números soltos não contam na média do guia (2.0.3)
                por_pagina.setdefault(pagina, []).append(palavras)
            if palavras > 30:
                problemas.append(f"p. {pagina}: frase com {palavras} palavras: {frase[:90]}")
    print(f"RX_AMOSTRA_FRASE_MAIOR={maior}")
    for pagina, contagens in sorted(por_pagina.items()):
        media = Decimal(sum(contagens)) / len(contagens)
        print(f"RX_AMOSTRA_FRASE_MEDIA_P{pagina}={_ptbr(_q(media, 1))}")
        if media > 18:
            problemas.append(f"p. {pagina}: média de {_ptbr(_q(media, 1))} palavras por frase (máximo 18)")
    return problemas


# ===========================================================================
# Blocos comuns às páginas de capítulo
# ===========================================================================
ROTULO_SITUACAO = {"encontrado": "Encontrado", "nao_encontrado": "Nenhum registro",
                   "pendente": "Consulta pendente"}
# Pergunta do capítulo → (número, rótulo, ícone, título corrente)
CAPITULOS = {
    "desmatamento": (1, "Tem desmatamento registrado?", "folha", "Desmatamento"),
    "restricoes": (2, "Tem embargo, área protegida ou mineração?", "escudo",
                   "Embargo, área protegida e mineração"),
    "documentos": (3, "O que os documentos provam?", "documento", "Documentos"),
    "agua": (4, "Tem água?", "gota", "Água"),
    "terra_uso": (5, "Como a terra está sendo usada?", "broto", "A terra"),
    "terra_chao": (5, "Dá para usar máquina? Como é o solo?", "camadas", "A terra"),
    "clima": (6, "Como chove e quando seca?", "gota", "Clima"),
    "entorno": (7, "O que existe e se produz em volta?", "trator", "O entorno"),
}

_TOTAL = [13]          # total de folhas do livro, decidido no planejamento
NOTA_PENDENTE = ("A fonte não respondeu a tempo nesta emissão. A consulta é refeita na próxima; "
                 "esta pergunta fica sem resposta aqui.")


def total_paginas() -> int:
    return _TOTAL[0]


def frase_pendente(L: Leitura, chave: str) -> str:
    """Resposta do capítulo ◌ (§2.0.5): discreta, sem número, sem cor e sem 'não encontrado'."""
    fonte = (L.fato(chave).get("fonte") or {}).get("nome_publico") or "A fonte"
    data = f_data_brasilia(L.p.get("generated_at") or "")
    return f"Consulta pendente. {fonte} não respondeu a tempo em {data}; esta pergunta fica sem resposta nesta edição."


def alcance(L: Leitura, chave: str, ligacao: str = " ") -> str:
    """Primeiro limite escrito do fato — o alcance que acompanha todo zero (guia de redação, 4)."""
    limites = L.fato(chave).get("limites") or []
    return (ligacao + str(limites[0])) if limites else ""


COLUNA_SELO = 50 * mm   # cabe o selo mais largo ("Consulta pendente") sem encostar no nome do item


def linha_item(rotulo_txt: str, estado: str, atencao_item: bool, detalhe: str, largura: float,
               icone_nome: str | None = None):
    """Linha de item com selo à esquerda, nome e detalhe. Estado sem selo entra como texto neutro.

    A coluna do selo tem largura fixa, medida pelo rótulo mais longo: o texto "Fora desta edição"
    passava por cima do nome do item quando a coluna era estreita demais.
    """
    nome_t = texto(rotulo_txt, "icone_nome", largura - COLUNA_SELO - 2 * mm)
    det_t = texto(detalhe, "legenda", largura - COLUNA_SELO - 2 * mm) if detalhe else None
    altura = max(st.SELO_ALTURA, nome_t.altura) + (det_t.altura + 0.8 * mm if det_t else 0)

    def desenhar(c, x, y_topo, h):
        y = y_topo
        if estado in st.ESTADOS_SEM_SELO:
            cap = altura_maiuscula(st.ESTILO["selo_rotulo"].fontName, st.ESTILO["selo_rotulo"].fontSize)
            rotulo(c, x, y - st.SELO_ALTURA / 2 - cap / 2, "Fora desta edição", "selo_rotulo", COR["MUTED"])
        else:
            st.desenhar_selo(c, x, y - st.SELO_ALTURA, estado, atencao_item)
        xn = x + COLUNA_SELO
        if icone_nome:
            icone(c, icone_nome, xn - 6.6 * mm, y - 5 * mm, 5 * mm, COR["INK2"], 1.0)
        nome_t.desenhar(c, xn, y + 0.6 * mm)
        if det_t:
            det_t.desenhar(c, xn, y - nome_t.altura - 0.8 * mm)

    return altura, desenhar


def tabela(colunas: list[tuple[str, float]], linhas: list[list[str]], nota: str | None = None,
           numericas: tuple[int, ...] = ()):
    """Tabela do livro: cabeçalho espacejado, fio fino entre linhas, sem grade fechada."""
    largura = sum(w for _, w in colunas)
    celulas = [[texto(v, "tab_num" if i in numericas else "tab_cel", colunas[i][1] - 3 * mm)
                for i, v in enumerate(linha)] for linha in linhas]
    alturas = [max(cel.altura for cel in linha) + 2.2 * mm for linha in celulas]
    nota_t = texto(nota, "legenda", largura) if nota else None
    cab_h = st.ESTILO["tab_cab"].leading + 1.6 * mm
    altura = cab_h + sum(alturas) + (nota_t.altura + 1.8 * mm if nota_t else 0)

    def desenhar(c, x, y_topo, h):
        y = y_topo - st.ESTILO["tab_cab"].fontSize
        xx = x
        for nome, w in colunas:
            rotulo(c, xx, y, nome, "tab_cab")
            xx += w
        y = y_topo - cab_h
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        c.line(x, y + 1.2 * mm, x + largura, y + 1.2 * mm)
        c.restoreState()
        for linha, alt in zip(celulas, alturas):
            xx = x
            for i, cel in enumerate(linha):
                cel.desenhar(c, xx + (colunas[i][1] - 3 * mm - cel.largura if i in numericas else 0), y)
                xx += colunas[i][1]
            y -= alt
            c.saveState()
            c.setStrokeColor(COR["RULE"])
            c.setLineWidth(G.fio_fino)
            c.line(x, y + 1.2 * mm, x + largura, y + 1.2 * mm)
            c.restoreState()
        if nota_t:
            nota_t.desenhar(c, x, y - 1.8 * mm)

    return altura, desenhar


def barra_composicao(partes: list[tuple[str, float, str]], largura: float = LARG, altura_barra: float = 9 * mm):
    """Barra de 100% com legenda embaixo. ``partes`` = (rótulo, percentual, cor hex)."""
    total = sum(p for _, p, _ in partes) or 1
    legendas = [texto(f"{rot} {f_pct(pct)}", "legenda", largura / max(1, min(4, len(partes))) - 4 * mm)
                for rot, pct, _ in partes]
    por_linha = max(1, min(4, len(partes)))
    linhas_leg = math.ceil(len(partes) / por_linha)
    altura = altura_barra + 2.4 * mm + linhas_leg * (max(t.altura for t in legendas) + 1.2 * mm)

    def desenhar(c, x, y_topo, h):
        xx = x
        c.saveState()
        for _, pct, cor_hex in partes:
            w = largura * pct / total
            c.setFillColor(HexColor(cor_hex))
            c.rect(xx, y_topo - altura_barra, w, altura_barra, stroke=0, fill=1)
            xx += w
        c.restoreState()
        col = largura / por_linha
        for i, (rot, pct, cor_hex) in enumerate(partes):
            lx = x + (i % por_linha) * col
            ly = y_topo - altura_barra - 2.4 * mm - (i // por_linha) * (legendas[i].altura + 1.2 * mm)
            c.saveState()
            c.setFillColor(HexColor(cor_hex))
            c.rect(lx, ly - 2.6 * mm, 2.6 * mm, 2.6 * mm, stroke=0, fill=1)
            c.restoreState()
            legendas[i].desenhar(c, lx + 3.8 * mm, ly)

    return altura, desenhar


def destaques(itens: list[tuple[str, str, str]], largura: float = LARG):
    """Números-destaque lado a lado (valor, unidade, legenda). Nunca mostra zero (§1.6).

    A unidade entra logo depois do algarismo, medida pela largura real do texto desenhado — e não
    pela largura da coluna, que jogava o "mm" para a borda e por cima do número seguinte.
    """
    col = largura / max(1, len(itens))
    estilo_valor = st.ESTILO["destaque_valor"]
    valores = [texto(v, "destaque_valor", col - 4 * mm, registrar=False) for v, _, _ in itens]
    legendas = [texto(lg, "legenda", col - 4 * mm) for _, _, lg in itens]
    alto_valor = max(v.altura for v in valores)
    altura = alto_valor + 1.4 * mm + max(lg.altura for lg in legendas)

    def desenhar(c, x, y_topo, h):
        for i, ((v, un, _lg), val_t, leg_t) in enumerate(zip(itens, valores, legendas)):
            cx = x + i * col
            if i:
                c.saveState()
                c.setStrokeColor(COR["RULE"])
                c.setLineWidth(G.fio_fino)
                c.line(cx - 2 * mm, y_topo - altura, cx - 2 * mm, y_topo)
                c.restoreState()
            REGISTRO.append((_PAGINA_ATUAL[0], v))
            val_t.desenhar(c, cx, y_topo)
            if un:
                largura_valor = pdfmetrics.stringWidth(v, estilo_valor.fontName, estilo_valor.fontSize)
                base = y_topo - estilo_valor.fontSize * 0.74
                linha_simples(c, cx + largura_valor + 1.2 * mm, base, un, "Sans", 11, COR["INK2"])
            leg_t.desenhar(c, cx, y_topo - alto_valor - 1.4 * mm)

    return altura, desenhar


def maiuscula_inicial(t: str) -> str:
    """Primeira letra maiúscula sem baixar o resto: ``str.capitalize`` virava "ANM" em "anm"."""
    t = str(t or "")
    return t[:1].upper() + t[1:] if t else t


def planta_do_imovel(L: Leitura, largura: float, altura_alvo: float):
    """Figura da p. 1 quando não há imagem: a planta do imóvel, com escala e norte (§2.0.1)."""
    geom = L.geometria

    def desenhar(c, x, y_topo, h):
        if not geom:
            return
        bb = geom["bbox_m"]
        folga = max(bb[2] - bb[0], bb[3] - bb[1]) * 0.12
        caixa = caixa_na_proporcao(((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2),
                                   max(bb[2] - bb[0], (bb[3] - bb[1]) * largura / h) + folga * 2, h / largura,
                                   (bb[0] - 1e6, bb[1] - 1e6, bb[2] + 1e6, bb[3] + 1e6))
        mapa = Mapa(x, y_topo - h, largura, h, caixa)
        c.saveState()
        c.setFillColor(COR["CREAM"])
        c.roundRect(x, y_topo - h, largura, h, 3 * mm, stroke=0, fill=1)
        c.setFillColor(COR["TERRA_LIGHT"])
        c.drawPath(caminho_geom(c, geom["car_m"], mapa), stroke=0, fill=1)
        c.setStrokeColor(COR["TERRA"])
        c.setLineWidth(1.6)
        c.setLineJoin(1)
        c.drawPath(caminho_geom(c, geom["car_m"], mapa), stroke=1, fill=0)
        c.restoreState()
        escala_e_norte(c, mapa, x + 2.5 * mm, y_topo - h + 2.5 * mm)
        encarte_uf(c, geom.get("uf") or "", geom["centroid_lonlat"], x + largura - 21.5 * mm,
                   y_topo - h + 2.5 * mm)

    return desenhar


def foto_do_imovel(L: Leitura, img: dict, largura: float, altura: float, x: float, y_topo: float, c,
                   fracao_quadro: float = 0.45) -> Mapa:
    """Recorte da imagem com o imóvel ocupando ~45% da largura, e o limite do CAR por cima."""
    geom = L.geometria
    lim = img["bounds_m"]
    bb = geom["bbox_m"]
    # A caixa precisa CABER o imóvel inteiro nos dois eixos: o lado do quadro é o maior entre o que a
    # largura pede e o que a altura pede (imóvel comprido e estreito cabia só pela largura e saía cortado).
    larg_m = max((bb[2] - bb[0]) / fracao_quadro, (bb[3] - bb[1]) / fracao_quadro * largura / altura)
    larg_m = min(larg_m, lim[2] - lim[0])
    caixa = caixa_na_proporcao(((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2), larg_m, altura / largura, lim)
    mapa = Mapa(x, y_topo - altura, largura, altura, caixa)
    foto = recorte_imagem(L.imagem(img["path"]), lim, caixa, int(largura / 72 * 300))
    c.saveState()
    clip = c.beginPath()
    clip.roundRect(x, y_topo - altura, largura, altura, 3 * mm)
    c.clipPath(clip, stroke=0, fill=0)
    buf = io.BytesIO()
    foto.save(buf, "JPEG", quality=90, optimize=True)
    buf.seek(0)
    c.drawImage(ImageReader(buf), x, y_topo - altura, largura, altura)
    c.restoreState()
    return mapa


def legenda_imagem(img: dict, com_limite: bool = True) -> str:
    """Legenda da imagem. Sem data quando a fonte não publica data: nunca uma data inventada."""
    partes = ["Imagem de satélite de alta resolução"]
    if img.get("date"):
        partes.append(f"de {f_data(img['date'])}")
    if img.get("resolution_m"):
        partes.append(f"cada ponto cobre cerca de {f_metros(img['resolution_m'])}")
    frase = ", ".join(partes) + "."
    if com_limite:
        frase += " Linha clara: limite declarado no CAR."
    if img.get("attribution"):
        frase += f" Fonte: {img['attribution']}."
    return frase


def rodape_capitulo(p: dict, site: str) -> str:
    codigo = str((p.get("property") or {}).get("car_code") or "")
    curto = f"{codigo[:11]}…{codigo[-8:]}" if len(codigo) > 22 else codigo
    return f"CAR {curto} · emitido em {f_data_brasilia(p.get('generated_at') or '')} · {site}"


def abrir_capitulo(L: Leitura, chave_cap: str, fato_principal: str, resposta, nota: str,
                   blocos: list) -> None:
    """Cabeça do capítulo com o selo do fato principal e a resposta já escolhida pela medida."""
    n, pergunta, nome_icone, _ = CAPITULOS[chave_cap]
    selo = L.selo(fato_principal) if fato_principal else None
    alt, des = cabeca_capitulo(n, CAPITULOS[chave_cap][3], pergunta, resposta, nota, nome_icone, selo)
    blocos.append(Bloco("cabeca", alt, des, espaco=0))


def fechar_pagina(c, L: Leitura, blocos: list, numero: int, chave_cap: str | None, grade: bool) -> float:
    ocupacao = compor(c, blocos, numero, grade)
    titulo = CAPITULOS[chave_cap][3] if chave_cap else None
    st.desenhar_moldura(c, numero, total_paginas(), titulo,
                        rodape_capitulo(L.p, L.site) if numero > 1 else None)
    return ocupacao


# ===========================================================================
# PÁGINA 1 — ficha e resposta rápida (seção 2.1)
# ===========================================================================
def pagina_1(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 1
    p = L.p
    prop = p.get("property") or {}
    car = p.get("car") or {}
    emitido = p.get("generated_at") or ""
    site = site_do_relatorio(L)
    geom = L.geometria
    blocos: list[Bloco] = []

    def marca(c) -> None:
        """Faixa de marca na altura do cabeçalho corrente das outras folhas (mesmo ritmo no topo)."""
        x, base = X0, G.pagina_a - G.cabecalho_base
        largura = rotulo(c, x, base, "Raio-X Territorial", "kicker", COR["FOREST"])
        linha_simples(c, x + largura + 2 * mm, base, "Relatório do imóvel rural", "Sans", 8.6, COR["INK2"])
        linha_simples(c, x + LARG, base, f"Emitido em {f_data_brasilia(emitido)}, às {f_hora_brasilia(emitido)} · "
                      f"Código {codigo_curto(str(p.get('report_id') or ''))}", "Sans", 8.6, COR["INK2"], "dir")
        c.saveState()
        c.setStrokeColor(COR["FOREST"])
        c.setLineWidth(0.8)
        c.line(x, G.pagina_a - G.cabecalho_fio, x + LARG, G.pagina_a - G.cabecalho_fio)
        c.restoreState()
        linha_simples(c, x, G.folio_base, site, "Sans", PISO_PT, COR["INK2"])

    # --- identidade: título = nome validado ou código do CAR (regra 5 do dono) --------------------
    codigo = str(prop.get("car_code") or "")
    nome_validado = (str(prop.get("name") or "").strip()
                     if str(prop.get("name_source") or "").startswith("SICAR:") else "")
    titulo = nome_validado or codigo
    pt_titulo, espaco_letra = (22.0, 0.0) if nome_validado else (15.0, 0.6)
    fonte_titulo = "Disp-Sb"
    while pdfmetrics.stringWidth(titulo, fonte_titulo, pt_titulo) + espaco_letra * (len(titulo) - 1) > LARG:
        pt_titulo -= 0.5
        if pt_titulo < 11:
            raise LayoutError("o título do imóvel não cabe numa linha")
    explica_txt = ("Nome declarado no Cadastro Ambiental Rural (CAR)." if nome_validado
                   else "Imóvel rural identificado pelo código do Cadastro Ambiental Rural (CAR).")
    explica = texto(explica_txt, "legenda", LARG)
    sub = texto(codigo, "codigo_sub", LARG) if nome_validado else None
    cap_k = altura_maiuscula("Sans-Sb", PISO_PT)
    cap_c = altura_maiuscula(fonte_titulo, pt_titulo)
    alt_ident = cap_k + 2.4 * mm + cap_c + 2 * mm + explica.altura + (sub.altura + 1 * mm if sub else 0)

    def identidade(c, x, y_topo, h):
        rotulo(c, x, y_topo - cap_k, "Imóvel no CAR", "kicker", COR["FOREST"])
        base = y_topo - cap_k - 2.4 * mm - cap_c
        REGISTRO.append((1, titulo))
        c.saveState()
        c.setFillColor(COR["INK"])
        c.setFont(fonte_titulo, pt_titulo)
        c.drawString(x, base, titulo, charSpace=espaco_letra)  # uma sequência só: copiar e colar sai inteiro
        c.restoreState()
        y = base - 2 * mm
        if sub:
            y -= sub.desenhar(c, x, y) + 1 * mm
        explica.desenhar(c, x, y)

    blocos.append(Bloco("identidade", alt_ident, identidade, espaco=0))

    # --- retrato (imagem ou planta) e coluna de fatos ---------------------------------------------
    img = L.imagem_base()
    col_w = LARG - 104 * mm - G.calha
    leg_img = texto(legenda_imagem(img) if img else
                    "Planta do imóvel pelo limite declarado no CAR. A imagem de satélite não respondeu "
                    "nesta emissão.", "legenda", 104 * mm)
    area = Decimal(str(prop.get("area_ha") or 0))
    fatos_ficha: list[tuple[str, str, str]] = []
    municipio = str(prop.get("municipality") or "")
    uf = str(prop.get("uf") or "")
    if municipio:
        legenda_loc = ""
        if geom:
            lon, lat = geom["centroid_lonlat"]
            legenda_loc = f"centro do imóvel: {f_gms(lat, 'N', 'S')}, {f_gms(lon, 'E', 'W')}"
        fatos_ficha.append(("Localização", f"{municipio} ({uf})", legenda_loc))
    if area > 0:
        # a comparação só entra quando ajuda a imaginar o tamanho; acima de 100 ha ela repetia o número
        fatos_ficha.append(("Área declarada no CAR", f_ha(area), f_comparar_area_ficha(area) or ""))
    fm = L.book.get("fiscal_module_ha") if isinstance(L.book.get("fiscal_module_ha"), dict) else None
    mf = car.get("fiscal_modules")
    if mf is not None:
        mod_legenda = ""
        if fm and municipio:
            mod_legenda = f"1&nbsp;módulo em {municipio} ≈ {f_inteiro(fm['value'])}&nbsp;ha"
        regra = L.fato("regra_credito")
        if regra.get("estado") == "encontrado" and (regra.get("valor") or {}).get("faixa_modulos") == "ate_4":
            mod_legenda = "até 4 módulos, a lei tem regras próprias" + (f"; {mod_legenda}" if mod_legenda else "")
        fatos_ficha.append(("Módulos fiscais", _ptbr(_q(Decimal(str(mf)), 2)), mod_legenda))
    situacao = {"AT": "Ativo", "PE": "Pendente", "SU": "Suspenso", "CA": "Cancelado"}.get(
        str(car.get("status") or "").upper())
    if situacao:
        analise = {"Aguardando análise": "ainda não conferido pelo órgão ambiental"}.get(
            str(car.get("analysis_status") or ""))
        fatos_ficha.append(("Cadastro", situacao, analise or ""))
    fatos_t = [(r, texto(v, "fato_valor", col_w), texto(lg, "legenda", col_w) if lg else None)
               for r, v, lg in fatos_ficha]
    passo_min = 2.6 * mm
    alt_fatos = sum(4 * mm + v.altura + (0.6 * mm + lg.altura if lg else 0) for _, v, lg in fatos_t) \
        + passo_min * max(0, len(fatos_t) - 1)
    img_min, img_max = 70 * mm, 88 * mm
    fixo_img = 1.8 * mm + leg_img.altura
    mola_min = max(img_min + fixo_img, alt_fatos)
    desenhar_planta = planta_do_imovel(L, 104 * mm, img_min)

    def retrato(c, x, y_topo, h):
        ih = h - fixo_img
        iw = 104 * mm
        if img and geom:
            mapa = foto_do_imovel(L, img, iw, ih, x, y_topo, c)
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(x, y_topo - ih, iw, ih, 3 * mm)
            c.clipPath(clip, stroke=0, fill=0)
            limite_car(c, geom["car_m"], mapa, grosso=3.4, fino=1.6, halo=0.7, preencher=0.12)
            c.restoreState()
            escala_e_norte(c, mapa, x + 2.5 * mm, y_topo - ih + 2.5 * mm)
            encarte_uf(c, geom.get("uf") or uf, geom["centroid_lonlat"], x + iw - 21.5 * mm,
                       y_topo - ih + 2.5 * mm)
        else:
            desenhar_planta(c, x, y_topo, ih)
        leg_img.desenhar(c, x, y_topo - ih - 1.8 * mm)
        xf = x + iw + G.calha
        natural = alt_fatos - passo_min * max(0, len(fatos_t) - 1)
        passo = max(passo_min, (h - natural) / max(1, len(fatos_t) - 1)) if len(fatos_t) > 1 else 0
        y = y_topo
        for i, (r, v, lg) in enumerate(fatos_t):
            if i:
                c.saveState()
                c.setStrokeColor(COR["RULE"])
                c.setLineWidth(G.fio_fino)
                c.line(xf, y + passo / 2, xf + col_w, y + passo / 2)
                c.restoreState()
            rotulo(c, xf, y - altura_maiuscula("Sans-Sb", PISO_PT), r, "tab_cab")
            y -= 4 * mm
            y -= v.desenhar(c, xf, y)
            if lg:
                y -= 0.6 * mm
                lg.desenhar(c, xf, y)
                y -= lg.altura
            y -= passo

    blocos.append(Bloco("retrato", mola_min, retrato, mola=(mola_min, max(mola_min, img_max + fixo_img)),
                        espaco=1))

    # --- em resumo ---------------------------------------------------------------------------------
    recuo_resumo = st.largura_espacejada("Em resumo", "kicker") + 3 * mm
    resumo_txt = frase_em_resumo(L)
    resumo = texto(resumo_txt, "resposta", LARG, firstLineIndent=recuo_resumo)
    if resumo.linhas > 4:
        resumo_txt = frase_em_resumo(L, curta=True)
        resumo = texto(resumo_txt, "resposta", LARG, registrar=False, firstLineIndent=recuo_resumo)
        REGISTRO.append((1, resumo_txt))
        if resumo.linhas > 4:
            raise LayoutError("p. 1: 'Em resumo' passa de 4 linhas")

    def em_resumo(c, x, y_topo, h):
        rotulo(c, x, y_topo - st.ESTILO["resposta"].fontSize, "Em resumo", "kicker", COR["FOREST"])
        resumo.desenhar(c, x, y_topo)

    blocos.append(Bloco("em_resumo", resumo.altura, em_resumo, espaco=1))

    # --- painel de 6 respostas: toda linha tem situação -------------------------------------------
    cartoes_t = [montar_cartao(d) for d in cartoes_do_painel(L)]
    card_w, card_h = G.col2, 27 * mm
    linhas_painel = math.ceil(len(cartoes_t) / 2)

    def painel(c, x, y_topo, h):
        for i, (nome_ic, tit, situacao_c, resp) in enumerate(cartoes_t):
            col, lin = i % 2, i // 2
            cx = x + col * (card_w + G.calha)
            cy = y_topo - lin * (card_h + G.us(1))
            c.saveState()
            c.setStrokeColor(COR["RULE"])
            c.setLineWidth(0.5)
            c.line(cx, cy, cx + card_w, cy)
            c.restoreState()
            y = cy - 1.4 * mm
            situacao_do_cartao(c, cx, y - st.SELO_ALTURA, situacao_c)
            y -= st.SELO_ALTURA + 1 * mm
            icone(c, nome_ic, cx, y - 6 * mm + 0.6 * mm, 6 * mm, COR["INK"], 1.0)
            y -= tit.desenhar(c, cx + 7.5 * mm, y + 0.4 * mm) + 1.2 * mm
            resp.desenhar(c, cx, y)

    blocos.append(Bloco("painel", linhas_painel * card_h + (linhas_painel - 1) * G.us(1), painel, espaco=1))

    # --- o que isso significa para você, ancorado no pé -------------------------------------------
    alt_cx, des_cx = caixa_significado([(None, frase_significado_p1(L))])
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    ocupacao = compor(c, blocos, 1, grade)
    marca(c)
    st.desenhar_moldura(c, 1, total_paginas())
    return ocupacao


def situacao_do_cartao(c, x, y, situacao_c) -> None:
    if situacao_c[0] == "selo":
        st.desenhar_selo(c, x, y, situacao_c[1], situacao_c[2])
        return
    # sem fato de estado: linha neutra, sem círculo (o círculo é só dos três estados)
    c.saveState()
    c.setStrokeColor(COR["MUTED"])
    c.setLineWidth(1)
    c.setLineCap(1)
    c.line(x + 0.5 * mm, y + st.SELO_ALTURA / 2, x + st.SELO_DIAMETRO - 0.5 * mm, y + st.SELO_ALTURA / 2)
    c.restoreState()
    cap = altura_maiuscula(st.ESTILO["selo_rotulo"].fontName, st.ESTILO["selo_rotulo"].fontSize)
    rotulo(c, x + st.SELO_DIAMETRO + st.SELO_ESPACO, y + st.SELO_ALTURA / 2 - cap / 2, "Informativo",
           "selo_rotulo", COR["INK2"])


def montar_cartao(dados):
    """Monta um cartão do painel da p. 1, escolhendo a versão do texto que cabe.

    O cartão NUNCA derruba o livro: depois do longo e do curto vem a resposta mínima do selo, que
    sempre cabe. Antes, um texto comprido levantava erro de diagramação e a emissão inteira caía
    para o relatório antigo — defeito grande para uma causa pequena.
    """
    nome_ic, pergunta, situacao_c, longo, curto, pag = dados
    card_w = G.col2
    tit = texto(pergunta, "painel_pergunta", card_w - 7.5 * mm)
    if tit.linhas > 2:
        raise LayoutError(f"cartão '{pergunta}': pergunta com mais de 2 linhas")
    max_linhas = 3 if tit.linhas == 1 else 2
    ref = (f' <font name="Sans-Sb" size="8.6" color="{st.HEX["FOREST"]}">→&nbsp;pág.&nbsp;{pag}</font>'
           if pag else "")
    minimo = RESPOSTA_MINIMA.get(situacao_c[1] if situacao_c[0] == "selo" else "informativo",
                                 "Veja a página indicada.")
    for modelo in (longo, curto, minimo):
        t = texto(modelo + ref, "painel_resposta", card_w, registrar=False)
        if t.linhas <= max_linhas:
            REGISTRO.append((1, modelo))
            return nome_ic, tit, situacao_c, t
    # último recurso: o selo já diz a situação, e a página indicada traz o detalhe
    t = texto(minimo, "painel_resposta", card_w, registrar=False)
    REGISTRO.append((1, minimo))
    return nome_ic, tit, situacao_c, t


# Resposta de uma linha por situação, para o cartão que não coube de nenhum outro jeito.
RESPOSTA_MINIMA = {
    "encontrado": "Sim. O detalhe está na página indicada.",
    "nao_encontrado": "Nada apareceu nas consultas desta emissão.",
    "pendente": "Consulta pendente nesta emissão.",
    "informativo": "Veja a página indicada.",
}


def pior_estado(L: Leitura, chaves: tuple[str, ...]) -> tuple[str, bool]:
    """Situação de um grupo de fatos, na ordem: achou > pendente > nenhum registro.

    "Encontrado" vem antes de "pendente" de propósito: um achado é afirmação, e o selo ◌ ao lado de
    "Sim, apareceu" diria o contrário do texto. O que ficou pendente é dito na própria frase.
    """
    estados = [L.estado(k) for k in chaves if L.estado(k) not in st.ESTADOS_SEM_SELO]
    if not estados:
        return ("pendente", False)
    for e in ("encontrado", "pendente", "nao_encontrado"):
        if e in estados:
            return (e, any(L.atencao(k) for k in chaves) if e == "encontrado" else False)
    return ("pendente", False)


NUCLEO_RESTRICAO = ("embargo_ibama", "embargo_icmbio", "autos", "ti", "uc", "quilombola",
                    "assentamento", "floresta_publica", "anm")


def frase_desmatamento(L: Leitura, curta: bool = False) -> str:
    """Frase do desmatamento para a p. 1, sempre a partir do que o fato sustenta."""
    e = L.fato("prodes")
    if e["estado"] == "pendente":
        return "A consulta de desmatamento ficou pendente nesta emissão."
    valor = e.get("valor") or {}
    occ = valor.get("occurrences") or []
    dentro = [o for o in occ if o.get("classe") == "dentro"]
    if not dentro:
        divisa = [o for o in occ if o.get("classe") == "divisa"]
        if divisa:
            return (f"Nenhuma retirada de vegetação nativa dentro do imóvel; "
                    f"{len(divisa)} {'mancha pega' if len(divisa) == 1 else 'manchas pegam'} só a divisa.")
        return "Nenhuma retirada de vegetação nativa mapeada dentro do imóvel."
    pct = Decimal(str(valor.get("pct_car_dentro") or 0))
    maior = max(dentro, key=lambda o: o.get("area_inside_ha") or 0)
    anos = periodo_da_ocorrencia(maior)
    antes_2008 = all(o.get("periodo_legal") == "ate_2008_07_22" for o in dentro)
    periodo_lei = ", antes de 22/07/2008" if antes_2008 else ""
    if curta:
        return f"{f_pct(pct)} da área perdeu a vegetação nativa {anos}{periodo_lei}."
    return f"{f_faixa_parte(pct)} teve a vegetação nativa retirada {anos}{periodo_lei}."


def periodo_da_ocorrencia(o: dict) -> str:
    """Período em que a retirada foi percebida: da imagem anterior à imagem do ano."""
    if o.get("prev_image_date"):
        return f"entre {f_mes_ano_curto(o['prev_image_date'])} e {f_mes_ano_curto(o['image_date'])}"
    if o.get("ano_anterior") and o.get("year"):
        return f"entre {o['ano_anterior']} e {o['year']}"
    return f"em {o.get('year')}" if o.get("year") else "no período mapeado"


def frase_em_resumo(L: Leitura, curta: bool = False) -> str:
    """"Em resumo": o que mais pede atenção, e só o que os fatos sustentam."""
    partes: list[str] = []
    atencoes = [k for k in ("prodes", "embargo_ibama", "embargo_icmbio", "autos", "ti", "uc",
                            "quilombola", "assentamento", "floresta_publica", "anm",
                            "sitio_arqueologico", "alertas_mensais") if L.atencao(k)]
    if "prodes" in atencoes:
        partes.append(("Pede atenção o desmatamento: " if not curta else "Atenção ao desmatamento: ")
                      + frase_desmatamento(L, curta))
    elif atencoes:
        partes.append("Pede atenção o que a página 4 mostra.")
    else:
        partes.append("Nada do que foi consultado apareceu como registro sobre o imóvel.")
    if "embargo_ibama" in atencoes:
        n = (L.fato("embargo_ibama").get("valor") or {}).get("quantidade") or 0
        partes.append(f"Há {f_inteiro(n)} {'embargo' if n == 1 else 'embargos'} do IBAMA com mapa sobre a área.")
    if "anm" in atencoes:
        n = (L.fato("anm").get("valor") or {}).get("quantidade") or 0
        partes.append(f"Há {f_inteiro(n)} {'processo' if n == 1 else 'processos'} de mineração na ANM sobre a área.")
    ausentes = [k for k in NUCLEO_RESTRICAO if L.estado(k) == "nao_encontrado"]
    if len(ausentes) >= 5 and not atencoes:
        partes.append("Não apareceu embargo, terra indígena, unidade de conservação nem mineração.")
    pendentes = [k for k in L.fatos if L.estado(k) == "pendente"]
    if pendentes:
        partes.append(f"{f_inteiro(len(pendentes))} {'consulta ficou pendente' if len(pendentes) == 1 else 'consultas ficaram pendentes'} "
                      "e a página 2 diz qual.")
    return " ".join(partes)


def frase_significado_p1(L: Leitura) -> str:
    emitido = L.p.get("generated_at") or ""
    inicio = "Comece pela página 3 (desmatamento). " if L.estado("prodes") != "pendente" else ""
    comp = complemento(L)
    if comp.get("alertas_desde") and L.estado("alertas_mensais") == "nao_encontrado":
        inicio += "Sem alerta recente de desmatamento. "
    return (f"{inicio}Consultas de {f_data_brasilia(emitido)} em bases públicas oficiais; não substituem "
            "certidão de cartório, vistoria nem laudo.")


def cartoes_do_painel(L: Leitura) -> list:
    """Os seis cartões da p. 1. Toda linha leva situação; nenhuma depende de um fato que não existe."""
    cartoes = []
    # 1 · desmatamento
    e_prodes = L.estado("prodes")
    if e_prodes == "pendente":
        longo = curto = "Consulta pendente nesta emissão."
    else:
        valor = L.fato("prodes").get("valor") or {}
        occ = valor.get("occurrences") or []
        dentro = [o for o in occ if o.get("classe") == "dentro"]
        divisa = [o for o in occ if o.get("classe") == "divisa"]
        if dentro:
            pct = Decimal(str(valor.get("pct_car_dentro") or 0))
            maior = max(dentro, key=lambda o: o.get("area_inside_ha") or 0)
            longo = f"Sim. Vegetação nativa retirada em {f_pct(pct)} da área, {periodo_da_ocorrencia(maior)}."
            curto = f"Sim. {f_pct(pct)} da área, {periodo_da_ocorrencia(maior)}."
            depois = [o for o in occ if o.get("periodo_legal") == "depois_2019_07_31"]
            if depois:
                recente = depois[0]
                extra = (f" Depois de 31/07/2019, {f_ha(recente.get('area_inside_ha') or 0)} "
                         f"{'na divisa' if recente.get('classe') == 'divisa' else 'dentro'}, em {recente.get('year')}.")
                longo += extra
        elif divisa:
            longo = (f"Não dentro do imóvel. {f_inteiro(len(divisa))} "
                     f"{'mancha pega' if len(divisa) == 1 else 'manchas pegam'} só a divisa.")
            curto = f"Não dentro do imóvel; {f_inteiro(len(divisa))} só na divisa."
        else:
            longo = "Não. Nenhuma retirada de vegetação nativa mapeada" + alcance(L, "prodes", ", ") + "."
            curto = "Não. Nenhuma retirada mapeada dentro do imóvel."
    cartoes.append(("folha", "Tem desmatamento registrado?",
                    ("selo", e_prodes, L.atencao("prodes")) if e_prodes not in st.ESTADOS_SEM_SELO
                    else ("informativo",), longo, curto, 3))

    # 2 · embargo e auto de infração
    chaves = ("embargo_ibama", "autos")
    estado, at = pior_estado(L, chaves)
    n_emb = (L.fato("embargo_ibama").get("valor") or {}).get("quantidade") or 0
    if estado == "pendente":
        longo = curto = "Consulta pendente nesta emissão."
    elif n_emb:
        longo = (f"Sim. {f_inteiro(n_emb)} {'embargo' if n_emb == 1 else 'embargos'} do IBAMA com mapa "
                 "sobre a área.")
        curto = f"Sim. {f_inteiro(n_emb)} do IBAMA sobre a área."
    else:
        longo = ("Não. Nenhum embargo do IBAMA com mapa na base oficial de áreas embargadas, nem auto "
                 "de infração com local dentro dela.")
        curto = "Não. Nenhum embargo com mapa, nem auto com local no imóvel."
    cartoes.append(("carimbo", "Tem embargo ou auto de infração no local?",
                    ("selo", estado, at), longo, curto, 4))

    # 3 · terra indígena, unidade de conservação e mineração
    chaves = ("ti", "uc", "quilombola", "assentamento", "floresta_publica", "anm")
    estado, at = pior_estado(L, chaves)
    achados = [k for k in chaves if L.estado(k) == "encontrado"]
    pendentes_area = [k for k in chaves if L.estado(k) == "pendente"]
    respondidos_area = [k for k in chaves if L.estado(k) in ("encontrado", "nao_encontrado")]
    nomes = {"ti": "terra indígena", "uc": "unidade de conservação", "quilombola": "território quilombola",
             "assentamento": "assentamento", "floresta_publica": "floresta pública", "anm": "mineração"}
    n_anm = (L.fato("anm").get("valor") or {}).get("quantidade") or 0
    if achados:
        lista = ", ".join(nomes[k] for k in achados)
        longo = f"Sim. Apareceu {lista}" + (f" ({f_inteiro(n_anm)} na ANM)" if "anm" in achados else "") + "."
        curto = f"Sim: {lista}."
    elif not respondidos_area:
        longo = curto = "Consulta pendente nesta emissão."
    elif pendentes_area:
        falta = ", ".join(nomes[k] for k in pendentes_area)
        longo = f"Nada no que foi consultado. Falta a resposta de {falta}."
        curto = ("Nada no que foi consultado; falta a resposta de "
                 + (nomes[pendentes_area[0]] if len(pendentes_area) == 1
                    else f"{f_inteiro(len(pendentes_area))} consultas") + ".")
    else:
        longo = ("Não. Fora de terra indígena, unidade de conservação, território quilombola, assentamento "
                 "e floresta pública; nenhum processo na ANM.")
        curto = "Não. Nem quilombola, assentamento, floresta pública ou processo na ANM."
    cartoes.append(("escudo", "Está em terra indígena ou unidade de conservação, ou tem mineração?",
                    ("selo", estado, at), longo, curto, 4))

    # 4 · documentos
    e_sigef, e_snci = L.estado("sigef"), L.estado("snci")
    if "encontrado" in (e_sigef, e_snci):
        partes = []
        if e_sigef == "encontrado":
            partes.append("parcela certificada no SIGEF")
        if e_snci == "encontrado":
            partes.append("certificação no SNCI")
        longo = (f"O CAR mostra onde fica e o que foi declarado. O INCRA registra {' e '.join(partes)}. "
                 "O CAR não prova quem é o dono.")
        curto = f"O INCRA registra {' e '.join(partes)}. O CAR não prova o dono."
    elif "pendente" in (e_sigef, e_snci):
        longo = "O CAR mostra onde fica e o que foi declarado. A consulta ao INCRA ficou pendente."
        curto = "O CAR não prova o dono; consulta ao INCRA pendente."
    else:
        longo = ("O CAR mostra onde fica e o que foi declarado; não prova quem é o dono. O INCRA não "
                 "registra certificação sobre o imóvel.")
        curto = "O CAR não prova quem é o dono."
    cartoes.append(("documento", "O que os documentos provam?", ("informativo",), longo, curto, 5))

    # 5 · água
    cartoes.append(("gota", "Tem água?", ("informativo",), *frases_agua(L), 6))

    # 6 · terra e clima
    cartoes.append(("broto", "Como é a terra e o clima?", ("informativo",), *frases_terra_clima(L), 7))
    return cartoes


def frases_agua(L: Leitura) -> tuple[str, str]:
    partes_longo, partes_curto = [], []
    e_out = L.estado("outorga")
    if e_out == "encontrado":
        n = len(L.valor("outorga") or [])
        partes_longo.append(f"Há {f_inteiro(n)} {'captação registrada' if n == 1 else 'captações registradas'} "
                            "no cadastro de outorgas dentro do imóvel.")
        partes_curto.append(f"{f_inteiro(n)} {'captação' if n == 1 else 'captações'} com outorga.")
    elif e_out == "nao_encontrado":
        partes_longo.append("Nenhuma captação registrada no cadastro de outorgas dentro do imóvel.")
        partes_curto.append("Nenhuma captação com outorga dentro do imóvel.")
    elif e_out == "pendente":
        partes_longo.append("A consulta de outorgas ficou pendente.")
        partes_curto.append("Consulta de outorgas pendente.")
    e_poco = L.estado("pocos")
    if e_poco == "encontrado":
        v = L.valor("pocos") or {}
        nivel = (v.get("nivel_estatico_mediano_m") or {}).get("valor")
        if nivel:
            partes_longo.append(f"Nos poços cadastrados da região, a água fica a uns {f_metros(nivel)}.")
            partes_curto.append(f"Água a uns {f_metros(nivel)} nos poços da região.")
        elif v.get("pocos"):
            partes_longo.append(f"Há {f_inteiro(v['pocos'])} poços cadastrados na região.")
    elif e_poco == "pendente":
        partes_longo.append("A consulta de poços ficou pendente.")
    if not partes_longo:
        partes_longo.append("As consultas de água ficaram pendentes nesta emissão.")
        partes_curto.append("Consultas de água pendentes.")
    return " ".join(partes_longo), " ".join(partes_curto or partes_longo)


def frases_terra_clima(L: Leitura) -> tuple[str, str]:
    partes_longo, partes_curto = [], []
    if L.tem("mapbiomas"):
        classes = (L.valor("mapbiomas") or {}).get("classes") or []
        if classes:
            pcts = f_pct_composicao([x.get("pct") or 0 for x in classes])
            principal = max(range(len(classes)), key=lambda i: classes[i].get("ha") or 0)
            nomes = {"Pastagem": "pasto"}
            nome = nomes.get(classes[principal]["classe"], classes[principal]["classe"]).capitalize()
            partes_longo.append(f"{nome} na maior parte ({pcts[principal]}%).")
            partes_curto.append(f"{nome} ({pcts[principal]}%).")
    if L.tem("relevo"):
        rel = L.valor("relevo") or {}
        ate_8 = sum(x.get("pct") or 0 for x in rel.get("classes_embrapa_pct") or []
                    if x.get("classe") in ("plano", "suave ondulado"))
        terreno = "pouco inclinado" if ate_8 >= 70 else ("inclinado em parte" if ate_8 >= 30 else "inclinado")
        partes_longo.append(f"Terreno {terreno}.")
    if L.tem("textura_solo"):
        classe = (L.valor("textura_solo") or {}).get("classe_textural")
        if classe and classe in SOLO_ADJETIVO:
            partes_longo.append(f"Solo {SOLO_ADJETIVO[classe]}.")
    if L.tem("climatologia"):
        clima = L.valor("climatologia") or {}
        faixa = intervalo_de_meses(clima.get("meses_chuvosos") or [])
        seca = intervalo_de_meses(clima.get("meses_secos") or [])
        if faixa:
            partes_longo.append(f"Chove de {faixa[0]} a {faixa[1]}" +
                                (f"; quase nada de {seca[0]} a {seca[1]}." if seca else "."))
            partes_curto.append(f"Chuvas de {faixa[0]} a {faixa[1]}.")
    if not partes_longo:
        return ("As consultas de terra e clima ficaram pendentes nesta emissão.",
                "Consultas de terra e clima pendentes.")
    return " ".join(partes_longo), " ".join(partes_curto or partes_longo)


def intervalo_de_meses(meses: list) -> tuple[str, str] | None:
    """Meses contíguos, podendo virar o ano (11, 12, 1, 2, 3). None quando não forem contíguos."""
    ordem = sorted(int(m) for m in meses if m)
    if not ordem:
        return None
    for i in range(len(ordem)):
        seq = ordem[i:] + ordem[:i]
        if all((seq[j] % 12) + 1 == seq[j + 1] for j in range(len(seq) - 1)):
            return MESES[seq[0] - 1], MESES[seq[-1] - 1]
    return MESES[ordem[0] - 1], MESES[ordem[-1] - 1]


# ===========================================================================
# PÁGINA 2 — antes de decidir (seção 2.2)
# ===========================================================================
PERFIL_ROTULO = {"comprar": "Se vai comprar:", "financiar": "Se vai financiar:",
                 "produzir": "Se vai produzir:", "qualquer": "Para qualquer decisão:"}


def itens_de_conferencia(L: Leitura) -> list[tuple[str, str, str, str]]:
    """(perfil, o que fazer, por quê, onde) — sai do estado dos fatos, nunca de uma lista fixa."""
    itens: list[tuple[str, str, str, str]] = []
    pendentes = [k for k in L.fatos if L.estado(k) == "pendente"]
    if pendentes:
        # o nome do assunto, não o da fonte cortada: "Floresta pública" diz mais que "SGB"
        nomes = [NOME_DO_FATO.get(k, k) for k in pendentes[:4]]
        resto = len(pendentes) - len(nomes)
        lista = ", ".join(dict.fromkeys(nomes)) + (f" e mais {f_inteiro(resto)}" if resto > 0 else "")
        itens.append(("qualquer", "Refazer a consulta pendente",
                      f"Ficou sem resposta nesta emissão: {lista}. Peça uma nova emissão antes de decidir.",
                      "Apêndice B"))
    if L.atencao("prodes"):
        itens.append(("financiar", "Conferir a regra do crédito rural",
                      "Há registro de retirada de vegetação nativa. O banco confere isso numa lista "
                      "oficial feita com o mesmo mapa.", "página 3"))
    if L.atencao("embargo_ibama") or L.atencao("embargo_icmbio"):
        itens.append(("qualquer", "Conferir o embargo no órgão ambiental",
                      "Embargo é medida do órgão sobre a área, e vale enquanto não for levantado.",
                      "página 4"))
    if L.atencao("anm"):
        itens.append(("comprar", "Ver o processo de mineração na ANM",
                      "Processo registrado sobre a área pode limitar o uso do terreno.", "página 4"))
    itens.append(("comprar", "Pedir a certidão da matrícula no cartório",
                  "O CAR diz onde fica e o que foi declarado; só a matrícula prova quem é o dono.",
                  "página 5"))
    if L.estado("outorga") == "encontrado":
        itens.append(("produzir", "Conferir a validade da outorga de água",
                      "A outorga tem prazo, e o que vale é o que está no ato do órgão.", "página 6"))
    elif L.estado("outorga") == "nao_encontrado":
        itens.append(("produzir", "Pedir a outorga antes de irrigar",
                      "Não apareceu captação registrada dentro do imóvel no cadastro de outorgas.",
                      "página 6"))
    if L.tem("relevo"):
        rel = L.valor("relevo") or {}
        if (rel.get("pct_25_45_graus") or 0) > 0 or (rel.get("pct_acima_45_graus") or 0) > 0:
            itens.append(("produzir", "Olhar as partes mais inclinadas",
                          "Parte do imóvel tem inclinação que a lei trata de forma própria.", "página 8"))
    itens.append(("qualquer", "Visitar o imóvel com quem entende",
                  "Tudo aqui vem de base pública e de satélite; nada substitui ver a terra de perto.", ""))
    return itens


def pagina_2(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 2
    blocos: list[Bloco] = []
    h1 = texto("Antes de decidir", "h1", LARG)
    dek = texto("O que conferir antes de assinar, financiar ou plantar — e onde está cada resposta "
                "neste relatório.", "dek", G.leitura)
    alt_cab = 5 * mm + 2.5 * mm + h1.altura + 2 * mm + 0.8 + 3.2 * mm + dek.altura

    def cabeca(c, x, y_topo, h):
        kicker(c, x, y_topo, "Sua conferência", COR["TERRA"], "lupa")
        y = y_topo - 5 * mm - 2.5 * mm
        y -= h1.desenhar(c, x, y)
        y -= 2 * mm
        c.saveState()
        c.setStrokeColor(COR["TERRA"])
        c.setLineWidth(0.8)
        c.line(x, y, x + 40 * mm, y)
        c.restoreState()
        dek.desenhar(c, x, y - 3.2 * mm)

    blocos.append(Bloco("cabeca", alt_cab, cabeca, espaco=0))

    itens = itens_de_conferencia(L)
    por_perfil: dict[str, list] = {}
    for perfil, o_que, por_que, onde in itens:
        por_perfil.setdefault(perfil, []).append((o_que, por_que, onde))
    ordem = [p for p in ("qualquer", "comprar", "financiar", "produzir") if p in por_perfil]
    for perfil in ordem:
        linhas = por_perfil[perfil]
        rot = PERFIL_ROTULO[perfil]
        cartoes = []
        for o_que, por_que, onde in linhas:
            t_o = texto(o_que, "conf_o_que", LARG - 44 * mm)
            t_p = texto(por_que, "conf_por_que", LARG - 44 * mm)
            t_w = texto(onde, "ref_pagina", 40 * mm) if onde else None
            cartoes.append((t_o, t_p, t_w, t_o.altura + 1 * mm + t_p.altura))
        altura = st.ESTILO["kicker"].leading + 2.4 * mm + sum(x[3] for x in cartoes) + \
            G.us(1) * max(0, len(cartoes) - 1)

        def desenhar(c, x, y_topo, h, rot=rot, cartoes=cartoes):
            rotulo(c, x, y_topo - st.ESTILO["kicker"].fontSize, rot, "kicker", COR["FOREST"])
            y = y_topo - st.ESTILO["kicker"].leading - 2.4 * mm
            for i, (t_o, t_p, t_w, alt) in enumerate(cartoes):
                if i:
                    y -= G.us(1)
                    c.saveState()
                    c.setStrokeColor(COR["RULE"])
                    c.setLineWidth(G.fio_fino)
                    c.line(x, y + G.us(0.5), x + LARG, y + G.us(0.5))
                    c.restoreState()
                c.saveState()
                c.setFillColor(COR["TERRA"])
                c.circle(x + 1.4 * mm, y - 3 * mm, 1.1 * mm, stroke=0, fill=1)
                c.restoreState()
                yy = y
                yy -= t_o.desenhar(c, x + 6 * mm, yy) + 1 * mm
                t_p.desenhar(c, x + 6 * mm, yy)
                if t_w:
                    t_w.desenhar(c, x + LARG - 40 * mm, y)
                y -= alt

        blocos.append(Bloco(f"perfil_{perfil}", altura, desenhar, espaco=2))

    nao_responde = ("Este relatório não diz quem é o dono, não mede o imóvel em campo e não dá laudo "
                    "ambiental. Ele reúne o que as bases públicas oficiais respondem hoje sobre esta terra.")
    alt_cx, des_cx = caixa_significado([(None, nao_responde)])
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 2, None, grade)


# ===========================================================================
# PÁGINA 3 — capítulo 1, desmatamento (seção 2.3)
# ===========================================================================
def pagina_3(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 3
    blocos: list[Bloco] = []
    fato = L.fato("prodes")
    estado = fato["estado"]
    nota_fonte = nota_de_fonte(L, "prodes")

    if estado == "pendente":
        abrir_capitulo(L, "desmatamento", "prodes", frase_pendente(L, "prodes"), nota_fonte, blocos)
        alt_cx, des_cx = caixa_significado([
            (None, "Sem esta consulta, o relatório não afirma que há nem que não há retirada de "
                   "vegetação nativa. Peça uma nova emissão antes de decidir.")])
        blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
        return fechar_pagina(c, L, blocos, 3, "desmatamento", grade)

    valor = fato.get("valor") or {}
    occ = valor.get("occurrences") or []
    dentro = [o for o in occ if o.get("classe") == "dentro"]
    divisa = sorted([o for o in occ if o.get("classe") == "divisa"], key=lambda o: str(o.get("image_date")))
    numero = {o["id"]: i + 1 for i, o in enumerate(divisa)}
    area_car = Decimal(str(valor.get("area_car_ha") or (L.p.get("property") or {}).get("area_ha") or 0))
    pct_dentro = Decimal(str(valor.get("pct_car_dentro") or 0))
    bioma = (L.fato("bioma").get("valor") or {}).get("bioma") if L.tem("bioma") else None

    if dentro:
        maior = max(dentro, key=lambda o: o.get("area_inside_ha") or 0)
        resposta_longa = (f"Sim. {maiuscula_inicial(f_faixa_parte(pct_dentro))} do imóvel — {f_ha(valor.get('union_inside_ha') or 0)} "
                          f"— teve a vegetação nativa retirada, {periodo_da_ocorrencia(maior)}.")
        resposta_curta = (f"Sim. {f_ha(valor.get('union_inside_ha') or 0)} do imóvel "
                          f"({f_pct(pct_dentro)}), {periodo_da_ocorrencia(maior)}.")
        if divisa:
            resposta_longa += (f" Outras {f_inteiro(len(divisa))} manchas pegam só a divisa e não contam."
                               if len(divisa) > 1 else " Outra mancha pega só a divisa e não conta.")
    elif divisa:
        resposta_longa = (f"Não dentro do imóvel. {f_inteiro(len(divisa))} "
                          f"{'mancha mapeada cruza' if len(divisa) == 1 else 'manchas mapeadas cruzam'} o limite "
                          "em faixas estreitas, que não contam como retirada dentro da área.")
        resposta_curta = f"Não dentro do imóvel; {f_inteiro(len(divisa))} só na divisa."
    else:
        resposta_longa = ("Não. Nenhuma retirada de vegetação nativa mapeada dentro do imóvel"
                          + alcance(L, "prodes", ", ") + ".")
        resposta_curta = "Não. Nenhuma retirada mapeada dentro do imóvel."
    abrir_capitulo(L, "desmatamento", "prodes", (resposta_longa, resposta_curta), nota_fonte, blocos)

    # --- mapa das manchas ------------------------------------------------------------------------
    geom = L.geometria
    figuras = L.book.get("prodes_figuras") or {}
    img = L.imagem_base()
    # O mapa entra mesmo sem mancha: ver a terra com o limite desenhado é a prova visual do "não".
    if geom:
        cap_mapa = texto(legenda_do_mapa(L, dentro, divisa), "legenda", LARG)
        mapa_min, mapa_max = 50 * mm, 104 * mm
        fixo = 1.8 * mm + cap_mapa.altura

        def mapa_bloco(c, x, y_topo, h):
            ih = h - fixo
            bb = geom["bbox_m"]
            folga = max(bb[2] - bb[0], bb[3] - bb[1]) * 0.16
            largura_m = max(bb[2] - bb[0] + folga * 2, (bb[3] - bb[1] + folga * 2) * LARG / ih)
            if img:
                lim = img["bounds_m"]
                caixa = caixa_na_proporcao(((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2),
                                           min(largura_m, lim[2] - lim[0]), ih / LARG, lim)
            else:
                caixa = caixa_na_proporcao(((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2), largura_m, ih / LARG,
                                           (bb[0] - 1e6, bb[1] - 1e6, bb[2] + 1e6, bb[3] + 1e6))
            mapa = Mapa(x, y_topo - ih, LARG, ih, caixa)
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(x, y_topo - ih, LARG, ih, 3 * mm)
            c.clipPath(clip, stroke=0, fill=0)
            fundo = img.get("path_muted") or img.get("path") if img else None
            if fundo:
                foto = recorte_imagem(L.imagem(fundo), img["bounds_m"], caixa, int(LARG / 72 * 220))
                buf = io.BytesIO()
                foto.save(buf, "JPEG", quality=84, optimize=True)
                buf.seek(0)
                c.drawImage(ImageReader(buf), x, y_topo - ih, LARG, ih)
            else:
                c.setFillColor(COR["CREAM"])
                c.rect(x, y_topo - ih, LARG, ih, stroke=0, fill=1)
            desenhar_manchas(c, mapa, occ, figuras, geom)
            for o in divisa:
                fig = figuras.get(o["id"])
                if not fig:
                    continue
                pontos = pontos_do_geom(fig.get("inside_geom_m") or fig["feature_geom_m"])
                if not pontos:
                    continue
                cx = sum(e for e, _ in pontos) / len(pontos)
                cy = sum(n for _, n in pontos) / len(pontos)
                px, py = mapa.pt(cx, cy)
                marcador_numero(c, px, py, numero[o["id"]], COR[PERIODO_COR[o["periodo_legal"]]])
            c.restoreState()
            escala_e_norte(c, mapa, x + 2.5 * mm, y_topo - ih + 2.5 * mm)
            cap_mapa.desenhar(c, x, y_topo - ih - 1.8 * mm)

        blocos.append(Bloco("mapa", mapa_min + fixo, mapa_bloco,
                            mola=(mapa_min + fixo, mapa_max + fixo), espaco=1))

    # --- legenda por período da lei ---------------------------------------------------------------
    periodos = sorted({o["periodo_legal"] for o in occ}, key=lambda k: PERIODO_ORDEM[k])
    if periodos:
        partes = []
        for periodo in periodos:
            do_periodo = [o for o in occ if o["periodo_legal"] == periodo]
            soma = sum(Decimal(str(o.get("area_inside_ha") or 0)) for o in do_periodo)
            partes.append((PERIODO_ROTULO[periodo], float(soma / (area_car or 1) * 100),
                           st.HEX[PERIODO_COR[periodo]]))
        alt, des = barra_composicao(partes)
        blocos.append(Bloco("legenda_periodos", alt, des, espaco=1, prioridade=3))

    # --- tabela das ocorrências -------------------------------------------------------------------
    # Imóvel com muitas manchas: a tabela mostra as maiores e DIZ quantas são ao todo (§2.0.1,
    # "mostramos N de M"). Cortar sem avisar seria esconder parte do que a fonte respondeu.
    TETO_LINHAS = 7
    maiores = sorted(occ, key=lambda o: -(o.get("area_inside_ha") or 0))[:TETO_LINHAS]
    linhas = []
    for o in sorted(maiores, key=lambda o: str(o.get("image_date"))):
        onde = "Dentro" if o["classe"] == "dentro" else f"Divisa ({numero.get(o['id'], '')})"
        largura = o.get("inner_mean_width_m")
        linhas.append([str(o.get("year") or ""),
                       periodo_da_ocorrencia(o).replace("entre ", "de ").replace(" e ", " a "),
                       onde,
                       f_metros(largura) if o["classe"] == "divisa" and largura else "",
                       f_ha(o.get("area_inside_ha") or 0)])
    if linhas:
        nota_tab = str((fato.get("valor") or {}).get("metodo_area") or "")
        if len(occ) > len(linhas):
            nota_tab = (f"Mostramos as {f_inteiro(len(linhas))} maiores manchas, de "
                        f"{f_inteiro(len(occ))} ao todo. " + nota_tab)
        # colunas curtas de propósito: texto comprido em célula estreita dobrava a altura da tabela
        alt, des = tabela([("Ano", 16 * mm), ("Período percebido", 48 * mm), ("Onde", 30 * mm),
                           ("Largura na divisa", 34 * mm), ("Área dentro", 42 * mm)], linhas,
                          nota=nota_tab, numericas=(3, 4))
        blocos.append(Bloco("tabela_occ", alt, des, espaco=1, prioridade=2))

    # --- regra do crédito rural -------------------------------------------------------------------
    depois = [o for o in occ if o.get("periodo_legal") == "depois_2019_07_31"]
    if depois and L.tem("regra_credito"):
        regra = L.valor("regra_credito") or {}
        frase = frase_credito(L, regra, bioma, depois)
        t = texto(frase, "regra", G.leitura)
        altura_regra = t.altura + 4 * mm

        def regra_bloco(c, x, y_topo, h, t=t):
            c.saveState()
            c.setFillColor(COR["TERRA_LIGHT"])
            c.rect(x, y_topo - h, LARG, h, stroke=0, fill=1)
            c.restoreState()
            t.desenhar(c, x + 4 * mm, y_topo - 2 * mm)

        blocos.append(Bloco("regra_credito", altura_regra, regra_bloco, espaco=1))

    alt_cx, des_cx = caixa_significado([(None, significado_desmatamento(L, dentro, divisa, depois))])
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 3, "desmatamento", grade)


def pontos_do_geom(geom: dict) -> list[tuple[float, float]]:
    tipo = geom.get("type")
    if tipo == "Polygon":
        aneis = geom["coordinates"]
    elif tipo == "MultiPolygon":
        aneis = [anel for poli in geom["coordinates"] for anel in poli]
    else:
        return []
    return [(float(x), float(y)) for anel in aneis for x, y in anel]


def legenda_do_mapa(L: Leitura, dentro: list, divisa: list) -> str:
    if not dentro and not divisa:
        return ("Linha clara: limite declarado no CAR. Nenhuma mancha do mapa de desmatamento cai "
                "sobre esta área nas camadas consultadas.")
    partes = []
    if dentro:
        partes.append("Área cheia: a parte da mancha que cai dentro do limite declarado no CAR")
    if divisa:
        partes.append("números: manchas que cruzam a divisa em faixa estreita")
    partes.append("linha tracejada: a mancha inteira, que segue fora do imóvel")
    return maiuscula_inicial("; ".join(partes)) + ". A cor mostra o período da lei."


def frase_credito(L: Leitura, regra: dict, bioma: str | None, depois: list) -> str:
    """Frase da Res. CMN 5.303/2026: o verbo segue a data de emissão, e o resultado não é afirmado."""
    emitido = str(L.p.get("generated_at") or "")[:10]
    inicio = str(regra.get("data_inicio") or "")
    faixa = regra.get("faixa_texto")
    base = ("Para crédito rural, o banco vai ter de conferir, numa lista do Ministério do Meio Ambiente "
            "feita com o mesmo mapa, se houve retirada de vegetação nativa depois de 31/07/2019.")
    if faixa and inicio:
        if emitido and emitido >= inicio:
            base += f" Para imóvel {faixa}, como este, a conferência vale desde {f_data_extenso(inicio)}"
        else:
            base += f" Para imóvel {faixa}, como este, a conferência começa em {f_data_extenso(inicio)}"
        base += " (Resolução CMN 5.303/2026)."
    limite = LIMITE_LISTA_MMA_HA.get(str(bioma or ""))
    if limite is not None:
        soma = sum(Decimal(str(o.get("area_inside_ha") or 0)) for o in depois if o.get("classe") == "dentro")
        if soma < limite:
            base += (f" A lista oficial do {bioma} só entra com soma a partir de {f_ha(limite)}; "
                     "o livro não afirma o que a lista traz.")
    return base


def significado_desmatamento(L: Leitura, dentro: list, divisa: list, depois: list) -> str:
    partes = []
    if dentro and all(o.get("periodo_legal") == "ate_2008_07_22" for o in dentro):
        partes.append("A retirada dentro do imóvel é anterior a 22/07/2008, data que a lei ambiental usa "
                      "como referência para áreas já abertas.")
    if depois:
        partes.append("Há registro depois de 31/07/2019: é o que o banco confere no crédito rural.")
    if divisa and not dentro:
        partes.append("As manchas da divisa são faixas mais estreitas que um ponto do satélite: elas "
                      "aparecem no mapa, mas não contam como retirada dentro do imóvel.")
    if not partes:
        partes.append("O mapa do INPE não registra retirada de vegetação nativa dentro deste imóvel.")
    partes.append("O mapa mostra onde a vegetação saiu; ele não diz se houve autorização.")
    return " ".join(partes)


def f_data_flexivel(valor: str) -> str:
    """Data da base da fonte: dia completo, mês/ano ou só o ano — como a fonte publica, sem completar."""
    bruto = str(valor or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", bruto):
        return f_data(bruto)
    if re.fullmatch(r"\d{4}-\d{2}", bruto):
        return f_mes_ano(bruto + "-01")
    if re.fullmatch(r"\d{4}", bruto):
        return bruto
    return bruto


def nota_de_fonte(L: Leitura, chave: str) -> str:
    """Nota da margem da cabeça de capítulo: fonte, data da base e alcance, sem jargão."""
    e = L.fato(chave)
    fonte = e.get("fonte") or {}
    if fonte.get("somente_apendice"):
        nome = "Consulta pública oficial"
    else:
        nome = str(fonte.get("nome_publico") or "Fonte pública oficial")
    partes = [nome]
    if e.get("data_base"):
        partes.append(f"base de {f_data_flexivel(str(e['data_base']))}")
    consultado = e.get("consultado_em") or L.p.get("generated_at")
    if consultado:
        partes.append(f"consultada em {f_data_brasilia(str(consultado))}")
    limites = e.get("limites") or []
    if limites:
        partes.append(str(limites[0]))
    return ". ".join(maiuscula_inicial(x) for x in partes) + "."


# ===========================================================================
# PÁGINA 4 — capítulo 2, embargo, área protegida e mineração (seção 2.4)
# ===========================================================================
# (chave do fato, nome na linha, ícone, nome no meio de uma frase). O nome de frase é escrito à mão
# porque ``str.lower`` estragava a sigla: "embargo do ibama", "processo na anm".
ITENS_RESTRICAO = (
    ("embargo_ibama", "Embargo do IBAMA", "carimbo", "embargo do IBAMA"),
    ("embargo_icmbio", "Embargo do ICMBio", "carimbo", "embargo do ICMBio"),
    ("autos", "Auto de infração com local no imóvel", "documento", "auto de infração com local no imóvel"),
    ("ti", "Terra indígena", "escudo", "terra indígena"),
    ("uc", "Unidade de conservação", "escudo", "unidade de conservação"),
    ("quilombola", "Território quilombola", "escudo", "território quilombola"),
    ("assentamento", "Assentamento", "escudo", "assentamento"),
    ("floresta_publica", "Floresta pública", "folha", "floresta pública"),
    ("anm", "Processo de mineração na ANM", "camadas", "processo de mineração na ANM"),
    ("sitio_arqueologico", "Sítio arqueológico cadastrado", "pino", "sítio arqueológico cadastrado"),
)


def pagina_4(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 4
    blocos: list[Bloco] = []
    chaves = tuple(k for k, _, _, _ in ITENS_RESTRICAO)
    achados = [k for k in chaves if L.estado(k) == "encontrado"]
    pendentes = [k for k in chaves if L.estado(k) == "pendente"]
    estado, at = pior_estado(L, chaves)
    nomes = {k: frase for k, _, _, frase in ITENS_RESTRICAO}

    respondidos = [k for k in chaves if L.estado(k) in ("encontrado", "nao_encontrado")]
    if achados:
        lista = ", ".join(nomes[k] for k in achados)
        resposta = (f"Sim. Apareceu: {lista}.", f"Sim: {lista}.")
    elif not respondidos:
        resposta = frase_pendente(L, pendentes[0] if pendentes else "ti")
    elif pendentes:
        # Uma fonte pendente não vira "Não": a resposta diz o que respondeu e o que ficou faltando.
        faltando = ", ".join(nomes[k] for k in pendentes)
        resposta = (f"Nada apareceu no que foi consultado. Ficou sem resposta: {faltando}. Enquanto essa "
                    "consulta não é refeita, o relatório não afirma que não há registro.",
                    f"Nada no que foi consultado; sem resposta ainda: {faltando}.")
    else:
        resposta = ("Não. Nenhum embargo, área protegida ou processo de mineração apareceu sobre o imóvel "
                    "nas consultas desta emissão.",
                    "Não. Nenhum embargo, área protegida ou mineração sobre o imóvel.")
    principal = achados[0] if achados else (pendentes[0] if pendentes else "ti")
    abrir_capitulo(L, "restricoes", principal, resposta, nota_restricoes(L, achados, pendentes), blocos)

    # --- quadro de itens, um por linha, cada um com a própria situação ----------------------------
    linhas_quadro = []
    for chave, nome, nome_icone, _frase in ITENS_RESTRICAO:
        e = L.estado(chave)
        if e == "nao_se_aplica":
            continue
        detalhe = detalhe_do_item(L, chave)
        linhas_quadro.append(linha_item(nome, e, L.atencao(chave), detalhe, LARG, nome_icone))
    if linhas_quadro:
        altura_q = sum(a for a, _ in linhas_quadro) + G.us(1.2) * (len(linhas_quadro) - 1)

        def quadro(c, x, y_topo, h, linhas_quadro=linhas_quadro):
            y = y_topo
            for i, (alt, des) in enumerate(linhas_quadro):
                if i:
                    y -= G.us(1.2)
                    c.saveState()
                    c.setStrokeColor(COR["RULE"])
                    c.setLineWidth(G.fio_fino)
                    c.line(x, y + G.us(0.6), x + LARG, y + G.us(0.6))
                    c.restoreState()
                des(c, x, y, alt)
                y -= alt

        blocos.append(Bloco("quadro", altura_q, quadro, espaco=1))

    # --- achados em tabela -------------------------------------------------------------------------
    if L.estado("embargo_ibama") == "encontrado":
        itens = (L.valor("embargo_ibama") or {}).get("itens") or []
        linhas = []
        for item in itens:
            if not isinstance(item, dict):
                continue
            linhas.append([str(item.get("tad") or ""), str(item.get("date") or ""),
                           str(item.get("type") or ""),
                           f_ha(item.get("area_in_property_ha") or 0)])
        if linhas:
            alt, des = tabela([("Termo", 34 * mm), ("Data", 28 * mm), ("Motivo", 68 * mm),
                               ("Área no imóvel", 40 * mm)], linhas,
                              nota="Embargo é uma medida do órgão ambiental sobre a área. "
                                   "Auto de infração é lavrado contra pessoa ou empresa; esta busca é pelo local.",
                              numericas=(3,))
            blocos.append(Bloco("tabela_embargos", alt, des, espaco=1, prioridade=1))

    if L.estado("anm") == "encontrado":
        v = L.valor("anm") or {}
        n = v.get("quantidade") or 0
        area = v.get("area_sobreposta_ha")
        itens_destaque = [(f_inteiro(n), "processos" if n != 1 else "processo", "registrados na ANM sobre a área")]
        if area:
            itens_destaque.append((f_ha(area), "", "de sobreposição com o imóvel"))
        alt, des = destaques(itens_destaque)
        blocos.append(Bloco("anm_numeros", alt, des, espaco=1, prioridade=4))

    if L.estado("sitio_arqueologico") in ("encontrado", "nao_encontrado"):
        v = L.valor("sitio_arqueologico") or {}
        perto = v.get("vizinhanca_quantidade") or 0
        if perto:
            t = texto(f"Há {f_inteiro(perto)} {'sítio cadastrado' if perto == 1 else 'sítios cadastrados'} "
                      "no IPHAN na vizinhança, fora do imóvel. Sítio próximo não é restrição dentro da área.",
                      "corpo", LARG)
            blocos.append(Bloco("iphan_perto", t.altura, lambda c, x, y, h, t=t: t.desenhar(c, x, y), espaco=1, prioridade=5))

    alt_cx, des_cx = caixa_significado([(None, significado_restricoes(L, achados, pendentes))])
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 4, "restricoes", grade)


def nota_restricoes(L: Leitura, achados: list, pendentes: list) -> str:
    """Nota da margem da p. 4: quantas camadas responderam, não o nome de uma só delas."""
    chaves = tuple(k for k, _, _, _ in ITENS_RESTRICAO)
    respondidos = [k for k in chaves if L.estado(k) in ("encontrado", "nao_encontrado")]
    partes = [f"{f_inteiro(len(respondidos))} de {f_inteiro(len([k for k in chaves if L.estado(k) not in st.ESTADOS_SEM_SELO]))} "
              f"camadas responderam nesta emissão"]
    partes.append(f"consultadas em {f_data_brasilia(str(L.p.get('generated_at') or ''))}")
    partes.append("cada linha abaixo traz o alcance da própria consulta")
    return ". ".join(maiuscula_inicial(x) for x in partes) + "."


def detalhe_do_item(L: Leitura, chave: str) -> str:
    e = L.fato(chave)
    estado = e["estado"]
    if estado == "pendente":
        return NOTA_PENDENTE
    if estado in st.ESTADOS_SEM_SELO:
        return ""
    valor = e.get("valor")
    limites = e.get("limites") or []
    if estado == "nao_encontrado":
        return maiuscula_inicial(str(limites[0])) + "." if limites else ""
    if isinstance(valor, dict):
        n = valor.get("quantidade")
        if chave == "sitio_arqueologico":
            n = valor.get("dentro_quantidade")
        if n:
            area = valor.get("area_ha") or valor.get("area_sobreposta_ha")
            return (f"{f_inteiro(n)} {'registro' if n == 1 else 'registros'}"
                    + (f" · {f_ha(area)} dentro do imóvel" if area else ""))
    return ""


def significado_restricoes(L: Leitura, achados: list, pendentes: list) -> str:
    partes = []
    if "embargo_ibama" in achados or "embargo_icmbio" in achados:
        partes.append("Embargo é uma medida do órgão ambiental sobre a área, e vale enquanto não for "
                      "levantado. Confira o termo no próprio órgão.")
    if "anm" in achados:
        partes.append("Processo de mineração é um pedido ou uma autorização registrada na ANM; ele não "
                      "diz, por si só, que existe lavra no local.")
    if not achados:
        partes.append("Nenhuma dessas camadas desenhou algo sobre o imóvel nas consultas desta emissão. "
                      "Cada consulta tem o alcance escrito ao lado dela.")
    if pendentes:
        partes.append("As consultas pendentes ficam sem resposta aqui e entram na página 2.")
    return " ".join(partes)


# ===========================================================================
# PÁGINA 5 — capítulo 3, o que os documentos provam (seção 2.5)
# ===========================================================================
# Códigos de tipo do SICAR, escritos como o cliente lê (guia de redação: nada de sigla solta).
TIPO_IMOVEL_CAR = {
    "IRU": "imóvel rural", "AST": "assentamento de reforma agrária",
    "PCT": "território de povo ou comunidade tradicional",
}


def pagina_5(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 5
    blocos: list[Bloco] = []
    prop = L.p.get("property") or {}
    car = L.p.get("car") or {}
    resposta = ("O CAR mostra onde o imóvel fica e o que o dono declarou. Ele não prova quem é o dono: "
                "isso só a matrícula do cartório faz.",
                "O CAR mostra onde fica e o que foi declarado; só a matrícula prova o dono.")
    abrir_capitulo(L, "documentos", "car", resposta, nota_de_fonte(L, "car"), blocos)

    # --- três documentos, lado a lado -------------------------------------------------------------
    docs = [
        ("CAR", "Onde fica e o que foi declarado",
         "Consultado nesta emissão" if L.tem("car") else "Consulta pendente",
         L.estado("car")),
        ("SIGEF e SNCI", "Se o INCRA já certificou o limite",
         texto_certificacao(L), pior_estado(L, ("sigef", "snci"))[0]),
        ("Matrícula", "Quem é o dono e o que pesa sobre o imóvel",
         "Não faz parte desta edição; peça a certidão no cartório", "fora_desta_edicao"),
    ]
    col = (LARG - 2 * G.calha) / 3
    cartoes = []
    for nome, responde, situacao_txt, estado_doc in docs:
        t_nome = texto(nome, "doc_nome", col - 4 * mm)
        t_resp = texto(responde, "legenda", col - 4 * mm)
        t_sit = texto(situacao_txt, "quadro_frase", col - 4 * mm)
        cartoes.append((t_nome, t_resp, t_sit, estado_doc))
    altura_docs = (max(t[0].altura for t in cartoes) + 1.6 * mm + max(t[1].altura for t in cartoes)
                   + 3 * mm + st.SELO_ALTURA + 2.4 * mm + max(t[2].altura for t in cartoes) + 6 * mm)

    def diagrama(c, x, y_topo, h):
        for i, (t_nome, t_resp, t_sit, estado_doc) in enumerate(cartoes):
            cx = x + i * (col + G.calha)
            c.saveState()
            c.setFillColor(COR["CREAM"])
            c.roundRect(cx, y_topo - h, col, h, 2 * mm, stroke=0, fill=1)
            c.restoreState()
            y = y_topo - 4 * mm
            y -= t_nome.desenhar(c, cx + 2 * mm, y) + 1.6 * mm
            y -= t_resp.desenhar(c, cx + 2 * mm, y) + 3 * mm
            if estado_doc in st.ESTADOS_SEM_SELO:
                cap = altura_maiuscula(st.ESTILO["selo_rotulo"].fontName, st.ESTILO["selo_rotulo"].fontSize)
                rotulo(c, cx + 2 * mm, y - st.SELO_ALTURA / 2 - cap / 2, "Fora desta edição", "selo_rotulo",
                       COR["MUTED"])
            else:
                st.desenhar_selo(c, cx + 2 * mm, y - st.SELO_ALTURA, estado_doc, False)
            y -= st.SELO_ALTURA + 2.4 * mm
            t_sit.desenhar(c, cx + 2 * mm, y)

    blocos.append(Bloco("diagrama", altura_docs, diagrama, espaco=1))

    # --- ficha do CAR -------------------------------------------------------------------------------
    linhas = []
    if prop.get("car_code"):
        linhas.append(["Código do CAR", str(prop["car_code"])])
    if car.get("status"):
        linhas.append(["Situação do cadastro",
                       {"AT": "Ativo", "PE": "Pendente", "SU": "Suspenso",
                        "CA": "Cancelado"}.get(str(car["status"]).upper(), str(car["status"]))])
    if car.get("analysis_status"):
        linhas.append(["Condição", str(car["analysis_status"])])
    if car.get("type"):
        # o código do SICAR não vai para o cliente: ele lê o nome, não a sigla do sistema
        linhas.append(["Tipo do imóvel no CAR", TIPO_IMOVEL_CAR.get(str(car["type"]).upper(),
                                                                    "não informado pelo cadastro")])
    if prop.get("area_ha"):
        linhas.append(["Área declarada", f_ha(prop["area_ha"])])
    if car.get("fiscal_modules") is not None:
        linhas.append(["Módulos fiscais", _ptbr(_q(Decimal(str(car["fiscal_modules"])), 2))])
    if linhas:
        alt, des = tabela([("Campo", 60 * mm), ("O que o CAR declara", 110 * mm)], linhas,
                          nota="O CAR é uma declaração do dono ao órgão ambiental. O órgão confere depois.")
        blocos.append(Bloco("ficha", alt, des, espaco=1, prioridade=2))

    # --- referência em outro cadastro (regra 4 do dono) --------------------------------------------
    referencias = []
    for chave, rotulo_ref in (("sigef", "SIGEF (INCRA)"), ("snci", "SNCI (INCRA)")):
        if L.estado(chave) != "encontrado":
            continue
        v = L.valor(chave) or {}
        parte = v.get("parte_do_car_pct")
        detalhe = f"{f_inteiro(v.get('parcelas') or 0)} registro no acervo do INCRA"
        if parte:
            detalhe += f", cobrindo {f_pct(parte)} do limite declarado no CAR"
        referencias.append((rotulo_ref, detalhe))
    if referencias:
        t_titulo = texto("Referência em outro cadastro", "h2", LARG)
        corpo = texto("; ".join(f"<b>{r}</b>: {d}" for r, d in referencias) +
                      ". Nome ou código de outro cadastro é referência, e não o nome do CAR.",
                      "corpo", LARG)
        altura_ref = t_titulo.altura + 1.6 * mm + corpo.altura

        def ref_bloco(c, x, y_topo, h):
            y = y_topo - t_titulo.desenhar(c, x, y_topo) - 1.6 * mm
            corpo.desenhar(c, x, y)

        blocos.append(Bloco("referencia", altura_ref, ref_bloco, espaco=1, prioridade=3))

    alt_cx, des_cx = caixa_significado([
        ("Se vai comprar:", "peça a certidão da matrícula no cartório do imóvel. O CAR e o INCRA não "
                            "provam quem é o dono nem o que pesa sobre a terra."),
        ("Se vai financiar:", "o banco costuma pedir a matrícula e o CAR ativo. As duas coisas são "
                              "conferidas no órgão que as emite.")], colunas=True)
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 5, "documentos", grade)


def texto_certificacao(L: Leitura) -> str:
    e_sigef, e_snci = L.estado("sigef"), L.estado("snci")
    if "encontrado" in (e_sigef, e_snci):
        quais = [n for n, k in (("SIGEF", "sigef"), ("SNCI", "snci")) if L.estado(k) == "encontrado"]
        return f"Há registro no {' e no '.join(quais)}, na base oficial do INCRA"
    if "pendente" in (e_sigef, e_snci):
        return "A consulta ao INCRA ficou pendente nesta emissão"
    return "Nenhuma certificação do INCRA sobre o imóvel na consulta desta emissão"


# ===========================================================================
# PÁGINA 6 — capítulo 4, água (seção 2.6)
# ===========================================================================
def pagina_6(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 6
    blocos: list[Bloco] = []
    e_out = L.estado("outorga")
    e_poco = L.estado("pocos")
    if e_out == "pendente" and e_poco == "pendente":
        abrir_capitulo(L, "agua", "outorga", frase_pendente(L, "outorga"), nota_de_fonte(L, "outorga"), blocos)
        alt_cx, des_cx = caixa_significado([
            (None, "Sem estas consultas, o relatório não diz se há captação registrada nem como está a "
                   "água na região.")])
        blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
        return fechar_pagina(c, L, blocos, 6, "agua", grade)

    longo, curto = frases_agua(L)
    abrir_capitulo(L, "agua", "outorga" if e_out != "pendente" else "pocos", (longo, curto),
                   nota_de_fonte(L, "outorga" if e_out != "pendente" else "pocos"), blocos)

    # --- números do subsolo --------------------------------------------------------------------------
    if e_poco == "encontrado":
        v = L.valor("pocos") or {}
        itens = []
        if v.get("pocos"):
            itens.append((f_inteiro(v["pocos"]), "poços",
                          f"cadastrados num raio de {f_inteiro(v.get('raio_busca_km') or 0)} km"
                          if v.get("raio_busca_km") else "cadastrados na região"))
        for chave, legenda_t in (("profundidade_mediana_m", "profundidade típica dos poços da região"),
                                 ("nivel_estatico_mediano_m", "onde a água costuma estar parada"),
                                 ("nivel_dinamico_mediano_m", "onde a água fica durante o bombeamento")):
            dado = v.get(chave)
            if isinstance(dado, dict) and dado.get("valor"):
                itens.append((f_metros(dado["valor"]).replace(" m", ""), "m", legenda_t))
        if itens:
            alt, des = destaques(itens[:4])
            blocos.append(Bloco("pocos", alt, des, espaco=1, prioridade=2))

    # --- outorgas em tabela ---------------------------------------------------------------------------
    if e_out == "encontrado":
        grants = L.valor("outorga") or []
        cabecalho = L.fato("outorga").get("cabecalho") or []
        # uma linha por coluna do órgão: cabe mais e lê melhor que uma tabela larga e apertada
        linhas = []
        for i, g in enumerate(grants):
            colunas = g.get("colunas") or {}
            prefixo = f"Captação {i + 1} · " if len(grants) > 1 else ""
            if colunas:
                for cabecalho_col in cabecalho:
                    valor_col = str(colunas.get(cabecalho_col, "")).strip()
                    if valor_col:
                        linhas.append([prefixo + cabecalho_col, valor_col])
            else:
                for rotulo_col, chave_col in (("Uso", "tipo"), ("Ato", "ato_tipo"), ("Situação", "situacao")):
                    if g.get(chave_col):
                        linhas.append([prefixo + rotulo_col, str(g[chave_col])])
        if linhas:
            alt, des = tabela([("O que o órgão registrou", 62 * mm), ("Como está no cadastro", 108 * mm)],
                              linhas)
            blocos.append(Bloco("outorgas", alt, des, espaco=1))
        notas = [str(x) for x in (L.fato("outorga").get("notas") or []) if str(x).strip()]
        if notas:
            t_notas = [texto("• " + n, "legenda", LARG) for n in notas[:3]]
            altura_n = sum(t.altura for t in t_notas) + 1.2 * mm * max(0, len(t_notas) - 1)

            def notas_bloco(c, x, y_topo, h, t_notas=t_notas):
                y = y_topo
                for t in t_notas:
                    y -= t.desenhar(c, x, y) + 1.2 * mm

            blocos.append(Bloco("notas_outorga", altura_n, notas_bloco, espaco=1, prioridade=4))
    elif e_out == "nao_encontrado":
        t = texto("Nenhuma captação registrada no cadastro de outorgas dentro do imóvel"
                  + alcance(L, "outorga", ", ") + ". Isso não quer dizer que não exista captação no local: "
                  "quer dizer que não há registro no cadastro consultado.", "corpo", G.leitura)
        blocos.append(Bloco("sem_outorga", t.altura, lambda c, x, y, h, t=t: t.desenhar(c, x, y), espaco=1, prioridade=5))

    # --- outorgas na vizinhança e pivôs -------------------------------------------------------------
    linhas_extra = []
    vizinhas = L.fato("outorga").get("vizinhas_5km") or {}
    if vizinhas.get("texto"):
        linhas_extra.append(("Na vizinhança", str(vizinhas["texto"])))
    if L.estado("pivo") in ("encontrado", "nao_encontrado"):
        v = L.valor("pivo") or {}
        n = v.get("quantidade") or 0
        if n:
            linhas_extra.append(("Pivô central", f"{f_inteiro(n)} {'pivô mapeado' if n == 1 else 'pivôs mapeados'} "
                                                 f"no imóvel" + alcance(L, "pivo", ", ") + "."))
        else:
            linhas_extra.append(("Pivô central", "Nenhum pivô central" + alcance(L, "pivo", " ") + "."))
    if linhas_extra:
        alt, des = tabela([("Tema", 40 * mm), ("O que a consulta mostra", 130 * mm)],
                          [[a, b] for a, b in linhas_extra])
        blocos.append(Bloco("agua_extra", alt, des, espaco=1, prioridade=3))

    alt_cx, des_cx = caixa_significado([(None, significado_agua(L))])
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 6, "agua", grade)


def significado_agua(L: Leitura) -> str:
    partes = []
    if L.estado("outorga") == "encontrado":
        partes.append("A outorga tem prazo de validade, e o que vale é o que está escrito no ato do órgão. "
                      "Vazão autorizada não é água garantida.")
    elif L.estado("outorga") == "nao_encontrado":
        partes.append("Para irrigar, a captação precisa de outorga do órgão estadual ou da ANA.")
    if L.estado("pocos") == "encontrado":
        partes.append("Os números dos poços são da região, não deste imóvel: servem para saber o que "
                      "esperar antes de perfurar.")
    elif L.estado("pocos") == "pendente":
        partes.append("A consulta de poços ficou pendente e é refeita na próxima emissão.")
    return " ".join(partes) or "As consultas de água desta emissão estão resumidas acima."


# ===========================================================================
# PÁGINA 7 — capítulo 5, como a terra está sendo usada (seção 2.7)
# ===========================================================================
COR_USO = {"Pastagem": "USO_PASTO", "Formação florestal": "USO_NATIVA", "Formação savânica": "USO_NATIVA",
           "Formação campestre": "USO_NATIVA", "Mosaico de usos": "USO_LAVOURA", "Soja": "USO_LAVOURA",
           "Outras lavouras temporárias": "USO_LAVOURA", "Outras lavouras perenes": "USO_LAVOURA",
           "Cana": "USO_LAVOURA", "Rio, lago e oceano": "USO_AGUA", "Silvicultura": "USO_NATIVA"}
NATIVAS = ("Formação florestal", "Formação savânica", "Formação campestre", "Campo alagado",
           "Restinga", "Mangue")


def cor_do_uso(classe: str) -> str:
    return st.HEX[COR_USO.get(classe, "USO_OUTROS")]


def pagina_7(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 7
    blocos: list[Bloco] = []
    e_uso = L.estado("mapbiomas")
    if e_uso == "pendente":
        abrir_capitulo(L, "terra_uso", "mapbiomas", frase_pendente(L, "mapbiomas"),
                       nota_de_fonte(L, "mapbiomas"), blocos)
        alt_cx, des_cx = caixa_significado([
            (None, "Sem o mapa de cobertura, o relatório não diz o que ocupa a terra hoje.")])
        blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
        return fechar_pagina(c, L, blocos, 7, "terra_uso", grade)

    uso = L.valor("mapbiomas") or {}
    classes = uso.get("classes") or []
    pcts = f_pct_composicao([x.get("pct") or 0 for x in classes]) if classes else []
    principal = max(range(len(classes)), key=lambda i: classes[i].get("ha") or 0) if classes else None
    ano = uso.get("ano")
    nativa = sum(Decimal(str(x.get("pct") or 0)) for x in classes if x.get("classe") in NATIVAS)
    if principal is not None:
        nome_p = classes[principal]["classe"]
        resposta = (f"{nome_p} ocupa a maior parte do imóvel: {pcts[principal]}% da área"
                    + (f", no mapa de {ano}" if ano else "") + ".",
                    f"{nome_p} na maior parte ({pcts[principal]}%)" + (f", em {ano}" if ano else "") + ".")
    else:
        resposta = ("O mapa de cobertura não trouxe classe com área dentro do imóvel nesta emissão.",) * 2
    abrir_capitulo(L, "terra_uso", "mapbiomas", resposta, nota_de_fonte(L, "mapbiomas"), blocos)

    # --- imagem do imóvel --------------------------------------------------------------------------
    img = L.imagem_media() or L.imagem_base()
    geom = L.geometria
    if img and geom:
        cap = texto(legenda_imagem(img), "legenda", LARG)
        fixo = 1.8 * mm + cap.altura
        img_min, img_max = 56 * mm, 86 * mm

        def figura(c, x, y_topo, h):
            ih = h - fixo
            mapa = foto_do_imovel(L, img, LARG, ih, x, y_topo, c, fracao_quadro=0.62)
            c.saveState()
            clip = c.beginPath()
            clip.roundRect(x, y_topo - ih, LARG, ih, 3 * mm)
            c.clipPath(clip, stroke=0, fill=0)
            limite_car(c, geom["car_m"], mapa, grosso=3.2, fino=1.6, halo=0.7)
            c.restoreState()
            escala_e_norte(c, mapa, x + 2.5 * mm, y_topo - ih + 2.5 * mm)
            cap.desenhar(c, x, y_topo - ih - 1.8 * mm)

        blocos.append(Bloco("figura", img_min + fixo, figura, mola=(img_min + fixo, img_max + fixo), espaco=1))

    # --- composição da cobertura --------------------------------------------------------------------
    if classes:
        partes = [(x["classe"], float(x.get("pct") or 0), cor_do_uso(x["classe"])) for x in classes]
        alt, des = barra_composicao(partes)
        blocos.append(Bloco("composicao", alt, des, espaco=1))
        linhas = [[x["classe"], f_ha(x.get("ha") or 0), f_pct_exato(x.get("pct") or 0)] for x in classes]
        alt, des = tabela([("Classe de cobertura", 96 * mm), ("Área", 38 * mm), ("Parte do imóvel", 36 * mm)],
                          linhas, nota=f"Classificação de satélite a {f_inteiro(uso.get('resolucao_m') or 30)} m"
                                       + (f", mapa de {ano}" if ano else "") + "; não é levantamento de campo.",
                          numericas=(1, 2))
        blocos.append(Bloco("tabela_uso", alt, des, espaco=1, prioridade=2))

    # --- verdor (NDVI), quando o Sentinel respondeu -------------------------------------------------
    if L.tem("sentinel"):
        s = L.valor("sentinel") or {}
        if s.get("ndvi_mediana") is not None:
            partes = []
            for rotulo_v, chave, cor_hex in (("pouco verde", "parte_baixa_pct", "#D9C47E"),
                                             ("verde médio", "parte_media_pct", "#9CAD6A"),
                                             ("bem verde", "parte_alta_pct", "#4E7A42")):
                if s.get(chave) is not None:
                    partes.append((rotulo_v, float(s[chave]), cor_hex))
            if partes:
                alt_b, des_b = barra_composicao(partes)
                t = texto(f"Verdor medido na imagem de {f_data(str(s.get('data') or ''))}: valor típico "
                          f"{_ptbr(_q(Decimal(str(s['ndvi_mediana'])), 2))} numa escala que vai de 0 a 1. "
                          "Ele mostra quanta folha verde havia no dia da imagem, não a produtividade.",
                          "corpo", LARG)
                altura_v = t.altura + 2 * mm + alt_b

                def verdor(c, x, y_topo, h, t=t, des_b=des_b, alt_b=alt_b):
                    y = y_topo - t.desenhar(c, x, y_topo) - 2 * mm
                    des_b(c, x, y, alt_b)

                blocos.append(Bloco("verdor", altura_v, verdor, espaco=1, prioridade=4))

    itens_sig = []
    if nativa > 0:
        itens_sig.append((None, f"A vegetação nativa ocupa {f_pct(nativa)} do imóvel no mapa de cobertura. "
                                "A lei trata reserva legal e faixa de proteção por declaração e por análise "
                                "do órgão, não por este mapa."))
    itens_sig.append((None, "O mapa de cobertura é feito por satélite, com pontos de 30 m. Ele diz o que "
                            "predomina em cada pedaço, e não substitui a visita ao imóvel."))
    alt_cx, des_cx = caixa_significado(itens_sig)
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 7, "terra_uso", grade)


# ===========================================================================
# PÁGINA 8 — capítulo 5, o chão: inclinação e solo (seção 2.7, p. 8)
# ===========================================================================
def pagina_8(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 8
    blocos: list[Bloco] = []
    e_rel = L.estado("relevo")
    if e_rel == "pendente":
        abrir_capitulo(L, "terra_chao", "relevo", frase_pendente(L, "relevo"), nota_de_fonte(L, "relevo"),
                       blocos)
        alt_cx, des_cx = caixa_significado([
            (None, "Sem a medida do terreno, o relatório não diz quanto o imóvel inclina.")])
        blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
        return fechar_pagina(c, L, blocos, 8, "terra_chao", grade)

    rel = L.valor("relevo") or {}
    classes = [x for x in rel.get("classes_embrapa_pct") or [] if (x.get("pct") or 0) > 0]
    ate_8 = sum(Decimal(str(x.get("pct") or 0)) for x in classes
                if x.get("classe") in ("plano", "suave ondulado"))
    mediana = rel.get("inclinacao_mediana_pct")
    resposta = (f"A maior parte do imóvel é plana ou pouco inclinada: {f_pct(ate_8)} da área tem até 8% de "
                f"inclinação. A inclinação típica é de {f_pct(mediana)}." if mediana is not None else
                f"{f_pct(ate_8)} da área tem até 8% de inclinação.",
                f"{f_pct(ate_8)} do imóvel tem até 8% de inclinação.")
    abrir_capitulo(L, "terra_chao", "relevo", resposta, nota_de_fonte(L, "relevo"), blocos)

    # --- rampas e barra de classes -------------------------------------------------------------------
    if classes:
        partes = [(x["classe"], float(x.get("pct") or 0), TONS_RELEVO.get(x["classe"], "#BFB8A8"))
                  for x in classes]
        alt, des = barra_composicao(partes)
        blocos.append(Bloco("barra_relevo", alt, des, espaco=1))
        alt, des = rampas_do_relevo(classes)
        blocos.append(Bloco("rampas", alt, des, espaco=1, prioridade=3))

    # --- números do terreno ---------------------------------------------------------------------------
    itens = []
    if rel.get("altitude_mediana_m") is not None:
        itens.append((f_inteiro(rel["altitude_mediana_m"]), "m", "altitude típica"
                      + (f" (de {f_inteiro(rel['altitude_min_m'])} a {f_inteiro(rel['altitude_max_m'])} m)"
                         if rel.get("altitude_min_m") is not None else "")))
    if mediana is not None:
        itens.append((f_pct(mediana).replace("%", ""), "%", "inclinação típica"))
    if rel.get("inclinacao_p90_pct") is not None:
        itens.append((f_pct(rel["inclinacao_p90_pct"]).replace("%", ""), "%",
                      "9 de cada 10 pontos ficam até aqui"))
    if itens:
        alt, des = destaques(itens)
        blocos.append(Bloco("numeros_relevo", alt, des, espaco=1, prioridade=3))

    legal = []
    if (rel.get("pct_25_45_graus") or 0) > 0:
        legal.append(f"{f_pct(rel['pct_25_45_graus'])} da área fica entre 25° e 45° de inclinação")
    if (rel.get("pct_acima_45_graus") or 0) > 0:
        legal.append(f"{f_pct(rel['pct_acima_45_graus'])} passa de 45°")
    if legal:
        t = texto(" e ".join(legal) + ". A lei ambiental trata essas faixas de forma própria; a medida "
                  "aqui é de triagem, por modelo de elevação, e não substitui o levantamento em campo.",
                  "corpo", G.leitura)
        blocos.append(Bloco("legal_relevo", t.altura, lambda c, x, y, h, t=t: t.desenhar(c, x, y), espaco=1, prioridade=1))

    # --- solo -------------------------------------------------------------------------------------------
    alt, des = cabeca_secao("Como é o solo e para que serve?", "camadas")
    blocos.append(Bloco("h2_solo", alt, des, espaco=1))
    linhas_solo = []
    for chave, rotulo_linha in (("solo_mapa", "Tipo de solo no mapa oficial"),
                                ("textura_solo", "Textura de 0 a 30 cm"),
                                ("soilgrids", "Argila, areia e silte"),
                                ("aptidao", "Para que serve (aptidão)"),
                                ("erosao", "Fragilidade do solo")):
        estado_solo = L.estado(chave)
        if estado_solo == "pendente":
            linhas_solo.append([rotulo_linha, NOTA_PENDENTE])
            continue
        if estado_solo not in ("encontrado", "nao_encontrado"):
            continue
        texto_linha = texto_do_solo(L, chave)
        if texto_linha:
            linhas_solo.append([rotulo_linha, texto_linha])
    if linhas_solo:
        nota = (L.fato("solo_mapa").get("valor") or {}).get("nota_escala") if L.tem("solo_mapa") else None
        alt, des = tabela([("Tema", 46 * mm), ("O que as fontes oficiais dizem", 124 * mm)], linhas_solo,
                          nota=str(nota) if nota else None)
        blocos.append(Bloco("tabela_solo", alt, des, espaco=1))

    alt_cx, des_cx = caixa_significado([
        (None, "Estes números vêm de mapas e de modelos nacionais, feitos para descrever a região. Eles "
               "dizem o que esperar antes da visita; análise de solo e topografia de campo continuam "
               "necessárias para planejar lavoura ou obra.")])
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 8, "terra_chao", grade)


def texto_do_solo(L: Leitura, chave: str) -> str:
    v = L.fato(chave).get("valor")
    if chave == "solo_mapa" and isinstance(v, dict):
        return "; ".join(str(u.get("descricao")) for u in v.get("unidades") or [] if u.get("descricao"))
    if chave == "textura_solo" and isinstance(v, dict):
        if v.get("texto"):
            return str(v["texto"])
        grupos = v.get("grupos_pct") or {}
        return "; ".join(f"{k} em {f_pct(p)} do imóvel" for k, p in sorted(grupos.items(), key=lambda kv: -kv[1]))
    if chave == "soilgrids" and isinstance(v, dict):
        return str(v.get("texto") or "")
    if chave == "aptidao" and isinstance(v, dict):
        return "; ".join(f"{c.get('descricao')} — {c.get('medida')}" for c in v.get("classes") or [])
    if chave == "erosao" and isinstance(v, dict):
        return str(v.get("texto") or "")
    return ""


def rampas_do_relevo(classes: list):
    """Rampas desenhadas no ângulo real de cada classe, com o limite de 25° tracejado (§1.7)."""
    mostrar = [x for x in classes if (x.get("pct") or 0) >= 1][:4]
    if not mostrar:
        mostrar = classes[:1]
    col = LARG / max(1, len(mostrar))
    rotulos = [texto(f"{x['classe']} · {f_pct_exato(x.get('pct') or 0)}", "legenda", col - 4 * mm)
               for x in mostrar]
    altura = 30 * mm + 2.4 * mm + max(t.altura for t in rotulos)

    def desenhar(c, x, y_topo, h):
        base = y_topo - 30 * mm
        largura_rampa = min(col - 8 * mm, 38 * mm)
        for i, (classe, rot) in enumerate(zip(mostrar, rotulos)):
            cx = x + i * col
            faixa = FAIXAS_RELEVO.get(classe["classe"], (0, 3))
            ate = min(faixa[1], 45)
            subida = largura_rampa * ate / 100
            c.saveState()
            c.setFillColor(HexColor(TONS_RELEVO.get(classe["classe"], "#BFB8A8")))
            c.setStrokeColor(COR["INK2"])
            c.setLineWidth(0.6)
            c.setLineJoin(1)
            tri = c.beginPath()
            tri.moveTo(cx, base)
            tri.lineTo(cx + largura_rampa, base)
            tri.lineTo(cx + largura_rampa, base + subida)
            tri.close()
            c.drawPath(tri, stroke=1, fill=1)
            c.restoreState()
            linha_simples(c, cx + largura_rampa + 1.4 * mm, base + max(0, subida - altura_maiuscula("Sans", ANOT)),
                          f"{f_inteiro(faixa[0])}–{f_inteiro(faixa[1])}%", "Sans", ANOT, COR["INK2"])
            rot.desenhar(c, cx, base - 2.4 * mm)

    return altura, desenhar


# ===========================================================================
# PÁGINA 9 — capítulo 6, clima (seção 2.8)
# ===========================================================================
def pagina_9(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 9
    blocos: list[Bloco] = []
    if L.estado("climatologia") == "pendente":
        abrir_capitulo(L, "clima", "climatologia", frase_pendente(L, "climatologia"),
                       nota_de_fonte(L, "climatologia"), blocos)
        alt_cx, des_cx = caixa_significado([
            (None, "Sem a climatologia, o relatório não diz quando chove nem quando seca nesta região.")])
        blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
        return fechar_pagina(c, L, blocos, 9, "clima", grade)

    clima = L.valor("climatologia") or {}
    meses = clima.get("meses") or []
    chuvoso = intervalo_de_meses(clima.get("meses_chuvosos") or [])
    seco = intervalo_de_meses(clima.get("meses_secos") or [])
    total = clima.get("chuva_anual_mm")
    resposta_longa = ""
    if chuvoso:
        resposta_longa = f"Chove de {chuvoso[0]} a {chuvoso[1]}"
        if seco:
            resposta_longa += f", e quase nada de {seco[0]} a {seco[1]}"
        resposta_longa += "."
    if total:
        resposta_longa += f" No ano, a chuva soma cerca de {f_inteiro(total)} mm."
    resposta_curta = (f"Chove de {chuvoso[0]} a {chuvoso[1]}." if chuvoso else resposta_longa)
    abrir_capitulo(L, "clima", "climatologia", (resposta_longa or resposta_curta, resposta_curta),
                   nota_de_fonte(L, "climatologia"), blocos)

    if meses:
        alt, des = grafico_de_chuva(meses)
        blocos.append(Bloco("grafico", alt, des, mola=(alt, alt + 16 * mm), espaco=1))

    # --- janela recente ---------------------------------------------------------------------------
    if L.tem("janela_chuva"):
        j = L.valor("janela_chuva") or {}
        itens = []
        if j.get("observado_mm") is not None:
            itens.append((f_inteiro(j["observado_mm"]), "mm",
                          "choveu na janela recente" + (f" ({f_data(j['janela'][0])} a {f_data(j['janela'][1])})"
                                                        if j.get("janela") and all(j["janela"]) else "")))
        if j.get("dias_secos") is not None:
            itens.append((f_inteiro(j["dias_secos"]), "dias", "sem chuva na mesma janela"))
        if itens:
            # O limite da janela recente vai colado nos números, como legenda: assim ele nunca sai da
            # folha. Um número de chuva sem o alcance dele seria lido como medição no imóvel.
            alt_d, des_d = destaques(itens)
            aviso = texto("A chuva recente vem de uma estimativa em grade de cerca de 50 km, no centro do "
                          "imóvel. O relatório não a compara com o normal do lugar: a grade é larga demais "
                          "para isso.", "legenda", LARG)
            altura_j = alt_d + 1.8 * mm + aviso.altura

            def janela_bloco(c, x, y_topo, h, des_d=des_d, alt_d=alt_d, aviso=aviso):
                des_d(c, x, y_topo, alt_d)
                aviso.desenhar(c, x, y_topo - alt_d - 1.8 * mm)

            blocos.append(Bloco("janela", altura_j, janela_bloco, espaco=1))

    if meses and any(m.get("temperatura_c") is not None for m in meses):
        linhas = [[MESES[int(m["mes"]) - 1].capitalize(), f_inteiro(m.get("chuva_mm") or 0) + " mm",
                   (f"{_ptbr(_q(Decimal(str(m['temperatura_c'])), 1))} °C"
                    if m.get("temperatura_c") is not None else "")] for m in meses]
        alt, des = tabela([("Mês", 56 * mm), ("Chuva típica", 57 * mm), ("Temperatura média", 57 * mm)],
                          linhas, nota=maiuscula_inicial(str((L.fato("climatologia").get("limites") or [""])[0])) + ".",
                          numericas=(1, 2))
        blocos.append(Bloco("tabela_clima", alt, des, espaco=1, prioridade=2))

    alt_cx, des_cx = caixa_significado([
        (None, "A climatologia mostra o padrão de muitos anos, não a previsão do tempo. Ela serve para "
               "decidir época de plantio e de obra; o ano que vem pode fugir do padrão.")])
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 9, "clima", grade)


def grafico_de_chuva(meses: list):
    """Barras de chuva por mês, com a linha de temperatura por cima quando a fonte traz as duas."""
    valores = [float(m.get("chuva_mm") or 0) for m in meses]
    temps = [m.get("temperatura_c") for m in meses]
    tem_temp = any(t is not None for t in temps)
    maximo = max(valores) or 1
    altura_barras = 37 * mm
    faixa_legenda = st.ESTILO["eixo"].leading + 1.6 * mm   # legenda em cima, fora da área das barras
    altura = altura_barras + 3 * mm + st.ESTILO["eixo"].leading + faixa_legenda

    def desenhar(c, x, y_topo, h):
        linha_simples(c, x, y_topo - st.ESTILO["eixo"].fontSize, "barras: chuva típica do mês, em milímetros",
                      "Sans", ANOT, COR["WATER"])
        if tem_temp:
            linha_simples(c, x + LARG, y_topo - st.ESTILO["eixo"].fontSize,
                          "linha: temperatura média do mês", "Sans", ANOT, COR["TERRA"], "dir")
        y_topo = y_topo - faixa_legenda
        hb = h - faixa_legenda - 3 * mm - st.ESTILO["eixo"].leading
        base = y_topo - hb
        largura_col = LARG / 12
        largura_barra = largura_col * 0.56
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        for passo in range(1, 5):
            yy = base + hb * passo / 4
            c.line(x, yy, x + LARG, yy)
        c.restoreState()
        for i, v in enumerate(valores):
            cx = x + i * largura_col + (largura_col - largura_barra) / 2
            alt_b = hb * v / maximo
            c.saveState()
            c.setFillColor(COR["WATER"])
            c.rect(cx, base, largura_barra, alt_b, stroke=0, fill=1)
            c.restoreState()
            linha_simples(c, cx + largura_barra / 2, base + alt_b + 1.2 * mm, f_inteiro(v), "Sans", ANOT,
                          COR["INK2"], "centro")
            linha_simples(c, x + i * largura_col + largura_col / 2, base - 3 * mm,
                          MESES_CURTOS[int(meses[i]["mes"]) - 1], "Sans", ANOT, COR["INK2"], "centro")
        if tem_temp:
            limpos = [(i, float(t)) for i, t in enumerate(temps) if t is not None]
            t_min = min(t for _, t in limpos)
            t_max = max(t for _, t in limpos) or 1
            faixa = (t_max - t_min) or 1
            c.saveState()
            c.setStrokeColor(COR["TERRA"])
            c.setLineWidth(1.2)
            c.setLineJoin(1)
            caminho = c.beginPath()
            for k, (i, t) in enumerate(limpos):
                px = x + i * largura_col + largura_col / 2
                py = base + hb * 0.18 + (hb * 0.62) * (t - t_min) / faixa
                (caminho.moveTo if k == 0 else caminho.lineTo)(px, py)
            c.drawPath(caminho, stroke=1, fill=0)
            c.restoreState()

    return altura, desenhar


# ===========================================================================
# PÁGINA 10 — capítulo 7, o entorno (seção 2.9)
# ===========================================================================
def pagina_10(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 10
    blocos: list[Bloco] = []
    municipio = str((L.p.get("property") or {}).get("municipality") or "")
    e_ppm = L.estado("ppm")
    if e_ppm == "pendente" and L.estado("armazens") != "encontrado":
        abrir_capitulo(L, "entorno", "ppm", frase_pendente(L, "ppm"), nota_de_fonte(L, "ppm"), blocos)
        alt_cx, des_cx = caixa_significado([
            (None, "Sem estas consultas, o relatório não descreve a produção da região.")])
        blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
        return fechar_pagina(c, L, blocos, 10, "entorno", grade)

    partes = []
    if e_ppm == "encontrado":
        ppm = L.valor("ppm") or {}
        rebanhos = ppm.get("rebanhos") or []
        if rebanhos:
            maior = rebanhos[0]
            partes.append(f"No município de {municipio}, o maior rebanho é de {maior['rebanho'].lower()}: "
                          f"{f_inteiro(maior['cabecas'])} cabeças"
                          + (f" em {maior['ano']}" if maior.get("ano") else "") + ".")
    if L.estado("armazens") == "encontrado":
        arm = L.valor("armazens") or {}
        n = arm.get("quantidade") or 0
        if n:
            perto = arm.get("mais_perto_km")
            partes.append(f"Há {f_inteiro(n)} {'armazém cadastrado' if n == 1 else 'armazéns cadastrados'} "
                          f"na CONAB até {f_inteiro(arm.get('raio_km') or 50)} km"
                          + (f"; o mais perto fica a {_ptbr(_q(Decimal(str(perto)), 1))} km" if perto else "")
                          + ".")
    resposta = (" ".join(partes) or f"O relatório reúne abaixo o que as fontes oficiais trazem sobre "
                f"{municipio} e a vizinhança.")
    abrir_capitulo(L, "entorno", "ppm" if e_ppm != "pendente" else "armazens",
                   (resposta, resposta), nota_de_fonte(L, "ppm" if e_ppm != "pendente" else "armazens"),
                   blocos)

    # --- números grandes ----------------------------------------------------------------------------
    itens = []
    if e_ppm == "encontrado":
        ppm = L.valor("ppm") or {}
        for r in (ppm.get("rebanhos") or [])[:2]:
            if r.get("cabecas"):
                itens.append((f_inteiro(r["cabecas"]), "cabeças",
                              f"de {r['rebanho'].lower()} no município"
                              + (f", em {r['ano']}" if r.get("ano") else "")))
    if L.estado("armazens") == "encontrado":
        arm = L.valor("armazens") or {}
        capacidade = sum(float(x.get("capacidade_t") or 0) for x in arm.get("itens") or [])
        if capacidade:
            itens.append((f_inteiro(capacidade), "toneladas", "de armazenagem cadastrada na vizinhança"))
    if itens:
        alt, des = destaques(itens[:3])
        blocos.append(Bloco("numeros_entorno", alt, des, espaco=1, prioridade=2))

    # --- rebanhos do município -----------------------------------------------------------------------
    if e_ppm == "encontrado":
        ppm = L.valor("ppm") or {}
        linhas = [[r["rebanho"], f_inteiro(r.get("cabecas") or 0), str(r.get("ano") or "")]
                  for r in (ppm.get("rebanhos") or [])[:8]]
        if linhas:
            alt, des = tabela([("Rebanho", 96 * mm), ("Cabeças no município", 44 * mm), ("Ano", 30 * mm)],
                              linhas, nota=f"Número do município inteiro: não é a produção deste imóvel.",
                              numericas=(1,))
            blocos.append(Bloco("tabela_ppm", alt, des, espaco=1, prioridade=2))

    # --- safras do município -------------------------------------------------------------------------
    if L.tem("safras"):
        safras = L.valor("safras") or {}
        linhas = [[cu["cultura"], f_ha(cu.get("ha") or 0),
                   f_inteiro(cu["toneladas"]) + " t" if cu.get("toneladas") else ""]
                  for cu in (safras.get("culturas") or [])[:8]]
        if linhas:
            alt, des = tabela([("Cultura", 96 * mm), ("Área plantada", 44 * mm), ("Colheita", 30 * mm)],
                              linhas, nota=f"Município inteiro, safra de {safras.get('year')}.",
                              numericas=(1, 2))
            blocos.append(Bloco("tabela_safras", alt, des, espaco=1, prioridade=3))

    # --- logística e vizinhança -----------------------------------------------------------------------
    linhas_extra = []
    if L.estado("armazens") == "encontrado":
        arm = L.valor("armazens") or {}
        for item in (arm.get("itens") or [])[:4]:
            onde = " · ".join(x for x in (item.get("municipio"), item.get("uf")) if x)
            linhas_extra.append([str(item.get("tipo") or "Armazém"), onde,
                                 (f"{_ptbr(_q(Decimal(str(item['distancia_km'])), 1))} km"
                                  if item.get("distancia_km") is not None else ""),
                                 (f_inteiro(item["capacidade_t"]) + " t" if item.get("capacidade_t") else "")])
    if linhas_extra:
        alt, des = tabela([("Armazém", 50 * mm), ("Onde fica", 56 * mm), ("Distância", 32 * mm),
                           ("Capacidade", 32 * mm)], linhas_extra,
                          nota=maiuscula_inicial(str((L.fato("armazens").get("limites") or [""])[0]))
                               + ". O livro não mostra o nome da empresa.", numericas=(2, 3))
        blocos.append(Bloco("tabela_armazens", alt, des, espaco=1, prioridade=4))

    if L.estado("sitio_arqueologico") == "encontrado" or (
            (L.fato("sitio_arqueologico").get("valor") or {}).get("vizinhanca_quantidade")):
        v = L.fato("sitio_arqueologico").get("valor") or {}
        perto = v.get("vizinhanca_quantidade") or 0
        if perto:
            t = texto(f"Há {f_inteiro(perto)} {'sítio arqueológico cadastrado' if perto == 1 else 'sítios arqueológicos cadastrados'} "
                      "no IPHAN na vizinhança. Isso mostra que a região tem histórico de ocupação antiga.",
                      "corpo", LARG)
            blocos.append(Bloco("iphan_entorno", t.altura,
                                lambda c, x, y, h, t=t: t.desenhar(c, x, y), espaco=1, prioridade=5))

    alt_cx, des_cx = caixa_significado([
        (None, f"Os números desta página são do município inteiro, e não deste imóvel. Eles mostram o que "
               f"a região produz e o que existe por perto — útil para escoamento e para comparar com o que "
               f"você pretende fazer na terra.")])
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, 10, "entorno", grade)


# ===========================================================================
# APÊNDICE A — números técnicos (seção 4.1)
# ===========================================================================
NOME_DO_FATO = {
    "car": "Cadastro Ambiental Rural", "composicao_car": "Composição declarada no CAR",
    "sigef": "Certificação SIGEF (INCRA)", "snci": "Certificação SNCI (INCRA)",
    "prodes": "Desmatamento mapeado (PRODES)", "bioma": "Bioma", "alertas_mensais": "Alertas recentes",
    "embargo_ibama": "Embargo do IBAMA", "embargo_icmbio": "Embargo do ICMBio",
    "autos": "Auto de infração pelo local", "ti": "Terra indígena", "uc": "Unidade de conservação",
    "quilombola": "Território quilombola", "assentamento": "Assentamento",
    "floresta_publica": "Floresta pública", "anm": "Processo de mineração (ANM)",
    "sitio_arqueologico": "Sítio arqueológico (IPHAN)", "regra_credito": "Regra do crédito rural",
    "outorga": "Outorga de água", "pocos": "Poços cadastrados na região", "pivo": "Pivô central",
    "climatologia": "Climatologia mensal", "janela_chuva": "Chuva na janela recente",
    "sentinel": "Imagem Sentinel-2", "mapbiomas": "Cobertura da terra (MapBiomas)",
    "relevo": "Relevo medido no imóvel", "solo_mapa": "Solo no mapa oficial",
    "textura_solo": "Textura do solo", "soilgrids": "Argila, areia e silte",
    "aptidao": "Aptidão agrícola", "erosao": "Fragilidade do solo",
    "ppm": "Rebanhos do município", "safras": "Safras do município", "sif": "Estabelecimentos no SIF",
    "armazens": "Armazéns da CONAB", "aerodromo": "Aeródromos", "matricula": "Matrícula do cartório",
    "ccir": "CCIR", "car_ultima_alteracao": "Última alteração do CAR",
    "embargos_estaduais": "Embargos estaduais",
}
ORDEM_APENDICE = ("car", "composicao_car", "sigef", "snci", "prodes", "bioma", "alertas_mensais",
                  "embargo_ibama", "embargo_icmbio", "autos", "ti", "uc", "quilombola", "assentamento",
                  "floresta_publica", "anm", "sitio_arqueologico", "regra_credito", "outorga", "pocos",
                  "pivo", "climatologia", "janela_chuva", "sentinel", "mapbiomas", "relevo", "solo_mapa",
                  "textura_solo", "soilgrids", "aptidao", "erosao", "ppm", "safras", "sif", "armazens",
                  "aerodromo", "matricula", "ccir", "car_ultima_alteracao", "embargos_estaduais")


def numero_do_fato(L: Leitura, chave: str) -> str:
    """Resumo numérico de um fato para o apêndice. Vazio quando não há número a mostrar."""
    e = L.fato(chave)
    if e["estado"] == "pendente":
        return ""
    v = e.get("valor")
    if isinstance(v, dict):
        if chave == "prodes":
            return (f"{f_inteiro(v.get('quantidade_dentro') or 0)} dentro · "
                    f"{f_inteiro(v.get('quantidade_divisa') or 0)} na divisa · "
                    f"{f_ha(v.get('union_inside_ha') or 0)} dentro do imóvel")
        if chave == "relevo":
            return (f"inclinação típica {f_pct(v.get('inclinacao_mediana_pct') or 0)} · altitude "
                    f"{f_inteiro(v.get('altitude_mediana_m') or 0)} m")
        if chave == "climatologia":
            return f"{f_inteiro(v.get('chuva_anual_mm') or 0)} mm por ano"
        if chave == "janela_chuva":
            return f"{f_inteiro(v.get('observado_mm') or 0)} mm na janela"
        if chave == "mapbiomas":
            classes = v.get("classes") or []
            return f"{f_inteiro(len(classes))} classes de cobertura" if classes else ""
        if chave == "sentinel":
            return f"cena de {f_data(str(v.get('data') or ''))}" if v.get("data") else ""
        if chave == "pocos":
            return f"{f_inteiro(v.get('pocos') or 0)} poços na região"
        if chave == "sitio_arqueologico":
            return (f"{f_inteiro(v.get('dentro_quantidade') or 0)} dentro · "
                    f"{f_inteiro(v.get('vizinhanca_quantidade') or 0)} na vizinhança")
        if chave == "regra_credito":
            return str(v.get("faixa_texto") or "")
        if chave == "bioma":
            return str(v.get("bioma") or "")
        if "quantidade" in v:
            return contagem_com_unidade(v.get("quantidade") or 0, e.get("unidade"))
        if "parcelas" in v:
            return contagem_com_unidade(v.get("parcelas") or 0, "parcelas")
    if isinstance(v, list):
        return contagem_com_unidade(len(v), e.get("unidade"))
    return ""


# Plural → singular das unidades do apêndice. O guia proíbe "(s)", então a palavra concorda de verdade.
SINGULAR = {"outorgas": "outorga", "embargos": "embargo", "autos": "auto", "áreas": "área",
            "processos": "processo", "poços": "poço", "pivôs": "pivô", "sítios": "sítio",
            "parcelas": "parcela", "armazéns": "armazém", "alertas": "alerta",
            "ocorrências": "ocorrência", "registros": "registro", "cabeças": "cabeça"}


def contagem_com_unidade(n, unidade: str | None) -> str:
    palavra = str(unidade or "registros")
    if Decimal(str(n or 0)) == 1:
        palavra = SINGULAR.get(palavra, palavra)
    return f"{f_inteiro(n)} {palavra}"


def quebrar_tabela(colunas, linhas: list, cabeca_alt: float, nota: str | None) -> list[list]:
    """Divide as linhas em folhas que cabem na área útil, medindo de verdade (nunca por chute).

    Mede o pedaço com a mesma função que vai desenhá-lo: a conta de "quantas linhas cabem" feita
    por estimativa erra sempre que uma linha quebra em duas.
    """
    folhas: list[list] = []
    restantes = list(linhas)
    while restantes:
        corte = len(restantes)
        while corte > 1:
            alt, _ = tabela(colunas, restantes[:corte], nota=nota if corte == len(restantes) else None)
            if cabeca_alt + G.us(1) + alt <= G.util_a:
                break
            corte -= 1
        folhas.append(restantes[:corte])
        restantes = restantes[corte:]
    return folhas or [[]]


def folhas_apendice(L: Leitura, titulo_cap: str, titulo: str, linha_dek: str, colunas, linhas: list,
                    nota: str | None):
    """Uma folha por pedaço da tabela, com a mesma cabeça e a continuação marcada."""
    cabeca_alt, _ = cabeca_apendice(titulo_cap, titulo, linha_dek)
    pedacos = quebrar_tabela(colunas, linhas, cabeca_alt, nota)

    def folha(indice: int):
        def desenhar(c, L_: Leitura, numero: int, grade: bool) -> float:
            _PAGINA_ATUAL[0] = numero
            blocos: list[Bloco] = []
            dek = linha_dek if indice == 0 else "Continuação."
            alt, des = cabeca_apendice(titulo_cap, titulo, dek)
            blocos.append(Bloco("cabeca", alt, des, espaco=0))
            alt, des = tabela(colunas, pedacos[indice],
                              nota=nota if indice == len(pedacos) - 1 else None)
            blocos.append(Bloco("tabela", alt, des, espaco=1))
            return fechar_pagina(c, L_, blocos, numero, None, grade)
        return desenhar

    return [folha(i) for i in range(len(pedacos))]


def linhas_apendice_a(L: Leitura) -> list[list[str]]:
    linhas = []
    for chave in ORDEM_APENDICE:
        e = L.fato(chave)
        if e["estado"] == "nao_se_aplica":
            continue
        linhas.append([NOME_DO_FATO.get(chave, chave), ROTULO_SITUACAO.get(e["estado"], "Fora desta edição"),
                       numero_do_fato(L, chave)])
    return linhas


COLUNAS_APENDICE_A = (("O que foi consultado", 78 * mm), ("Situação", 38 * mm),
                      ("O que a consulta trouxe", 54 * mm))
NOTA_APENDICE_A = ("\"Nenhum registro\" quer dizer que a fonte respondeu e não encontrou nada no alcance "
                   "descrito no Apêndice B. \"Consulta pendente\" quer dizer que a fonte não respondeu a "
                   "tempo, e a resposta fica para a próxima emissão.")


def cabeca_apendice(rotulo_cap: str, titulo: str, linha: str):
    h1 = texto(titulo, "h1", LARG)
    dek = texto(linha, "dek", G.leitura)
    altura = 5 * mm + 2.5 * mm + h1.altura + 2 * mm + 0.8 + 3.2 * mm + dek.altura

    def desenhar(c, x, y_topo, h):
        kicker(c, x, y_topo, rotulo_cap, COR["MUTED"], "camadas")
        y = y_topo - 5 * mm - 2.5 * mm
        y -= h1.desenhar(c, x, y)
        y -= 2 * mm
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(0.8)
        c.line(x, y, x + 40 * mm, y)
        c.restoreState()
        dek.desenhar(c, x, y - 3.2 * mm)

    return altura, desenhar


# ===========================================================================
# APÊNDICE B — de onde vêm os dados (seção 4.2)
# ===========================================================================
COLUNAS_APENDICE_B = (("Fonte", 58 * mm), ("Situação", 26 * mm), ("Base de", 19 * mm),
                      ("Consultada", 19 * mm), ("Alcance da consulta", 48 * mm))
NOTA_APENDICE_B = ("O alcance diz até onde a consulta enxerga. Fora dele, o relatório não afirma nem "
                   "presença nem ausência.")


def linhas_apendice_b(L: Leitura) -> list[list[str]]:
    emitido = str(L.p.get("generated_at") or "")
    vistos: set[str] = set()
    linhas = []
    for chave in ORDEM_APENDICE:
        e = L.fato(chave)
        if e["estado"] == "nao_se_aplica":
            continue
        fonte = e.get("fonte") or {}
        nome = str(fonte.get("nome_publico") or "")
        if not nome:
            nome = NOME_DO_FATO.get(chave, chave)
        data_ref = f_data_flexivel(str(e["data_base"])) if e.get("data_base") else ""
        bruto = str(e.get("consultado_em") or emitido)
        consultada = f_data(bruto[:10]) if re.match(r"\d{4}-\d{2}-\d{2}", bruto) else ""
        limites = "; ".join(str(x) for x in (e.get("limites") or []))
        chave_linha = nome + "|" + limites
        if chave_linha in vistos:
            continue
        vistos.add(chave_linha)
        linhas.append([nome, ROTULO_SITUACAO.get(e["estado"], "Fora desta edição"),
                       data_ref, consultada, limites])
    return linhas


# ===========================================================================
# APÊNDICE C — glossário e colofão (seção 4.3)
# ===========================================================================
GLOSSARIO = (
    ("CAR", "Cadastro Ambiental Rural. Registro em que o dono declara ao órgão ambiental onde fica o "
            "imóvel e o que há dentro dele. Não prova quem é o dono."),
    ("Módulo fiscal", "Medida de área que muda de município para município. A lei usa o número de "
                      "módulos para separar regras por tamanho de imóvel."),
    ("PRODES", "Mapa anual de desmatamento feito pelo Instituto Nacional de Pesquisas Espaciais (INPE) "
               "a partir de imagens de satélite."),
    ("Embargo", "Medida do órgão ambiental sobre uma área. Enquanto vale, o uso daquela área fica "
                "proibido."),
    ("Auto de infração", "Documento lavrado pelo órgão contra uma pessoa ou empresa. Esta busca é pelo "
                         "local, não pela pessoa."),
    ("SIGEF e SNCI", "Sistemas do INCRA em que o limite de um imóvel rural pode ser certificado por "
                     "levantamento feito por profissional habilitado."),
    ("Outorga", "Autorização do órgão de água para retirar água de um poço, rio ou lago, com prazo e "
                "quantidade definidos."),
    ("APP", "Área de Preservação Permanente: faixa que a lei protege, como a margem de rio e o entorno "
            "de nascente."),
    ("Aptidão agrícola", "Classificação de mapa regional que indica para que tipo de uso a terra da "
                         "região costuma servir. É o nome oficial da classe, não um juízo sobre o imóvel."),
    ("NDVI (verdor)", "Número calculado da imagem de satélite que mostra quanta folha verde havia no dia "
                      "da imagem."),
)


def pagina_apendice_c(c, L: Leitura, numero: int, grade: bool) -> float:
    _PAGINA_ATUAL[0] = numero
    blocos: list[Bloco] = []
    alt, des = cabeca_apendice("Apêndice C", "Glossário", "As palavras deste relatório, em português claro.")
    blocos.append(Bloco("cabeca", alt, des, espaco=0))

    col = (LARG - G.calha) / 2
    termos = [(texto(t, "glos_termo", col), texto(d, "glos_def", col)) for t, d in GLOSSARIO]
    alturas = [a.altura + 0.8 * mm + b.altura for a, b in termos]
    metade = 0
    soma = 0
    total_alt = sum(alturas) + G.us(1) * (len(alturas) - 1)
    for i, a in enumerate(alturas):
        soma += a + G.us(1)
        if soma >= total_alt / 2:
            metade = i + 1
            break
    altura_glos = max(sum(alturas[:metade]) + G.us(1) * max(0, metade - 1),
                      sum(alturas[metade:]) + G.us(1) * max(0, len(alturas) - metade - 1))

    def glossario(c, x, y_topo, h):
        for coluna, faixa in enumerate((range(0, metade), range(metade, len(termos)))):
            y = y_topo
            cx = x + coluna * (col + G.calha)
            for i in faixa:
                termo, definicao = termos[i]
                y -= termo.desenhar(c, cx, y) + 0.8 * mm
                y -= definicao.desenhar(c, cx, y) + G.us(1)

    blocos.append(Bloco("glossario", altura_glos, glossario, espaco=1))

    creditos = creditos_do_livro(L)
    t_colofao = texto(creditos, "legenda", LARG)

    def colofao(c, x, y_topo, h):
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        c.line(x, y_topo, x + LARG, y_topo)
        c.restoreState()
        t_colofao.desenhar(c, x, y_topo - 2.4 * mm)

    blocos.append(Bloco("colofao", t_colofao.altura + 2.4 * mm, colofao, espaco=1, ancorar=True))
    return fechar_pagina(c, L, blocos, numero, None, grade)


def creditos_do_livro(L: Leitura) -> str:
    creditos = []
    for chave in ORDEM_APENDICE:
        fonte = L.fato(chave).get("fonte") or {}
        citacao = fonte.get("citacao")
        if citacao and str(citacao) not in creditos:
            creditos.append(str(citacao))
    img = L.imagem_base()
    if img and img.get("attribution"):
        creditos.append(f"Imagem de satélite: {img['attribution']}")
    base = (f"Raio-X Territorial · relatório do imóvel rural · {L.site}. "
            f"Emitido em {f_data_brasilia(str(L.p.get('generated_at') or ''))}, código "
            f"{codigo_curto(str(L.p.get('report_id') or ''))}. ")
    base += "Letra Source Serif e Source Sans, da Adobe, com licença aberta SIL OFL. "
    if creditos:
        base += "Créditos das fontes: " + "; ".join(creditos) + ". "
    base += ("Este relatório reúne consultas a bases públicas oficiais. Ele não substitui certidão de "
             "cartório, vistoria no imóvel nem laudo técnico.")
    return base


# ===========================================================================
# Planejamento e geração
# ===========================================================================
def planejar(L: Leitura) -> list[tuple[str, Callable]]:
    """Quais folhas o livro tem para este imóvel. Tema sem achado e sem consulta não ganha página."""
    folhas: list[tuple[str, Callable]] = [("p1", _corpo(pagina_1)), ("p2", _corpo(pagina_2))]
    if L.estado("prodes") != "nao_se_aplica":
        folhas.append(("desmatamento", _corpo(pagina_3)))
    folhas.append(("restricoes", _corpo(pagina_4)))
    folhas.append(("documentos", _corpo(pagina_5)))
    if any(L.estado(k) != "fora_desta_edicao" for k in ("outorga", "pocos", "pivo")):
        folhas.append(("agua", _corpo(pagina_6)))
    if L.estado("mapbiomas") != "fora_desta_edicao":
        folhas.append(("terra_uso", _corpo(pagina_7)))
    if any(L.estado(k) != "fora_desta_edicao" for k in ("relevo", "solo_mapa", "aptidao")):
        folhas.append(("terra_chao", _corpo(pagina_8)))
    if L.estado("climatologia") != "fora_desta_edicao":
        folhas.append(("clima", _corpo(pagina_9)))
    if any(L.estado(k) == "encontrado" for k in ("ppm", "safras", "armazens", "sif")):
        folhas.append(("entorno", _corpo(pagina_10)))
    for i, desenhar in enumerate(folhas_apendice(
            L, "Apêndice A", "Números técnicos",
            "Tudo o que este relatório mediu ou contou, com a situação de cada consulta.",
            COLUNAS_APENDICE_A, linhas_apendice_a(L), NOTA_APENDICE_A)):
        folhas.append((f"ap_numeros_{i}", desenhar))
    emitido = f_data_brasilia(str(L.p.get("generated_at") or ""))
    for i, desenhar in enumerate(folhas_apendice(
            L, "Apêndice B", "De onde vêm os dados",
            f"Toda consulta deste relatório, com a data e o alcance de cada uma. Emissão de {emitido}.",
            COLUNAS_APENDICE_B, linhas_apendice_b(L), NOTA_APENDICE_B)):
        folhas.append((f"ap_fontes_{i}", desenhar))
    folhas.append(("ap_glossario", pagina_apendice_c))
    return folhas


def _corpo(funcao: Callable) -> Callable:
    """Casa a assinatura das folhas de corpo (c, L, grade) com a das folhas de apêndice."""
    def desenhar(c, L: Leitura, numero: int, grade: bool) -> float:
        return funcao(c, L, grade)
    return desenhar


def gerar_livro(caminho_pdf, payload: dict, result: dict | None = None, *, grade: bool = False,
                pasta_imagens=None) -> str:
    """Desenha o livro inteiro e devolve o sha256 do PDF (a mesma saída do gerador atual).

    Não levanta por falta de dado: o que faltar vira ◌ ou sai da folha. Só levanta ``LayoutError``
    quando um bloco não cabe — e aí o chamador decide (o serviço cai para o relatório antigo).
    """
    import hashlib

    caminho = Path(caminho_pdf)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    inicio = time.monotonic()
    REGISTRO.clear()
    aplicar_piso_de_leitura()
    st.registrar_fontes()
    L = Leitura(payload, pasta_imagens or caminho.parent, result=result)
    folhas = planejar(L)
    _TOTAL[0] = len(folhas)

    c = Canvas(str(caminho), pagesize=(G.pagina_l, G.pagina_a), initialFontName="Sans", pageCompression=1)
    titulo_pdf = str((payload.get("property") or {}).get("car_code") or "Imóvel rural")
    c.setTitle(f"Raio-X Territorial — {titulo_pdf}")
    c.setAuthor("Raio-X Territorial")
    c.setSubject("Relatório do imóvel rural")
    if hasattr(c, "setLang"):
        c.setLang("pt-BR")

    ocupacoes: dict[int, float] = {}
    numero = 0
    for _chave, desenhar in folhas:
        numero += 1
        ocupacoes[numero] = desenhar(c, L, numero, grade)
        c.showPage()
    c.save()
    ms = round((time.monotonic() - inicio) * 1000)
    digest = hashlib.sha256(caminho.read_bytes()).hexdigest()
    corpo = sum(1 for chave, _ in folhas if not chave.startswith("ap_"))
    print(f"RX_BOOK_RENDER={ms}ms:paginas={numero}:bytes={caminho.stat().st_size}", flush=True)
    print(f"RX_BOOK_FOLHAS_CORPO={corpo}", flush=True)
    for pagina, oc in sorted(ocupacoes.items()):
        print(f"RX_BOOK_OCUPACAO_P{pagina}={int(oc * 100)}", flush=True)
    return digest


def problemas_de_redacao() -> list[str]:
    """Os problemas que o autoteste de redação achou no que acabou de ser desenhado."""
    return autoteste_redacao()


print("RX_BOOK_V10=motor_do_livro_carregado", flush=True)
