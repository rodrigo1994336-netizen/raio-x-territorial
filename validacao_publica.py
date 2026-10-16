"""Página pública de conferência do relatório: /validar e /validar/{codigo}.

Quem recebe um Raio-X Territorial pode conferir, sem conta e sem pedir nada a ninguém,
se o documento foi emitido por nós e se não foi alterado depois. A conferência é da
assinatura do documento — não é uma nova consulta às fontes oficiais, e a página diz
isso com essas palavras.

Regras que moldam o que está escrito aqui:

* sem segredo configurado a página não diz "não confere": diz que a conferência não está
  disponível nesta instalação. Fingir o acerto é proibido; fingir o erro também engana;
* nenhum dado pessoal entra na página. O código do CAR é dado público do cadastro e já
  vem impresso no relatório que a pessoa tem na mão;
* o código conferido não vai para registro nenhum, e o segredo não aparece em texto,
  em cabeçalho, em endereço nem em mensagem de erro;
* limite de tentativas por origem, para ninguém sair testando código no chute.

A página não usa JavaScript: é formulário puro, com política de conteúdo que proíbe
qualquer script. Assim ela abre no celular do comprador, no computador do cartório e no
navegador velho do escritório.
"""
from __future__ import annotations

import html
import threading
import time
from collections import OrderedDict

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

import report_validacao_codigo as validacao
from report_api import app

VERSAO = "1.0.0-validador-publico"

# Teto por origem: 15 tentativas por minuto e 90 por hora. Quem erra o código algumas
# vezes não é incomodado; quem varre no chute para antes da primeira chance real.
LIMITE_MINUTO = 15
LIMITE_HORA = 90
ORIGENS_LEMBRADAS = 4096

_TRAVA = threading.Lock()
_TENTATIVAS: "OrderedDict[str, list[float]]" = OrderedDict()

_POLITICA = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
_CABECALHOS = {
    "content-security-policy": _POLITICA,
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
}

_ESTILO = """
:root{--tinta:#16241c;--apagado:#5c6b63;--linha:#d9e0e6;--fundo:#f6f8f7;--verde:#1d7a4c;--atencao:#8a5a00}
*{box-sizing:border-box}
body{margin:0;background:var(--fundo);color:var(--tinta);
 font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
 -webkit-text-size-adjust:100%}
.folha{max-width:680px;margin:0 auto;padding:20px 16px 56px}
header{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.marca{font-weight:700;letter-spacing:.3px;color:var(--verde)}
.marca small{display:block;font-weight:400;color:var(--apagado);letter-spacing:0}
h1{font-size:1.45rem;line-height:1.25;margin:0 0 8px}
p{margin:0 0 12px}
.aviso{color:var(--apagado);font-size:.92rem}
.cartao{background:#fff;border:1px solid var(--linha);border-radius:14px;padding:18px;margin:0 0 18px}
form{display:flex;flex-direction:column;gap:12px}
label{font-weight:600}
input[type=text]{min-height:48px;width:100%;padding:12px 14px;font-size:1.05rem;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.06em;
 border:1px solid var(--linha);border-radius:10px;background:#fff;color:var(--tinta)}
button{min-height:48px;padding:12px 20px;font-size:1.02rem;font-weight:700;color:#fff;
 background:var(--verde);border:0;border-radius:10px;cursor:pointer}
dl{margin:0;display:grid;grid-template-columns:1fr;gap:2px 16px}
dt{font-size:.82rem;text-transform:uppercase;letter-spacing:.05em;color:var(--apagado);margin-top:10px}
dd{margin:0;font-size:1.02rem;overflow-wrap:anywhere}
dd.codigo{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.96rem}
.selo{display:inline-block;padding:7px 12px;border-radius:999px;font-weight:700;font-size:.9rem}
.selo.ok{background:#e4f4ea;color:#14603c}
.selo.nao{background:#fdeceb;color:#8a2017}
.selo.espera{background:#fdf3e0;color:var(--atencao)}
ul{margin:6px 0 0;padding-left:20px}
footer{color:var(--apagado);font-size:.86rem;border-top:1px solid var(--linha);padding-top:14px}
@media (min-width:620px){dl{grid-template-columns:200px 1fr}dt{margin-top:0}dl>*{padding:6px 0}}
"""

_RODAPE = (
    "Esta página confere a autenticidade do documento: se o código foi emitido pelo "
    "Raio-X Territorial e se nada nele foi alterado depois da emissão. Ela não repete as "
    "consultas às fontes oficiais e não diz nada sobre o conteúdo delas."
)


def _origem(request: Request) -> str:
    # O endereço que o nosso servidor de borda enxergou é o último da lista: se a pessoa
    # mandar um encaminhamento inventado, ele entra antes e não substitui o verdadeiro.
    encaminhado = request.headers.get("x-forwarded-for") or ""
    partes = [x.strip() for x in encaminhado.split(",") if x.strip()]
    if partes:
        return partes[-1][:60]
    cliente = getattr(request, "client", None)
    return (getattr(cliente, "host", None) or "desconhecida")[:60]


def _liberado(origem: str, agora: float | None = None) -> tuple[bool, int]:
    agora = time.monotonic() if agora is None else agora
    with _TRAVA:
        marcas = [t for t in _TENTATIVAS.get(origem, []) if agora - t < 3600]
        no_minuto = [t for t in marcas if agora - t < 60]
        if len(no_minuto) >= LIMITE_MINUTO:
            espera = int(61 - (agora - min(no_minuto)))
        elif len(marcas) >= LIMITE_HORA:
            espera = int(3601 - (agora - min(marcas)))
        else:
            espera = 0
        if espera <= 0:
            marcas.append(agora)
        _TENTATIVAS[origem] = marcas
        _TENTATIVAS.move_to_end(origem)
        while len(_TENTATIVAS) > ORIGENS_LEMBRADAS:
            _TENTATIVAS.popitem(last=False)
    return (espera <= 0), max(espera, 0)


def _linhas(resultado: dict) -> list[tuple[str, str, str]]:
    linhas = [
        ("Imóvel (código do CAR)", resultado.get("car_code", ""), "codigo"),
        ("Emitido em", resultado.get("emitido_em", ""), ""),
    ]
    if resultado.get("versao_relatorio"):
        linhas.append(("Versão do relatório", resultado["versao_relatorio"], ""))
    return [(rotulo, valor, classe) for rotulo, valor, classe in linhas if str(valor).strip()]


def _bloco_pendencias(resultado: dict) -> str:
    if not resultado.get("pendencias_registradas"):
        return (
            "<dt>Consultas pendentes</dt><dd>Este código não guarda a lista de consultas "
            "pendentes desta emissão. Ela está escrita no próprio relatório.</dd>"
        )
    itens = list(resultado.get("pendencias") or [])
    if resultado.get("pendencias_outras"):
        itens.append("Outras consultas desta emissão")
    if not itens:
        return (
            "<dt>Consultas pendentes</dt><dd>Nenhuma. Na hora da emissão, todas as fontes "
            "previstas responderam.</dd>"
        )
    lista = "".join(f"<li>{html.escape(str(x))}</li>" for x in itens)
    return (
        "<dt>Consultas pendentes</dt><dd>Na hora da emissão, estas fontes ainda não "
        f"tinham respondido:<ul>{lista}</ul></dd>"
    )


def _pagina(corpo: str, titulo: str = "Conferir um relatório") -> str:
    return (
        "<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex\">"
        f"<title>{html.escape(titulo)} · Raio-X Territorial</title>"
        f"<style>{_ESTILO}</style></head><body><div class=\"folha\">"
        "<header><span class=\"marca\">RAIO-X TERRITORIAL"
        "<small>conferência de documento</small></span></header>"
        f"{corpo}"
        f"<footer>{html.escape(_RODAPE)}</footer>"
        "</div></body></html>"
    )


def _formulario(codigo: str = "", mensagem: str = "") -> str:
    aviso = f"<p class=\"aviso\">{html.escape(mensagem)}</p>" if mensagem else ""
    return (
        "<div class=\"cartao\">"
        "<h1>Confira se o relatório é autêntico</h1>"
        "<p>Digite o código impresso no fim do relatório, ou aponte a câmera para o QR que "
        "está ao lado dele. Maiúsculas, minúsculas, traços e espaços não mudam nada.</p>"
        f"{aviso}"
        "<form method=\"get\" action=\"/validar\">"
        "<label for=\"codigo\">Código do documento</label>"
        f"<input type=\"text\" id=\"codigo\" name=\"codigo\" autocomplete=\"off\" "
        f"spellcheck=\"false\" inputmode=\"latin\" maxlength=\"{validacao.ENTRADA_MAXIMA}\" "
        f"placeholder=\"ABCDEF-GHJKMN-...\" value=\"{html.escape(codigo)}\">"
        "<button type=\"submit\">Conferir</button>"
        "</form></div>"
    )


def _resultado_html(resultado: dict, codigo_digitado: str) -> str:
    estado = resultado.get("estado")
    if estado == "autentico":
        campos = "".join(
            f"<dt>{html.escape(rotulo)}</dt><dd class=\"{classe}\">{html.escape(str(valor))}</dd>"
            for rotulo, valor, classe in _linhas(resultado)
        )
        return (
            "<div class=\"cartao\">"
            "<p><span class=\"selo ok\">Documento autêntico</span></p>"
            "<h1>Este código foi emitido pelo Raio-X Territorial</h1>"
            "<p>Os dados abaixo estão assinados dentro do próprio código: se alguém mudar "
            "uma letra, a conferência deixa de fechar.</p>"
            f"<dl>{campos}{_bloco_pendencias(resultado)}</dl>"
            "</div>" + _formulario()
        )
    if estado == "indisponivel":
        return (
            "<div class=\"cartao\">"
            "<p><span class=\"selo espera\">Conferência pendente</span></p>"
            "<h1>A conferência por código não está disponível agora</h1>"
            "<p>Não dá para afirmar nada sobre este código neste momento. Tente de novo mais "
            "tarde; se precisar da confirmação hoje, fale com quem lhe enviou o relatório.</p>"
            "</div>"
        )
    pista = (
        "O código parece incompleto ou com letra trocada. Confira letra por letra e tente de novo."
        if resultado.get("motivo") == "formato_do_codigo"
        else "Este código não foi emitido pelo Raio-X Territorial, ou o documento foi alterado "
             "depois da emissão."
    )
    return (
        "<div class=\"cartao\">"
        "<p><span class=\"selo nao\">Não confere</span></p>"
        "<h1>Não foi possível confirmar este documento</h1>"
        f"<p>{html.escape(pista)}</p>"
        "</div>" + _formulario(codigo_digitado)
    )


def _html(corpo: str, status: int = 200, titulo: str = "Conferir um relatório",
          extra: dict | None = None) -> HTMLResponse:
    cabecalhos = dict(_CABECALHOS)
    cabecalhos.update(extra or {})
    return HTMLResponse(_pagina(corpo, titulo), status_code=status, headers=cabecalhos)


def _muitas_tentativas(espera: int) -> str:
    return (
        "<div class=\"cartao\">"
        "<p><span class=\"selo espera\">Aguarde um instante</span></p>"
        "<h1>Muitas tentativas desta conexão</h1>"
        f"<p>Espere cerca de {max(espera, 1)} segundo(s) e confira de novo. O limite existe "
        "para ninguém ficar testando códigos no chute.</p>"
        "</div>"
    )


@app.get("/validar", include_in_schema=False)
async def validar_pagina(request: Request):
    digitado = (request.query_params.get("codigo") or "").strip()
    if not digitado:
        if not validacao.validacao_disponivel():
            return _html(_resultado_html({"estado": "indisponivel"}, ""))
        return _html(_formulario())
    liberado, espera = _liberado(_origem(request))
    if not liberado:
        return _html(_muitas_tentativas(espera), 429, extra={"retry-after": str(max(espera, 1))})
    # O código conferido não é registrado em lugar nenhum: ele identifica um documento de cliente.
    return _html(_resultado_html(validacao.conferir(digitado), digitado))


@app.get("/validar/{codigo}", include_in_schema=False)
async def validar_codigo(codigo: str, request: Request):
    liberado, espera = _liberado(_origem(request))
    if not liberado:
        return _html(_muitas_tentativas(espera), 429, extra={"retry-after": str(max(espera, 1))})
    return _html(_resultado_html(validacao.conferir(codigo), codigo))


@app.get("/v1/publico/validar/{codigo}", include_in_schema=False)
async def validar_json(codigo: str, request: Request):
    liberado, espera = _liberado(_origem(request))
    if not liberado:
        return JSONResponse(
            {"estado": "muitas_tentativas", "espere_segundos": max(espera, 1)},
            status_code=429, headers={"retry-after": str(max(espera, 1)), "cache-control": "no-store"},
        )
    resultado = validacao.conferir(codigo)
    corpo = {k: v for k, v in resultado.items() if k != "motivo"}
    corpo["confere"] = "autenticidade do documento, nao o conteudo das fontes"
    status = 200 if resultado.get("estado") == "autentico" else (
        503 if resultado.get("estado") == "indisponivel" else 404)
    return JSONResponse(corpo, status_code=status, headers={"cache-control": "no-store"})


print(f"RX_VALIDACAO_PUBLICA={VERSAO} rotas=/validar,/validar/{{codigo}},/v1/publico/validar", flush=True)
