"""Quem desenha por cima da foto limpa: o relatório.

A fonte (``sentinel_limpo``) entrega o recorte sem nada escrito. Aqui o relatório desenha
o limite do CAR sobre a imagem, põe a data e a nuvem da cena na legenda EMBAIXO da foto e
desenha a legenda do verdor AO LADO do recorte. Nada de texto dentro da imagem: a regra do
dono vale também no papel.

O mesmo bloco serve ao relatório de hoje e ao relatório-livro. O livro, quando entrar,
chama ``blocos_da_foto_limpa(payload)`` e recebe os mesmos elementos prontos.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Spacer, Table, TableStyle

import report_engine_v5 as v5
import sentinel_limpo

LARGURA = 165 * mm
LARGURA_VERDOR = 96 * mm
# A mesma cor do limite que o cliente já vê no mapa da tela, para ninguém ler duas
# identidades do mesmo imóvel. É linha, não é texto.
LIMITE_CLARO = (1.0, 0.933, 0.275)
LIMITE_ESCURO = (0.071, 0.137, 0.094)
CHAVE = "foto_limpa"
PENDENTE = "Imagem de satélite: consulta pendente nesta emissão."


class FotoComLimite(Flowable):
    """A foto, e por cima dela o limite do CAR. Só linha: nenhum caractere é desenhado."""

    def __init__(self, caminho: str, limite_px, largura_px: int, altura_px: int,
                 largura: float = LARGURA):
        super().__init__()
        self.caminho = caminho
        self.limite_px = limite_px or []
        self.largura_px = max(int(largura_px or 1), 1)
        self.altura_px = max(int(altura_px or 1), 1)
        self.largura = largura
        self.altura = largura * self.altura_px / self.largura_px

    def wrap(self, disponivel_largura, disponivel_altura):
        return self.largura, self.altura

    def draw(self):
        canvas = self.canv
        canvas.drawImage(self.caminho, 0, 0, width=self.largura, height=self.altura)
        if not self.limite_px:
            return
        escala = self.largura / self.largura_px
        canvas.saveState()
        recorte = canvas.beginPath()
        recorte.rect(0, 0, self.largura, self.altura)
        canvas.clipPath(recorte, stroke=0)
        canvas.setLineJoin(1)
        canvas.setLineCap(1)
        for cor, espessura in ((LIMITE_ESCURO, 3.4), (LIMITE_CLARO, 1.6)):
            canvas.setStrokeColorRGB(*cor)
            canvas.setLineWidth(espessura)
            for anel in self.limite_px:
                if len(anel) < 3:
                    continue
                caminho = canvas.beginPath()
                primeiro = True
                for x, y in anel:
                    ponto = (x * escala, self.altura - y * escala)
                    if primeiro:
                        caminho.moveTo(*ponto)
                        primeiro = False
                    else:
                        caminho.lineTo(*ponto)
                caminho.close()
                canvas.drawPath(caminho, stroke=1, fill=0)
        canvas.restoreState()


def _virgula(valor: float) -> str:
    return ("%g" % valor).replace(".", ",")


def legenda_do_verdor(meta: dict) -> Table:
    legenda = meta.get("legenda_verdor") or sentinel_limpo.legenda_de_verdor()
    linhas = []
    estilo = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BOX", (0, 0), (-1, -1), 0.4, v5.LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.white),
    ]
    faixas = list(legenda.get("faixas") or [])
    faixas.append(legenda.get("sem_leitura") or {})
    for indice, faixa in enumerate(faixas):
        if not faixa:
            continue
        if "de" in faixa:
            rotulo = f"de {_virgula(max(faixa['de'], 0.0))} a {_virgula(faixa['ate'])} — {faixa['rotulo']}"
        else:
            rotulo = str(faixa.get("rotulo") or "")
        linhas.append(["", v5.P(rotulo, "small")])
        estilo.append(("BACKGROUND", (0, indice), (0, indice), HexColor(faixa["cor"])))
    tabela = Table(linhas, colWidths=[9 * mm, 59 * mm], rowHeights=[7.2 * mm] * len(linhas),
                   style=TableStyle(estilo))
    tabela.hAlign = "LEFT"
    return tabela


def blocos_da_foto_limpa(payload) -> list:
    """Foto em cor natural com o limite por cima, e o verdor com a legenda ao lado.

    Lista vazia quando não há foto nesta emissão — campo vazio não aparece. Quando a fonte
    não respondeu, sai uma linha discreta de consulta pendente, nunca um espaço em branco
    que o leitor interpreta como "não tem nada aqui".
    """
    meta = ((payload or {}).get("satellite_imagery") or {}).get(CHAVE) or {}
    if not meta:
        return []
    if not meta.get("ok"):
        return [v5.P(PENDENTE, "small")]
    cor = meta.get("cor_natural_path")
    if not cor or not Path(str(cor)).exists():
        return [v5.P(PENDENTE, "small")]

    blocos = [
        FotoComLimite(str(cor), meta.get("limite_px"), meta.get("largura_px"), meta.get("altura_px")),
        Spacer(1, 1.6 * mm),
        v5.P(sentinel_limpo.texto_da_cena(meta) + " O limite do imóvel é desenhado por nós sobre a "
             "imagem, a partir da geometria do Cadastro Ambiental Rural.", "small"),
    ]
    verdor = meta.get("verdor_path")
    if verdor and Path(str(verdor)).exists():
        prancha = Table(
            [[FotoComLimite(str(verdor), meta.get("limite_px"), meta.get("largura_px"),
                            meta.get("altura_px"), largura=LARGURA_VERDOR),
              legenda_do_verdor(meta)]],
            colWidths=[LARGURA_VERDOR + 3 * mm, 66 * mm],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                              ("TOPPADDING", (0, 0), (-1, -1), 0),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]),
        )
        prancha.hAlign = "CENTER"
        nota = (meta.get("legenda_verdor") or {}).get("nota")
        blocos += [Spacer(1, 4 * mm), prancha]
        if nota:
            blocos += [Spacer(1, 1.6 * mm), v5.P(nota, "small")]
    for bloco in blocos:
        try:
            bloco.hAlign = "CENTER"
        except Exception:
            pass
    return blocos


print("RX_FOTO_LIMPA_RELATORIO=limite_e_legenda_desenhados_por_cima", flush=True)
