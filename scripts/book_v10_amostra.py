"""Amostra aprovável do relatório-livro v10 (parte PA, onda 0,5): p. 1, 3 e 7 de Curvelo.

SPEC_RELATORIO_LIVRO seções 2.1, 2.3, 2.7, 8.3 e 9.4, com as decisões do dono D1–D12 e as leituras
corrigidas gravadas na F1. Desenha, **sem compositor** (pilha de blocos medidos com mola, no lugar da P10),
as três páginas que o dono aprova antes da onda 2 (D12):

- p. 1 · ficha e resposta rápida: código do CAR como título, retrato Sentinel-2 com vizinhança e limite do
  CAR (D1 desligado), 4 fatos, "Em resumo", painel de 6 respostas e "O que isso significa para você";
- p. 3 · capítulo 1, "Tem desmatamento registrado?": mapa das manchas sobre a imagem esmaecida (1 dentro,
  3 toques na divisa), lupa na faixa de 2021, legenda com todas as manchas, linha do tempo com os três
  períodos legais, alerta recente (D9, sem nome da fonte no corpo) e a regra do crédito (Res. CMN 5.303/2026);
- p. 7 · capítulo 5, "A terra": rampas no ângulo real pelas classes de relevo da Embrapa (em %), leitura
  legal em graus, solo do mapa do IBGE, textura do MapBiomas Solo, aptidão e erosão da Embrapa.

Entradas: o estilo da P3 (``report_book_v10_style``) e a F1 congelada da P0
(``tests/fixtures/book/curvelo_corrigido.json``, hash conferido antes de desenhar). Nenhum número é
digitado aqui: todos saem dos Fatos ``_v51`` e do ``book_v10`` da F1, por um formatador provisório (regras
da seção 1.9) que a P1 substitui por ``report_book_fmt``. Os textos são provisórios da P2/P7: onde o modelo
longo da SPEC não cabe, entra a versão curta, escolhida por medida.

Saídas (pasta ``--saida``): ``amostra_livro_v10.pdf`` (3 páginas), ``p1.png``, ``p3.png`` e ``p7.png`` a
1440 px, as larguras 375 e 768 em ``larguras/`` e, com ``--farmscan``, ``lado_a_lado.png`` com as páginas
equivalentes do Farm-Scan (p. 1 e 4; p. 5; p. 6 e 8).

O script sai com erro (``LayoutError``) se um bloco não couber na área útil ou a cabeça de capítulo passar de
55 mm, e devolve 1 se uma página ficar abaixo de 80% de ocupação ou se um texto desenhado ferir o que o guia
de redação permite conferir por máquina (palavras proibidas, "multa" na p. 1, ponto decimal, frase acima de
30 palavras, média acima de 18). ``RX_AMOSTRA_SOLTO=1`` só serve para olhar uma página que ainda não cabe.

Uso:
    python scripts/book_v10_amostra.py --saida DIR [--farmscan Farm-Scan.pdf] [--grade]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import report_ptbr_v50  # noqa: E402

report_ptbr_v50.install()  # como em produção: o texto do livro não pode passar pelo normalizador

import report_book_v10_style as st  # noqa: E402
from report_book_v10_contracts import LayoutError  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
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
FIXTURE = ROOT / "tests" / "fixtures" / "book" / "curvelo_corrigido.json"
TOTAL_PAGINAS = 13  # mapa do livro de Curvelo (seção 2.0.1)

# Contorno de MG simplificado (malha do IBGE, qualidade mínima, simplificado a 0,05°), só para o
# encarte desta amostra. O asset definitivo é assets/geo/uf_simplificado.json (P3, ainda não feito).
_CONTORNO_UF = {
    "MG": [
        [-47.28, -18.04], [-47.95, -18.5], [-48.27, -18.33], [-48.92, -18.31], [-49.21, -18.41],
        [-49.37, -18.63], [-49.49, -18.53], [-49.79, -18.64], [-50.03, -18.6], [-50.08, -18.67],
        [-50.27, -18.68], [-50.54, -19.1], [-50.83, -19.31], [-50.83, -19.47], [-50.94, -19.47],
        [-50.93, -19.59], [-51.04, -19.69], [-51.0, -20.09], [-50.45, -19.79], [-50.04, -19.92],
        [-49.25, -19.97], [-49.31, -20.12], [-49.26, -20.26], [-49.18, -20.31], [-49.07, -20.15],
        [-48.99, -20.17], [-48.96, -20.4], [-48.89, -20.44], [-48.82, -20.16], [-48.22, -20.13],
        [-48.2, -20.05], [-48.08, -20.15], [-47.98, -20.04], [-47.9, -20.13], [-47.85, -19.99],
        [-47.64, -20.05], [-47.49, -19.97], [-47.23, -20.22], [-47.29, -20.42], [-47.1, -20.68],
        [-47.23, -20.86], [-47.01, -21.42], [-46.76, -21.36], [-46.51, -21.49], [-46.69, -21.84],
        [-46.62, -22.0], [-46.72, -22.08], [-46.6, -22.14], [-46.72, -22.31], [-46.46, -22.52],
        [-46.39, -22.66], [-46.47, -22.7], [-46.33, -22.77], [-46.34, -22.91], [-45.87, -22.87],
        [-45.71, -22.77], [-45.81, -22.74], [-45.68, -22.57], [-45.67, -22.65], [-45.48, -22.59],
        [-45.4, -22.65], [-45.12, -22.5], [-43.76, -22.06], [-43.35, -22.0], [-43.13, -22.03],
        [-43.14, -22.1], [-42.27, -21.71], [-42.35, -21.59], [-42.08, -21.03], [-42.15, -20.97],
        [-41.98, -20.94], [-41.87, -20.77], [-41.8, -20.48], [-41.86, -20.37], [-41.76, -20.21],
        [-41.41, -20.21], [-41.3, -19.94], [-41.19, -19.89], [-41.17, -19.67], [-40.97, -19.51],
        [-40.96, -19.12], [-41.06, -19.06], [-41.06, -18.94], [-41.24, -18.85], [-41.23, -18.8],
        [-40.92, -18.82], [-40.94, -18.69], [-41.05, -18.63], [-41.02, -18.46], [-41.18, -18.44],
        [-41.16, -18.31], [-41.05, -18.17], [-40.77, -18.11], [-40.9, -17.99], [-40.66, -18.01],
        [-40.53, -17.89], [-40.22, -17.98], [-40.22, -17.73], [-40.6, -17.36], [-40.58, -17.15],
        [-40.49, -16.89], [-40.28, -16.9], [-40.28, -16.57], [-40.16, -16.58], [-39.86, -16.11],
        [-39.91, -16.0], [-40.24, -15.8], [-40.52, -15.8], [-40.7, -15.67], [-41.14, -15.77],
        [-41.33, -15.74], [-41.36, -15.49], [-41.8, -15.1], [-41.95, -15.18], [-42.17, -15.09],
        [-42.27, -15.12], [-42.94, -14.71], [-43.17, -14.65], [-43.53, -14.82], [-43.88, -14.65],
        [-43.78, -14.34], [-43.97, -14.27], [-44.3, -14.25], [-44.56, -14.34], [-45.1, -14.75],
        [-45.21, -14.74], [-46.08, -15.26], [-45.97, -14.97], [-46.04, -14.87], [-46.18, -14.95],
        [-46.32, -14.9], [-46.32, -14.81], [-46.5, -14.7], [-46.56, -14.81], [-46.5, -15.05],
        [-46.93, -15.06], [-46.85, -15.37], [-46.95, -15.56], [-46.81, -15.87], [-47.31, -16.04],
        [-47.45, -16.46], [-47.23, -16.72], [-47.13, -16.98], [-47.51, -17.33], [-47.54, -17.46],
        [-47.26, -17.61], [-47.37, -17.83], [-47.28, -18.04],
    ],
}


# ===========================================================================
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
# Leitura da F1 (só Fatos _v51 e book_v10; nunca os campos antigos proibidos da seção 2.0.2)
# ===========================================================================
class Leitura:
    def __init__(self, payload: dict, pasta: Path):
        self.p = payload
        self.pasta = pasta
        self.caminhos = payload["_fixture"]["fatos"]

    def fato(self, chave: str) -> dict:
        alvo = self.p
        for parte in self.caminhos[chave].split("."):
            alvo = alvo[parte]
        return alvo

    def valor(self, chave: str):
        f = self.fato(chave)
        if f["estado"] not in ("encontrado", "nao_encontrado"):
            raise LayoutError(f"a amostra espera o fato {chave} respondido; veio {f['estado']}")
        return f["valor"]

    @property
    def book(self) -> dict:
        return self.p["book_v10"]

    def imagem(self, rel: str) -> Path:
        return self.pasta / rel


def conferir_hash_f1() -> None:
    """A amostra só vale sobre a F1 congelada: confere JSON e imagens contra o .sha256 da P0."""
    pasta = FIXTURE.parent
    linhas = (pasta / "curvelo_corrigido.sha256").read_text(encoding="utf-8").split("\n")
    conferidos = 0
    for linha in linhas:
        if not linha.strip():
            continue
        soma, nome = linha.split(None, 1)
        arquivo = pasta / nome.lstrip("*").strip()
        if hashlib.sha256(arquivo.read_bytes()).hexdigest() != soma.lower():
            raise SystemExit(f"F1 alterada: {arquivo.name} não bate com curvelo_corrigido.sha256")
        conferidos += 1
    if conferidos < 5:
        raise SystemExit(f"curvelo_corrigido.sha256 lista só {conferidos} arquivos (esperado JSON + 4 imagens)")


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
    base = y_topo - 5 * mm / 2 - altura_maiuscula("Sans-Sb", 7.8) / 2
    xx = x
    if nome_icone:
        icone(c, nome_icone, x, y_topo - 5 * mm, 5 * mm, cor, 1.0)
        xx += 5 * mm + 1.8 * mm
    rotulo(c, xx, base, t, "kicker", cor)
    return 5 * mm


# ===========================================================================
# Pilha vertical com mola (substitui o compositor só nesta amostra)
# ===========================================================================
@dataclass
class Bloco:
    nome: str
    altura: float
    desenhar: Callable  # (c, x, y_topo, altura)
    mola: tuple[float, float] | None = None
    espaco: float = 2  # em unidades de 7,5 pt, antes do bloco
    ancorar: bool = False  # só o último bloco: desenhado colado ao pé da área útil (FolhaSpec.ancorar_rodape)


def compor(c, blocos: list[Bloco], pagina: int, grade: bool = False) -> float:
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
    # a sobra abre os espaços entre blocos (até +1u cada) e o resto vai para o pé
    extra = min(G.us(1), sobra / max(1, len(blocos) - 1)) if sobra > 0 else 0
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


def limite_car(c, geom: dict, mapa: Mapa, grosso: float = 2.8, fino: float = 1.5) -> None:
    c.saveState()
    c.setLineJoin(1)
    c.setStrokeColor(COR["INK"])
    c.setStrokeAlpha(0.45)
    c.setLineWidth(grosso)
    c.drawPath(caminho_geom(c, geom, mapa), stroke=1, fill=0)
    c.setStrokeAlpha(1)
    c.setStrokeColor(COR["CREAM_LINE"])
    c.setLineWidth(fino)
    c.drawPath(caminho_geom(c, geom, mapa), stroke=1, fill=0)
    c.restoreState()


def pilula(c, x, y, w, h, alpha: float = 0.9) -> None:
    c.saveState()
    c.setFillColor(COR["CREAM"])
    c.setFillAlpha(alpha)
    c.roundRect(x, y, w, h, 1.6 * mm, stroke=0, fill=1)
    c.restoreState()


def escala_e_norte(c, mapa: Mapa, x, y) -> None:
    """Pílula com barra de escala (a opção que dá 18–30 mm) e seta norte de 5 mm."""
    escolha = 500
    for metros in (20, 50, 100, 200, 500, 1000):
        if 18 * mm <= metros * mapa.s <= 30 * mm:
            escolha = metros
            break
    barra = escolha * mapa.s
    w, h = barra + 16 * mm, 9 * mm
    pilula(c, x, y, w, h)
    bx, by = x + 3 * mm, y + 3.2 * mm
    c.saveState()
    c.setStrokeColor(COR["INK"])
    c.setFillColor(COR["INK"])
    c.setLineWidth(0.8)
    c.line(bx, by, bx + barra, by)
    for t in (0, 0.5, 1):
        c.line(bx + barra * t, by, bx + barra * t, by + (1.4 if t != 0.5 else 0.9) * mm)
    c.restoreState()
    linha_simples(c, bx + barra / 2, by - 2.3 * mm, _ptbr(Decimal(escolha)) + " m", "Sans", 7.8, COR["INK"], "centro")
    nx, ny = bx + barra + 6.5 * mm, y + 1.6 * mm
    c.saveState()
    c.setFillColor(COR["INK"])
    p = c.beginPath()
    p.moveTo(nx, ny + 5 * mm)
    p.lineTo(nx - 1.6 * mm, ny + 1.2 * mm)
    p.lineTo(nx, ny + 2 * mm)
    p.lineTo(nx + 1.6 * mm, ny + 1.2 * mm)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.restoreState()
    linha_simples(c, nx, ny - 0.4 * mm, "N", "Sans-Sb", 7.8, COR["INK"], "centro")


def encarte_uf(c, uf: str, lonlat, x, y, w=24 * mm, h=20 * mm) -> None:
    contorno = _CONTORNO_UF[uf]
    lons = [p[0] for p in contorno]
    lats = [p[1] for p in contorno]
    k = math.cos(math.radians(sum(lats) / len(lats)))
    lx, ly = (max(lons) - min(lons)) * k, max(lats) - min(lats)
    pilula(c, x, y, w, h)
    s = min((w - 5 * mm) / lx, (h - 5 * mm) / ly)
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


# ===========================================================================
# Componentes de página (versões de amostra dos da seção 1.6)
# ===========================================================================
KICKER_SIGNIFICADO = "O que isso significa para você"


def caixa_significado(itens: list[tuple[str | None, str]], largura: float = LARG, corrido: bool = False):
    """Fundo CREAM, barra FOREST de 2,6 pt, kicker e itens em ``caixa``. Devolve (altura, desenhar).

    ``corrido=True`` (p. 1, onde cada milímetro conta): o kicker entra na primeira linha do primeiro item,
    como título corrido de livro, em vez de ocupar uma linha própria.
    """
    pad_esq, pad = 6 * mm, (G.us(1) if corrido else 4 * mm)
    interno = largura - pad_esq - pad
    recuo = st.largura_espacejada(KICKER_SIGNIFICADO, "kicker") + 3 * mm if corrido else 0
    pars = [texto((f"<b>{perfil}</b> " if perfil else "") + t, "caixa", interno,
                  **({"firstLineIndent": recuo} if corrido and i == 0 else {}))
            for i, (perfil, t) in enumerate(itens)]
    cabeca = 0 if corrido else 5 * mm + 1.5 * mm
    altura = pad + cabeca + sum(p.altura for p in pars) + 1.2 * mm * (len(pars) - 1) + pad

    def desenhar(c, x, y_topo, h):
        c.saveState()
        c.setFillColor(COR["CREAM"])
        c.rect(x, y_topo - h, largura, h, stroke=0, fill=1)
        c.setStrokeColor(COR["FOREST"])
        c.setLineWidth(2.6)
        c.line(x + 1.3, y_topo - h, x + 1.3, y_topo)
        c.restoreState()
        y = y_topo - pad - (h - altura) / 2
        if corrido:
            rotulo(c, x + pad_esq, y - st.ESTILO["caixa"].fontSize, KICKER_SIGNIFICADO, "kicker", COR["FOREST"])
        else:
            kicker(c, x + pad_esq, y, KICKER_SIGNIFICADO, COR["FOREST"])
            y -= cabeca
        for par in pars:
            y -= par.desenhar(c, x + pad_esq, y) + 1.2 * mm

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
# PÁGINA 1 — ficha e resposta rápida (seção 2.1)
# ===========================================================================
def pagina_1(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 1
    p = L.p
    prop = p["property"]
    car = p["car"]
    b = L.book
    emitido = p["generated_at"]
    inscricao = L.valor("car")
    prodes = L.valor("prodes")
    occ = prodes["occurrences"]
    area = Decimal(str(prop["area_ha"]))
    dentro = [o for o in occ if o["classe"] == "dentro"]
    pos2019 = [o for o in occ if o["periodo_legal"] == "depois_2019_07_31"]
    pct_dentro = sum(Decimal(str(o["area_inside_ha"])) for o in dentro) / area * 100
    maior = max(dentro, key=lambda o: o["area_inside_ha"])
    anos_maior = f"{maior['prev_image_date'][:4]} e {maior['image_date'][:4]}"
    recente = pos2019[0]

    blocos: list[Bloco] = []

    # --- faixa de marca -----------------------------------------------------
    def marca(c) -> None:
        """Faixa de marca da p. 1 na altura do cabeçalho corrente das outras folhas (mesmo ritmo no topo)."""
        x, base = X0, G.pagina_a - G.cabecalho_base
        largura = rotulo(c, x, base, "Raio-X Territorial", "kicker", COR["FOREST"])
        linha_simples(c, x + largura + 2 * mm, base, "Relatório do imóvel rural", "Sans", 8.6, COR["INK2"])
        linha_simples(c, x + LARG, base, f"Emitido em {f_data_brasilia(emitido)}, às {f_hora_brasilia(emitido)} · "
                      f"{p['report_id']}", "Sans", 8.6, COR["INK2"], "dir")
        c.saveState()
        c.setStrokeColor(COR["FOREST"])
        c.setLineWidth(0.8)
        c.line(x, G.pagina_a - G.cabecalho_fio, x + LARG, G.pagina_a - G.cabecalho_fio)
        c.restoreState()

    # --- identidade (regra 5: sem nome validado, título = código do CAR) ------
    codigo = prop["car_code"]
    if prop.get("name") and str(prop.get("name_source", "")).startswith("SICAR:"):
        raise LayoutError("a F1 de Curvelo não tem nome validado; a variante com nome é da P11")
    partes = codigo.split("-", 2)
    titulo = texto(f"{partes[0]}-{partes[1]}-<br/>{partes[2]}", "codigo_titulo", LARG)
    # O rótulo "imóvel rural identificado pelo código do CAR" abre a frase explicativa (regra 5): assim a
    # identidade cabe em 2 linhas de título + 1 de legenda e sobra altura para o retrato.
    explica = texto("<b>Imóvel rural identificado pelo código do Cadastro Ambiental Rural (CAR).</b> O SICAR não "
                    "publica nome para este imóvel.", "legenda", LARG)
    alt_ident = titulo.altura + 1.4 * mm + explica.altura

    def identidade(c, x, y_topo, h):
        y = y_topo
        y -= titulo.desenhar(c, x, y) + 1.4 * mm
        explica.desenhar(c, x, y)

    blocos.append(Bloco("identidade", alt_ident, identidade, espaco=0))

    # --- retrato com vizinhança + coluna de fatos ----------------------------
    base_img = b["base_image"]
    geom = b["geometry"]
    nuvens = "sem nuvens" if Decimal(str(base_img["cloud_pct"])) < Decimal("0.5") else \
        f"com {f_pct(base_img['cloud_pct'])} de nuvens"
    leg_img = texto(f"Imagem Sentinel-2 de {f_data(base_img['date'])}, {nuvens}, com a vizinhança; cada ponto cobre "
                    f"{f_inteiro(base_img['resolution_m'])} × {f_inteiro(base_img['resolution_m'])} m. Linha clara: "
                    "limite declarado no CAR.",
                    "legenda", 104 * mm)
    fm = b["fiscal_module_ha"]
    mf = Decimal(str(car["fiscal_modules"]))
    unidade_mf = "módulo fiscal" if mf < 2 else "módulos fiscais"
    leg_mf = (f"pela conta do próprio CAR, 1 módulo fiscal em {prop['municipality']} tem cerca de "
              f"{f_inteiro(fm['value'])} ha") if fm["origin"] == "conta_car" else \
        f"em {prop['municipality']}, 1 módulo fiscal = {f_inteiro(fm['value'])} ha"
    situacao = {"AT": "Ativo", "PE": "Pendente", "SU": "Suspenso", "CA": "Cancelado"}[car["status"]]
    analise = {"Aguardando análise": "ainda não conferido pelo órgão ambiental"}.get(car["analysis_status"])
    leg_cad = f"inscrito no CAR em {f_data(inscricao)}" + (f"; {analise}" if analise else "")
    col_w = LARG - 104 * mm - G.calha
    fatos = [
        ("Localização", f"{prop['municipality']} ({prop['uf']})", "município onde o imóvel está"),
        ("Área declarada no CAR", f_ha(area), f_ha_extenso(area)),
        ("Tamanho", f"{_ptbr(_q(mf, 2))} {unidade_mf}", leg_mf),
        ("Cadastro", situacao, leg_cad),
    ]
    fatos_t = [(r, texto(v, "fato_valor", col_w), texto(lg, "legenda", col_w)) for r, v, lg in fatos]
    passo_fato = 2.6 * mm
    alt_fatos = sum(4 * mm + v.altura + 0.6 * mm + lg.altura for _, v, lg in fatos_t) + passo_fato * 3
    img_min, img_max = 70 * mm, 88 * mm
    fixo_img = 1.8 * mm + leg_img.altura
    mola_min = max(img_min + fixo_img, alt_fatos)

    def retrato(c, x, y_topo, h):
        ih = h - fixo_img
        iw = 104 * mm
        lim = base_img["bounds_m"]
        caixa = caixa_na_proporcao(((lim[0] + lim[2]) / 2, (lim[1] + lim[3]) / 2), lim[2] - lim[0], ih / iw, lim)
        if caixa[3] - caixa[1] > lim[3] - lim[1] + 0.01:  # a figura é mais alta que a imagem: corta a largura
            altura_m = lim[3] - lim[1]
            largura_m = altura_m * iw / ih
            caixa = caixa_na_proporcao(((lim[0] + lim[2]) / 2, (lim[1] + lim[3]) / 2), largura_m, ih / iw, lim)
        mapa = Mapa(x, y_topo - ih, iw, ih, caixa)
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y_topo - ih, iw, ih, 3 * mm)
        c.clipPath(clip, stroke=0, fill=0)
        mapa.imagem(c, L.imagem(base_img["path"]), lim)
        limite_car(c, geom["car_m"], mapa)
        c.restoreState()
        escala_e_norte(c, mapa, x + 3 * mm, y_topo - ih + 3 * mm)
        encarte_uf(c, geom["uf"], geom["centroid_lonlat"], x + iw - 27 * mm, y_topo - ih + 3 * mm)
        leg_img.desenhar(c, x, y_topo - ih - 1.8 * mm)
        # coluna de fatos
        xf = x + iw + G.calha
        y = y_topo
        for i, (r, v, lg) in enumerate(fatos_t):
            if i:
                c.saveState()
                c.setStrokeColor(COR["RULE"])
                c.setLineWidth(G.fio_fino)
                c.line(xf, y + passo_fato / 2, xf + col_w, y + passo_fato / 2)
                c.restoreState()
            rotulo(c, xf, y - altura_maiuscula("Sans-Sb", 7.8), r, "tab_cab")
            y -= 4 * mm
            y -= v.desenhar(c, xf, y) + 0.6 * mm
            y -= lg.altura
            lg.desenhar(c, xf, y + lg.altura)
            y -= passo_fato

    blocos.append(Bloco("retrato", mola_min, retrato, mola=(mola_min, max(mola_min, img_max + fixo_img)), espaco=1))

    # --- em resumo (kicker corrido na 1ª linha; no máximo 3 linhas em 170 mm) ---------------
    nucleo_ausente = all(L.fato(k)["estado"] == "nao_encontrado" for k in
                         ("embargo_ibama", "embargo_icmbio", "autos", "ti", "uc", "quilombola", "assentamento",
                          "floresta_publica", "anm"))
    recuo_resumo = st.largura_espacejada("Em resumo", "kicker") + 3 * mm
    frase_ausencia = (" Nas bases consultadas, não apareceu embargo, área protegida nem processo de mineração sobre "
                      "o imóvel." if nucleo_ausente else "")
    ausencia_curta = (" Não apareceu embargo, área protegida nem processo de mineração."
                      if nucleo_ausente else "")
    detalhe = (f"{f_faixa_parte(pct_dentro).capitalize()} teve a vegetação nativa retirada entre {anos_maior}; "
               f"em {recente['year']}, uma faixa de {f_ha(recente['area_inside_ha'])} na divisa.")
    variantes = (  # modelo longo da seção 2.1 e as versões curtas, na ordem; entra a primeira que cabe em 3 linhas
        f"O que mais pede atenção é o desmatamento mapeado pelo INPE. {detalhe}{frase_ausencia}",
        f"O que mais pede atenção é o desmatamento: {detalhe[0].lower()}{detalhe[1:]}{ausencia_curta}",
        f"O que mais pede atenção é o desmatamento. {detalhe}{frase_ausencia}",
        f"O que mais pede atenção é o desmatamento mapeado pelo INPE. {detalhe}",
    )
    for modelo in variantes:
        resumo = texto(modelo, "resposta", LARG, registrar=False, firstLineIndent=recuo_resumo)
        if resumo.linhas <= 3:
            REGISTRO.append((1, modelo))
            break
    else:
        raise LayoutError(f"p. 1: 'Em resumo' com {resumo.linhas} linhas (máximo 3)")

    def em_resumo(c, x, y_topo, h):
        rotulo(c, x, y_topo - st.ESTILO["resposta"].fontSize, "Em resumo", "kicker", COR["FOREST"])
        resumo.desenhar(c, x, y_topo)

    blocos.append(Bloco("em_resumo", resumo.altura, em_resumo, espaco=1))

    # --- painel de 6 respostas -------------------------------------------------
    solo = L.valor("textura_solo")
    relevo = L.valor("relevo")
    uso = L.valor("mapbiomas")
    clima = L.valor("climatologia")
    pocos = L.valor("pocos")
    outorgas = L.valor("outorga")
    classes_uso = uso["classes"]
    pcts_uso = f_pct_composicao([x["pct"] for x in classes_uso])
    principal = max(range(len(classes_uso)), key=lambda i: classes_uso[i]["ha"])
    nomes_uso = {"Pastagem": "pasto"}
    ate_8 = sum(x["pct"] for x in relevo["classes_embrapa_pct"] if x["classe"] in ("plano", "suave ondulado"))
    terreno = "pouco inclinado" if ate_8 >= 70 else ("inclinado em parte" if ate_8 >= 30 else "inclinado")
    chuvosos, secos = clima["meses_chuvosos"], clima["meses_secos"]

    def intervalo(meses):  # meses contíguos, podendo virar o ano (11, 12, 1, 2, 3)
        ordem = sorted(meses)
        for i in range(len(ordem)):
            seq = ordem[i:] + ordem[:i]
            if all((seq[j] % 12) + 1 == seq[j + 1] for j in range(len(seq) - 1)):
                return MESES[seq[0] - 1], MESES[seq[-1] - 1]
        raise LayoutError("meses não contíguos")

    ch_de, ch_ate = intervalo(chuvosos)
    se_de, se_ate = intervalo(secos)
    dentro_outorga = [g for g in outorgas if g["posicao"] == "dentro"]
    nivel = pocos["nivel_estatico_mediano_m"]["valor"]
    sigef = L.fato("sigef")
    atencao_prodes = bool(pos2019) or any(o["periodo_legal"] != "ate_2008_07_22" for o in dentro)
    estados_embargo = {L.fato(k)["estado"] for k in ("embargo_ibama", "embargo_icmbio", "autos")}
    estados_area = {L.fato(k)["estado"] for k in ("ti", "uc", "quilombola", "assentamento", "floresta_publica", "anm")}

    def pior(estados):
        for e in ("pendente", "encontrado", "nao_encontrado"):
            if e in estados:
                return e
        raise LayoutError(f"estados inesperados: {estados}")

    sigef_frase = {
        "nao_encontrado": f" Não há parcela certificada no SIGEF (INCRA) sobre o imóvel, na base de "
                          f"{f_data(sigef['data_base'])}.",
        "pendente": " A consulta ao INCRA está pendente.",
    }.get(sigef["estado"], "")
    sigef_curta = {
        "nao_encontrado": f" Nenhuma parcela certificada no SIGEF (INCRA) na base de {f_data(sigef['data_base'])}.",
        "pendente": " Consulta ao INCRA pendente.",
    }.get(sigef["estado"], "")
    cartoes = [
        ("folha", "Tem desmatamento registrado?", ("encontrado", atencao_prodes),
         f"Sim. Vegetação nativa retirada em {f_pct(pct_dentro)} da área entre {anos_maior}. Depois de 31/07/2019, "
         f"só uma faixa de {f_ha(recente['area_inside_ha'])} na divisa, em {recente['year']}.",
         f"Sim. {f_pct(pct_dentro)} da área, entre {anos_maior}; 1 registro na divisa depois de 31/07/2019.", 3),
        ("carimbo", "Tem embargo ou auto de infração no local?", (pior(estados_embargo), False),
         "Não. Nenhum embargo do IBAMA ou do ICMBio no mapa de embargos sobre a área, nem auto de infração do "
         "IBAMA com local dentro dela.",
         "Não. Nenhum embargo no mapa do IBAMA ou do ICMBio, nem auto com local no imóvel.", 4),
        ("escudo", "Está em área protegida ou tem mineração?", (pior(estados_area), False),
         "Não. Fora de terra indígena, unidade de conservação, território quilombola, assentamento e floresta "
         "pública. Nenhum processo de mineração na ANM.",
         "Não. Fora das áreas protegidas consultadas; nenhum processo de mineração na ANM.", 4),
        ("documento", "O que os documentos provam?", None,
         "O CAR mostra onde fica e o que foi declarado; não prova quem é o dono." + sigef_frase,
         "O CAR não prova quem é o dono." + sigef_curta, 5),
        ("gota", "Tem água?", None,
         f"Nos poços cadastrados da região, a água costuma estar a uns {f_metros(nivel)}. Há "
         f"{'uma outorga' if len(dentro_outorga) == 1 else f'{len(dentro_outorga)} outorgas'} do IGAM para poço "
         "dentro do imóvel.",
         f"Água a uns {f_metros(nivel)} nos poços da região; {len(dentro_outorga)} outorga dentro do imóvel.", 6),
        ("broto", "Como é a terra e o clima?", None,
         f"{nomes_uso.get(classes_uso[principal]['classe'], 'uso').capitalize()} na maior parte "
         f"({pcts_uso[principal]}%), terreno {terreno}, solo {SOLO_ADJETIVO[solo['classe_textural']]}. Chove de {ch_de} a "
         f"{ch_ate}; quase nada de {se_de} a {se_ate}.",
         f"{nomes_uso.get(classes_uso[principal]['classe'], 'uso').capitalize()} ({pcts_uso[principal]}%); "
         f"chuvas de {ch_de} a {ch_ate}.", 7),
    ]
    card_w, card_h = G.col2, 27 * mm

    def montar_cartao(dados):
        nome_ic, pergunta, selo, longo, curto, pag = dados
        ref = f' <font name="Sans-Sb" size="8.6" color="{st.HEX["FOREST"]}">→&nbsp;pág.&nbsp;{pag}</font>'
        for modelo in (longo, curto):
            t = texto(modelo + ref, "painel_resposta", card_w, registrar=False)
            if t.linhas <= 3:
                REGISTRO.append((1, modelo))
                REGISTRO.append((1, pergunta))
                return nome_ic, pergunta, selo, t
        raise LayoutError(f"cartão '{pergunta}': o texto curto passa de 3 linhas")

    cartoes_t = [montar_cartao(d) for d in cartoes]

    def painel(c, x, y_topo, h):
        for i, (nome_ic, pergunta, selo, resp) in enumerate(cartoes_t):
            col, lin = i % 2, i // 2
            cx = x + col * (card_w + G.calha)
            cy = y_topo - lin * (card_h + G.us(1))
            c.saveState()
            c.setStrokeColor(COR["RULE"])
            c.setLineWidth(0.5)
            c.line(cx, cy, cx + card_w, cy)
            c.restoreState()
            y = cy - 1.4 * mm
            if selo:
                st.desenhar_selo(c, cx, y - 4.2 * mm, *selo)
            y -= 4.2 * mm + 1 * mm
            icone(c, nome_ic, cx, y - 6 * mm + 0.6 * mm, 6 * mm, COR["INK"], 1.0)
            base = y - 4.6 * mm + 0.9 * mm
            c.saveState()
            c.setFont("Sans-Sb", 10.5)
            c.setFillColor(COR["INK"])
            c.drawString(cx + 7.5 * mm, base, pergunta)
            c.restoreState()
            y -= 4.6 * mm + 1.4 * mm
            resp.desenhar(c, cx, y)

    blocos.append(Bloco("painel", 3 * card_h + 2 * G.us(1), painel, espaco=1))

    # --- o que isso significa para você, com a nota da consulta, ancorada no pé -----------
    antes_2008 = all(o["periodo_legal"] == "ate_2008_07_22" for o in dentro)
    alt_cx, des_cx = caixa_significado([
        (None, "Comece pelo desmatamento (pág. 3): "
               + ("a retirada dentro do imóvel é anterior a 22/07/2008, " if antes_2008 else "")
               + f"e a faixa de {recente['year']} na divisa pode aparecer na conferência do crédito rural, que "
               f"começa em {f_data(L.valor('regra_credito')['inicio_exigencia'])}. "
               f"Consultas feitas em {f_data_brasilia(emitido)} em bases públicas oficiais; não substituem certidão "
               "de cartório, vistoria ou laudo técnico."),
    ], corrido=True)
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))
    ocupacao = compor(c, blocos, 1, grade)
    marca(c)
    st.desenhar_moldura(c, 1, TOTAL_PAGINAS)
    return ocupacao


# ===========================================================================
# PÁGINA 3 — capítulo 1, desmatamento (seção 2.3, leitura corrigida)
# ===========================================================================
PERIODO_COR = {"ate_2008_07_22": "PRODES_ATE_2008", "ate_2019_07_31": "PRODES_ATE_2019",
               "depois_2019_07_31": "PRODES_DEPOIS_2019"}
PERIODO_ORDEM = {"depois_2019_07_31": 3, "ate_2019_07_31": 2, "ate_2008_07_22": 1}


def pagina_3(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 3
    p = L.p
    fato = L.fato("prodes")
    val = fato["valor"]
    occ = val["occurrences"]
    area = Decimal(str(p["property"]["area_ha"]))
    bioma = L.valor("bioma")["bioma"]
    dentro = [o for o in occ if o["classe"] == "dentro"]
    divisa = [o for o in occ if o["classe"] == "divisa"]
    pos2019 = [o for o in occ if o["periodo_legal"] == "depois_2019_07_31"]
    atencao = bool(pos2019) or any(o["periodo_legal"] != "ate_2008_07_22" for o in dentro)
    maior = max(dentro, key=lambda o: o["area_inside_ha"])
    pct_d = Decimal(str(maior["area_inside_ha"])) / area * 100
    quase_fora = all(Decimal(str(o["feature_area_ha"])) / Decimal(str(o["area_inside_ha"])) >= 10 for o in divisa)
    recente = pos2019[0]
    regra = L.valor("regra_credito")
    alertas = L.fato("alertas_mensais")
    b = L.book
    geom = b["geometry"]
    figuras = b["prodes_figuras"]
    consulta = fato["consultado_em"]
    rodape = f"CAR {p['property']['car_code'][:11]}…{p['property']['car_code'][-8:]} · emitido em " \
             f"{f_data_brasilia(p['generated_at'])}"
    blocos: list[Bloco] = []

    # --- cabeça -------------------------------------------------------------------
    numeros = {1: "uma", 2: "duas", 3: "três", 4: "quatro"}
    periodo = f"entre {f_mes_ano(maior['prev_image_date'])} e {f_mes_ano(maior['image_date'])}"
    resposta = (
        f"Sim. O Instituto Nacional de Pesquisas Espaciais (INPE) mapeou vegetação nativa retirada em "
        f"{f_faixa_parte(pct_d)}, {periodo}. Outras {numeros[len(divisa)]} manchas só encostam na divisa; uma é "
        f"de {recente['year']}.",
        f"Sim. O mapa de desmatamento do Instituto Nacional de Pesquisas Espaciais (INPE) mostra a vegetação "
        f"nativa retirada em {f_faixa_parte(pct_d)}, {periodo}. Outras {numeros[len(divisa)]} manchas"
        f"{', quase inteiras fora do imóvel,' if quase_fora else ''} encostam na divisa; uma delas é de "
        f"{recente['year']}, depois de 31/07/2019.",
        f"Sim. O mapa do Instituto Nacional de Pesquisas Espaciais (INPE) mostra a vegetação nativa retirada em "
        f"{f_faixa_parte(pct_d)}, {periodo}. Outras {numeros[len(divisa)]} manchas só encostam na divisa; uma "
        f"é de {recente['year']}, depois de 31/07/2019.",
        f"Sim. O mapa do INPE mostra a vegetação nativa retirada em {f_faixa_parte(pct_d)}, {periodo}. Outras "
        f"{numeros[len(divisa)]} manchas só encostam na divisa; uma é de {recente['year']}, depois de 31/07/2019.",
    )
    nota_bioma = {"Cerrado": "No Cerrado, até 2012 o mapa saiu a cada dois anos; por isso a data diz entre quais "
                             "imagens a retirada foi percebida.",
                  "Amazônia": "Na Amazônia o mapa é anual."}.get(bioma, "")
    nota = (f"INPE, programa PRODES, feito com imagens de satélite. {nota_bioma} Consulta de "
            f"{f_data_brasilia(consulta)}, {f_hora_brasilia(consulta)}.")
    alt, des = cabeca_capitulo(1, "Desmatamento", "Tem desmatamento registrado?", resposta, nota, "folha",
                               ("encontrado", atencao))
    blocos.append(Bloco("cabeca", alt, des, espaco=0))

    # --- mapa + legenda-tabela --------------------------------------------------------
    ordenadas = sorted(occ, key=lambda o: (PERIODO_ORDEM[o["periodo_legal"]], o["area_inside_ha"]), reverse=True)
    leg_w = LARG - 110 * mm - G.calha
    destaque_valor = f_ha(maior["area_inside_ha"])
    destaque_leg = texto(f"dentro do imóvel, {f_pct(pct_d)} da área<br/>entre {f_mes_ano_curto(maior['prev_image_date'])} "
                         f"e {f_mes_ano_curto(maior['image_date'])}", "legenda", leg_w)
    entradas = []
    for o in ordenadas:
        pct_o = Decimal(str(o["area_inside_ha"])) / area * 100
        valores = f"{f_ha(o['area_inside_ha'])} · {f_pct(pct_o)}".replace(" ha", "&nbsp;ha")
        if o["classe"] == "dentro":
            linha = f"<b>{o['year']}</b> · dentro do imóvel<br/>{valores}"
        else:
            linha = f"<b>{o['year']}</b> · na divisa<br/>faixa de uns {f_metros(o['inner_mean_width_m'])} · {valores}"
        entradas.append((o, texto(linha, "legenda", leg_w - 6 * mm)))
    chave_car = texto("limite declarado no CAR", "legenda", leg_w - 9 * mm)
    chave_inpe = texto("mancha inteira mapeada pelo INPE", "legenda", leg_w - 9 * mm)
    alt_leg = (11.5 * mm + destaque_leg.altura + 3 * mm + 4.5 * mm + sum(t.altura + 1.6 * mm for _, t in entradas)
               + 1.8 * mm + chave_car.altura + chave_inpe.altura)
    comp = f_comparar_area(recente["area_inside_ha"])
    legenda_mapa = texto(
        f"Fundo: Sentinel-2 de {f_data(b['base_image']['date'])}, esmaecida. A mancha de {recente['year']} tem "
        f"{f_ha(recente['feature_area_ha'])}; só {f_ha(recente['area_inside_ha'])}"
        f"{', ' + comp + ',' if comp else ''} entram neste CAR.", "legenda", 110 * mm)
    fixo_mapa = 1.6 * mm + legenda_mapa.altura
    mola_min = max(70 * mm, alt_leg - fixo_mapa) + fixo_mapa

    def mapa_bloco(c, x, y_topo, h):
        mh = h - fixo_mapa
        mw = 110 * mm
        lim = b["base_image"]["bounds_m"]
        # Enquadramento das manchas: a caixa cabe o CAR e a mancha inteira de cada ocorrência, com margem,
        # na proporção da figura; sobra à direita para a lupa não cobrir mancha nenhuma.
        xs, ys = [], []
        for g in [geom["car_m"]] + [f["feature_geom_m"] for f in figuras.values()]:
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            for poly in polys:
                for e, n in poly[0]:
                    xs.append(e)
                    ys.append(n)
        alt_m = min(lim[3] - lim[1], (max(ys) - min(ys)) + 80)
        larg_m = max(alt_m * mw / mh, (max(xs) - min(xs)) + 300 + 600)
        alt_m = larg_m * mh / mw
        x_min = max(lim[0], min(min(xs) - 300, lim[2] - larg_m))
        y_min = max(lim[1], min(min(ys) - 40, lim[3] - alt_m))
        caixa = (x_min, y_min, x_min + larg_m, y_min + alt_m)
        mapa = Mapa(x, y_topo - mh, mw, mh, caixa)
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y_topo - mh, mw, mh, 3 * mm)
        c.clipPath(clip, stroke=0, fill=0)
        mapa.imagem(c, L.imagem(b["base_image"]["path_muted"]), lim)
        desenhar_manchas(c, mapa, occ, figuras, geom)
        c.restoreState()
        # lupa na ocorrência de divisa depois de 31/07/2019
        alvo = figuras[recente["id"]]["inside_geom_m"]
        pts = [pt for poly in ([alvo["coordinates"]] if alvo["type"] == "Polygon" else alvo["coordinates"])
               for pt in poly[0]]
        ce = (min(e for e, _ in pts) + max(e for e, _ in pts)) / 2
        cn = (min(n for _, n in pts) + max(n for _, n in pts)) / 2
        r = 15 * mm
        lx, ly = x + mw - r - 4 * mm, y_topo - mh + r + 4 * mm
        tx, ty = mapa.pt(ce, cn)
        c.saveState()
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.5)
        ang = math.atan2(ty - ly, tx - lx)
        c.line(lx + r * math.cos(ang), ly + r * math.sin(ang), tx - 2.2 * mm * math.cos(ang),
               ty - 2.2 * mm * math.sin(ang))
        c.setLineWidth(0.7)
        c.circle(tx, ty, 2.2 * mm, stroke=1, fill=0)
        c.restoreState()
        escala_lupa = (2 * r - 6 * mm) / max(90.0, max(e for e, _ in pts) - min(e for e, _ in pts) + 30)
        lado_m = 2 * r / escala_lupa
        mapa_l = Mapa(lx - r, ly - r, 2 * r, 2 * r, (ce - lado_m / 2, cn - lado_m / 2, ce + lado_m / 2, cn + lado_m / 2))
        c.saveState()
        c.setFillColor(COR["PAPER"])
        c.circle(lx, ly, r + 0.9 * mm, stroke=0, fill=1)
        clip = c.beginPath()
        clip.circle(lx, ly, r)
        c.clipPath(clip, stroke=0, fill=0)
        mapa_l.imagem(c, L.imagem(b["base_image"]["path_muted"]), lim)
        desenhar_manchas(c, mapa_l, [recente], {recente["id"]: figuras[recente["id"]]}, geom, fino=True)
        c.restoreState()
        c.saveState()
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.8)
        c.circle(lx, ly, r, stroke=1, fill=0)
        c.restoreState()
        rotulo_lupa = f"{recente['year']} · faixa de uns {f_metros(recente['inner_mean_width_m'])}"
        wl = pdfmetrics.stringWidth(rotulo_lupa, "Sans-Sb", 7.8) + 4 * mm
        pilula(c, lx - wl / 2, ly + r + 1.2 * mm, wl, 4.6 * mm, 0.95)
        linha_simples(c, lx, ly + r + 2.7 * mm, rotulo_lupa, "Sans-Sb", 7.8, COR["INK"], "centro")
        escala_e_norte(c, mapa, x + 3 * mm, y_topo - mh + 3 * mm)
        legenda_mapa.desenhar(c, x, y_topo - mh - 1.6 * mm)

        # coluna da legenda
        xl = x + mw + G.calha
        y = y_topo
        c.saveState()
        c.setFillColor(COR["INK"])
        c.setFont("Disp", 30)
        valor, unidade = destaque_valor.rsplit(" ", 1)
        c.drawString(xl, y - 9 * mm, valor)
        wv = pdfmetrics.stringWidth(valor, "Disp", 30)
        c.setFont("Sans", 11)
        c.drawString(xl + wv + 1.2 * mm, y - 9 * mm, unidade)
        c.restoreState()
        REGISTRO.append((3, destaque_valor))
        c.saveState()
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.8)
        c.line(xl, y, xl + leg_w, y)
        c.restoreState()
        y -= 11.5 * mm
        y -= destaque_leg.desenhar(c, xl, y) + 3 * mm
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        c.line(xl, y + 1.2 * mm, xl + leg_w, y + 1.2 * mm)
        c.restoreState()
        rotulo(c, xl, y - altura_maiuscula("Sans-Sb", 7.8) - 0.6 * mm, "Todas as manchas", "tab_cab")
        y -= 4.5 * mm
        for o, t in entradas:
            c.saveState()
            c.setFillColor(COR[PERIODO_COR[o["periodo_legal"]]])
            c.setStrokeColor(COR["INK"])
            c.setLineWidth(0.5 if o["periodo_legal"] != "depois_2019_07_31" else 0.6)
            c.rect(xl, y - 4 * mm, 4 * mm, 4 * mm, stroke=1, fill=1)
            c.restoreState()
            y -= t.desenhar(c, xl + 6 * mm, y + 0.6 * mm) + 1.6 * mm
        y -= 1.8 * mm
        # chaves de linha
        c.saveState()
        c.setLineCap(1)
        yy = y - 2 * mm
        c.setStrokeColor(COR["INK"])
        c.setStrokeAlpha(0.45)
        c.setLineWidth(2.8)
        c.line(xl + 0.5 * mm, yy, xl + 6.5 * mm, yy)
        c.setStrokeAlpha(1)
        c.setStrokeColor(COR["CREAM_LINE"])
        c.setLineWidth(1.5)
        c.line(xl + 0.5 * mm, yy, xl + 6.5 * mm, yy)
        c.restoreState()
        y -= chave_car.desenhar(c, xl + 9 * mm, y + 0.6 * mm)
        c.saveState()
        yy = y - 2 * mm
        c.setStrokeColor(COR["INK2"])
        c.setLineWidth(0.6)
        c.setDash(1.6, 1.2)
        c.line(xl + 0.5 * mm, yy, xl + 6.5 * mm, yy)
        c.restoreState()
        chave_inpe.desenhar(c, xl + 9 * mm, y + 0.6 * mm)

    blocos.append(Bloco("mapa", mola_min, mapa_bloco, mola=(mola_min, 100 * mm + fixo_mapa), espaco=2))

    # --- linha do tempo -------------------------------------------------------------------
    def linha_tempo(c, x, y_topo, h):
        """Linha do tempo com a faixa dos três períodos legais nas mesmas cores das manchas do mapa."""
        ano0, ano1 = 2000, int(p["generated_at"][:4]) + 1
        eixo_y = y_topo - 15.2 * mm
        faixa_h = 1.8 * mm
        faixa_topo = eixo_y - 0.9 * mm

        def xa(iso):
            a, m, d = (int(v) for v in iso[:10].split("-"))
            frac = (datetime(a, m, d) - datetime(a, 1, 1)).days / 365.0
            return x + (a + frac - ano0) / (ano1 - ano0) * LARG

        periodos = ((f"{ano0}-01-01", "2008-07-22", "ate_2008_07_22", "até 22/07/2008"),
                    ("2008-07-22", regra["marco"], "ate_2019_07_31", "de 23/07/2008 a 31/07/2019"),
                    (regra["marco"], f"{ano1}-01-01", "depois_2019_07_31", "depois de 31/07/2019"))
        for ini, fim, periodo, rot in periodos:
            x1, x2 = xa(ini), xa(fim)
            c.saveState()
            c.setFillColor(COR[PERIODO_COR[periodo]])
            c.rect(x1, faixa_topo - faixa_h, x2 - x1, faixa_h, stroke=0, fill=1)
            c.restoreState()
            linha_simples(c, (x1 + x2) / 2, faixa_topo - faixa_h - 7.6 * mm, rot, "Sans-Sb", 7.8, COR["INK2"], "centro")
        c.saveState()
        c.setStrokeColor(COR["INK2"])
        c.setLineWidth(0.6)
        c.line(x, eixo_y, x + LARG, eixo_y)
        c.setStrokeColor(COR["PAPER"])
        c.setLineWidth(0.8)
        for iso in ("2008-07-22", regra["marco"]):  # corte branco entre os períodos
            c.line(xa(iso), faixa_topo, xa(iso), faixa_topo - faixa_h)
        c.restoreState()
        for a in range(ano0, ano1 + 1, 4):
            xx = x + (a - ano0) / (ano1 - ano0) * LARG
            alinhar = "esq" if a == ano0 else "centro"  # o primeiro ano não sai da área útil
            linha_simples(c, xx, faixa_topo - faixa_h - 3.8 * mm, str(a), "Sans", 7.8, COR["INK2"], alinhar)
        # marco do crédito rural: tracejado do eixo ao alto, rótulo à esquerda
        xm = xa(regra["marco"])
        c.saveState()
        c.setStrokeColor(COR["INK2"])
        c.setLineWidth(0.6)
        c.setDash(1.4, 1.2)
        c.line(xm, faixa_topo - faixa_h, xm, y_topo - 0.6 * mm)
        c.restoreState()
        linha_simples(c, xm - 1.4 * mm, y_topo - 3 * mm, "31/07/2019 · marco da regra do crédito rural "
                      "(Res. CMN 5.303/2026)", "Sans", 7.8, COR["INK2"], "dir")
        for o in sorted(occ, key=lambda o: o["image_date"]):
            xx = xa(o["image_date"])
            cor = COR[PERIODO_COR[o["periodo_legal"]]]
            if o["prev_image_date"]:  # período percebido: da imagem anterior até a data da imagem
                c.saveState()
                c.setStrokeColor(cor if o["periodo_legal"] != "ate_2008_07_22" else COR["SOLO_3"])
                c.setLineWidth(2.2)
                c.setLineCap(1)
                c.line(xa(o["prev_image_date"]), eixo_y, xx, eixo_y)
                c.restoreState()
            c.saveState()
            if o["classe"] == "dentro":
                c.setFillColor(cor)
                c.setStrokeColor(COR["INK"])
                c.setLineWidth(0.5)
                c.circle(xx, eixo_y, 2.1 * mm, stroke=1, fill=1)
                rot = f"{o['prev_image_date'][:4]}–{o['year']} · {f_ha(o['area_inside_ha'])} dentro"
                nivel, fonte = 8.6 * mm, "Sans-Sb"
            else:
                c.setFillColor(COR["PAPER"])
                c.setStrokeColor(cor if o["periodo_legal"] != "ate_2008_07_22" else COR["SOLO_3"])
                c.setLineWidth(1.6)
                c.circle(xx, eixo_y, 1.3 * mm, stroke=1, fill=1)
                rot = f"{o['year']} · na divisa"
                nivel, fonte = 4.4 * mm, "Sans"
            c.restoreState()
            c.saveState()
            c.setStrokeColor(COR["RULE"])
            c.setLineWidth(0.35)
            c.line(xx, eixo_y + 2.4 * mm, xx, eixo_y + nivel - 1 * mm)
            c.restoreState()
            largura = pdfmetrics.stringWidth(rot, fonte, 7.8)
            # o rótulo não atravessa o tracejado do marco nem sai da área útil
            cx = min(max(xx, x + largura / 2), x + LARG - largura / 2)
            if cx - largura / 2 < xm < cx + largura / 2:
                cx = xm + 1.4 * mm + largura / 2 if xx > xm else xm - 1.4 * mm - largura / 2
            linha_simples(c, cx, eixo_y + nivel, rot, fonte, 7.8, COR["INK"], "centro")

    blocos.append(Bloco("linha_tempo", 26.5 * mm, linha_tempo, espaco=2))

    # --- alertas recentes (D9: sem nome da fonte no corpo) ---------------------------------
    lim_alerta = re.search(r"a partir de ([\d,]+) ha", " ".join(alertas["limites"]))
    frase_alerta = texto(f"Nenhum alerta recente nem validado sobre o imóvel em "
                         f"{f_data_brasilia(alertas['consultado_em'])}"
                         f"{' (alertas a partir de ' + lim_alerta.group(1) + ' ha)' if lim_alerta else ''}.",
                         "quadro_frase", LARG)
    alt_alerta = 1.6 * mm + 4.8 * mm + frase_alerta.altura + 1.6 * mm

    def alerta(c, x, y_topo, h):
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        c.line(x, y_topo, x + LARG, y_topo)
        c.line(x, y_topo - h, x + LARG, y_topo - h)
        c.restoreState()
        linha_simples(c, x, y_topo - 1.6 * mm - 3.4 * mm, "Tem alerta recente de desmatamento?", "Sans-Sb", 10.5,
                      COR["INK"])
        frase_alerta.desenhar(c, x, y_topo - 1.6 * mm - 4.8 * mm)
        largura = st.largura_selo(alertas["estado"], False)
        st.desenhar_selo(c, x + LARG - largura, y_topo - 1.6 * mm - 4.2 * mm + 0.2 * mm, alertas["estado"], False)

    blocos.append(Bloco("alertas", alt_alerta, alerta, espaco=1))

    # --- o que isso significa para você -------------------------------------------------------
    antes_2008 = all(o["periodo_legal"] == "ate_2008_07_22" for o in dentro)
    faixa_txt = {"ate_4": "de até 4 módulos fiscais", "4_a_15": "com mais de 4 e até 15 módulos fiscais",
                 "acima_15": "com mais de 15 módulos fiscais"}[regra["faixa_modulos"]]
    emissao = p["generated_at"][:10]
    verbo, quando = ("vai ter de conferir", "começa em") if emissao < regra["inicio_exigencia"] else \
        ("deve conferir", "vale desde")
    larguras = [f_inteiro(v) for v in sorted(Decimal(str(o["inner_mean_width_m"])) for o in divisa)]
    itens = [
        (None, "Estar no mapa do INPE não é multa nem prova de irregularidade: ele mostra onde a vegetação nativa "
               "foi retirada, não se havia autorização."),
        ("Se vai comprar:", (f"a retirada dentro do imóvel é anterior a 22/07/2008. " if antes_2008 else "")
         + f"As faixas de {larguras[0]} a {larguras[-1]} m na divisa indicam que o limite do CAR e o do INPE não "
           "coincidem ali."),
        ("Se vai pedir crédito rural:",
         f"o banco {verbo}, numa lista feita com este mapa, se houve retirada depois de 31/07/2019; para imóvel "
         f"{faixa_txt}, como este, isso {quando} {f_data_extenso(regra['inicio_exigencia'])} (Resolução CMN "
         f"5.303/2026). Só a faixa de {recente['year']} na divisa é posterior; se aparecer, a regra prevê "
         "documentos, como autorização de supressão."),
    ]
    alt_cx, des_cx = caixa_significado(itens)
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=2, ancorar=True))

    ocupacao = compor(c, blocos, 3, grade)
    st.desenhar_moldura(c, 3, TOTAL_PAGINAS, "Desmatamento", rodape)
    return ocupacao


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
SOLO_ADJETIVO = {"muito argilosa": "muito argiloso", "argilosa": "argiloso", "média": "de textura média",
                 "siltosa": "siltoso", "arenosa": "arenoso"}
ACIDEZ = ((Decimal("4.3"), "extremamente ácido"), (Decimal("5.3"), "fortemente ácido"),
          (Decimal("6.5"), "moderadamente ácido"), (Decimal("7.3"), "praticamente neutro"),
          (Decimal("8.3"), "moderadamente alcalino"))


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


def pagina_7(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 7
    p = L.p
    relevo = L.valor("relevo")
    solo = L.valor("solo_mapa")
    textura = L.valor("textura_solo")
    sg = L.valor("soilgrids")
    apt = L.valor("aptidao")
    eros = L.valor("erosao")
    uso = L.valor("mapbiomas")
    rodape = f"CAR {p['property']['car_code'][:11]}…{p['property']['car_code'][-8:]} · emitido em " \
             f"{f_data_brasilia(p['generated_at'])}"
    classes = [x for x in relevo["classes_embrapa_pct"] if Decimal(str(x["pct"])) > 0]
    pcts = f_pct_composicao([x["pct"] for x in classes])
    suave = sum(pcts[i] for i, x in enumerate(classes) if x["classe"] in ("plano", "suave ondulado"))
    legal = relevo["leitura_legal_graus"]
    nada_25 = Decimal(str(legal["acima_de_25_graus_pct"])) == 0
    camada = next(x for x in textura["camadas"] if "areia_pct" in x)
    tex = f_pct_composicao([camada["argila_pct"]["mediana"], camada["silte_pct"]["mediana"],
                            camada["areia_pct"]["mediana"]])
    argilas = sorted({x["argila_pct"]["mediana"] for x in textura["camadas"]})
    ph = Decimal(str(sg["ph_h2o"]))
    acidez = next((t for teto, t in ACIDEZ if ph <= teto), "fortemente alcalino")
    unidades = sorted(solo["unidades"], key=lambda u: u["pct_mapa"], reverse=True)
    solo_adj = SOLO_ADJETIVO[textura["classe_textural"]]
    blocos: list[Bloco] = []

    # --- cabeça -------------------------------------------------------------------------------
    apt_codigos = [a["codigo"] for a in apt["classes"]]
    sem_lavoura = all(cod.startswith(("4", "5", "6")) for cod in apt_codigos)
    frase_apt = " O mapa de aptidão indica pasto plantado ou preservação, não lavoura." if sem_lavoura else ""
    resposta = (
        f"O terreno é plano ou suave ondulado em {suave}% da área{', e nada passa de 25°' if nada_25 else ''}. "
        f"O solo é {solo_adj}.{frase_apt}",
        f"Terreno plano ou suave ondulado em {suave}% da área{'; nada passa de 25°' if nada_25 else ''}. "
        f"Solo {solo_adj}.{frase_apt}",
    )
    consultas = sorted({f_data_brasilia(L.fato(k)["consultado_em"]) for k in
                        ("relevo", "solo_mapa", "textura_solo", "aptidao", "erosao")})
    nota = ("Relevo: Copernicus, 30 m. Solo: IBGE (1:250.000) e MapBiomas Solo. Aptidão e erosão: Embrapa. "
            f"Consulta de {' e '.join(consultas)}.")
    alt, des = cabeca_capitulo(5, "A terra", "Dá para usar máquina? Como é o solo?", resposta, nota, "broto", None)
    blocos.append(Bloco("cabeca", alt, des, espaco=0))

    # --- seção relevo: rampas no ângulo real ----------------------------------------------------
    alt, des = cabeca_secao("Quanto o terreno inclina?", "trator")
    blocos.append(Bloco("h2_relevo", alt, des, espaco=2))

    faixas = {"plano": (0, 3), "suave ondulado": (3, 8), "ondulado": (8, 20), "forte ondulado": (20, 45),
              "montanhoso": (45, 75), "escarpado": (75, 100)}
    n = len(classes)
    painel_w = (LARG - G.calha * (n - 1)) / n
    teto_m = max(faixas[x["classe"]][1] for x in classes)

    def subida(classe):
        de, ate = faixas[classe]
        return f"até {ate} m a cada 100 m" if de == 0 else f"{de} a {ate} m a cada 100 m"

    subidas = [texto(subida(x["classe"]), "legenda", painel_w) for x in classes]
    alt_sub = max(t.altura for t in subidas)
    topo_fig = 3.6 * mm + 11.5 * mm   # chave "73% ..." + fio, barra da parte e número com o nome
    pe_fig = 3.4 * mm + 1 * mm + alt_sub  # "100 m" e a subida

    def rampas(c, x, y_topo, h):
        desenho_h = h - topo_fig - pe_fig
        base_w = painel_w - 9 * mm
        s = min(base_w / 100, desenho_h / teto_m)  # mesma escala nos dois eixos e em todos os painéis
        yb = y_topo - topo_fig - desenho_h
        suaves = [i for i, cl in enumerate(classes) if cl["classe"] in ("plano", "suave ondulado")]
        for i, cl in enumerate(classes):
            px = x + i * (painel_w + G.calha)
            y = y_topo - 3.6 * mm
            c.saveState()
            c.setStrokeColor(COR["INK"])
            c.setLineWidth(0.8)
            c.line(px, y, px + painel_w, y)
            c.setFillColor(COR["SOLO_3"])
            c.rect(px, y - 1.4 * mm, painel_w * pcts[i] / 100, 1.4 * mm, stroke=0, fill=1)
            c.restoreState()
            valor = f"{pcts[i]}%"
            linha_simples(c, px, y - 9.6 * mm, valor, "Disp", 22, COR["INK"])
            wv = pdfmetrics.stringWidth(valor, "Disp", 22)
            linha_simples(c, px + wv + 1.6 * mm, y - 9.6 * mm, cl["classe"], "Sans-Sb", 10, COR["INK"])
            # triângulo no ângulo real do limite de cima da faixa: sobe "ate" metros em 100 m
            ate = faixas[cl["classe"]][1]
            c.saveState()
            c.setStrokeColor(COR["RULE"])
            c.setLineWidth(0.35)
            c.line(px, yb, px + 100 * s, yb)
            c.setFillColor(COR["SOLO_2"])
            c.setStrokeColor(COR["INK"])
            c.setLineWidth(0.6)
            c.setLineJoin(1)
            tri = c.beginPath()
            tri.moveTo(px, yb)
            tri.lineTo(px + 100 * s, yb)
            tri.lineTo(px + 100 * s, yb + ate * s)
            tri.close()
            c.drawPath(tri, stroke=1, fill=1)
            c.restoreState()
            linha_simples(c, px + 100 * s + 1 * mm, yb + max(0, ate * s / 2 - 1.2 * mm), f"{ate} m", "Sans", 7.8,
                          COR["INK2"])
            linha_simples(c, px + 50 * s, yb - 3 * mm, "100 m", "Sans", 7.8, COR["INK2"], "centro")
            subidas[i].desenhar(c, px, yb - 3.4 * mm - 1 * mm)
        if len(suaves) >= 2:
            x1 = x + suaves[0] * (painel_w + G.calha)
            x2 = x + suaves[-1] * (painel_w + G.calha) + painel_w
            c.saveState()
            c.setStrokeColor(COR["FOREST"])
            c.setLineWidth(0.8)
            c.line(x1, y_topo - 0.2 * mm, x2, y_topo - 0.2 * mm)
            c.line(x1, y_topo - 0.2 * mm, x1, y_topo - 1.6 * mm)
            c.line(x2, y_topo - 0.2 * mm, x2, y_topo - 1.6 * mm)
            c.restoreState()
            rot = f"{suave}% plano ou suave ondulado"
            wl = st.largura_espacejada(rot, "kicker") + 3 * mm
            c.saveState()
            c.setFillColor(COR["PAPER"])
            c.rect((x1 + x2) / 2 - wl / 2, y_topo - 1.8 * mm, wl, 3.4 * mm, stroke=0, fill=1)
            c.restoreState()
            rotulo(c, (x1 + x2) / 2, y_topo - 1.2 * mm, rot, "kicker", COR["FOREST"], "centro")

    alt_min = topo_fig + pe_fig + 13 * mm
    blocos.append(Bloco("rampas", alt_min, rampas, mola=(alt_min, topo_fig + pe_fig + 22 * mm), espaco=1))

    corpo_relevo = (("Nenhuma parte passa de 25°, inclinação a partir da qual a lei restringe o uso "
                     "(Lei 12.651/2012, art. 11). " if nada_25 else "")
                    + "A inclinação é um dos fatores para usar máquina; quem confirma é a visita.")
    corpo_t = texto(corpo_relevo, "corpo", G.leitura)
    alt_leg = texto("modelo de terreno de 30 m; não substitui levantamento topográfico", "legenda", G.nota - 3 * mm)
    altitude = f"{f_inteiro(relevo['altitude_min_m'])} a {f_inteiro(relevo['altitude_max_m'])} m"
    alt_relevo = max(corpo_t.altura, 4.2 * mm + 8 * mm + alt_leg.altura)

    def texto_relevo(c, x, y_topo, h):
        corpo_t.desenhar(c, x, y_topo)
        xn = x + G.leitura + G.calha
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        c.line(xn, y_topo, xn, y_topo - h)
        c.restoreState()
        rotulo(c, xn + 3 * mm, y_topo - altura_maiuscula("Sans-Sb", 7.8) - 0.3 * mm, "Altitude", "tab_cab")
        linha_simples(c, xn + 3 * mm, y_topo - 4.2 * mm - 6.2 * mm, altitude, "Disp", 18, COR["INK"])
        alt_leg.desenhar(c, xn + 3 * mm, y_topo - 4.2 * mm - 8 * mm)

    blocos.append(Bloco("texto_relevo", alt_relevo, texto_relevo, espaco=1))

    # --- seção solo e aptidão ---------------------------------------------------------------------
    alt, des = cabeca_secao("Como é o solo e para que serve?", "camadas")
    blocos.append(Bloco("h2_solo", alt, des, espaco=2))

    # A aptidão e a erodibilidade da Embrapa usam as mesmas manchas do mapa de solos do IBGE: a linha só junta
    # as três leituras quando a parte do mapa bate (senão a amostra para, em vez de casar errado).
    apt_por_pct = {Decimal(str(x["pct_mapa"])): x for x in apt["classes"]}
    eros_por_pct = {Decimal(str(x["pct_mapa"])): x for x in eros["erodibilidade"]}
    colunas = (("Parte do imóvel no mapa", 47 * mm), ("Tipo de solo", 52 * mm), ("Para que serve (aptidão)", 45 * mm),
               ("Erosão", 26 * mm))
    linhas = []
    for u in unidades:
        chave = Decimal(str(u["pct_mapa"]))
        if chave not in apt_por_pct or chave not in eros_por_pct:
            raise LayoutError("aptidão ou erosão não casam com as manchas do mapa de solos")
        curto, explica = nome_solo(u["classe"])
        a = apt_por_pct[chave]
        partes = int(re.search(r"(\d+) em cada 10", u["fracao_texto"]).group(1))
        linhas.append({
            "partes": partes,
            "fracao": texto(u["fracao_texto"], "legenda", colunas[0][1] - 2 * mm),
            "solo": texto(f"<b>{curto}</b><br/>{explica}", "tab_cel", colunas[1][1] - 3 * mm),
            "apt": texto(APTIDAO.get(a["codigo"], a["descricao"]), "tab_cel", colunas[2][1] - 3 * mm),
            "eros": texto(f"fragilidade {eros_por_pct[chave]['classe']}", "tab_cel", colunas[3][1]),
        })
    cab_h = 5.4 * mm
    alt_linhas = [max(6.2 * mm + l["fracao"].altura, l["solo"].altura, l["apt"].altura) + 3.4 * mm for l in linhas]
    alt_quadro = cab_h + sum(alt_linhas)

    def quadro_solo(c, x, y_topo, h):
        y = y_topo
        xx = x
        for nome_col, w in colunas:
            rotulo(c, xx, y - altura_maiuscula("Sans-Sb", 7.8) - 0.4 * mm, nome_col, "tab_cab")
            xx += w
        y -= cab_h
        c.saveState()
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.8)
        c.line(x, y + 1 * mm, x + LARG, y + 1 * mm)
        c.restoreState()
        tons = ("SOLO_3", "SOLO_2", "SOLO_1")
        for k, (l, hl) in enumerate(zip(linhas, alt_linhas)):
            yt = y - 1.8 * mm
            q, gap = 3.6 * mm, 0.9 * mm  # pictograma: 10 quadrados, os da mancha preenchidos
            for i in range(10):
                qx = x + i * (q + gap)
                cheio = (i < l["partes"]) if k == 0 else (i >= 10 - l["partes"])
                c.saveState()
                c.setLineWidth(0.5)
                c.setStrokeColor(COR["INK"] if cheio else COR["RULE"])
                c.setFillColor(COR[tons[k % 3]] if cheio else COR["PAPER"])
                c.rect(qx, yt - q, q, q, stroke=1, fill=1)
                c.restoreState()
            l["fracao"].desenhar(c, x, yt - q - 1.6 * mm)
            xx = x + colunas[0][1]
            l["solo"].desenhar(c, xx, yt + 0.8 * mm)
            xx += colunas[1][1]
            l["apt"].desenhar(c, xx, yt + 0.8 * mm)
            xx += colunas[2][1]
            l["eros"].desenhar(c, xx, yt + 0.8 * mm)
            y -= hl
            c.saveState()
            c.setStrokeColor(COR["INK"] if k == len(linhas) - 1 else COR["RULE"])
            c.setLineWidth(0.8 if k == len(linhas) - 1 else 0.35)
            c.line(x, y + 1 * mm, x + LARG, y + 1 * mm)
            c.restoreState()

    blocos.append(Bloco("quadro_solo", alt_quadro, quadro_solo, espaco=1))

    # --- textura (barra) + leitura do solo, com a nota do que o mapa não diz -------------------------
    tex_titulo = texto(f"<b>Textura da terra de cima</b> (0 a 30 cm): {textura['classe_textural']}", "tab_cel",
                       G.leitura)
    corpo_solo = texto(
        f"Pelo MapBiomas Solo, a terra de cima tem de {argilas[0]}% a {argilas[-1]}% de argila: solo {solo_adj}. "
        f"Outro modelo, em quadrados de 250 m, estima {f_pct(sg['argila_pct'])} de argila e pH "
        f"{_ptbr(_q(ph, 1))}, solo {acidez}. Quem decide é a análise de solo.", "corpo", G.leitura)
    nota_solo = texto("Os mapas de solo e de aptidão são regionais: descrevem a região, não medem o imóvel. Não dizem "
                      "se o uso de hoje é permitido.", "legenda", G.nota - 3 * mm)
    barra_h = 5.6 * mm
    alt_tex = tex_titulo.altura + 1.2 * mm + barra_h + G.us(1) + corpo_solo.altura
    alt_tex = max(alt_tex, 4.2 * mm + nota_solo.altura)

    def bloco_textura(c, x, y_topo, h):
        y = y_topo
        y -= tex_titulo.desenhar(c, x, y) + 1.2 * mm
        bw = G.leitura
        acumulado = 0
        nomes = ("argila", "silte", "areia")
        for pct_v, cor, nome in zip(tex, ("TEX_ARGILA", "TEX_SILTE", "TEX_AREIA"), nomes):
            x0 = x + bw * acumulado / 100
            w = bw * pct_v / 100
            c.saveState()
            c.setFillColor(COR[cor])
            c.rect(x0, y - barra_h, w, barra_h, stroke=0, fill=1)
            c.restoreState()
            rot = f"{nome} {pct_v}%"
            if pdfmetrics.stringWidth(rot, "Sans-Sb", 7.8) + 3 * mm <= w:
                linha_simples(c, x0 + 1.6 * mm, y - barra_h / 2 - 1 * mm, rot, "Sans-Sb", 7.8,
                              COR["PAPER"] if cor == "TEX_ARGILA" else COR["INK"])
            acumulado += pct_v
        c.saveState()
        c.setStrokeColor(COR["PAPER"])
        c.setLineWidth(1)
        for corte in (tex[0], tex[0] + tex[1]):
            c.line(x + bw * corte / 100, y - barra_h, x + bw * corte / 100, y)
        c.restoreState()
        y -= barra_h + G.us(1)
        corpo_solo.desenhar(c, x, y)
        xn = x + G.leitura + G.calha
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        c.line(xn, y_topo, xn, y_topo - h)
        c.restoreState()
        rotulo(c, xn + 3 * mm, y_topo - altura_maiuscula("Sans-Sb", 7.8) - 0.3 * mm, "O que o mapa não diz", "tab_cab")
        nota_solo.desenhar(c, xn + 3 * mm, y_topo - 4.2 * mm)

    blocos.append(Bloco("textura", alt_tex, bloco_textura, espaco=2))

    # --- o que isso significa para você -------------------------------------------------------------
    classes_uso = uso["classes"]
    pcts_uso = f_pct_composicao([x["pct"] for x in classes_uso])
    i_pasto = next(i for i, x in enumerate(classes_uso) if x["classe"] == "Pastagem")
    alta = [u for u in unidades if eros_por_pct[Decimal(str(u["pct_mapa"]))]["classe"] == "alta"]
    itens = [
        ("Se vai produzir ou arrendar:",
         f"é terra de pasto ({pcts_uso[i_pasto]}% em {uso['ano']}), em terreno pouco inclinado. Antes de plantar "
         "lavoura, faça análise de solo e fale com um agrônomo."),
        ("Se vai comprar:",
         f"em {alta[0]['fracao_texto']}, o mapa marca o solo como muito sujeito à erosão; na visita, veja se já há "
         "sulcos no chão." if alta else
         "o mapa de erosão não marca fragilidade alta; a visita confirma o estado do chão."),
    ]
    alt_cx, des_cx = caixa_significado(itens)
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=2, ancorar=True))

    ocupacao = compor(c, blocos, 7, grade)
    st.desenhar_moldura(c, 7, TOTAL_PAGINAS, "A terra", rodape)
    return ocupacao


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
# PNG e folha lado a lado
# ===========================================================================
def render_pngs(pdf: Path, saida: Path) -> list[Path]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    feitos = []
    (saida / "larguras").mkdir(exist_ok=True)
    for i, nome in enumerate(("p1", "p3", "p7")):
        pagina = doc[i]
        w_pt, _ = pagina.get_size()
        for largura in (1440, 768, 375):
            img = pagina.render(scale=largura / w_pt).to_pil()
            destino = saida / f"{nome}.png" if largura == 1440 else saida / "larguras" / f"{nome}_{largura}.png"
            img.save(destino, optimize=True)
            feitos.append(destino)
    return feitos


def lado_a_lado(pdf: Path, farmscan: Path, destino: Path) -> None:
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw, ImageFont

    altura = 1180
    ours = pdfium.PdfDocument(str(pdf))
    fs = pdfium.PdfDocument(str(farmscan))

    def pagina(doc, i):
        pg = doc[i]
        _, h_pt = pg.get_size()
        return pg.render(scale=altura / h_pt).to_pil().convert("RGB")

    linhas = [
        ("Raio-X · p. 1 · ficha e resposta rápida", pagina(ours, 0),
         "Farm-Scan · p. 1 (capa) e p. 4 (sumário de conformidade)", [pagina(fs, 0), pagina(fs, 3)]),
        ("Raio-X · p. 3 · capítulo 1, desmatamento", pagina(ours, 1),
         "Farm-Scan · p. 5 (PRODES)", [pagina(fs, 4)]),
        ("Raio-X · p. 7 · capítulo 5, a terra", pagina(ours, 2),
         "Farm-Scan · p. 6 (aptidão agrícola) e p. 8 (composição do solo)", [pagina(fs, 5), pagina(fs, 7)]),
    ]
    fonte = ImageFont.truetype(str(ROOT / "assets" / "fonts" / "SourceSans3-Semibold.ttf"), 30)
    gap, faixa, margem = 36, 64, 40
    largura_pagina = linhas[0][1].width
    largura_total = margem * 2 + largura_pagina * 3 + gap * 2 + 70
    altura_total = margem * 2 + len(linhas) * (altura + faixa) + (len(linhas) - 1) * gap
    folha = Image.new("RGB", (largura_total, altura_total), (236, 233, 226))
    d = ImageDraw.Draw(folha)
    y = margem
    for rot_nosso, nosso, rot_fs, deles in linhas:
        d.text((margem, y + 14), rot_nosso, font=fonte, fill=(31, 74, 54))
        x_fs = margem + largura_pagina + gap + 70
        d.text((x_fs, y + 14), rot_fs, font=fonte, fill=(90, 90, 90))
        folha.paste(nosso, (margem, y + faixa))
        d.line([(margem + largura_pagina + gap + 35, y + faixa), (margem + largura_pagina + gap + 35,
                                                                  y + faixa + altura)], fill=(160, 150, 130), width=3)
        for k, img in enumerate(deles):
            folha.paste(img, (x_fs + k * (img.width + gap), y + faixa))
        y += altura + faixa + gap
    folha.save(destino, optimize=True)


# ===========================================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--saida", required=True, type=Path)
    ap.add_argument("--farmscan", type=Path)
    ap.add_argument("--grade", action="store_true", help="desenha a caixa de cada bloco (conferência)")
    args = ap.parse_args(argv)
    conferir_hash_f1()
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    L = Leitura(payload, FIXTURE.parent)
    args.saida.mkdir(parents=True, exist_ok=True)
    pdf = args.saida / "amostra_livro_v10.pdf"
    st.registrar_fontes()
    c = Canvas(str(pdf), pagesize=(G.pagina_l, G.pagina_a), initialFontName="Sans", pageCompression=1)
    c.setTitle("Raio-X Territorial — Amostra do relatório-livro (p. 1, 3 e 7)")
    c.setAuthor("Raio-X Territorial")
    c.setSubject("Relatório do imóvel rural")
    if hasattr(c, "setLang"):
        c.setLang("pt-BR")
    ocupacoes = {}
    for numero, desenhar in ((1, pagina_1), (3, pagina_3), (7, pagina_7)):
        ocupacoes[numero] = desenhar(c, L, args.grade)
        c.showPage()
    c.save()
    problemas = autoteste_redacao()
    for numero, oc in ocupacoes.items():
        print(f"RX_AMOSTRA_OCUPACAO_P{numero}={int(oc * 100)}%")
        if oc < 0.80:
            problemas.append(f"p. {numero}: ocupação {int(oc * 100)}% abaixo de 80%")
    for linha in problemas:
        print("PROBLEMA:", linha)
    pngs = render_pngs(pdf, args.saida)
    if args.farmscan:
        lado_a_lado(pdf, args.farmscan, args.saida / "lado_a_lado.png")
    print(f"RX_AMOSTRA_PDF={pdf} ({pdf.stat().st_size // 1024} KB)")
    print("RX_AMOSTRA_PNG=" + ", ".join(str(x) for x in pngs[:1]))
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())
