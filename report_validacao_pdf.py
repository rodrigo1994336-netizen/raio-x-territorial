"""Imprime no relatório o código de autenticidade e o QR da página de conferência.

Aqui só se monta o bloco. Quem o encaixa na página "Rastreabilidade e limitações" do
relatório de hoje é ``report_blocos_r3``; o relatório-livro chama
``bloco_de_autenticidade(payload)`` direto quando entrar.

Sem segredo configurado no ambiente, nada é impresso: nem código, nem QR, nem a frase que
promete conferência. Prometer uma conferência que não funciona é fingir o acerto.

O texto do código é desenhado como célula de tabela, e não como parágrafo, de propósito: a
normalização pt-BR do relatório reescreve número e data dentro de parágrafo, e o código
não é número nem data — é a assinatura do documento e não pode ser reescrita por ninguém.
"""
from __future__ import annotations

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Spacer, Table, TableStyle

import report_engine_v5 as v5
import report_validacao_codigo as validacao

SECAO = "Rastreabilidade e limitações"
TITULO = "Como conferir se este documento é autêntico"
EXPLICACAO = (
    "Qualquer pessoa confere este relatório sozinha, sem conta e sem pedir nada a ninguém: "
    "abra {endereco} e digite o código abaixo, ou aponte a câmera do celular para o QR. A "
    "conferência diz se o documento foi emitido pelo Raio-X Territorial e se nada nele foi "
    "alterado depois. Ela não repete as consultas às fontes oficiais."
)
QR_LADO = 30 * mm
LINHA_1 = 5  # grupos na primeira linha do código impresso
# A fonte, o corpo e a largura útil da célula ficam declarados aqui porque o portão mede a
# linha com ESTES valores. Medir com um número copiado no portão é não medir: trocar o
# corpo da fonte no desenho passaria despercebido e o código sairia cortado no papel.
FONTE_DO_CODIGO = "Courier-Bold"
CORPO_DO_CODIGO = 8.2
LARGURA_DO_CODIGO = 122 * mm


def versao_do_relatorio() -> str | None:
    try:
        import report_api

        return str(getattr(report_api, "APP_VERSION", "") or "") or None
    except Exception:
        return None


def codigo_do_relatorio(payload) -> dict:
    """Emite o código desta emissão a partir do payload do relatório."""
    dados = payload or {}
    propriedade = dados.get("property") or {}
    car_code = propriedade.get("car_code") or dados.get("car_code")
    emitido_em = dados.get("generated_at")
    fontes = dados.get("sources")
    # Registrar a lista de pendências só faz sentido quando existe tabela de situação das
    # fontes nesta emissão: senão o código diria "nenhuma pendência" sem ter olhado.
    registrar = isinstance(fontes, list) and bool(fontes)
    pendentes, outras = validacao.pendencias_do_relatorio(dados) if registrar else ([], False)
    if outras:
        pendentes = list(pendentes) + ["outras"]
    return validacao.emitir(
        car_code, emitido_em, versao_do_relatorio(),
        fontes_sem_resposta=pendentes, registrar_pendencias=registrar,
    )


def qr_do_codigo(url: str, lado: float = QR_LADO) -> Drawing:
    """O QR do endereço de conferência. Um desenho só, usado pelos dois formatos do relatório."""
    widget = QrCodeWidget(url, barLevel="M", barBorder=2)
    x0, y0, x1, y1 = widget.getBounds()
    desenho = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0, lado / (y1 - y0),
                                             -x0 * lado / (x1 - x0), -y0 * lado / (y1 - y0)])
    desenho.add(widget)
    return desenho


def linhas_do_codigo(formatado: str) -> list[str]:
    """O código quebrado para caber na linha impressa, sem perder nenhum grupo."""
    grupos = formatado.split("-")
    return [x for x in ("-".join(grupos[:LINHA_1]), "-".join(grupos[LINHA_1:])) if x]


def endereco_de_conferencia() -> str:
    return validacao.base_url().replace("https://", "").replace("http://", "") + validacao.CAMINHO


def _codigo_em_duas_linhas(formatado: str) -> str:
    return "\n".join(linhas_do_codigo(formatado))


def bloco_de_autenticidade(payload) -> list:
    """O bloco pronto para entrar na história do relatório. Lista vazia = nada a imprimir."""
    emitido = codigo_do_relatorio(payload)
    if not emitido.get("ok"):
        return []
    texto = EXPLICACAO.format(endereco=endereco_de_conferencia())
    cartao = Table(
        [[
            Table(
                [[v5.P(TITULO, "cellb")],
                 [v5.P(texto, "small")],
                 [_codigo_em_duas_linhas(emitido["codigo_formatado"])]],
                colWidths=[LARGURA_DO_CODIGO],
                style=TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("FONTNAME", (0, 2), (0, 2), FONTE_DO_CODIGO),
                    ("FONTSIZE", (0, 2), (0, 2), CORPO_DO_CODIGO),
                    ("LEADING", (0, 2), (0, 2), 11.4),
                    ("TEXTCOLOR", (0, 2), (0, 2), v5.INK),
                    ("TOPPADDING", (0, 2), (0, 2), 5),
                ]),
            ),
            qr_do_codigo(emitido["url"]),
        ]],
        colWidths=[128 * mm, 37 * mm],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("BACKGROUND", (0, 0), (-1, -1), v5.SOFT),
            ("BOX", (0, 0), (-1, -1), 0.6, v5.LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]),
    )
    cartao.hAlign = "CENTER"
    return [Spacer(1, 5 * mm), cartao]


print("RX_VALIDACAO_PDF=bloco_de_codigo_e_qr_pronto", flush=True)
