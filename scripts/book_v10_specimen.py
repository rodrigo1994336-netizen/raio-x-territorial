"""Página espécime do sistema visual do livro v10 (P3).

Uma folha A4 com todos os estilos de ``report_book_v10_style.ESTILO``, todas as cores
de ``COR``, os selos, a rampa do verdor, o cabeçalho corrente e o rodapé. Roda com o
normalizador pt-BR do v9 instalado, como em produção, e mostra lado a lado o milhar
intacto no ``Paragrafo`` e estragado no ``Paragraph`` comum (controle positivo).

Uso:
    PYTHONPATH=. python scripts/book_v10_specimen.py --out especime.pdf [--png especime.png] [--grade]

Sai com erro se algum bloco passar da área útil (170 × 248 mm) ou se algum estilo ou
cor ficar fora da folha.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import report_ptbr_v50  # noqa: E402

report_ptbr_v50.install()

import report_book_v10_style as st  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen.canvas import Canvas  # noqa: E402
from reportlab.platypus import Paragraph  # noqa: E402

G = st.GRADE
COR = st.COR
X0 = G.origem_x
TOPO = G.origem_y + G.util_a
BASE = G.origem_y
DIREITA = X0 + G.util_l
ESPACO = G.us(1)

usados_estilo: set[str] = set()
usadas_cor: set[str] = set()
estouros: list[str] = []


def _n(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def _etiqueta(token: str) -> str:
    s = st.ESTILO[token]
    return f"{token} · {s.fontName} {_n(s.fontSize)}/{_n(s.leading)}"


def etiqueta(c, x: float, y_topo: float, texto: str, largura: float | None = None) -> float:
    """Rótulo técnico do espécime (rodape_id, MUTED). Devolve a altura ocupada."""
    s = st.ESTILO["rodape_id"]
    c.setFillColor(s.textColor)
    c.setFont(s.fontName, s.fontSize)
    if largura is not None:
        while len(texto) > 4 and c.stringWidth(texto, s.fontName, s.fontSize) > largura:
            texto = texto[:-2] + "…"
    c.drawString(x, y_topo - s.fontSize, texto)
    return s.leading + 1


def texto(c, x: float, y_topo: float, conteudo: str, token: str, largura: float, cor: str | None = None) -> float:
    usados_estilo.add(token)
    estilo = st.ESTILO[token]
    if cor is not None:
        usadas_cor.add(cor)
        from reportlab.lib.styles import ParagraphStyle

        estilo = ParagraphStyle(token + "_" + cor, parent=estilo, textColor=COR[cor])
    p = st.Paragrafo(conteudo, estilo)
    _, h = p.wrap(largura, 10000)
    p.drawOn(c, x, y_topo - h)
    return h


def amostra(c, x: float, y_topo: float, token: str, conteudo: str, largura: float, cor: str | None = None,
            rotulo: str | None = None) -> float:
    h = etiqueta(c, x, y_topo, rotulo or _etiqueta(token), largura)
    return h + texto(c, x, y_topo - h, conteudo, token, largura, cor)


def rotulo(c, x: float, y_topo: float, conteudo: str, token: str, cor: str, alinhar: str = "esq") -> float:
    usados_estilo.add(token)
    usadas_cor.add(cor)
    s = st.ESTILO[token]
    st.texto_espacejado(c, x, y_topo - s.fontSize, conteudo, s, COR[cor], alinhar)
    return s.leading


def conferir_limite(y: float, onde: str) -> None:
    if y < BASE - 0.01:
        estouros.append(f"{onde}:{_n(int((BASE - y) / mm * 10) / 10)}mm")


# ---------------------------------------------------------------------------
def cabeca(c, y: float) -> float:
    rotulo(c, X0, y, "Espécime · sistema visual do livro", "kicker", "FOREST")
    s = st.ESTILO["legenda"]
    usados_estilo.add("legenda")
    c.setFont(s.fontName, s.fontSize)
    c.setFillColor(s.textColor)
    c.drawRightString(DIREITA, y - st.ESTILO["kicker"].fontSize,
                      "Source Serif 4 e Source Sans 3 (OFL 1.1) · cabeçalho e rodapé como na p. 3")
    y -= st.ESTILO["kicker"].leading + 2
    c.setStrokeColor(COR["INK"])
    c.setLineWidth(0.8)
    c.line(X0, y, DIREITA, y)
    return y - G.us(1)


def coluna_leitura(c, y: float) -> float:
    x, w = X0, G.leitura
    y -= etiqueta(c, x, y, "kicker (capítulo, TERRA) · Sans-Sb 7,8/10 · charSpace 1,4")
    y -= rotulo(c, x, y, "Capítulo 1 · Desmatamento", "kicker", "TERRA")
    y -= 2
    y -= amostra(c, x, y, "h1", "Tem desmatamento registrado?", w)
    y -= 2 * mm
    usadas_cor.add("TERRA")
    c.setStrokeColor(COR["TERRA"])
    c.setLineWidth(0.8)
    c.line(x, y, x + 40 * mm, y)
    y -= 2 * mm
    y -= amostra(c, x, y, "dek", "O que os satélites viram dentro do imóvel e na divisa.", w)
    y -= ESPACO

    # resposta na coluna de 116 com nota de 48 à direita (NotaMargem)
    y_bloco = y
    h_resp = amostra(c, x, y, "resposta",
                     "Sim: <b>14,27 ha</b> percebidos entre 2004 e 2006. Nada depois de 31/07/2019.", w - G.calha - 34 * mm)
    y_nota = y_bloco
    xn = x + w - 34 * mm
    h_nota = amostra(c, xn, y_nota, "legenda", "Imagens do satélite Landsat 5 de 2004 e 2006.", 34 * mm)
    usadas_cor.add("RULE")
    c.setStrokeColor(COR["RULE"])
    c.setLineWidth(G.fio_fino)
    c.line(xn - G.calha / 2, y_bloco, xn - G.calha / 2, y_bloco - max(h_resp, h_nota))
    y -= max(h_resp, h_nota) + ESPACO

    # números-destaque
    col = (w - 2 * G.calha) / 3
    y_num = y
    alturas = []
    for i, (token, valor, unidade, leg) in enumerate((
        ("destaque_valor", "14,27", "ha", "dentro do imóvel"),
        ("fato_valor", "10 m³/h", None, "vazão da outorga"),
        ("rampa_valor", "62%", None, "suave ondulado"),
    )):
        xi = x + i * (col + G.calha)
        usadas_cor.add("INK")
        c.setStrokeColor(COR["INK"])
        c.setLineWidth(0.8)
        c.line(xi, y_num, xi + col, y_num)
        h = 2 + etiqueta(c, xi, y_num - 2, _etiqueta(token), col)
        yv = y_num - h
        s = st.ESTILO[token]
        usados_estilo.add(token)
        c.setFont(s.fontName, s.fontSize)
        c.setFillColor(s.textColor)
        base = yv - s.fontSize
        c.drawString(xi, base, valor)
        if unidade:
            su = st.ESTILO["destaque_unidade"]
            usados_estilo.add("destaque_unidade")
            c.setFont(su.fontName, su.fontSize)
            c.drawString(xi + c.stringWidth(valor, s.fontName, s.fontSize) + 1.2 * mm, base, unidade)
        h += s.leading + 0.22 * s.fontSize  # espaço para a vírgula e o descendente
        h += texto(c, xi, y_num - h, leg, "legenda", col)
        alturas.append(h)
    y -= max(alturas) + ESPACO

    y -= amostra(c, x, y, "h2", "Dá para usar máquina?", w)
    y -= 1
    y -= amostra(c, x, y, "corpo", "A maior parte da área (62%) é <b>suave ondulada</b>, com <i>subida de até 8 m a cada "
                 "100 m</i>. Outros 11% são planos.", w,
                 rotulo=_etiqueta("corpo") + " · negrito: " + _etiqueta("corpo_sb") + " · itálico: Serif-It")
    usados_estilo.add("corpo_sb")
    y -= ESPACO

    # identidade da p. 1: nome validado (com o código abaixo) ou só o código em 2 linhas
    w_cod = 46 * mm
    w_nome = w - w_cod - G.calha
    y_id = y
    h1 = amostra(c, x, y_id, "nome_titulo", "Nome validado", w_nome)
    h1 += texto(c, x, y_id - h1, "MG-3120904-…A14D011F", "codigo_sub", w_nome)
    h1 += etiqueta(c, x, y_id - h1 - 1, _etiqueta("codigo_sub"), w_nome)
    h2 = amostra(c, x + w_nome + G.calha, y_id, "codigo_titulo", "MG-3120904-<br/>…A14D011F", w_cod)
    y -= max(h1, h2) + ESPACO

    # caixa "O que isso significa para você"
    pad = 3.2 * mm
    usadas_cor.update(("CREAM", "FOREST"))
    corpo_caixa = st.Paragrafo("<b>Se vai comprar:</b> pergunte ao vendedor o que foi feito na área aberta entre "
                               "2004 e 2006.", "caixa")
    usados_estilo.add("caixa")
    _, hc = corpo_caixa.wrap(w - pad - 6 * mm, 1000)
    altura = pad + st.ESTILO["kicker"].leading + 2 + hc + pad
    y -= etiqueta(c, x, y, _etiqueta("caixa") + " · fundo CREAM · barra FOREST 2,6 pt")
    c.setFillColor(COR["CREAM"])
    c.rect(x, y - altura, w, altura, stroke=0, fill=1)
    c.setFillColor(COR["FOREST"])
    c.rect(x, y - altura, 2.6, altura, stroke=0, fill=1)
    yy = y - pad
    yy -= rotulo(c, x + 6 * mm, yy, "O que isso significa para você", "kicker", "FOREST") + 2
    corpo_caixa.drawOn(c, x + 6 * mm, yy - hc)
    y -= altura
    conferir_limite(y, "coluna_leitura")
    return y


def coluna_cores(c, y_topo: float) -> float:
    x = X0 + G.leitura + G.calha
    w = G.nota
    y = y_topo
    y -= etiqueta(c, x, y, "COR · 28 tokens (1.4)")
    passo = 3.6 * mm
    s = st.ESTILO["rodape_id"]
    for nome, valor in st.HEX.items():
        usadas_cor.add(nome)
        c.setFillColor(COR[nome])
        c.setStrokeColor(COR["RULE"])
        c.setLineWidth(0.35)
        c.rect(x, y - 3.0 * mm, 7 * mm, 3.0 * mm, stroke=1, fill=1)
        c.setFillColor(COR["INK"])
        c.setFont("Sans-Sb", s.fontSize)
        c.drawString(x + 8.5 * mm, y - 2.45 * mm, nome)
        c.setFillColor(COR["INK2"])
        c.setFont("Sans", s.fontSize)
        c.drawRightString(x + w, y - 2.45 * mm, valor)
        y -= passo
    y -= G.us(1)
    y = selos(c, x, y, w)
    y = rampa_verdor(c, x, y - G.us(1), w)
    conferir_limite(y, "coluna_cores")
    return y


def selos(c, x: float, y: float, w: float) -> float:
    """Os 4 selos desenhados (1.5), o caso sem selo e o selo grande de 6 mm da faixa Como ler."""
    usados_estilo.add("selo_rotulo")
    usadas_cor.update(("TERRA", "INK", "INK2", "PENDING", "PENDING_TEXT"))
    y -= etiqueta(c, x, y, "selo · 3,4 mm · traço 0,9 pt · selo_rotulo", w)
    casos = (("encontrado", True, "atenção"), ("encontrado", False, "neutro"),
             ("nao_encontrado", False, ""), ("pendente", False, ""))
    for estado, atencao, nota in casos:
        largura = st.desenhar_selo(c, x, y - st.SELO_ALTURA, estado, atencao)
        if nota:
            etiqueta(c, x + largura + 2 * mm, y - 1.2, nota)
        y -= st.SELO_ALTURA + 0.8 * mm
    y -= texto(c, x, y, "sem selo: não faz parte desta edição", "edicao", w)
    y -= 2 * mm
    xi = x
    for estado, atencao, _ in casos:
        st.desenhar_selo(c, xi, y - 6 * mm, estado, atencao, diametro=6 * mm, com_rotulo=False)
        xi += 8.5 * mm
    etiqueta(c, xi + 1 * mm, y - 1.5 * mm, "6 mm", w)
    return y - 6 * mm


def rampa_verdor(c, x: float, y: float, w: float) -> float:
    """Legenda do verdor pela fórmula de sentinel_cog._ndvi_image, marcas em 0,30 e 0,55."""
    usados_estilo.add("eixo")
    y -= etiqueta(c, x, y, "verdor (sentinel_cog) · marcas: eixo 7,8/10", w)
    passos = 120
    for i in range(passos):
        c.setFillColor(st.cor_verdor(-0.15 + 0.95 * i / (passos - 1)))
        c.rect(x + w * i / passos, y - 4 * mm, w / passos + 0.3, 4 * mm, stroke=0, fill=1)
    s = st.ESTILO["eixo"]
    c.setFont(s.fontName, s.fontSize)
    c.setFillColor(s.textColor)
    c.setStrokeColor(COR["INK"])
    c.setLineWidth(0.6)
    for marca, rot in ((0.30, "0,30"), (0.55, "0,55")):
        xm = x + w * (marca + 0.15) / 0.95
        c.line(xm, y - 4 * mm, xm, y - 5.2 * mm)
        c.drawCentredString(xm, y - 5.2 * mm - s.fontSize, rot)
    return y - 5.2 * mm - s.leading


def grade_pequena(c, y: float) -> float:
    col = G.col3
    c.setStrokeColor(COR["INK"])
    c.setLineWidth(0.8)
    c.line(X0, y, DIREITA, y)
    y -= 3
    linhas = (
        (
            lambda xi, y0: amostra(c, xi, y0, "painel_pergunta", "Está em área protegida ou tem mineração?", col),
            lambda xi, y0: amostra(c, xi, y0, "painel_resposta", "A base respondeu e não tinha registro na data "
                                   "da consulta.", col),
            lambda xi, y0: amostra(c, xi, y0, "quadro_frase", "Nenhum embargo desenhado no mapa do IBAMA.", col),
        ),
        (
            lambda xi, y0: _tabela(c, xi, y0, col),
            lambda xi, y0: _glossario(c, xi, y0, col),
            lambda xi, y0: _pilha(c, xi, y0, col, (
                ("legenda", "Cada ponto da imagem cobre 10 × 10 m.", "legenda = conf_onde = edicao · Sans 8,6/12"),
                ("apend_cel", "Base de 28/08/2026 · 18h52", None))),
        ),
        (
            lambda xi, y0: _conferencia(c, xi, y0, col),
            lambda xi, y0: amostra(c, xi, y0, "limite_texto", "Este relatório não substitui a certidão do "
                                   "cartório.", col),
            lambda xi, y0: amostra(c, xi, y0, "regra", "Sem selo: não faz parte desta edição.", col,
                                   rotulo="regra = conf_por_que · Serif 9,6/13,5"),
        ),
        (
            lambda xi, y0: amostra(c, xi, y0, "icone_nome", "Terra indígena", col),
            lambda xi, y0: amostra(c, xi, y0, "doc_nome", "SIGEF e SNCI", col),
            lambda xi, y0: etiqueta(c, xi, y0, "corrente · folio · rodape_id: no", col)
            + etiqueta(c, xi, y0 - 9.5, "cabeçalho e no rodapé desta folha", col),
        ),
    )
    for i, linha in enumerate(linhas):
        altura = max(desenho(X0 + j * (col + G.calha), y) for j, desenho in enumerate(linha))
        y -= altura + 4
        if i < len(linhas) - 1:
            usadas_cor.add("RULE")
            c.setStrokeColor(COR["RULE"])
            c.setLineWidth(G.fio_fino)
            c.line(X0, y + 2, DIREITA, y + 2)
    usados_estilo.update(("conf_onde", "edicao", "conf_por_que"))
    conferir_limite(y, "grade_pequena")
    return y


def _pilha(c, x, y_topo, w, itens):
    h = 0.0
    for token, conteudo, rot in itens:
        h += amostra(c, x, y_topo - h, token, conteudo, w, rotulo=rot) + 2
    return h - 2


def _tabela(c, x, y_topo, w):
    h = etiqueta(c, x, y_topo, "tab_cab · tab_cel · tab_num", w)
    y = y_topo - h
    h += rotulo(c, x, y, "Inclinação", "tab_cab", "MUTED")
    st.texto_espacejado(c, x + w, y - st.ESTILO["tab_cab"].fontSize, "Parte da área", "tab_cab", COR["MUTED"], "dir")
    y = y_topo - h
    c.setStrokeColor(COR["INK"])
    c.setLineWidth(0.8)
    c.line(x, y, x + w, y)
    for i, (classe, area) in enumerate((("Plano", "11%"), ("Suave ondulado", "62%"))):
        y -= 2.2
        hl = texto(c, x, y, classe, "tab_cel", w * 0.6)
        texto(c, x + w * 0.4, y, area, "tab_num", w * 0.6)
        y -= hl + 2.2
        c.setStrokeColor(COR["INK"] if i == 1 else COR["RULE"])
        c.setLineWidth(0.8 if i == 1 else 0.35)
        c.line(x, y, x + w, y)
    return y_topo - y


def _glossario(c, x, y_topo, w):
    h = etiqueta(c, x, y_topo, "glos_termo · glos_def", w)
    h += texto(c, x, y_topo - h, "Outorga", "glos_termo", w)
    h += texto(c, x, y_topo - h, "Licença do governo para tirar água de rio ou de poço.", "glos_def", w)
    return h


def _conferencia(c, x, y_topo, w):
    h = etiqueta(c, x, y_topo, "conf_o_que · conf_por_que · ref_pagina", w)
    c.setStrokeColor(COR["INK"])
    c.setLineWidth(0.9)
    c.roundRect(x, y_topo - h - 3.6 * mm - 1, 3.6 * mm, 3.6 * mm, 0.6 * mm, stroke=1, fill=0)
    h += texto(c, x + 6 * mm, y_topo - h, "Pedir o CCIR", "conf_o_que", w - 6 * mm)
    h += texto(c, x + 6 * mm, y_topo - h, "Mostra o cadastro no INCRA. <font name='Sans-Sb' color='#1F4A36'>"
               "→ pág. 5</font>", "conf_por_que", w - 6 * mm)
    usados_estilo.add("ref_pagina")
    return h


def faixa_milhar(c, y: float) -> float:
    livro = " · ".join(("1.500 ha", "cerca de 1.100 mm", "12.000 cabeças"))
    c.setStrokeColor(COR["RULE"])
    c.setLineWidth(G.fio_fino)
    c.line(X0, y, DIREITA, y)
    y -= 3
    h = etiqueta(c, X0, y, "com report_ptbr_v50.install() ativo · Paragrafo (livro)")
    p = st.Paragrafo(livro, "tab_cel")
    _, hp = p.wrap(G.col2, 100)
    p.drawOn(c, X0, y - h - hp)
    x2 = X0 + G.col2 + G.calha
    etiqueta(c, x2, y, "controle positivo · Paragraph comum (tem que sair estragado)")
    q = Paragraph(livro, st.ESTILO["tab_cel"])
    _, hq = q.wrap(G.col2, 100)
    q.drawOn(c, x2, y - h - hq)
    if p.text != livro or q.text == livro:
        raise SystemExit("ESPECIME_MILHAR=falhou")
    y -= h + max(hp, hq)
    conferir_limite(y, "faixa_milhar")
    return y


def sobrepor_grade(c) -> None:
    c.saveState()
    c.setStrokeColorRGB(0.2, 0.5, 0.9, alpha=0.35)
    c.setLineWidth(0.25)
    c.rect(X0, BASE, G.util_l, G.util_a, stroke=1, fill=0)
    xn = X0 + G.leitura
    c.line(xn, BASE, xn, TOPO)
    c.line(xn + G.calha, BASE, xn + G.calha, TOPO)
    c.setStrokeColorRGB(0.2, 0.5, 0.9, alpha=0.12)
    y = TOPO
    while y > BASE:
        c.line(X0, y, DIREITA, y)
        y -= G.u
    c.restoreState()


def gerar(saida: Path, grade: bool = False) -> dict:
    st.registrar_fontes()
    c = Canvas(str(saida), pagesize=(G.pagina_l, G.pagina_a), pageCompression=1, initialFontName="Sans")
    c.setTitle("Raio-X Territorial — espécime do sistema visual")
    c.setAuthor("Raio-X Territorial")
    c.setSubject("Relatório do imóvel rural")
    st.desenhar_moldura(c, 3, 13, "Espécime do sistema visual", "CAR MG-3120904-…A14D011F · emitido em 13/09/2026")
    usados_estilo.update(("kicker", "corrente", "folio", "rodape_id"))
    y = cabeca(c, TOPO)
    y_col = y
    y_leitura = coluna_leitura(c, y_col)
    y_cores = coluna_cores(c, y_col)
    y = min(y_leitura, y_cores) - G.us(2)
    y = grade_pequena(c, y)
    y = faixa_milhar(c, y)
    if grade:
        sobrepor_grade(c)
    c.showPage()
    c.save()
    problemas = list(estouros)
    faltam_estilos = sorted(set(st.ESTILO) - usados_estilo)
    faltam_cores = sorted(set(st.COR) - usadas_cor)
    if faltam_estilos or faltam_cores:
        problemas.append(f"incompleto:estilos={faltam_estilos}:cores={faltam_cores}")
    sobra_mm = (y - BASE) / mm
    return {"pdf": str(saida), "sobra_mm": sobra_mm, "estilos": len(usados_estilo), "cores": len(usadas_cor),
            "problemas": problemas}


def renderizar_png(pdf: Path, png: Path, dpi: int = 110) -> None:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    doc[0].render(scale=dpi / 72).to_pil().save(png)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="book_v10_specimen.pdf")
    ap.add_argument("--png")
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--grade", action="store_true", help="sobrepõe colunas e unidade vertical")
    args = ap.parse_args()
    info = gerar(Path(args.out), grade=args.grade)
    if args.png:
        renderizar_png(Path(args.out), Path(args.png), args.dpi)
    if info["problemas"]:
        raise SystemExit("BOOK_V10_SPECIMEN=falhou " + " | ".join(info["problemas"]))
    print(f"BOOK_V10_SPECIMEN=ok pdf={info['pdf']} estilos={info['estilos']} cores={info['cores']} "
          f"sobra_mm={_n(int(info['sobra_mm'] * 10) / 10)}")


if __name__ == "__main__":
    main()
