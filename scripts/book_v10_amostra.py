"""Amostra aprovável do relatório-livro v10 (parte PA, onda 0,5): p. 1, 3 e 7 de Curvelo.

SPEC_RELATORIO_LIVRO seções 2.1, 2.3, 2.7, 8.3 e 9.4, com as decisões do dono D1–D12 e as leituras
corrigidas gravadas na F1. Desenha, **sem compositor** (pilha de blocos medidos com mola, no lugar da P10),
as três páginas que o dono aprova antes da onda 2 (D12):

- p. 1 · ficha e resposta rápida: código do CAR numa linha como título, retrato Sentinel-2 fechado no imóvel
  (40–50% da largura, cor natural, D1 desligado), 4 fatos, "Em resumo" com a APP a recompor (D6), painel de
  6 respostas, todas com linha de situação, e "O que isso significa para você" com rótulo pendurado;
- p. 3 · capítulo 1, "Tem desmatamento registrado?": mapa das manchas sobre a imagem esmaecida (1 dentro e 3
  chamadas numeradas na divisa), lupa nos 3 pedacinhos de 2021, legenda agrupada pelos três períodos da lei,
  linha do tempo com uma pista para dentro e outra para a divisa, alerta recente (D9, sem nome da fonte) e a
  regra do crédito pela Res. CMN 5.303/2026 com o limite da lista oficial;
- p. 7 · capítulo 5, "A terra": barra 100% das classes de relevo da Embrapa (em %, com a casa decimal),
  rampas iguais no ângulo real com o limite de 25° tracejado, mapa das células de 30 m (complemento conferido
  contra a F1), solo do IBGE com os componentes de lavoura da mancha, leitura do mapa regional × terreno medido,
  textura do MapBiomas Solo e a outra estimativa ao lado.

Revisão de 13/09 (duas notas 8): piso de 8,5 pt para anotação, selo e rodapé; "Nenhum registro" no lugar de
"Não encontrado"; código curto RX-… no cabeçalho; site (D4) no rodapé. O que a F1 ainda não traz fica em
``COMPLEMENTO_F1``, com a fonte conferida.

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
import io
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
FIXTURE = ROOT / "tests" / "fixtures" / "book" / "curvelo_corrigido.json"
CELULAS = FIXTURE.parent / "curvelo_declividade_celulas.json"  # complemento da F1 (scripts/book_v10_celulas_declividade.py)
TOTAL_PAGINAS = 13  # mapa do livro de Curvelo (seção 2.0.1)

# Piso de leitura da revisão da amostra (13/09): nenhuma anotação, rótulo, selo ou rodapé abaixo de 8,5 pt, e o
# cabeçalho de tabela em INK2 (o MUTED some no celular). Proposta para a tabela 1.3 da SPEC; aqui só sobrepõe os
# estilos em memória, sem mexer no módulo da P3.
PISO_PT = 8.5
ANOT = PISO_PT  # tamanho de toda anotação desenhada direto no canvas (escala, eixo, rótulo de figura)

# Rampa de inclinação, do plano (claro) ao forte ondulado (escuro): a mesma na barra, nos ícones e no mapa de células.
TONS_RELEVO = {"plano": "#EFE7D6", "suave ondulado": "#D8C3A1", "ondulado": "#B48A5C", "forte ondulado": "#6E4A2C",
               "montanhoso": "#4A3020", "escarpado": "#2E1E14"}

# O que a F1 ainda não traz e esta amostra usa, com a fonte já conferida (a P0 leva para a F1 gerada):
COMPLEMENTO_F1 = {
    "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F": {
        # fontes/01_car_composicao_datas.md, linha 54: APP_TOTAL 1,4547 = APP_LAGO_NATURAL 1,4547
        "app_em_volta_de": "da lagoa",
        # fontes/03_alertas_desmatamento.md, linha 90: a camada de alertas atuais começa em 01/08/2025
        "alertas_desde": "2025-08-01",
    },
}
# Limite de soma da lista oficial do MMA para o MCR 2-9-17 (plano/F4_checagem_mcr.md, linhas 103–104)
LIMITE_LISTA_MMA_HA = {"Cerrado": Decimal("5"), "Pantanal": Decimal("5"), "Amazônia": Decimal("6.25")}


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
    soma = CELULAS.with_suffix(".sha256").read_text(encoding="utf-8").split()[0]
    if hashlib.sha256(CELULAS.read_bytes()).hexdigest() != soma.lower():
        raise SystemExit(f"{CELULAS.name} não bate com o .sha256")


def celulas_declividade(L: "Leitura") -> list[dict]:
    """Células de 30 m do complemento, conferidas contra a F1: mesmo total e mesmas partes por classe (0,1 ponto)."""
    dados = json.loads(CELULAS.read_text(encoding="utf-8"))
    relevo = L.valor("relevo")
    celulas = dados["celulas"]
    if len(celulas) != relevo["pixels"]:
        raise LayoutError(f"{CELULAS.name}: {len(celulas)} células; a F1 diz {relevo['pixels']}")
    for linha in relevo["classes_embrapa_pct"]:
        n = sum(1 for x in celulas if x["classe"] == linha["classe"])
        if _q(Decimal(n * 100) / len(celulas), 1) != _q(linha["pct"], 1):
            raise LayoutError(f"{CELULAS.name}: {linha['classe']} não bate com a F1")
    return celulas


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


def encarte_uf(c, uf: str, lonlat, x, y, w=19 * mm, h=16 * mm) -> None:
    contorno = _CONTORNO_UF[uf]
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
    """D4: o contato é só o site, lido da decisão gravada na F1 (sem o esquema, como se escreve num livro)."""
    achado = re.search(r"https?://([\w.\-]+)", L.p["_fixture"]["decisoes_do_dono"]["D4"])
    if not achado:
        raise LayoutError("D4 sem endereço do site")
    return achado.group(1)


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
    antes_2008 = all(o["periodo_legal"] == "ate_2008_07_22" for o in dentro)
    recente = pos2019[0]
    compl = COMPLEMENTO_F1.get(prop["car_code"], {})
    composicao = L.fato("composicao_car")
    comp = composicao["valor"] if composicao["estado"] == "encontrado" else None
    site = site_do_relatorio(L)

    blocos: list[Bloco] = []

    # --- faixa de marca -----------------------------------------------------
    def marca(c) -> None:
        """Faixa de marca da p. 1 na altura do cabeçalho corrente das outras folhas (mesmo ritmo no topo)."""
        x, base = X0, G.pagina_a - G.cabecalho_base
        largura = rotulo(c, x, base, "Raio-X Territorial", "kicker", COR["FOREST"])
        linha_simples(c, x + largura + 2 * mm, base, "Relatório do imóvel rural", "Sans", 8.6, COR["INK2"])
        linha_simples(c, x + LARG, base, f"Emitido em {f_data_brasilia(emitido)}, às {f_hora_brasilia(emitido)} · "
                      f"Código {codigo_curto(p['report_id'])}", "Sans", 8.6, COR["INK2"], "dir")
        c.saveState()
        c.setStrokeColor(COR["FOREST"])
        c.setLineWidth(0.8)
        c.line(x, G.pagina_a - G.cabecalho_fio, x + LARG, G.pagina_a - G.cabecalho_fio)
        c.restoreState()
        linha_simples(c, x, G.folio_base, site, "Sans", PISO_PT, COR["INK2"])

    # --- identidade (regra 5: sem nome validado, título = código do CAR, numa linha só) ------------
    codigo = prop["car_code"]
    if prop.get("name") and str(prop.get("name_source", "")).startswith("SICAR:"):
        raise LayoutError("a F1 de Curvelo não tem nome validado; a variante com nome é da P11")
    pt_codigo, espaco_letra = 15.0, 0.6
    while pdfmetrics.stringWidth(codigo, "Disp-Sb", pt_codigo) + espaco_letra * (len(codigo) - 1) > LARG:
        pt_codigo -= 0.5
        if pt_codigo < 12:
            raise LayoutError("o código do CAR não cabe numa linha")
    explica = texto("Imóvel rural identificado pelo código do Cadastro Ambiental Rural (CAR).", "legenda", LARG)
    cap_k = altura_maiuscula("Sans-Sb", PISO_PT)
    cap_c = altura_maiuscula("Disp-Sb", pt_codigo)
    alt_ident = cap_k + 2.4 * mm + cap_c + 2 * mm + explica.altura

    def identidade(c, x, y_topo, h):
        rotulo(c, x, y_topo - cap_k, "Imóvel no CAR", "kicker", COR["FOREST"])
        base = y_topo - cap_k - 2.4 * mm - cap_c
        REGISTRO.append((1, codigo))
        c.saveState()
        c.setFillColor(COR["INK"])
        c.setFont("Disp-Sb", pt_codigo)
        c.drawString(x, base, codigo, charSpace=espaco_letra)  # uma sequência só: copiar e colar sai inteiro
        c.restoreState()
        explica.desenhar(c, x, base - 2 * mm)

    blocos.append(Bloco("identidade", alt_ident, identidade, espaco=0))

    # --- retrato com vizinhança próxima + coluna de fatos ----------------------------
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
    regra = L.valor("regra_credito")
    mod_legenda = (f"1&nbsp;módulo em {prop['municipality']} ≈ {f_inteiro(fm['value'])}&nbsp;ha" if fm["origin"] ==
                   "conta_car" else f"em {prop['municipality']}, 1 módulo fiscal = {f_inteiro(fm['value'])} ha")
    if regra["faixa_modulos"] == "ate_4":
        mod_legenda = "até 4 módulos, a lei tem regras próprias; " + mod_legenda
    situacao = {"AT": "Ativo", "PE": "Pendente", "SU": "Suspenso", "CA": "Cancelado"}[car["status"]]
    analise = {"Aguardando análise": "ainda não conferido pelo órgão ambiental"}.get(car["analysis_status"])
    leg_cad = f"inscrito no CAR em {f_data(inscricao)}" + (f"; {analise}" if analise else "")
    lon, lat = geom["centroid_lonlat"]
    col_w = LARG - 104 * mm - G.calha
    fatos = [
        ("Localização", f"{prop['municipality']} ({prop['uf']})",
         f"centro do imóvel: {f_gms(lat, 'N', 'S')}, {f_gms(lon, 'E', 'W')}"),
        ("Área declarada no CAR", f_ha(area), f_comparar_area_ficha(area) or f_ha_extenso(area)),
        ("Módulos fiscais", _ptbr(_q(mf, 2)), mod_legenda),
        ("Cadastro", situacao, leg_cad),
    ]
    fatos_t = [(r, texto(v, "fato_valor", col_w), texto(lg, "legenda", col_w)) for r, v, lg in fatos]
    passo_min = 2.6 * mm
    alt_fatos = sum(4 * mm + v.altura + 0.6 * mm + lg.altura for _, v, lg in fatos_t) + passo_min * 3
    img_min, img_max = 70 * mm, 88 * mm
    fixo_img = 1.8 * mm + leg_img.altura
    mola_min = max(img_min + fixo_img, alt_fatos)

    def retrato(c, x, y_topo, h):
        ih = h - fixo_img
        iw = 104 * mm
        lim = base_img["bounds_m"]
        bb = geom["bbox_m"]
        # imóvel com 40–50% da largura do quadro (revisão, item 3): ~1,2–1,5 km, e nunca mais que a imagem
        larg_m = min(max((bb[2] - bb[0]) / 0.45, (bb[3] - bb[1]) / 0.62 * iw / ih), lim[2] - lim[0])
        caixa = caixa_na_proporcao(((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2), larg_m, ih / iw, lim)
        mapa = Mapa(x, y_topo - ih, iw, ih, caixa)
        foto = recorte_imagem(L.imagem(base_img["path"]), lim, caixa, int(iw / 72 * 300), natural=True)
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y_topo - ih, iw, ih, 3 * mm)
        c.clipPath(clip, stroke=0, fill=0)
        buf = io.BytesIO()
        foto.save(buf, "JPEG", quality=90, optimize=True)  # JPEG: sem perda, o retrato dobrava o PDF
        buf.seek(0)
        c.drawImage(ImageReader(buf), x, y_topo - ih, iw, ih)
        limite_car(c, geom["car_m"], mapa, grosso=3.4, fino=1.6, halo=0.7, preencher=0.12)
        c.restoreState()
        escala_e_norte(c, mapa, x + 2.5 * mm, y_topo - ih + 2.5 * mm)
        encarte_uf(c, geom["uf"], geom["centroid_lonlat"], x + iw - 19 * mm - 2.5 * mm, y_topo - ih + 2.5 * mm)
        leg_img.desenhar(c, x, y_topo - ih - 1.8 * mm)
        # coluna de fatos: o pé alinha com o pé da legenda da imagem
        xf = x + iw + G.calha
        natural = alt_fatos - passo_min * 3
        passo = max(passo_min, (h - natural) / 3)
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
            y -= v.desenhar(c, xf, y) + 0.6 * mm
            lg.desenhar(c, xf, y)
            y -= lg.altura + passo

    blocos.append(Bloco("retrato", mola_min, retrato, mola=(mola_min, max(mola_min, img_max + fixo_img)), espaco=1))

    # --- em resumo -----------------------------------------------------------------
    nucleo = ("embargo_ibama", "embargo_icmbio", "autos", "ti", "uc", "quilombola", "assentamento",
              "floresta_publica", "anm")
    nucleo_ausente = all(L.fato(k)["estado"] == "nao_encontrado" for k in nucleo)
    recuo_resumo = st.largura_espacejada("Em resumo", "kicker") + 3 * mm
    periodo_lei = ", antes de 22/07/2008" if antes_2008 else ""
    frase_desm = f"{f_faixa_parte(pct_dentro)} teve a vegetação nativa retirada entre {anos_maior}{periodo_lei}."
    frase_desm_curta = f"{f_pct(pct_dentro)} da área perdeu a vegetação nativa entre {anos_maior}{periodo_lei}."
    em_volta = compl.get("app_em_volta_de")
    frase_app = ""
    if comp and Decimal(str(comp["app"]["a_recompor_ha"])) > 0:
        frase_app = (f" O CAR também declara {f_ha(comp['app']['a_recompor_ha'])} a recompor na faixa de proteção"
                     f"{' ' + em_volta if em_volta else ''} (APP).")
    ausencia = " Não apareceu embargo, terra indígena, unidade de conservação nem mineração." if nucleo_ausente else ""
    frase_app_curta = (f" O CAR declara {f_ha(comp['app']['a_recompor_ha'])} a recompor na APP"
                       f"{' ' + em_volta if em_volta else ''}." if frase_app else "")
    ausencia_curta = ausencia.replace(" Não apareceu embargo, terra indígena, unidade de conservação nem mineração.",
                                      " Sem embargo, terra indígena, unidade de conservação ou mineração.")
    variantes = (  # primeiro o que cabe em 3 linhas (seção 2.1); se nenhum couber, aceita 4
        f"O que mais pede atenção é o desmatamento: {frase_desm}{frase_app}{ausencia}",
        f"Pede atenção o desmatamento: {frase_desm}{frase_app_curta}{ausencia}",
        f"Pede atenção o desmatamento: {frase_desm}{frase_app_curta}{ausencia_curta}",
        # sem a sigla solta: "faixa de proteção" por extenso (a APP é apresentada no cartão logo abaixo)
        f"Atenção ao desmatamento: {frase_desm_curta}"
        + (frase_app_curta.replace(" na APP", " na faixa de proteção") if frase_app_curta else "") + ausencia_curta,
        f"Pede atenção o desmatamento: {frase_desm_curta}"
        + (frase_app_curta.replace(" na APP da ", " em volta da ") if frase_app_curta and em_volta else "")
        + ausencia_curta,
        f"Pede atenção o desmatamento: {frase_desm_curta}{frase_app_curta}{ausencia_curta}",
    )
    medidos = [(m, texto(m, "resposta", LARG, registrar=False, firstLineIndent=recuo_resumo)) for m in variantes]
    escolhido = next(((m, t) for m, t in medidos if t.linhas <= 3), None) or \
        next(((m, t) for m, t in medidos if t.linhas <= 4), None)
    if not escolhido:
        raise LayoutError("p. 1: 'Em resumo' passa de 4 linhas")
    REGISTRO.append((1, escolhido[0]))
    resumo = escolhido[1]

    def em_resumo(c, x, y_topo, h):
        rotulo(c, x, y_topo - st.ESTILO["resposta"].fontSize, "Em resumo", "kicker", COR["FOREST"])
        resumo.desenhar(c, x, y_topo)

    blocos.append(Bloco("em_resumo", resumo.altura, em_resumo, espaco=1))

    # --- painel de 6 respostas: toda linha tem situação ------------------------------------------
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
    atencao_prodes = bool(pos2019) or any(o["periodo_legal"] != "ate_2008_07_22" for o in dentro)
    estados_embargo = {L.fato(k)["estado"] for k in ("embargo_ibama", "embargo_icmbio", "autos")}
    estados_area = {L.fato(k)["estado"] for k in ("ti", "uc", "quilombola", "assentamento", "floresta_publica", "anm")}

    def pior(estados):
        for e in ("pendente", "encontrado", "nao_encontrado"):
            if e in estados:
                return e
        raise LayoutError(f"estados inesperados: {estados}")

    n_out = "uma outorga" if len(dentro_outorga) == 1 else f"{len(dentro_outorga)} outorgas"
    if comp:
        app = comp["app"]
        atencao_comp = Decimal(str(app["a_recompor_ha"])) > 0
        rl = "nenhuma Reserva Legal declarada" if comp["reserva_legal"]["declarada"]["estado"] == "nao_encontrado" \
            else "Reserva Legal declarada"
        cartao_docs = (
            "documento", "O que o dono declarou no CAR?", ("selo", "encontrado", atencao_comp),
            f"Faixa de proteção{' ' + em_volta if em_volta else ''} (APP) de {f_ha(app['total_ha'])}, com "
            f"{f_ha(app['a_recompor_ha'])} a recompor, e {rl}. O CAR não prova quem é o dono.",
            f"APP de {f_ha(app['total_ha'])}, {f_ha(app['a_recompor_ha'])} a recompor; {rl}. O CAR não prova o dono.", 5)
        agua_cad = Decimal(str(comp["agua_declarada_ha"]))
        lagoa = "uma lagoa" if em_volta == "da lagoa" else "água"
        agua_longo = (f"Há {lagoa} declarada no CAR, com {f_ha(agua_cad)} de água, e {n_out} do IGAM para poço dentro do "
                      f"imóvel. Nos poços da região, a água fica a uns {f_metros(nivel)}.")
        agua_curto = (f"{lagoa.capitalize()} com {f_ha(agua_cad)} no CAR e {n_out} de poço; nos poços da região, água a "
                      f"uns {f_metros(nivel)}.")
    else:
        cartao_docs = ("documento", "O que os documentos provam?", ("informativo",),
                       "O CAR mostra onde fica e o que foi declarado; não prova quem é o dono.",
                       "O CAR não prova quem é o dono.", 5)
        agua_longo = (f"Nos poços cadastrados da região, a água costuma estar a uns {f_metros(nivel)}. Há {n_out} do "
                      "IGAM para poço dentro do imóvel.")
        agua_curto = f"Água a uns {f_metros(nivel)} nos poços da região; {n_out} dentro do imóvel."
    cartoes = [
        ("folha", "Tem desmatamento registrado?", ("selo", "encontrado", atencao_prodes),
         f"Sim. Vegetação nativa retirada em {f_pct(pct_dentro)} da área entre {anos_maior}{periodo_lei}. Depois de "
         f"31/07/2019, só {f_ha(recente['area_inside_ha'])} na divisa, em {recente['year']}.",
         f"Sim. {f_pct(pct_dentro)} da área, entre {anos_maior}{periodo_lei}; {f_ha(recente['area_inside_ha'])} na "
         f"divisa em {recente['year']}.", 3),
        ("carimbo", "Tem embargo ou auto de infração no local?", ("selo", pior(estados_embargo), False),
         "Não. Nenhum embargo do IBAMA ou do ICMBio no mapa de embargos sobre a área, nem auto de infração do "
         "IBAMA com local dentro dela.",
         "Não. Nenhum embargo no mapa do IBAMA ou do ICMBio, nem auto com local no imóvel.", 4),
        ("escudo", "Está em terra indígena ou unidade de conservação, ou tem mineração?",
         ("selo", pior(estados_area), False),
         "Não. Também fora de território quilombola, assentamento e floresta pública; nenhum processo na ANM.",
         "Não. Nem quilombola, assentamento, floresta pública ou processo na ANM.", 4),
        cartao_docs,
        ("gota", "Tem água?", ("informativo",), agua_longo, agua_curto, 6),
        ("broto", "Como é a terra e o clima?", ("informativo",),
         f"{nomes_uso.get(classes_uso[principal]['classe'], 'uso').capitalize()} na maior parte "
         f"({pcts_uso[principal]}%), terreno {terreno}, solo {SOLO_ADJETIVO[solo['classe_textural']]}. Chove de {ch_de} a "
         f"{ch_ate}; quase nada de {se_de} a {se_ate}.",
         f"{nomes_uso.get(classes_uso[principal]['classe'], 'uso').capitalize()} ({pcts_uso[principal]}%); "
         f"chuvas de {ch_de} a {ch_ate}.", 7),
    ]
    card_w, card_h = G.col2, 27 * mm
    larg_pergunta = card_w - 7.5 * mm

    def montar_cartao(dados):
        nome_ic, pergunta, situacao_c, longo, curto, pag = dados
        tit = texto(pergunta, "painel_pergunta", larg_pergunta)
        if tit.linhas > 2:
            raise LayoutError(f"cartão '{pergunta}': pergunta com mais de 2 linhas")
        max_linhas = 3 if tit.linhas == 1 else 2
        ref = f' <font name="Sans-Sb" size="8.6" color="{st.HEX["FOREST"]}">→&nbsp;pág.&nbsp;{pag}</font>'
        for modelo in (longo, curto):
            t = texto(modelo + ref, "painel_resposta", card_w, registrar=False)
            if t.linhas <= max_linhas:
                REGISTRO.append((1, modelo))
                return nome_ic, tit, situacao_c, t
        raise LayoutError(f"cartão '{pergunta}': o texto curto passa de {max_linhas} linhas")

    cartoes_t = [montar_cartao(d) for d in cartoes]

    def situacao_do_cartao(c, x, y, situacao_c):
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
        rotulo(c, x + st.SELO_DIAMETRO + st.SELO_ESPACO, y + st.SELO_ALTURA / 2 - cap / 2, "Informativo", "selo_rotulo",
               COR["INK2"])

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

    blocos.append(Bloco("painel", 3 * card_h + 2 * G.us(1), painel, espaco=1))

    # --- o que isso significa para você, com a nota da consulta, ancorada no pé -----------
    lei_2008 = ("a retirada dentro é anterior a 22/07/2008, data que a lei usa para área já em uso. "
                if antes_2008 else "")
    depois = (f"Depois, a APP a recompor (pág. 5). "
              if comp and Decimal(str(comp["app"]["a_recompor_ha"])) > 0 else "")
    respondidos = {k: L.fato(k)["estado"] in ("encontrado", "nao_encontrado")
                   for k in ("alertas_mensais", "sitio_arqueologico", "armazens")}
    em_volta_itens = [nome for chave, nome in (("sitio_arqueologico", "sítios arqueológicos"), ("armazens", "armazéns"))
                      if respondidos[chave]]
    amplitude = ""
    if respondidos["alertas_mensais"] and em_volta_itens:
        amplitude = f"Vimos ainda alertas recentes (pág. 3), {' e '.join(em_volta_itens)} (pág. 10). "
    alt_cx, des_cx = caixa_significado([
        (None, f"Comece pelo desmatamento (pág. 3): {lei_2008}{depois}{amplitude}"
               f"Consultas de {f_data_brasilia(emitido)} em bases públicas oficiais; não substituem certidão de "
               "cartório, vistoria ou laudo."),
    ])
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
PERIODO_ROTULO = {"ate_2008_07_22": "até 22/07/2008", "ate_2019_07_31": "de 23/07/2008 a 31/07/2019",
                  "depois_2019_07_31": "depois de 31/07/2019"}
RESOLUCAO_SATELITE_M = {"Landsat": 30, "Sentinel": 10, "CBERS": 20}


def f_intervalo(o: dict) -> str:
    """Período em que a retirada foi percebida: da imagem anterior à imagem do ano (regra 3.4-3); sem a imagem
    anterior, só o ano."""
    if o.get("prev_image_date"):
        return f"{f_mes_ano_curto(o['prev_image_date'])} a {f_mes_ano_curto(o['image_date'])}"
    return str(o["year"])


def rodape_capitulo(p: dict, site: str) -> str:
    return (f"CAR {p['property']['car_code'][:11]}…{p['property']['car_code'][-8:]} · emitido em "
            f"{f_data_brasilia(p['generated_at'])} · {site}")


def pagina_3(c, L: Leitura, grade: bool) -> float:
    _PAGINA_ATUAL[0] = 3
    p = L.p
    fato = L.fato("prodes")
    val = fato["valor"]
    occ = val["occurrences"]
    area = Decimal(str(p["property"]["area_ha"]))
    bioma = L.valor("bioma")["bioma"]
    dentro = [o for o in occ if o["classe"] == "dentro"]
    divisa = sorted([o for o in occ if o["classe"] == "divisa"], key=lambda o: o["image_date"])
    numero = {o["id"]: i + 1 for i, o in enumerate(divisa)}  # chamadas 1, 2, 3 na ordem do tempo
    pos2019 = [o for o in occ if o["periodo_legal"] == "depois_2019_07_31"]
    atencao = bool(pos2019) or any(o["periodo_legal"] != "ate_2008_07_22" for o in dentro)
    maior = max(dentro, key=lambda o: o["area_inside_ha"])
    pct_d = Decimal(str(maior["area_inside_ha"])) / area * 100
    recente = pos2019[0]
    regra = L.valor("regra_credito")
    alertas = L.fato("alertas_mensais")
    composicao = L.fato("composicao_car")
    comp = composicao["valor"] if composicao["estado"] == "encontrado" else None
    compl = COMPLEMENTO_F1.get(p["property"]["car_code"], {})
    b = L.book
    geom = b["geometry"]
    figuras = b["prodes_figuras"]
    consulta = fato["consultado_em"]
    site = site_do_relatorio(L)
    satelite = next((m for nome, m in RESOLUCAO_SATELITE_M.items() if all(nome in o["plataforma"] for o in occ)), None)
    blocos: list[Bloco] = []

    # --- cabeça -------------------------------------------------------------------
    numeros = {1: "uma", 2: "duas", 3: "três", 4: "quatro"}
    periodo = f"entre {f_mes_ano(maior['prev_image_date'])} e {f_mes_ano(maior['image_date'])}"
    resposta = (
        f"Sim. O Instituto Nacional de Pesquisas Espaciais (INPE) mapeou vegetação nativa retirada em "
        f"{f_faixa_parte(pct_d)}, {periodo}. Outras {numeros[len(divisa)]} manchas só encostam na divisa; uma é "
        f"de {recente['year']}.",
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

    # --- mapa + legenda agrupada pelos três períodos da lei --------------------------------------
    mw = 110 * mm
    leg_w = LARG - mw - G.calha
    destaque_valor = f_ha(maior["area_inside_ha"])
    destaque_leg = texto(f"dentro do imóvel, {f_pct(pct_d)} da área", "legenda", leg_w)
    grupos = []
    for periodo_legal in sorted({o["periodo_legal"] for o in occ}, key=lambda k: PERIODO_ORDEM[k]):
        cab = PERIODO_ROTULO[periodo_legal]
        itens = []
        for o in sorted([o for o in occ if o["periodo_legal"] == periodo_legal], key=lambda o: o["image_date"]):
            pct_o = Decimal(str(o["area_inside_ha"])) / area * 100
            if o["classe"] == "dentro":
                linha = f"<b>{f_intervalo(o)}</b> · dentro"
            else:
                pedacos = o.get("pieces_inside") or 1
                qtd = f"{pedacos} pedacinhos, " if pedacos > 1 else ""
                linha = (f"<b>{f_intervalo(o)}</b> · na divisa<br/>{qtd}{f_ha(o['area_inside_ha'])} · "
                         f"{f_pct(pct_o)}").replace(" ha", "&nbsp;ha")
            itens.append((o, texto(linha, "legenda", leg_w - 6.5 * mm)))
        grupos.append((periodo_legal, cab, itens))
    chave_car = texto("limite declarado no CAR", "legenda", leg_w - 9 * mm)
    chave_inpe = texto("mancha inteira mapeada pelo INPE", "legenda", leg_w - 9 * mm)
    cab_grupo = 5.2 * mm
    alt_leg = (11.5 * mm + destaque_leg.altura + 2.6 * mm
               + sum(cab_grupo + sum(t.altura + 1.2 * mm for _, t in itens) for _, _, itens in grupos)
               + 1.4 * mm * (len(grupos) - 1) + 1.6 * mm + chave_car.altura + chave_inpe.altura)
    comp_area = f_comparar_area(recente["area_inside_ha"])
    borda_cima = [numero[o["id"]] for o in divisa
                  if max(n for poly in ([figuras[o["id"]]["feature_geom_m"]["coordinates"]]
                                        if figuras[o["id"]]["feature_geom_m"]["type"] == "Polygon"
                                        else figuras[o["id"]]["feature_geom_m"]["coordinates"])
                         for _, n in poly[0]) >= b["base_image"]["bounds_m"][3] - 0.5]
    nomes_divisa = ", ".join(str(numero[o["id"]]) for o in divisa[:-1]) + f" e {numero[divisa[-1]['id']]}"
    estreitas = (f" em faixas mais estreitas que um ponto do satélite ({satelite} m)" if satelite and
                 all(Decimal(str(o["inner_mean_width_m"])) < satelite for o in divisa) else "")
    legenda_mapa = texto(
        f"Fundo: Sentinel-2 de {f_data(b['base_image']['date'])}, esmaecida. As manchas {nomes_divisa} só encostam na "
        f"divisa{estreitas}" + (f"; a {borda_cima[0]} sai do quadro." if borda_cima else "."),
        "legenda", mw)
    fixo_mapa = 1.6 * mm + legenda_mapa.altura
    mola_min = max(70 * mm, alt_leg - fixo_mapa) + fixo_mapa

    def mapa_bloco(c, x, y_topo, h):
        from shapely.geometry import Point, box, shape as sh_shape
        from shapely.ops import unary_union

        mh = h - fixo_mapa
        lim = b["base_image"]["bounds_m"]
        todas = [sh_shape(geom["car_m"])] + [sh_shape(f["feature_geom_m"]) for f in figuras.values()]
        ux0, uy0, ux1, uy1 = unary_union(todas).bounds
        # escala que cabe todas as manchas, com margem, deixando uma faixa à direita para a lupa
        s = min((mw - 38 * mm) / (ux1 - ux0 + 80), mh / (uy1 - uy0 + 60))
        larg_m, alt_m = mw / s, mh / s
        x_min = max(lim[0], min(ux0 - 40, lim[2] - larg_m))
        y_min = max(lim[1], min((uy0 + uy1) / 2 - alt_m / 2, lim[3] - alt_m))
        caixa = (x_min, y_min, x_min + larg_m, y_min + alt_m)
        mapa = Mapa(x, y_topo - mh, mw, mh, caixa)
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y_topo - mh, mw, mh, 3 * mm)
        c.clipPath(clip, stroke=0, fill=0)
        mapa.imagem(c, L.imagem(b["base_image"]["path_muted"]), lim)
        desenhar_manchas(c, mapa, occ, figuras, geom)
        c.restoreState()

        def na_pagina(g):
            from shapely.ops import transform as sh_transform
            return sh_transform(lambda e, n, z=None: mapa.pt(e, n), g)

        ocupado = unary_union([na_pagina(g) for g in todas])
        pilula_esc = box(x, y_topo - mh, x + 42 * mm, y_topo - mh + 12 * mm)

        # chamadas numeradas nas manchas da divisa (a última vai para a lupa)
        car_c = na_pagina(sh_shape(geom["car_m"])).centroid
        chamadas = []
        for o in divisa:
            if o["id"] == recente["id"]:
                continue
            alvo = na_pagina(sh_shape(figuras[o["id"]]["inside_geom_m"])).centroid
            dx, dy = alvo.x - car_c.x, alvo.y - car_c.y
            norma = math.hypot(dx, dy) or 1
            mx = min(max(alvo.x + dx / norma * 8 * mm, x + 4 * mm), x + mw - 4 * mm)
            my = min(max(alvo.y + dy / norma * 8 * mm, y_topo - mh + 4 * mm), y_topo - 4 * mm)
            chamadas.append((o, alvo, mx, my))

        # lupa na ocorrência depois de 31/07/2019: posição que menos cobre manchas e chamadas
        alvo_g = sh_shape(figuras[recente["id"]]["inside_geom_m"])
        ce, cn = alvo_g.centroid.x, alvo_g.centroid.y
        tx, ty = mapa.pt(ce, cn)
        r = 15 * mm
        candidatos = []
        passo = 3 * mm
        yy = y_topo - mh + r + 3 * mm
        while yy <= y_topo - r - 8 * mm:
            candidatos.append((x + mw - r - 3 * mm, yy))
            yy += passo
        xx = x + 46 * mm + r
        while xx <= x + mw - r - 3 * mm:
            candidatos.append((xx, y_topo - mh + r + 3 * mm))
            xx += passo
        evitar = unary_union([ocupado.buffer(1.2 * mm), pilula_esc]
                             + [Point(mx, my).buffer(3 * mm) for _, _, mx, my in chamadas])

        def custo(ponto):
            circ = Point(ponto).buffer(r + 1 * mm)
            rot = box(ponto[0] - 26 * mm, ponto[1] + r, ponto[0] + 26 * mm, ponto[1] + r + 7 * mm)
            return (circ.intersection(evitar).area + rot.intersection(evitar).area,
                    math.hypot(ponto[0] - tx, ponto[1] - ty))

        lx, ly = min(candidatos, key=custo)
        for o, alvo, mx, my in chamadas:
            cor = COR[PERIODO_COR[o["periodo_legal"]]]
            c.saveState()
            c.setStrokeColor(COR["INK"])
            c.setLineWidth(0.6)
            ang = math.atan2(alvo.y - my, alvo.x - mx)
            c.line(mx + 2.1 * mm * math.cos(ang), my + 2.1 * mm * math.sin(ang), alvo.x, alvo.y)
            c.restoreState()
            marcador_numero(c, mx, my, numero[o["id"]], cor)
        c.saveState()
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(1)
        ang = math.atan2(ty - ly, tx - lx)
        c.line(lx + r * math.cos(ang), ly + r * math.sin(ang), tx - 2.2 * mm * math.cos(ang),
               ty - 2.2 * mm * math.sin(ang))
        c.setLineWidth(0.8)
        c.circle(tx, ty, 2.2 * mm, stroke=1, fill=0)
        c.restoreState()
        pedacos = alvo_g.geoms if alvo_g.geom_type == "MultiPolygon" else [alvo_g]
        ex0, ey0, ex1, ey1 = alvo_g.bounds
        lado_m = max(90.0, ex1 - ex0 + 30, ey1 - ey0 + 30)
        mapa_l = Mapa(lx - r, ly - r, 2 * r, 2 * r, (ce - lado_m / 2, cn - lado_m / 2, ce + lado_m / 2, cn + lado_m / 2))
        c.saveState()
        c.setFillColor(COR["PAPER"])
        c.circle(lx, ly, r + 0.9 * mm, stroke=0, fill=1)
        clip = c.beginPath()
        clip.circle(lx, ly, r)
        c.clipPath(clip, stroke=0, fill=0)
        mapa_l.imagem(c, L.imagem(b["base_image"]["path_muted"]), lim)
        c.setFillColor(COR["PAPER"])
        c.setFillAlpha(0.72)  # foto a ~30%: o fundo não disputa com as células
        c.rect(lx - r, ly - r, 2 * r, 2 * r, stroke=0, fill=1)
        c.setFillAlpha(1)
        c.setStrokeColor(COR["INK2"])
        c.setLineWidth(0.7)
        c.setDash(1.6, 1.2)
        c.drawPath(caminho_geom(c, figuras[recente["id"]]["feature_geom_m"], mapa_l), stroke=1, fill=0)
        c.setDash()
        c.setFillColor(COR[PERIODO_COR[recente["periodo_legal"]]])
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.5)
        for peca in pedacos:
            c.drawPath(caminho_geom(c, {"type": "Polygon", "coordinates": [list(peca.exterior.coords)]}, mapa_l),
                       stroke=1, fill=1)
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(1.4)
        c.setLineJoin(1)
        c.drawPath(caminho_geom(c, geom["car_m"], mapa_l), stroke=1, fill=0)
        c.restoreState()
        c.saveState()
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.8)
        c.circle(lx, ly, r, stroke=1, fill=0)
        c.restoreState()
        n_pecas = recente.get("pieces_inside") or len(pedacos)
        rotulo_lupa = (f"{recente['year']} · {n_pecas} pedacinhos, {f_ha(recente['area_inside_ha'])} no total"
                       if n_pecas > 1 else f"{recente['year']} · {f_ha(recente['area_inside_ha'])}")
        if comp_area:  # D2: o tamanho que dá para imaginar
            rotulo_lupa += " (" + comp_area.replace("mais ou menos o tamanho de ", "≈ ").replace("menor que ", "< ") + ")"
        wt = pdfmetrics.stringWidth(rotulo_lupa, "Sans-Sb", ANOT)
        wl = wt + 4.2 * mm + 4.5 * mm
        xp = min(max(lx - wl / 2, x + 2 * mm), x + mw - 2 * mm - wl)  # a pílula não sai do mapa
        yp = ly + r + 1.2 * mm if ly + r + 7 * mm <= y_topo else ly - r - 6.6 * mm
        pilula(c, xp, yp, wl, 5.4 * mm, 0.95)
        marcador_numero(c, xp + 3.3 * mm, yp + 2.7 * mm, numero[recente["id"]],
                        COR[PERIODO_COR[recente["periodo_legal"]]], raio=1.9 * mm)
        REGISTRO.append((3, rotulo_lupa))
        linha_simples(c, xp + 6.2 * mm, yp + 2.7 * mm - altura_maiuscula("Sans-Sb", ANOT) / 2,
                      rotulo_lupa, "Sans-Sb", ANOT, COR["INK"])
        escala_e_norte(c, mapa, x + 2.5 * mm, y_topo - mh + 2.5 * mm)
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
        y -= destaque_leg.desenhar(c, xl, y) + 2.6 * mm
        for k, (periodo_legal, cab, itens) in enumerate(grupos):
            if k:
                y -= 1.4 * mm
            # cabeça do grupo: faixa na cor do período (a mesma da linha do tempo) com o período escrito
            c.saveState()
            c.setFillColor(COR[PERIODO_COR[periodo_legal]])
            c.rect(xl, y - 4.4 * mm, leg_w, 4.4 * mm, stroke=0, fill=1)
            c.restoreState()
            cor_cab = COR["PAPER"] if periodo_legal != "ate_2008_07_22" else COR["INK"]
            linha_simples(c, xl + 1.6 * mm, y - 2.2 * mm - altura_maiuscula("Sans-Sb", ANOT) / 2, cab, "Sans-Sb", ANOT,
                          cor_cab)
            y -= cab_grupo
            for o, t in itens:
                my = y - 2.1 * mm
                if o["classe"] == "dentro":
                    c.saveState()
                    c.setFillColor(COR[PERIODO_COR[o["periodo_legal"]]])
                    c.setStrokeColor(COR["INK"])
                    c.setLineWidth(0.5)
                    c.rect(xl + 0.2 * mm, my - 2 * mm, 4 * mm, 4 * mm, stroke=1, fill=1)
                    c.restoreState()
                else:
                    marcador_numero(c, xl + 2.2 * mm, my, numero[o["id"]], COR[PERIODO_COR[o["periodo_legal"]]])
                y -= t.desenhar(c, xl + 6.5 * mm, y + 0.4 * mm) + 1.2 * mm
        y -= 1.6 * mm
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

    blocos.append(Bloco("mapa", mola_min, mapa_bloco, mola=(mola_min, 96 * mm + fixo_mapa), espaco=2))

    # --- linha do tempo: uma pista para a mancha de dentro, outra para as da divisa ------------------------
    def linha_tempo(c, x, y_topo, h):
        ano0, ano1 = 2000, int(p["generated_at"][:4]) + 1
        eixo_y = y_topo - 16.6 * mm
        faixa_h = 1.8 * mm
        faixa_topo = eixo_y - 0.9 * mm
        cap = altura_maiuscula("Sans", ANOT)

        def xa(iso):
            a, m, d = (int(v) for v in iso[:10].split("-"))
            frac = (datetime(a, m, d) - datetime(a, 1, 1)).days / 365.0
            return x + (a + frac - ano0) / (ano1 - ano0) * LARG

        periodos = ((f"{ano0}-01-01", "2008-07-22", "ate_2008_07_22"),
                    ("2008-07-22", regra["marco"], "ate_2019_07_31"),
                    (regra["marco"], f"{ano1}-01-01", "depois_2019_07_31"))
        for ini, fim, periodo_legal in periodos:
            x1, x2 = xa(ini), xa(fim)
            c.saveState()
            c.setFillColor(COR[PERIODO_COR[periodo_legal]])
            c.rect(x1, faixa_topo - faixa_h, x2 - x1, faixa_h, stroke=0, fill=1)
            c.restoreState()
            linha_simples(c, (x1 + x2) / 2, faixa_topo - faixa_h - 8 * mm, PERIODO_ROTULO[periodo_legal], "Sans-Sb",
                          ANOT, COR["INK2"], "centro")
        c.saveState()
        c.setStrokeColor(COR["INK2"])
        c.setLineWidth(0.6)
        c.line(x, eixo_y, x + LARG, eixo_y)
        c.setStrokeColor(COR["PAPER"])
        c.setLineWidth(0.8)
        for iso in ("2008-07-22", regra["marco"]):
            c.line(xa(iso), faixa_topo, xa(iso), faixa_topo - faixa_h)
        c.restoreState()
        for a in range(ano0, ano1 + 1, 4):
            xx = x + (a - ano0) / (ano1 - ano0) * LARG
            linha_simples(c, xx, faixa_topo - faixa_h - 4 * mm, str(a), "Sans", ANOT, COR["INK2"],
                          "esq" if a == ano0 else "centro")
        xm = xa(regra["marco"])
        c.saveState()
        c.setStrokeColor(COR["INK2"])
        c.setLineWidth(0.6)
        c.setDash(1.4, 1.2)
        c.line(xm, faixa_topo - faixa_h, xm, y_topo - 0.4 * mm)
        c.restoreState()
        linha_simples(c, xm - 1.4 * mm, y_topo - 3.2 * mm, "31/07/2019 · marco da regra do crédito rural "
                      "(Res. CMN 5.303/2026)", "Sans", ANOT, COR["INK2"], "dir")
        pistas = {"dentro": eixo_y + 9.4 * mm, "divisa": eixo_y + 3.9 * mm}
        for o in sorted(occ, key=lambda o: o["image_date"]):
            y_p = pistas[o["classe"]]
            cor = COR[PERIODO_COR[o["periodo_legal"]]]
            x2 = xa(o["image_date"])
            x1 = xa(o["prev_image_date"]) if o["prev_image_date"] else x2
            c.saveState()
            c.setStrokeColor(COR["RULE"])
            c.setLineWidth(0.4)
            for xx in {x1, x2}:
                c.line(xx, y_p - 1.3 * mm, xx, eixo_y)
            c.restoreState()
            c.saveState()
            if o["classe"] == "dentro":
                c.setFillColor(cor)
                c.setStrokeColor(COR["INK"])
                c.setLineWidth(0.5)
                c.roundRect(x1, y_p - 1.3 * mm, max(x2 - x1, 2.6 * mm), 2.6 * mm, 1.3 * mm, stroke=1, fill=1)
            elif x2 > x1:
                c.setFillColor(COR["PAPER"])
                c.setStrokeColor(cor)
                c.setLineWidth(1.4)
                c.roundRect(x1, y_p - 1 * mm, x2 - x1, 2 * mm, 1 * mm, stroke=1, fill=1)
            c.restoreState()
            if o["classe"] == "dentro":
                rot = f"{f_intervalo(o)} · {f_ha(o['area_inside_ha'])} dentro do imóvel"
                largura = pdfmetrics.stringWidth(rot, "Sans-Sb", ANOT)
                xi = x2 + 1.8 * mm if x2 + 1.8 * mm + largura < xm - 1.4 * mm else x1 - 1.8 * mm - largura
                REGISTRO.append((3, rot))
                linha_simples(c, xi, y_p - cap / 2, rot, "Sans-Sb", ANOT, COR["INK"])
            else:  # chamada numerada (a mesma do mapa e da legenda): no meio da barra longa, antes da curta, no ponto
                if x2 - x1 >= 10 * mm or x2 == x1:
                    marcador_numero(c, (x1 + x2) / 2, y_p, numero[o["id"]], cor)
                else:
                    marcador_numero(c, x1 - 2.9 * mm, y_p, numero[o["id"]], cor)
        if divisa:
            REGISTRO.append((3, "na divisa"))
            linha_simples(c, x + LARG, pistas["divisa"] - cap / 2, "na divisa", "Sans", ANOT, COR["INK2"], "dir")

    blocos.append(Bloco("linha_tempo", 28.6 * mm, linha_tempo, espaco=1))

    # --- alertas recentes (D9: sem nome da fonte no corpo) ---------------------------------
    lim_alerta = re.search(r"a partir de ([\d,]+) ha", " ".join(alertas["limites"]))
    desde = compl.get("alertas_desde")
    frase_alerta = texto(
        f"Nenhum alerta de desmatamento sobre o imóvel{' desde ' + f_mes_ano(desde) if desde else ''}, nem em análise "
        f"nem já validado (consulta de {f_data_brasilia(alertas['consultado_em'])})."
        + (f" Só aparecem aberturas a partir de {lim_alerta.group(1)} ha." if lim_alerta else ""),
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

    # --- o que isso significa para você: comprar e crédito lado a lado -----------------------------------
    antes_2008 = all(o["periodo_legal"] == "ate_2008_07_22" for o in dentro)
    emissao = p["generated_at"][:10]
    soma_pos = sum(Decimal(str(o["area_inside_ha"])) for o in pos2019)
    limite = LIMITE_LISTA_MMA_HA.get(bioma)
    faixa_txt = {"ate_4": "de até 4 módulos fiscais", "4_a_15": "com mais de 4 e até 15 módulos fiscais",
                 "acima_15": "com mais de 15 módulos fiscais"}[regra["faixa_modulos"]]
    if emissao < regra["inicio_exigencia"]:
        credito = (f"em imóvel {faixa_txt}, o banco só confere desmatamento recente a partir de "
                   f"{f_data(regra['inicio_exigencia'])} (Res. CMN 5.303/2026).")
    else:
        credito = (f"para imóvel {faixa_txt}, como este, o banco confere desmatamento recente desde "
                   f"{f_data(regra['inicio_exigencia'])} (Resolução CMN 5.303/2026).")
    anos_pos = " e ".join(sorted({str(o["year"]) for o in pos2019}))
    if limite is not None and soma_pos < limite:
        credito += (f" Pelo método da lista oficial, os {f_ha(soma_pos)} de {anos_pos} ficam abaixo do limite de "
                    f"{_ptbr(limite)} ha do {bioma}.")
    elif limite is not None:
        credito += (f" Pelo mapa do INPE, os {f_ha(soma_pos)} depois de 31/07/2019 passam do limite de "
                    f"{_ptbr(limite)} ha do {bioma}; com juros controlados, o banco pode pedir documento.")
    comprar = ("a retirada dentro é anterior a 22/07/2008, data que a lei usa para área já em uso" if antes_2008
               else "")
    if comp:
        comprar += (f"; no CAR, o dono declarou {f_ha(comp['uso_consolidado_ha'])} de uso anterior a essa data, ainda "
                    "sem conferência"
                    if antes_2008 else "")
    comprar += (". " if comprar else "") + "Na divisa, confira as cercas."
    itens_cx = [
        (None, "Estar no mapa do INPE não é multa nem prova de irregularidade: ele mostra onde a vegetação nativa "
               "foi retirada, não se havia autorização."),
        ("Se vai comprar:", comprar[0].lower() + comprar[1:]),
        ("Se vai pedir crédito rural:", credito),
    ]
    alt_cx, des_cx = caixa_significado(itens_cx, colunas=True)
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))

    ocupacao = compor(c, blocos, 3, grade)
    st.desenhar_moldura(c, 3, TOTAL_PAGINAS, "Desmatamento", rodape_capitulo(p, site))
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
    celulas = celulas_declividade(L)
    site = site_do_relatorio(L)
    geom = L.book["geometry"]
    classes = [x for x in relevo["classes_embrapa_pct"] if Decimal(str(x["pct"])) > 0]
    pcts = [Decimal(str(x["pct"])) for x in classes]
    suave = sum(pcts[i] for i, x in enumerate(classes) if x["classe"] in ("plano", "suave ondulado"))
    inclinado = 100 - suave
    legal = relevo["leitura_legal_graus"]
    nada_25 = Decimal(str(legal["acima_de_25_graus_pct"])) == 0
    m_25 = f_inteiro(Decimal(math.tan(math.radians(25)) * 100))
    camada = next(x for x in textura["camadas"] if "areia_pct" in x)
    tex = f_pct_composicao([camada["argila_pct"]["mediana"], camada["silte_pct"]["mediana"],
                            camada["areia_pct"]["mediana"]])
    unidades = sorted(solo["unidades"], key=lambda u: u["pct_mapa"], reverse=True)
    solo_adj = SOLO_ADJETIVO[textura["classe_textural"]]
    blocos: list[Bloco] = []

    # --- cabeça: responde à pergunta do título -----------------------------------------------------------
    quanto = "na maior parte" if suave >= 70 else ("em parte" if suave >= 30 else "em pouca parte")
    resposta = (  # a página está cheia: entra primeiro o modelo de 3 linhas
        f"Pela inclinação, dá para usar máquina {quanto}: {f_pct_exato(suave)} da área é plana ou suave ondulada. "
        f"Cuidado nas partes mais inclinadas ({f_pct_exato(inclinado)}). O solo é {solo_adj}.",
        f"Pela inclinação, dá para usar máquina {quanto}: o terreno é plano ou suave ondulado em "
        f"{f_pct_exato(suave)} da área{', e nada passa de 25°' if nada_25 else ''}. Cuidado nas partes mais "
        f"inclinadas ({f_pct_exato(inclinado)}). O solo é {solo_adj}.",
    )
    consultas = sorted({f_data_brasilia(L.fato(k)["consultado_em"]) for k in
                        ("relevo", "solo_mapa", "textura_solo", "aptidao", "erosao")})
    nota = ("Relevo: Copernicus, 30 m. Solo: IBGE (1:250.000) e MapBiomas Solo. Aptidão e erosão: Embrapa. "
            f"Consulta de {' e '.join(consultas)}.")
    alt, des = cabeca_capitulo(5, "A terra", "Dá para usar máquina? Como é o solo?", resposta, nota, "broto", None)
    blocos.append(Bloco("cabeca", alt, des, espaco=0))

    # --- relevo: barra 100% (principal), ícones de rampa iguais e mapa de células ---------------------------
    alt, des = cabeca_secao("Quanto o terreno inclina?", "trator")
    blocos.append(Bloco("h2_relevo", alt, des, espaco=2))

    esq_w = G.leitura
    gap_col = 3 * mm
    n = len(classes)
    col_w = (esq_w - gap_col * (n - 1)) / n
    subidas = [texto((f"até {FAIXAS_RELEVO[x['classe']][1]}&nbsp;m em 100&nbsp;m"
                      if FAIXAS_RELEVO[x["classe"]][0] == 0 else
                      f"{FAIXAS_RELEVO[x['classe']][0]}–{FAIXAS_RELEVO[x['classe']][1]}&nbsp;m em 100&nbsp;m"),
                     "legenda", col_w) for x in classes]
    alt_sub = max(t.altura for t in subidas)
    frase_lei = texto(
        (f"Nenhuma parte passa de 25° (tracejado: cerca de {m_25} m a cada 100 m), a inclinação a partir da qual a lei "
         "restringe o uso (Lei 12.651/2012, art. 11)."
         if nada_25 else "Parte do terreno passa de 25°, a inclinação a partir da qual a lei restringe o uso "
                         "(Lei 12.651/2012, art. 11)."),
        "legenda", esq_w)
    tri_w = col_w - 8 * mm
    teto_m = max(max(FAIXAS_RELEVO[x["classe"]][1] for x in classes), float(m_25) if nada_25 else 0)
    tri_h = tri_w * teto_m / 100
    barra_h = 7 * mm
    topo_barra = 4.4 * mm  # chave "73% plano ou suave ondulado"
    esq_alt = (topo_barra + barra_h + 2.4 * mm + 7.5 * mm + 4.6 * mm + 0.8 * mm + tri_h + 1.6 * mm + alt_sub
               + 2.2 * mm + frase_lei.altura)
    cap_mapa = texto(f"Quadrados de 30 × 30 m, nos tons da barra. Altitude de "
                     f"{f_inteiro(relevo['altitude_min_m'])} a {f_inteiro(relevo['altitude_max_m'])} m.",
                     "legenda", G.nota)

    def relevo_bloco(c, x, y_topo, h):
        # chave dos suaves
        idx_suaves = [i for i, cl in enumerate(classes) if cl["classe"] in ("plano", "suave ondulado")]
        acumulado = [Decimal(0)]
        for v in pcts:
            acumulado.append(acumulado[-1] + v)
        xb = lambda v: x + esq_w * float(v) / 100  # noqa: E731
        yb = y_topo - topo_barra
        if len(idx_suaves) >= 2:
            x1, x2 = xb(acumulado[idx_suaves[0]]), xb(acumulado[idx_suaves[-1] + 1])
            c.saveState()
            c.setStrokeColor(COR["FOREST"])
            c.setLineWidth(0.8)
            c.line(x1, y_topo - 2 * mm, x2, y_topo - 2 * mm)
            c.line(x1, y_topo - 2 * mm, x1, y_topo - 3.4 * mm)
            c.line(x2, y_topo - 2 * mm, x2, y_topo - 3.4 * mm)
            c.restoreState()
            rot = f"{f_pct_exato(suave)} plano ou suave ondulado"
            wl = st.largura_espacejada(rot, "kicker") + 3 * mm
            c.saveState()
            c.setFillColor(COR["PAPER"])
            c.rect((x1 + x2) / 2 - wl / 2, y_topo - 3.6 * mm, wl, 3.6 * mm, stroke=0, fill=1)
            c.restoreState()
            rotulo(c, (x1 + x2) / 2, y_topo - 2 * mm - altura_maiuscula("Sans-Sb", PISO_PT) / 2, rot, "kicker",
                   COR["FOREST"], "centro")
        # barra 100%
        for i, cl in enumerate(classes):
            tom = HexColor(TONS_RELEVO[cl["classe"]])
            x0, x1 = xb(acumulado[i]), xb(acumulado[i + 1])
            c.saveState()
            c.setFillColor(tom)
            c.rect(x0, yb - barra_h, x1 - x0, barra_h, stroke=0, fill=1)
            c.restoreState()
            rot = f_pct_exato(pcts[i])
            if pdfmetrics.stringWidth(rot, "Sans-Sb", ANOT) + 2.4 * mm <= x1 - x0:
                escuro = i >= 2
                linha_simples(c, x0 + 1.2 * mm, yb - barra_h / 2 - altura_maiuscula("Sans-Sb", ANOT) / 2, rot,
                              "Sans-Sb", ANOT, COR["PAPER"] if escuro else COR["INK"])
        c.saveState()
        c.setStrokeColor(COR["PAPER"])
        c.setLineWidth(0.8)
        for v in acumulado[1:-1]:
            c.line(xb(v), yb - barra_h, xb(v), yb)
        c.restoreState()
        # colunas: tom, número, nome, ícone da rampa no ângulo real (mesma caixa e escala) e subida
        y_col = yb - barra_h - 2.4 * mm
        s = tri_w / 100
        for i, cl in enumerate(classes):
            tom = HexColor(TONS_RELEVO[cl["classe"]])
            px = x + i * (col_w + gap_col)
            c.saveState()
            c.setFillColor(tom)
            c.setStrokeColor(COR["INK"])
            c.setLineWidth(0.4)
            c.rect(px, y_col - 5.6 * mm, 3.2 * mm, 3.2 * mm, stroke=1, fill=1)
            c.restoreState()
            REGISTRO.append((7, f_pct_exato(pcts[i])))
            linha_simples(c, px + 4.6 * mm, y_col - 5.6 * mm, f_pct_exato(pcts[i]), "Disp", 19, COR["INK"])
            linha_simples(c, px, y_col - 7.5 * mm - 3.6 * mm, cl["classe"], "Sans-Sb", 9.6, COR["INK"])
            base = y_col - 7.5 * mm - 4.6 * mm - 0.8 * mm - tri_h
            ate = FAIXAS_RELEVO[cl["classe"]][1]
            c.saveState()
            c.setFillColor(tom)
            c.setStrokeColor(COR["INK"])
            c.setLineWidth(0.6)
            c.setLineJoin(1)
            tri = c.beginPath()
            tri.moveTo(px, base)
            tri.lineTo(px + 100 * s, base)
            tri.lineTo(px + 100 * s, base + ate * s)
            tri.close()
            c.drawPath(tri, stroke=1, fill=1)
            c.restoreState()
            linha_simples(c, px + 100 * s + 1 * mm, base + max(0, ate * s - altura_maiuscula("Sans", ANOT)), f"{ate} m",
                          "Sans", ANOT, COR["INK2"])
            if nada_25 and i == n - 1:
                c.saveState()
                c.setStrokeColor(COR["TERRA"])
                c.setLineWidth(0.8)
                c.setDash(1.4, 1.1)
                c.line(px, base, px + 100 * s, base + float(m_25) * s)
                c.restoreState()
                linha_simples(c, px + 30 * s, base + 30 * float(m_25) / 100 * s + 1.4 * mm, "25°", "Sans-Sb", ANOT,
                              COR["TERRA"], "dir")
            subidas[i].desenhar(c, px, base - 1.6 * mm)
        frase_lei.desenhar(c, x, y_topo - esq_alt + frase_lei.altura)
        # mapa de células de 30 m (resolução honesta), nos mesmos tons
        xn = x + esq_w + G.calha
        rotulo(c, xn, y_topo - altura_maiuscula("Sans-Sb", PISO_PT), "Onde inclina mais", "tab_cab")
        topo_m = y_topo - 4.6 * mm
        disp_h = h - 4.6 * mm - 1.6 * mm - cap_mapa.altura
        car_pts = [pt for anel in geom["car_m"]["coordinates"] for pt in anel] if geom["car_m"]["type"] == "Polygon" \
            else [pt for poli in geom["car_m"]["coordinates"] for anel in poli for pt in anel]
        xs = [e for cel in celulas for e, _ in cel["anel_m"]] + [e for e, _ in car_pts]
        ys = [nn for cel in celulas for _, nn in cel["anel_m"]] + [nn for _, nn in car_pts]
        esc = min(G.nota / (max(xs) - min(xs)), disp_h / (max(ys) - min(ys)))
        w_m, h_m = G.nota / esc, disp_h / esc
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        mapa = Mapa(xn, topo_m - disp_h, G.nota, disp_h, (cx - w_m / 2, cy - h_m / 2, cx + w_m / 2, cy + h_m / 2))
        c.saveState()
        c.setStrokeColor(COR["PAPER"])
        c.setLineWidth(0.35)
        for cel in celulas:
            c.setFillColor(HexColor(TONS_RELEVO[cel["classe"]]))
            anel = cel["anel_m"]
            c.drawPath(caminho_geom(c, {"type": "Polygon", "coordinates": [anel + [anel[0]]]}, mapa), stroke=1, fill=1)
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(1.1)
        c.setLineJoin(1)
        c.drawPath(caminho_geom(c, geom["car_m"], mapa), stroke=1, fill=0)
        c.restoreState()
        cap_mapa.desenhar(c, xn, topo_m - disp_h - 1.6 * mm)

    rel_min = max(esq_alt, 4.6 * mm + 36 * mm + 1.6 * mm + cap_mapa.altura)
    blocos.append(Bloco("relevo", rel_min, relevo_bloco, mola=(rel_min, rel_min + 10 * mm), espaco=1))

    # --- solo e aptidão ---------------------------------------------------------------------------------
    alt, des = cabeca_secao("Como é o solo e para que serve?", "camadas")
    blocos.append(Bloco("h2_solo", alt, des, espaco=1))

    apt_por_pct = {Decimal(str(x["pct_mapa"])): x for x in apt["classes"]}
    eros_por_pct = {Decimal(str(x["pct_mapa"])): x for x in eros["erodibilidade"]}
    colunas = (("Parte no mapa", 41 * mm), ("Tipo de solo", 47 * mm), ("Para que serve (aptidão)", 60 * mm),
               ("Erosão", 22 * mm))
    linhas = []
    for u in unidades:
        chave = Decimal(str(u["pct_mapa"]))
        if chave not in apt_por_pct or chave not in eros_por_pct:
            raise LayoutError("aptidão ou erosão não casam com as manchas do mapa de solos")
        curto, explica = nome_solo(u["classe"])
        a = apt_por_pct[chave]
        partes = int(re.search(r"(\d+) em cada 10", u["fracao_texto"]).group(1))
        apt_txt = APTIDAO.get(a["codigo"], a["descricao"])
        lavoura = componentes_lavoura(a.get("componentes_secundarios", []))
        if lavoura and not a["codigo"].startswith(("1", "2", "3")):
            nomes = " e ".join(nm for nm, _ in lavoura)
            graus = {g for _, g in lavoura}
            grau = graus.pop() if len(graus) == 1 else "alguma"
            apt_txt += (f'<br/><font name="Sans" size="8.6" color="{st.HEX["INK2"]}">a mancha também tem {nomes}, '
                        f"de aptidão {grau} para lavoura</font>")
        linhas.append({
            "partes": partes,
            "fracao": texto(u["fracao_texto"], "legenda", colunas[0][1] - 2 * mm),
            "solo": texto(f"<b>{curto}</b><br/>{explica}", "tab_cel", colunas[1][1] - 3 * mm),
            "apt": texto(apt_txt, "tab_cel", colunas[2][1] - 3 * mm),
            "eros": texto(f"fragilidade {eros_por_pct[chave]['classe']}", "tab_cel", colunas[3][1]),
        })
    cab_h = 5.4 * mm
    alt_linhas = [max(5.2 * mm + l["fracao"].altura, l["solo"].altura, l["apt"].altura) + 2.4 * mm for l in linhas]
    alt_quadro = cab_h + sum(alt_linhas)

    def quadro_solo(c, x, y_topo, h):
        y = y_topo
        xx = x
        for nome_col, w in colunas:
            rotulo(c, xx, y - altura_maiuscula("Sans-Sb", PISO_PT) - 0.4 * mm, nome_col, "tab_cab")
            xx += w
        y -= cab_h
        c.saveState()
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.8)
        c.line(x, y + 1 * mm, x + LARG, y + 1 * mm)
        c.restoreState()
        tons = ("SOLO_3", "SOLO_2", "SOLO_1")
        for k, (l, hl) in enumerate(zip(linhas, alt_linhas)):
            yt = y - 1.6 * mm
            q, gap = 3.2 * mm, 0.7 * mm
            for i in range(10):
                qx = x + i * (q + gap)
                cheio = (i < l["partes"]) if k == 0 else (i >= 10 - l["partes"])
                c.saveState()
                c.setLineWidth(0.5)
                c.setStrokeColor(COR["INK"] if cheio else COR["RULE"])
                c.setFillColor(COR[tons[k % 3]] if cheio else COR["PAPER"])
                c.rect(qx, yt - q, q, q, stroke=1, fill=1)
                c.restoreState()
            l["fracao"].desenhar(c, x, yt - q - 1.4 * mm)
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

    # --- leitura: o mapa regional × o terreno medido, e a textura; notas à direita ------------------------
    maior_u = unidades[0]
    apt_maior = apt_por_pct[Decimal(str(maior_u["pct_mapa"]))]
    descrito = maior_u.get("relevo_descrito_no_mapa", "")
    sem_lavoura = all(a["codigo"].startswith(("4", "5", "6")) for a in apt["classes"])
    leitura = ""
    if sem_lavoura and apt_maior.get("limitacoes") and ("forte" in descrito or "montanhoso" in descrito) and suave >= 50:
        leitura = (f"Na mancha maior do mapa de aptidão, as limitações para lavoura são "
                   f"{' e '.join(apt_maior['limitacoes'])}, num relevo descrito como {descrito}. Mas o terreno medido "
                   f"do imóvel é {f_pct_exato(suave)} plano ou suave ondulado: vale visita de agrônomo antes de decidir "
                   "por lavoura.")
    leitura_t = texto(leitura, "corpo", G.leitura) if leitura else None
    tex_titulo = texto(f"<b>Textura da terra de {camada['profundidade_cm'].replace('–', ' a ')} cm</b>: "
                       f"{textura['classe_textural']}", "tab_cel", G.leitura)
    barra_tex = 5.6 * mm
    outra_classe = classe_textural(sg["argila_pct"], sg["areia_pct"])
    nota_outra = texto("Mapas regionais não medem o imóvel nem dizem se o uso de hoje é permitido. "
                       + (f"Outro modelo, mais grosseiro (quadrados de {f_inteiro(sg['grade_m'])} m), dá "
                          f"{f_pct(sg['argila_pct'])} de argila, textura {outra_classe}; decide a análise de solo."
                          if sg.get("diverge_da_textura_mapbiomas") else ""), "legenda", G.nota - 3 * mm)
    esq = (leitura_t.altura + G.us(1) if leitura_t else 0) + tex_titulo.altura + 1.2 * mm + barra_tex
    dir_ = 4.4 * mm + nota_outra.altura
    alt_leitura = max(esq, dir_)

    def leitura_bloco(c, x, y_topo, h):
        y = y_topo
        if leitura_t:
            y -= leitura_t.desenhar(c, x, y) + G.us(1)
        y -= tex_titulo.desenhar(c, x, y) + 1.2 * mm
        bw = G.leitura
        acumulado = 0
        for pct_v, cor, nome in zip(tex, ("TEX_ARGILA", "TEX_SILTE", "TEX_AREIA"), ("argila", "silte", "areia")):
            x0 = x + bw * acumulado / 100
            w = bw * pct_v / 100
            c.saveState()
            c.setFillColor(COR[cor])
            c.rect(x0, y - barra_tex, w, barra_tex, stroke=0, fill=1)
            c.restoreState()
            rot = f"{nome} {pct_v}%"
            if pdfmetrics.stringWidth(rot, "Sans-Sb", ANOT) + 3 * mm <= w:
                linha_simples(c, x0 + 1.6 * mm, y - barra_tex / 2 - altura_maiuscula("Sans-Sb", ANOT) / 2, rot,
                              "Sans-Sb", ANOT, COR["PAPER"] if cor == "TEX_ARGILA" else COR["INK"])
            acumulado += pct_v
        c.saveState()
        c.setStrokeColor(COR["PAPER"])
        c.setLineWidth(1)
        for corte in (tex[0], tex[0] + tex[1]):
            c.line(x + bw * corte / 100, y - barra_tex, x + bw * corte / 100, y)
        c.restoreState()
        xn = x + G.leitura + G.calha
        c.saveState()
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(G.fio_fino)
        c.line(xn, y_topo, xn, y_topo - h)
        c.restoreState()
        yn = y_topo
        rotulo(c, xn + 3 * mm, yn - altura_maiuscula("Sans-Sb", PISO_PT) - 0.3 * mm, "O que o mapa não diz", "tab_cab")
        nota_outra.desenhar(c, xn + 3 * mm, yn - 4.4 * mm)

    blocos.append(Bloco("leitura_solo", alt_leitura, leitura_bloco, espaco=1))

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
         f"em {alta[0]['fracao_texto']}, o solo é muito sujeito à erosão, pelo mapa. Na visita, procure sulcos nas "
         "partes escuras do mapa de inclinação." if alta else
         "o mapa de erosão não marca fragilidade alta; a visita confirma o estado do chão."),
    ]
    alt_cx, des_cx = caixa_significado(itens, colunas=True)
    blocos.append(Bloco("significado", alt_cx, des_cx, espaco=1, ancorar=True))

    ocupacao = compor(c, blocos, 7, grade)
    st.desenhar_moldura(c, 7, TOTAL_PAGINAS, "A terra", rodape_capitulo(p, site))
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
    aplicar_piso_de_leitura()
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
