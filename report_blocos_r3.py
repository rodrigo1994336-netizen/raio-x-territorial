"""Encaixa no relatório de hoje os dois blocos novos, sem tocar no motor.

Um ponto só captura o payload da emissão em curso e um ponto só escreve nas seções:

* "Solo, aptidão, relevo e uso da terra" recebe a foto de satélite limpa, com o limite do
  CAR desenhado por cima e a data da cena na legenda de baixo;
* "Rastreabilidade e limitações" recebe o código de autenticidade e o QR da página de
  conferência.

O relatório-livro (ainda em revisão) não precisa deste arquivo: ele chama
``foto_limpa_no_relatorio.blocos_da_foto_limpa`` e
``report_validacao_pdf.bloco_de_autenticidade`` direto, e recebe os mesmos elementos.

Seção que não tem o que mostrar não ganha espaço em branco: os montadores devolvem lista
vazia, e o remendo não insere nada.
"""
from __future__ import annotations

import threading

import report_engine_v6 as v6
from foto_limpa_no_relatorio import blocos_da_foto_limpa
from report_validacao_pdf import bloco_de_autenticidade

SECAO_DA_FOTO = "Solo, aptidão, relevo e uso da terra"
SECAO_DA_AUTENTICIDADE = "Rastreabilidade e limitações"

_LOCAL = threading.local()


def payload_em_uso():
    return getattr(_LOCAL, "payload", None)


def instalar() -> None:
    if getattr(v6, "_rx_blocos_r3", False):
        return
    construtor_original = v6.build_premium_property_report_v6
    secao_original = v6._section

    def construir(path, payload):
        anterior = getattr(_LOCAL, "payload", None)
        _LOCAL.payload = payload
        try:
            return construtor_original(path, payload)
        finally:
            _LOCAL.payload = anterior

    def secao(title, subtitle=None):
        blocos = secao_original(title, subtitle)
        payload = payload_em_uso()
        if payload is None:
            return blocos
        if title == SECAO_DA_FOTO:
            return blocos + blocos_da_foto_limpa(payload)
        if title == SECAO_DA_AUTENTICIDADE:
            return blocos + bloco_de_autenticidade(payload)
        return blocos

    v6.build_premium_property_report_v6 = construir
    v6._section = secao
    v6._rx_blocos_r3 = True


instalar()
print("RX_BLOCOS_R3=foto_limpa_e_codigo_de_autenticidade_no_relatorio", flush=True)
