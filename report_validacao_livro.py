"""O código de autenticidade e o QR no relatório-livro, na última folha.

O livro é o formato que o cliente recebe desde o PR #83. Ele desenha em tela própria, e
não na pilha do motor antigo — por isso o bloco entra aqui, pela mesma composição de
blocos que o livro já usa, e não pelo remendo do formato antigo (``report_blocos_r3``).

O texto, o código e o QR vêm do mesmo lugar que o formato antigo usa
(``report_validacao_pdf``): um relatório não pode dizer uma coisa no livro e outra no
formato de volta.

Onde entra: no Apêndice C (glossário), logo acima do colofão, que já é onde o livro diz
quando foi emitido. O bloco é opcional na composição: se um dia a folha não fechar, ele
sai em vez de derrubar a emissão inteira — e, saindo, não sobra promessa nenhuma de
conferência na folha.
"""
from __future__ import annotations

from reportlab.graphics import renderPDF
from reportlab.lib.units import mm

import report_book_v10 as livro
import report_book_v10_style as estilo
import report_validacao_pdf as bloco_pdf

NOME = "autenticidade"
TITULO = "CÓDIGO DE AUTENTICIDADE"
TEXTO = ("Qualquer pessoa confere este documento sozinha, sem conta e sem pedir nada a ninguém: "
         "abra {endereco} e digite o código abaixo, ou aponte a câmera do celular para o QR ao "
         "lado. A conferência diz se o documento foi emitido pelo Raio-X Territorial e se nada "
         "nele foi alterado depois. Ela não repete as consultas às fontes oficiais.")
QR_LADO = 23 * mm
COLUNA_QR = 27 * mm
PASSO_DO_CODIGO = 4.8 * mm
FONTE_DO_CODIGO = "Sans-Sb"
CORPO_DO_CODIGO = 9.2


def altura_do_codigo(linhas: list[str]) -> float:
    return PASSO_DO_CODIGO * len(linhas)


def bloco_de_autenticidade(L):
    """O bloco pronto para a pilha do livro, ou None quando não há código a imprimir."""
    emitido = bloco_pdf.codigo_do_relatorio(L.p)
    if not emitido.get("ok"):
        return None
    largura_texto = livro.LARG - COLUNA_QR
    corpo = livro.texto(TEXTO.format(endereco=bloco_pdf.endereco_de_conferencia()),
                        "legenda", largura_texto)
    linhas = bloco_pdf.linhas_do_codigo(emitido["codigo_formatado"])
    altura_texto = 5 * mm + 1.2 * mm + corpo.altura + 2.2 * mm + altura_do_codigo(linhas)
    altura = max(QR_LADO, altura_texto)

    def desenhar(c, x, y_topo, h):
        c.saveState()
        c.setStrokeColor(estilo.COR["RULE"])
        c.setLineWidth(livro.G.fio_fino)
        c.line(x, y_topo, x + livro.LARG, y_topo)
        c.restoreState()
        y = y_topo - 1.6 * mm
        y -= livro.kicker(c, x, y, TITULO, estilo.COR["MUTED"])
        y -= 1.2 * mm
        y -= corpo.desenhar(c, x, y)
        y -= 2.2 * mm
        for linha in linhas:
            livro.linha_simples(c, x, y - PASSO_DO_CODIGO + 1.3 * mm, linha,
                                FONTE_DO_CODIGO, CORPO_DO_CODIGO, estilo.COR["INK"])
            y -= PASSO_DO_CODIGO
        desenho = bloco_pdf.qr_do_codigo(emitido["url"], QR_LADO)
        renderPDF.draw(desenho, c, x + livro.LARG - QR_LADO, y_topo - 1.6 * mm - QR_LADO)

    return livro.Bloco(NOME, altura, desenhar, espaco=2, prioridade=1)


def instalar() -> None:
    if getattr(livro, "_rx_validacao_livro", False):
        return
    fechar_original = livro.fechar_pagina

    def fechar_pagina(c, L, blocos, numero, chave_cap, grade):
        # A folha do colofão é a última do livro, e é onde ele já diz a data da emissão.
        if blocos and blocos[-1].nome == "colofao":
            novo = bloco_de_autenticidade(L)
            if novo is not None:
                blocos.insert(len(blocos) - 1, novo)
        return fechar_original(c, L, blocos, numero, chave_cap, grade)

    livro.fechar_pagina = fechar_pagina
    livro._rx_validacao_livro = True


instalar()
print("RX_VALIDACAO_LIVRO=codigo_e_qr_na_ultima_folha", flush=True)
