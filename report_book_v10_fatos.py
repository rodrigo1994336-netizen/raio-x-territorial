"""Camada de leitura do relatório-livro v10: do payload REAL para os envelopes `Fato`.

SPEC_RELATORIO_LIVRO §2.0.2 ("Camada de leitura: de onde sai o estado") e §9.3.

O livro nunca lê um campo do payload direto: lê daqui. Duas regras, nesta ordem:

1. **Envelope `_v51` no payload manda.** Quando a faixa dona de um fato já grava o envelope
   (``FATO_CHAVES`` do contrato), este módulo devolve o envelope como está, com ``origem="v51"``.
   A transição daquele fato é ignorada — o livro não tem dois caminhos para o mesmo dado.
2. **Regra de transição**, só para o fato cujo `_v51` ainda não existe: converte o campo que o
   relatório de hoje já traz para o MESMO envelope, com o estado saindo da tabela §2.0.2:

   ```
   estado = "pendente"          a fonte não respondeu, ou o número não existe
          = "encontrado"        n > 0
          = "nao_encontrado"    n == 0 e a fonte sustenta ausência ("zero vale?")
          = "fora_desta_edicao" n == 0 e a fonte NÃO sustenta ausência
          = "nao_se_aplica"     fonte fora da cobertura
   ```

Regras do dono que moram aqui:

- **Zero não é ausência.** Zero só vira ``nao_encontrado`` com a fonte respondida e completa, e com
  o alcance escrito em ``limites``; sem isso é ``pendente`` ou ``fora_desta_edicao``. Resposta
  cortada (``complete``/``count_is_minimum``) nunca vira ausência.
- **Tentar não é responder.** "INDISPONÍVEL", "CONSULTA PENDENTE" e "NÃO CONSULTADA" na lista de
  fontes contam como não respondido.
- **Nunca inventa.** Faltando o campo, o fato sai ``pendente`` ou ``fora_desta_edicao``; nenhum
  valor é estimado nem arredondado para zero.
- **Pessoas e empresas nunca entram.** ``limpar_pessoas`` tira as chaves de ``CAMPOS_PROIBIDOS``, e
  os blocos que vêm em linha (armazém da CONAB) entram só pela coluna que não identifica ninguém.

Só biblioteca padrão. A classificação do PRODES por mancha usa ``prodes_reading_f2`` quando a
análise ao vivo está à mão (é o mesmo classificador do relatório atual, não um segundo caminho).
"""
from __future__ import annotations

import re
from typing import Any, Callable

from report_book_v10_contracts import (
    CAMPOS_PROIBIDOS,
    FATO_CHAVES,
    FATOS_SEMPRE_FORA,
    PRODES_MARCO_2008,
    PRODES_MARCO_2019,
)

# --------------------------------------------------------------------------------------------------
# Lista de fontes do payload
# --------------------------------------------------------------------------------------------------
# Só estes contam como "a fonte respondeu". Qualquer outro texto é não respondido.
STATUS_RESPONDEU: frozenset[str] = frozenset({"CONSULTADA", "CONSULTADO"})
# Fonte desligada de propósito (licença, credencial, integração não contratada): não é pendência,
# porque ◌ promete uma nova tentativa que não vai acontecer (§2.0.2, "fatos de escopo").
STATUS_DESLIGADA_PREFIXOS = ("INTEGRAÇÃO PREPARADA", "INTEGRACAO PREPARADA")

MESES_EN = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
# Colunas que o livro pode mostrar de um armazém: nenhuma identifica pessoa nem empresa.
ARMAZEM_COLUNAS = ("tipo", "especie", "municipio", "uf", "capacidade_t", "distancia_km")


def _txt(v: Any) -> str:
    return "" if v is None else str(v)


def _num(v: Any) -> float | None:
    """Número do payload, ou None quando não é número (texto de pendência, '-', vazio)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _txt(v).strip().replace(" ", " ").strip()
    if not s:
        return None
    achado = re.match(r"^-?[\d.]*\d(?:,\d+)?$|^-?\d+(?:\.\d+)?$", s)
    if not achado:
        return None
    s = achado.group()
    if "," in s:                     # 1.234,56 → 1234.56 (pt-BR)
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:           # 1.234.567 → 1234567 (milhar pt-BR sem decimal)
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _num_en(v: Any) -> float | None:
    """Número escrito à inglesa no payload antigo ("12.6398 ha", "95399.0 cabeças")."""
    achado = re.search(r"-?\d+(?:\.\d+)?", _txt(v).replace(",", ""))
    return float(achado.group()) if achado else None


def _int_do_texto(v: Any) -> int | None:
    """Primeiro inteiro de um texto de linha ("0 ocorrência(s) • 0.0 ha") — None sem inteiro."""
    achado = re.search(r"-?\d+", _txt(v))
    return int(achado.group()) if achado else None


def _cam(no: Any, caminho: str) -> Any:
    """Valor de um caminho pontuado, ou None se qualquer passo faltar."""
    alvo = no
    for parte in caminho.split("."):
        if not isinstance(alvo, dict) or parte not in alvo:
            return None
        alvo = alvo[parte]
    return alvo


def limpar_pessoas(valor: Any) -> Any:
    """Tira, em qualquer profundidade, as chaves de ``CAMPOS_PROIBIDOS`` (nome, CPF, CNPJ, endereço).

    O livro não mostra pessoas (guia de redação, regra 13, e PLANO §3.1). A comparação é por nome
    exato de chave, sem diferença de maiúsculas, como o contrato manda. Linha posicional (lista de
    colunas) não tem chave: quem monta o valor escolhe a coluna, nunca a linha inteira.
    """
    if isinstance(valor, dict):
        return {k: limpar_pessoas(v) for k, v in valor.items() if k.casefold() not in CAMPOS_PROIBIDOS}
    if isinstance(valor, list):
        return [limpar_pessoas(x) for x in valor]
    return valor


class Fontes:
    """Índice da lista ``payload["sources"]``, consultada por expressão do nome público."""

    def __init__(self, payload: dict):
        self.linhas: list[dict] = [s for s in (payload.get("sources") or []) if isinstance(s, dict)]

    def achar(self, regex: str) -> dict | None:
        padrao = re.compile(regex, re.IGNORECASE)
        for linha in self.linhas:
            if padrao.search(_txt(linha.get("name"))):
                return linha
        return None

    def respondeu(self, regex: str) -> bool:
        linha = self.achar(regex)
        return bool(linha) and _txt(linha.get("status")).strip().upper() in STATUS_RESPONDEU

    def desligada(self, regex: str) -> bool:
        linha = self.achar(regex)
        if not linha:
            return False
        status = _txt(linha.get("status")).strip().upper()
        return any(status.startswith(p) for p in STATUS_DESLIGADA_PREFIXOS)

    def nome(self, regex: str, padrao_nome: str) -> str:
        linha = self.achar(regex)
        return _txt(linha.get("name")) if linha else padrao_nome

    def data_base(self, regex: str) -> str | None:
        """Data da base escrita na descrição da fonte ("base de 16/10/2026")."""
        linha = self.achar(regex)
        if not linha:
            return None
        achado = re.search(r"base de (\d{2}/\d{2}/\d{4})", _txt(linha.get("description")))
        if not achado:
            return None
        d, m, a = achado.group(1).split("/")
        return f"{a}-{m}-{d}"


# --------------------------------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------------------------------
def envelope(estado: str, valor: Any = None, *, unidade: str | None = None,
             fonte_id: str = "", fonte_nome: str = "", licenca: str | None = None,
             citacao: str | None = None, somente_apendice: bool = False,
             data_base: str | None = None, consultado_em: str | None = None,
             limites: tuple[str, ...] = (), texto_base: str | None = None,
             origem: str = "transicao") -> dict:
    """Envelope `Fato` do PLANO §3.1, no formato que o payload grava e o livro lê.

    ``valor`` é sempre ``None`` em ``pendente`` (regra do plano): um valor ao lado de ◌ seria lido
    como resposta. O ``motivo`` do envelope é interno e não existe aqui: o livro nunca o imprime.
    """
    if estado == "pendente":
        valor = None
    fonte: dict[str, Any] = {"id": fonte_id, "nome_publico": fonte_nome}
    if licenca:
        fonte["licenca"] = licenca
    if citacao:
        fonte["citacao"] = citacao
    if somente_apendice:
        fonte["somente_apendice"] = True
    saida = {
        "estado": estado,
        "valor": limpar_pessoas(valor),
        "unidade": unidade,
        "fonte": fonte,
        "data_base": data_base,
        "consultado_em": consultado_em,
        "limites": list(limites),
        "origem": origem,
    }
    if texto_base:
        saida["texto_base"] = texto_base
    return saida


def estado_por_contagem(fonte_ok: bool, n: float | None, zero_vale: bool, completa: bool = True) -> str:
    """O cálculo de estado da tabela §2.0.2, isolado para o controle por mutação do portão.

    ``completa=False`` (resposta cortada, "pelo menos N") nunca vira ausência: zero só conta quando
    a fonte respondeu inteira.
    """
    if not fonte_ok or n is None:
        return "pendente"
    if n > 0:
        return "encontrado"
    if not completa:
        return "pendente"
    return "nao_encontrado" if zero_vale else "fora_desta_edicao"


def _estado_f2(bloco: Any, zero_vale: bool = True) -> str:
    """Estado de um bloco F2 (``state`` found/not_found/pending/unavailable + ``complete``)."""
    if not isinstance(bloco, dict):
        return "pendente"
    estado = _txt(bloco.get("state")).strip().lower()
    completa = bloco.get("complete") is not False and bloco.get("partial") is not True
    if estado == "found":
        return "encontrado"
    if estado == "not_found":
        if not completa or bloco.get("count_is_minimum") is True:
            return "pendente"
        return "nao_encontrado" if zero_vale else "fora_desta_edicao"
    return "pendente"


# --------------------------------------------------------------------------------------------------
# Contexto
# --------------------------------------------------------------------------------------------------
class Contexto:
    """O que as transições podem ler: o payload, a lista de fontes e a análise ao vivo."""

    def __init__(self, payload: dict, result: dict | None = None):
        self.p = payload
        self.result = result or {}
        self.fontes = Fontes(payload)
        self.emitido = _txt(payload.get("generated_at")) or None
        prop = payload.get("property") or {}
        self.uf = _txt(prop.get("uf")).upper() or _txt(prop.get("car_code"))[:2].upper()
        self.municipio = _txt(prop.get("municipality"))
        self.area_ha = _num(prop.get("area_ha"))
        self._prodes_leitura: dict | None = None
        self._prodes_tentado = False

    def em(self, caminho: str) -> Any:
        return _cam(self.p, caminho)

    def linhas(self, caminho: str) -> list:
        v = self.em(caminho)
        return v if isinstance(v, list) else []

    def linha_por_rotulo(self, caminho: str, prefixo: str) -> str | None:
        for linha in self.linhas(caminho):
            if isinstance(linha, (list, tuple)) and len(linha) >= 2 and _txt(linha[0]).startswith(prefixo):
                return _txt(linha[1])
        return None

    # -- PRODES: a classificação por mancha é a do relatório atual, não uma segunda ---------------
    def prodes_leitura(self) -> dict | None:
        """Leitura completa do PRODES por mancha (dentro/divisa, largura, período).

        Primeiro a leitura que a análise já carrega; se ela vier compacta, chama o mesmo
        classificador do relatório (``prodes_reading_f2.classify_prodes``) sobre os mesmos dados.
        Devolve None quando não há como classificar — e então o capítulo fica ``pendente``.
        """
        if self._prodes_tentado:
            return self._prodes_leitura
        self._prodes_tentado = True
        prodes = self.result.get("prodes")
        leitura = (prodes or {}).get("reading") if isinstance(prodes, dict) else None
        if isinstance(leitura, dict) and (leitura.get("inside") or {}).get("occurrences") is not None:
            self._prodes_leitura = leitura
            return leitura
        car = self.result.get("car") or {}
        geometria = car.get("geometry") if isinstance(car, dict) else None
        if isinstance(prodes, dict) and geometria:
            try:
                import prodes_reading_f2
            except Exception:
                return None
            try:
                self._prodes_leitura = prodes_reading_f2.classify_prodes(
                    prodes, geometria, (car.get("properties") or {}).get("area") or self.area_ha)
            except Exception:
                self._prodes_leitura = None
        return self._prodes_leitura


Transicao = Callable[[Contexto], dict]
_TRANSICOES: dict[str, Transicao] = {}


def transicao(fato_id: str) -> Callable[[Transicao], Transicao]:
    def registrar(fn: Transicao) -> Transicao:
        _TRANSICOES[fato_id] = fn
        return fn
    return registrar


def data_br(iso: str | None) -> str:
    """Data em dd/mm/aaaa para o texto de limite. Nada de ISO no que o cliente lê."""
    bruto = _txt(iso)[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", bruto):
        a, m, d = bruto.split("-")
        return f"{d}/{m}/{a}"
    return bruto


def periodo_legal(data_iso: str) -> str:
    """Período da lei de uma data de imagem: as duas datas-marco do contrato, nunca texto solto."""
    d = _txt(data_iso)[:10]
    if not d:
        return "depois_2019_07_31"
    if d <= PRODES_MARCO_2008:
        return "ate_2008_07_22"
    if d <= PRODES_MARCO_2019:
        return "ate_2019_07_31"
    return "depois_2019_07_31"


# ==================================================================================================
# CAR e documentos
# ==================================================================================================
_SITUACAO_CAR = {"AT", "PE", "SU", "CA"}


@transicao("car")
def _t_car(ctx: Contexto) -> dict:
    """``car.status`` existe sempre que o CAR foi lido; sem data de inscrição, a linha da data some."""
    status = _txt(ctx.em("car.status")).strip().upper()
    nome = ctx.fontes.nome(r"^SICAR$", "SICAR")
    if not (ctx.fontes.respondeu(r"^SICAR$") and status in _SITUACAO_CAR):
        return envelope("pendente", fonte_id="sicar_wfs", fonte_nome=nome, consultado_em=ctx.emitido,
                        limites=("situação do cadastro como o SICAR publica",))
    return envelope("encontrado", None, unidade="data", fonte_id="sicar_wfs", fonte_nome=nome,
                    consultado_em=ctx.emitido,
                    limites=("a data de inscrição no CAR não entra nesta edição: a linha da data não aparece",))


@transicao("car_ultima_alteracao")
def _t_car_ultima(ctx: Contexto) -> dict:
    return envelope("fora_desta_edicao", fonte_id="sicar_wfs", fonte_nome="SICAR")


@transicao("composicao_car")
def _t_composicao(ctx: Contexto) -> dict:
    """D6 fica desligado enquanto o SICAR não devolve a composição: o bloco não existe.

    ``car.areas`` vem com "NÃO CONSULTADO NESTA FONTE" e ``car.environmental_composition`` vazio.
    Nenhum dos dois sustenta presença nem ausência de APP e de Reserva Legal declaradas.
    """
    composicao = ctx.linhas("car.environmental_composition")
    if composicao:
        return envelope("encontrado", {"linhas": limpar_pessoas(composicao)}, fonte_id="sicar_composicao",
                        fonte_nome=ctx.fontes.nome(r"composi(ç|c)(ã|a)o ambiental", "SICAR — composição ambiental"),
                        consultado_em=ctx.emitido,
                        limites=("o que o dono declarou no CAR; o órgão ambiental ainda não conferiu",))
    return envelope("fora_desta_edicao", fonte_id="sicar_composicao",
                    fonte_nome=ctx.fontes.nome(r"composi(ç|c)(ã|a)o ambiental", "SICAR — composição ambiental"))


def _acervo(ctx: Contexto, qual: str) -> dict:
    """SIGEF e SNCI pela base oficial do INCRA (acervo fundiário), com data da consulta."""
    acervo = ctx.em("incra_acervo_f2") or {}
    bloco = acervo.get(qual) if isinstance(acervo, dict) else None
    fonte = dict(fonte_id="incra_acervo_" + qual,
                 fonte_nome=ctx.fontes.nome(rf"INCRA — Acervo Fundi(á|a)rio \({qual.upper()}\)",
                                            f"INCRA — Acervo Fundiário ({qual.upper()})"))
    limites = ("consulta à base oficial do INCRA: certificação não é matrícula nem prova de dono",
               "nome de parcela de outro cadastro é referência, nunca o nome do CAR")
    if not isinstance(bloco, dict):
        return envelope("fora_desta_edicao", **fonte)
    estado = _estado_f2(bloco)
    valor = None
    if estado in ("encontrado", "nao_encontrado"):
        itens = [limpar_pessoas(x) for x in (bloco.get("items") or []) if isinstance(x, dict)]
        valor = {"parcelas": len(itens), "itens": itens,
                 "parte_do_car_pct": _num(bloco.get("car_share"))}
    return envelope(estado, valor, unidade="parcelas", **fonte,
                    consultado_em=ctx.emitido, limites=limites)


@transicao("sigef")
def _t_sigef(ctx: Contexto) -> dict:
    return _acervo(ctx, "sigef")


@transicao("snci")
def _t_snci(ctx: Contexto) -> dict:
    return _acervo(ctx, "snci")


# ==================================================================================================
# Desmatamento
# ==================================================================================================
def _ocorrencia_livro(o: dict, classe: str, area_car_ha: float | None) -> dict:
    """Uma ocorrência do classificador no formato que o capítulo 1 desenha."""
    area = _num(o.get("area_ha")) or 0.0
    mancha = _num(o.get("feature_area_ha"))
    rotulo = _txt(o.get("period_label"))
    ano_anterior = None
    achado = re.search(r"entre (\d{4})", rotulo)
    if achado:
        ano_anterior = int(achado.group(1))
    return {
        # nomes do contrato §9.3 (os mesmos da F1), para o livro ter uma leitura só
        "id": _txt(o.get("id")).split(".", 1)[-1],
        "classe": classe,
        "year": _int_do_texto(o.get("year")),
        "image_date": _txt(o.get("image_date")) or None,
        "prev_image_date": None,          # a leitura de hoje traz o ano anterior, não a data
        "ano_anterior": ano_anterior,
        "periodo": rotulo or None,
        "periodo_legal": periodo_legal(_txt(o.get("image_date"))),
        "area_inside_ha": area,
        "feature_area_ha": mancha,
        "share_of_feature_pct": _num(o.get("share_of_feature_pct")),
        "inner_mean_width_m": _num(o.get("max_width_m")),
        "pct_car": (area / area_car_ha * 100) if area_car_ha else None,
        "quase_inteira_fora": bool(mancha and area and mancha / area >= 10),
        "bioma": _txt(o.get("biome")) or None,
        "layer": _txt(o.get("layer")) or None,
        "depois_do_corte": bool(o.get("post_cutoff")),
    }


@transicao("prodes")
def _t_prodes(ctx: Contexto) -> dict:
    """Ocorrências por mancha, pela mesma classificação do relatório atual (§0.3 item 1).

    Sem a classificação à mão, o capítulo fica ``pendente``: a contagem antiga não diz, por mancha,
    se a retirada está dentro ou só pega a divisa, nem em que período da lei caiu, e afirmar com ela
    seria transmitir o que a fonte não sustenta.
    """
    nome = ctx.fontes.nome(r"PRODES", "INPE / TerraBrasilis / PRODES")
    fonte = dict(fonte_id="inpe_prodes", fonte_nome=nome, licenca="CC-BY-SA 4.0",
                 citacao="INPE / TerraBrasilis — PRODES")
    limites = ("mapa anual do PRODES, por interseção exata com o limite declarado no CAR",
               "mancha que cruza a divisa em faixa mais estreita que um ponto do satélite aparece à parte")
    if not ctx.fontes.respondeu(r"PRODES"):
        return envelope("pendente", **fonte, consultado_em=ctx.emitido, limites=limites)
    leitura = ctx.prodes_leitura()
    if not isinstance(leitura, dict) or _txt(leitura.get("state")) not in ("found", "not_found"):
        return envelope("pendente", **fonte, consultado_em=ctx.emitido, limites=limites)
    if leitura.get("complete") is False:
        return envelope("pendente", **fonte, consultado_em=ctx.emitido, limites=limites)
    area_car = _num(leitura.get("car_area_ha")) or ctx.area_ha
    occ: list[dict] = []
    for o in ((leitura.get("inside") or {}).get("occurrences") or []):
        if isinstance(o, dict):
            occ.append(_ocorrencia_livro(o, "dentro", area_car))
    for o in ((leitura.get("boundary") or {}).get("occurrences") or []):
        if isinstance(o, dict):
            occ.append(_ocorrencia_livro(o, "divisa", area_car))
    occ.sort(key=lambda o: (_txt(o.get("image_date")), _txt(o.get("id"))))
    criterio = leitura.get("criterion") or {}
    valor = {
        "occurrences": occ,
        "union_inside_ha": _num((leitura.get("inside") or {}).get("area_ha")) or 0.0,
        "area_car_ha": area_car,
        "pct_car_dentro": _num((leitura.get("inside") or {}).get("pct_car")),
        "quantidade_dentro": len([o for o in occ if o["classe"] == "dentro"]),
        "quantidade_divisa": len([o for o in occ if o["classe"] == "divisa"]),
        "ponto_satelite_m": _num(criterio.get("pixel_m")) or 30.0,
        "metodo_area": "interseção exata com o limite declarado no CAR; a mesma mancha conta uma vez",
        "metodo_classe": _txt(criterio.get("inside_when")) or None,
    }
    if not occ:
        return envelope("nao_encontrado", valor, unidade="ocorrências", **fonte,
                        consultado_em=ctx.emitido, limites=limites)
    return envelope("encontrado", valor, unidade="ocorrências", **fonte,
                    consultado_em=ctx.emitido, limites=limites)


@transicao("bioma")
def _t_bioma(ctx: Contexto) -> dict:
    """Bioma pela camada do PRODES que respondeu (prodes-cerrado-nb, prodes-amazonia-nb…).

    É leitura da própria camada consultada, não um segundo mapa: sem ela, as notas que dependem de
    bioma não aparecem.
    """
    leitura = ctx.prodes_leitura()
    nomes = []
    for grupo in ("inside", "boundary", "accumulated", "other_classes"):
        for o in ((leitura or {}).get(grupo) or {}).get("occurrences") or []:
            if isinstance(o, dict) and _txt(o.get("biome")):
                nomes.append(_txt(o.get("biome")))
    if not nomes:
        return envelope("pendente", fonte_id="inpe_prodes_bioma",
                        fonte_nome="INPE / TerraBrasilis — camadas do PRODES por bioma",
                        consultado_em=ctx.emitido)
    bioma = max(set(nomes), key=nomes.count)
    return envelope("encontrado", {"bioma": bioma}, fonte_id="inpe_prodes_bioma",
                    fonte_nome="INPE / TerraBrasilis — camadas do PRODES por bioma",
                    consultado_em=ctx.emitido,
                    limites=("bioma da camada do PRODES que cobre o imóvel",))


@transicao("alertas_mensais")
def _t_alertas(ctx: Contexto) -> dict:
    """D9: o alerta recente aparece sem o nome da fonte no corpo; a fonte vai só no apêndice."""
    bloco = ctx.em("environment.deforestation_alerts")
    if not isinstance(bloco, dict):
        return envelope("fora_desta_edicao", fonte_id="alertas", fonte_nome="Alertas de desmatamento")
    estado = _estado_f2(bloco)
    desde = _txt(bloco.get("recent_after")) or None
    valor = None
    if estado in ("encontrado", "nao_encontrado"):
        valor = {"eventos": limpar_pessoas(bloco.get("items") or []),
                 "quantidade": _int_do_texto(bloco.get("event_count")) or 0,
                 "area_ha": _num(bloco.get("area_union_ha")),
                 "desde": desde}
    return envelope(estado, valor, unidade="alertas", fonte_id="alertas",
                    fonte_nome="Alertas recentes de desmatamento (INPE/DETER e consulta pública por código do CAR)",
                    somente_apendice=True, data_base=desde, consultado_em=ctx.emitido,
                    limites=(f"alertas a partir de {data_br(desde)}" if desde else "alertas do período recente",
                             "alerta é aviso para conferência, não auto de infração"))


# ==================================================================================================
# Fiscalização, áreas protegidas e mineração
# ==================================================================================================
@transicao("embargo_ibama")
def _t_embargo_ibama(ctx: Contexto) -> dict:
    n = _num(ctx.em("enforcement.embargo_count"))
    pendente = ctx.em("enforcement.embargo_pending") is True
    ok = ctx.fontes.respondeu(r"IBAMA.*embargad") and not pendente
    estado = estado_por_contagem(ok, n, zero_vale=True)
    valor = None
    if estado in ("encontrado", "nao_encontrado") and n is not None:
        valor = {"quantidade": int(n), "area_ha": _num(ctx.em("enforcement.embargo_area_ha")),
                 "itens": limpar_pessoas(ctx.linhas("enforcement.embargo_items"))}
    return envelope(estado, valor, unidade="embargos", fonte_id="ibama_embargos",
                    fonte_nome=ctx.fontes.nome(r"IBAMA.*embargad", "IBAMA — áreas embargadas"),
                    consultado_em=ctx.emitido,
                    limites=("só embargos com mapa na base oficial de áreas embargadas",
                             "embargo registrado sem mapa não aparece nesta consulta"))


@transicao("embargo_icmbio")
def _t_embargo_icmbio(ctx: Contexto) -> dict:
    """O mapa de embargos do ICMBio não entra nesta edição: a linha vai para o apêndice B."""
    if ctx.fontes.respondeu(r"ICMBio"):
        n = _num(ctx.em("enforcement.embargo_icmbio_count"))
        if n is not None:
            return envelope(estado_por_contagem(True, n, zero_vale=True),
                            {"quantidade": int(n)}, unidade="embargos", fonte_id="icmbio_embargos",
                            fonte_nome=ctx.fontes.nome(r"ICMBio", "ICMBio — embargos"),
                            consultado_em=ctx.emitido,
                            limites=("só embargos desenhados no mapa público do órgão",))
    return envelope("fora_desta_edicao", fonte_id="icmbio_embargos", fonte_nome="ICMBio — embargos")


@transicao("autos")
def _t_autos(ctx: Contexto) -> dict:
    bruto = ctx.em("enforcement.auto_count")
    n = float(bruto) if isinstance(bruto, (int, float)) and not isinstance(bruto, bool) else None
    estado = estado_por_contagem(ctx.fontes.respondeu(r"autos de infra"), n, zero_vale=True)
    valor = {"quantidade": int(n)} if estado in ("encontrado", "nao_encontrado") and n is not None else None
    return envelope(estado, valor, unidade="autos", fonte_id="ibama_autos",
                    fonte_nome=ctx.fontes.nome(r"autos de infra", "IBAMA — autos de infração"),
                    consultado_em=ctx.emitido,
                    limites=("busca pelo local: só autos com local dentro do imóvel",))


_CAMADAS = {
    "ti": (r"^Terra Ind(í|i)gena$", "Terra Indígena", "funai_ti", "FUNAI — Terras Indígenas"),
    "uc": (r"^Unidade de Conserva(ç|c)(ã|a)o$", "Unidade de Conservação", "mma_uc",
           "CNUC / MMA — Unidades de Conservação"),
    "quilombola": (r"Quilombola", "Território Quilombola", "incra_quilombola",
                   "INCRA — Territórios Quilombolas"),
    "assentamento": (r"^Assentamento$", "Assentamento", "incra_assentamento", "INCRA — Assentamentos"),
    "floresta_publica": (r"Floresta P(ú|u)blica", "Floresta Pública", "sfb_fp",
                         "Serviço Florestal Brasileiro — Florestas Públicas"),
}


def _camada(ctx: Contexto, chave: str) -> dict:
    regex, rotulo, fid, nome_padrao = _CAMADAS[chave]
    texto = None
    for linha in ctx.linhas("environment.layer_rows"):
        if isinstance(linha, (list, tuple)) and len(linha) >= 2 and _txt(linha[0]).strip() == rotulo:
            texto = _txt(linha[1])
            break
    # "FONTE INDISPONÍVEL NESTA EMISSÃO" não tem inteiro: a fonte não respondeu, e o fato fica ◌
    n = _int_do_texto(texto) if texto and re.search(r"\d", texto) else None
    estado = estado_por_contagem(ctx.fontes.respondeu(regex), None if n is None else float(n), zero_vale=True)
    valor = {"quantidade": int(n)} if estado in ("encontrado", "nao_encontrado") and n is not None else None
    return envelope(estado, valor, unidade="áreas", fonte_id=fid,
                    fonte_nome=ctx.fontes.nome(regex, nome_padrao), consultado_em=ctx.emitido,
                    limites=("interseção com o polígono público da camada",))


for _chave in _CAMADAS:
    _TRANSICOES[_chave] = (lambda k: lambda ctx: _camada(ctx, k))(_chave)


@transicao("anm")
def _t_anm(ctx: Contexto) -> dict:
    n = _num(ctx.em("mining.process_count"))
    estado = estado_por_contagem(ctx.fontes.respondeu(r"^ANM / SIGMINE$"), n, zero_vale=True)
    valor = None
    if estado in ("encontrado", "nao_encontrado") and n is not None:
        valor = {"quantidade": int(n),
                 "area_sobreposta_ha": _num(ctx.em("mining.overlap_area_ha")),
                 "processos": [limpar_pessoas(x) for x in ctx.linhas("mining.processes") if isinstance(x, dict)]}
    return envelope(estado, valor, unidade="processos", fonte_id="anm_sigmine",
                    fonte_nome=ctx.fontes.nome(r"^ANM / SIGMINE$", "ANM / SIGMINE"),
                    consultado_em=ctx.emitido,
                    limites=("processos desenhados no mapa público da ANM",))


@transicao("sitio_arqueologico")
def _t_iphan(ctx: Contexto) -> dict:
    bloco = ctx.em("environment.iphan_f2")
    if not isinstance(bloco, dict):
        return envelope("fora_desta_edicao", fonte_id="iphan_sicg",
                        fonte_nome=ctx.fontes.nome(r"IPHAN", "IPHAN — patrimônio arqueológico"))
    dentro = bloco.get("inside") if isinstance(bloco.get("inside"), dict) else {}
    perto = bloco.get("near") if isinstance(bloco.get("near"), dict) else {}
    estado = _estado_f2(dentro)
    valor = None
    if estado in ("encontrado", "nao_encontrado"):
        valor = {"dentro": limpar_pessoas(dentro.get("rows") or []),
                 "dentro_quantidade": _int_do_texto(dentro.get("count")) or 0,
                 "vizinhanca": limpar_pessoas(perto.get("rows") or []),
                 "vizinhanca_quantidade": _int_do_texto(perto.get("count")) or 0}
    return envelope(estado, valor, unidade="sítios", fonte_id="iphan_sicg",
                    fonte_nome=ctx.fontes.nome(r"IPHAN", "IPHAN — patrimônio arqueológico"),
                    consultado_em=ctx.emitido,
                    limites=("cadastro oficial de sítios do IPHAN, pontos e polígonos",
                             "sítio na vizinhança não é restrição dentro do imóvel"))


@transicao("regra_credito")
def _t_regra_credito(ctx: Contexto) -> dict:
    """Faixa e data de início pela tabela da Res. CMN 5.303/2026 e os módulos fiscais do CAR."""
    mf = _num(ctx.em("car.fiscal_modules"))
    tipo = _txt(ctx.em("car.type")).strip().upper()
    if tipo in ("AST", "PCT"):
        faixa, texto, inicio = ("coletivo_17a",
                                "de assentamento ou de território tradicional com CAR coletivo", "2028-01-03")
    elif mf is None:
        return envelope("pendente", fonte_id="cmn_5303", fonte_nome="Resolução CMN 5.303/2026",
                        consultado_em=ctx.emitido,
                        limites=("sem os módulos fiscais do CAR, a faixa e a data de início não aparecem",))
    elif mf > 15:
        faixa, texto, inicio = "acima_15", "com mais de 15 módulos fiscais", "2027-01-04"
    elif mf > 4:
        faixa, texto, inicio = "de_4_a_15", "com mais de 4 e até 15 módulos fiscais", "2027-07-01"
    else:
        faixa, texto, inicio = "ate_4", "de até 4 módulos fiscais", "2028-01-03"
    return envelope("encontrado",
                    {"faixa_modulos": faixa, "faixa_texto": texto, "data_inicio": inicio,
                     "marco": PRODES_MARCO_2019, "norma": "Resolução CMN 5.303, de 12/05/2026"},
                    fonte_id="cmn_5303",
                    fonte_nome="Resolução CMN 5.303/2026 (Manual de Crédito Rural, 2-9, itens 17, 17-A e 18)",
                    data_base="2026-05-12", consultado_em=ctx.emitido,
                    limites=("o livro não consulta a lista do Ministério do Meio Ambiente",))


# ==================================================================================================
# Água
# ==================================================================================================
@transicao("outorga")
def _t_outorga(ctx: Contexto) -> dict:
    """Outorgas dentro do imóvel pelo bloco F2, com as colunas que o órgão publica. `atencao` é falso."""
    bloco = ctx.em("water.grants_f2")
    nome = ctx.fontes.nome(r"Outorga", "Outorgas de água — IGAM/ANA")
    if not isinstance(bloco, dict):
        n = _num(ctx.em("water.grant_count"))
        estado = estado_por_contagem(ctx.fontes.respondeu(r"Outorga"), n, zero_vale=True)
        valor = None
        if estado in ("encontrado", "nao_encontrado") and n is not None:
            valor = [{"posicao": "dentro", "colunas": {}} for _ in range(int(n))]
        return envelope(estado, valor, unidade="outorgas", fonte_id="igam_ana", fonte_nome=nome,
                        consultado_em=ctx.emitido,
                        limites=("pontos de outorga publicados pelo órgão estadual e pela ANA",))
    estado = _estado_f2(bloco)
    valor = None
    if estado in ("encontrado", "nao_encontrado"):
        # `valor` é a lista de outorgas do contrato §9.3 (uma por captação dentro do imóvel)
        valor = []
        cabecalho = [_txt(h) for h in bloco.get("headers") or []]
        linhas = [[_txt(c) for c in linha] for linha in bloco.get("rows") or []
                  if isinstance(linha, (list, tuple))]
        for i, item in enumerate(bloco.get("items") or []):
            if not isinstance(item, dict):
                continue
            limpo = limpar_pessoas(item)
            limpo["posicao"] = "dentro"
            limpo["colunas"] = dict(zip(cabecalho, linhas[i])) if i < len(linhas) else {}
            valor.append(limpo)
    envelope_saida = envelope(estado, valor, unidade="outorgas", fonte_id="igam_ana", fonte_nome=nome,
                              consultado_em=ctx.emitido,
                              limites=("cadastro de outorgas do órgão estadual e da ANA",
                                       "vazão autorizada não é água garantida"))
    if estado in ("encontrado", "nao_encontrado"):
        # extra previsto no contrato (ENVELOPE_V51_EXTRAS): outorgas perto, que não são do imóvel
        envelope_saida["vizinhas_5km"] = {
            "quantidade": _int_do_texto(bloco.get("other_count")) or 0,
            "texto": _txt(bloco.get("near_text")) or None,
        }
        envelope_saida["notas"] = [_txt(x) for x in bloco.get("notes") or []]
        envelope_saida["cabecalho"] = [_txt(h) for h in bloco.get("headers") or []]
    return envelope_saida


@transicao("pocos")
def _t_pocos(ctx: Contexto) -> dict:
    g = ctx.em("water.groundwater") or {}
    if not isinstance(g, dict):
        g = {}
    ok = ctx.fontes.respondeu(r"SIAGAS") and g.get("ok") is True
    n = _num(g.get("well_count"))
    estado = estado_por_contagem(ok, n, zero_vale=True, completa=g.get("partial") is not True)
    valor = None
    if estado in ("encontrado", "nao_encontrado"):
        valor = {"pocos": int(n or 0),
                 "raio_busca_km": _num(g.get("radius_km")) or _num(g.get("search_radius_km"))}
        for alvo, origem in (("profundidade_mediana_m", "depth_median_m"),
                             ("nivel_estatico_mediano_m", "static_level_median_m"),
                             ("nivel_dinamico_mediano_m", "dynamic_level_median_m")):
            v = _num(g.get(origem))
            quantos = _num(g.get(origem.replace("_median_m", "_count")))
            if v is not None:
                valor[alvo] = {"valor": v, "pocos": int(quantos) if quantos is not None else None}
    return envelope(estado, valor, unidade="poços", fonte_id="sgb_siagas",
                    fonte_nome=ctx.fontes.nome(r"SIAGAS", "SGB / SIAGAS — Água subterrânea"),
                    consultado_em=ctx.emitido,
                    limites=("poços cadastrados na região, não no imóvel",))


@transicao("pivo")
def _t_pivo(ctx: Contexto) -> dict:
    n = _num(ctx.em("water.pivot_count"))
    estado = estado_por_contagem(ctx.fontes.respondeu(r"Piv(ô|o)"), n, zero_vale=True)
    valor = ({"quantidade": int(n), "ano_referencia": 2022}
             if estado in ("encontrado", "nao_encontrado") and n is not None else None)
    return envelope(estado, valor, unidade="pivôs", fonte_id="ana_pivos",
                    fonte_nome=ctx.fontes.nome(r"Piv(ô|o)", "ANA / SNIRH — Pivôs Centrais"),
                    data_base="2022-12-31", consultado_em=ctx.emitido,
                    limites=("no mapa da ANA de 2022: pivô instalado depois não aparece",))


@transicao("climatologia")
def _t_climatologia(ctx: Contexto) -> dict:
    """Os 12 meses das linhas "Climatologia MMM" de ``water.rain_rows`` (§2.0.2)."""
    meses: list[dict] = []
    for linha in ctx.linhas("water.rain_rows"):
        if not (isinstance(linha, (list, tuple)) and len(linha) >= 2):
            continue
        rotulo = _txt(linha[0]).strip().upper()
        if not rotulo.startswith("CLIMATOLOGIA"):
            continue
        sigla = rotulo.replace("CLIMATOLOGIA", "").strip()[:3]
        if sigla not in MESES_EN:
            continue
        texto = _txt(linha[1])
        chuva = re.search(r"chuva\s*≈?\s*([\d.,]+)\s*mm", texto)
        temp = re.search(r"temperatura m(é|e)dia\s*([\d.,]+)", texto)
        if not chuva:
            continue
        meses.append({"mes": MESES_EN.index(sigla) + 1,
                      "chuva_mm": _num(chuva.group(1)) or 0.0,
                      "temperatura_c": _num(temp.group(2)) if temp else None})
    nome = ctx.fontes.nome(r"climatologia mensal", "NASA POWER — climatologia mensal")
    limites = ("estimativa em grade de ~50 km no centro do imóvel, não medição de pluviômetro",)
    if not (ctx.fontes.respondeu(r"climatologia mensal") and len(meses) == 12):
        return envelope("pendente", fonte_id="nasa_power_clima", fonte_nome=nome,
                        consultado_em=ctx.emitido, limites=limites)
    ordenados = sorted(meses, key=lambda m: m["mes"])
    total = sum(m["chuva_mm"] for m in ordenados)
    media = total / 12
    return envelope("encontrado",
                    {"meses": ordenados, "chuva_anual_mm": total, "periodo": None,
                     "meses_chuvosos": [m["mes"] for m in ordenados if m["chuva_mm"] >= media],
                     "meses_secos": [m["mes"] for m in ordenados if m["chuva_mm"] < media / 3]},
                    unidade="mm", fonte_id="nasa_power_clima", fonte_nome=nome,
                    consultado_em=ctx.emitido, limites=limites)


@transicao("janela_chuva")
def _t_janela(ctx: Contexto) -> dict:
    """A chuva recente entra sem comparação com o normal (decisão T1): só o observado."""
    d = ctx.em("water.drought_screening") or {}
    if not isinstance(d, dict):
        d = {}
    observado = _num(d.get("rain_sum_mm"))
    nome = ctx.fontes.nome(r"NASA POWER - Agro|NASA POWER — Agro", "NASA POWER — Agroclimatologia")
    if not ctx.fontes.respondeu(r"NASA POWER") or observado is None:
        return envelope("pendente", fonte_id="nasa_power", fonte_nome=nome, consultado_em=ctx.emitido)

    def _data(v: Any) -> str | None:
        s = re.sub(r"\D", "", _txt(v))
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else None

    return envelope("encontrado",
                    {"janela": [_data(d.get("period_start")), _data(d.get("period_end"))],
                     "dias_validos": _num(d.get("days")), "observado_mm": observado,
                     "dias_secos": _num(ctx.linha_por_rotulo("water.rain_rows", "Dias secos"))},
                    unidade="mm", fonte_id="nasa_power", fonte_nome=nome, consultado_em=ctx.emitido,
                    limites=("janela recente em grade de ~50 km; não é comparada com o normal do lugar",))


# ==================================================================================================
# Imagem e cobertura
# ==================================================================================================
@transicao("sentinel")
def _t_sentinel(ctx: Contexto) -> dict:
    s = ctx.em("satellite_imagery.sentinel") or {}
    if not isinstance(s, dict):
        s = {}
    if not (ctx.fontes.respondeu(r"Sentinel-2") and s.get("ok") is True):
        return envelope("pendente", fonte_id="sentinel2", fonte_nome="Copernicus Sentinel-2 L2A",
                        consultado_em=ctx.emitido)
    return envelope("encontrado",
                    {"cena": _txt(s.get("scene_id")), "data": _txt(s.get("date")),
                     "nuvens_pct": _num(s.get("cloud_cover_pct")),
                     "resolucao_m": _num(s.get("resolution_m")) or 10,
                     "ndvi_mediana": _num(s.get("ndvi_median")), "ndvi_media": _num(s.get("ndvi_mean")),
                     "ndvi_p10": _num(s.get("ndvi_p10")), "ndvi_p90": _num(s.get("ndvi_p90")),
                     "parte_baixa_pct": _num(s.get("ndvi_low_share_pct")),
                     "parte_media_pct": _num(s.get("ndvi_medium_share_pct")),
                     "parte_alta_pct": _num(s.get("ndvi_high_share_pct"))},
                    fonte_id="sentinel2", fonte_nome="Copernicus Sentinel-2 L2A",
                    licenca="Copernicus", citacao="Contém dados modificados do Copernicus Sentinel-2",
                    data_base=_txt(s.get("date")) or None, consultado_em=ctx.emitido,
                    limites=("cada ponto da imagem cobre 10 × 10 m",))


@transicao("mapbiomas")
def _t_mapbiomas(ctx: Contexto) -> dict:
    """Classes de cobertura de ``productive.landcover_rows`` ("MapBiomas — Classe", "N ha • P%")."""
    past = ctx.em("agropecuaria.pasture") or {}
    if not isinstance(past, dict):
        past = {}
    classes = []
    for linha in ctx.linhas("productive.landcover_rows"):
        if not (isinstance(linha, (list, tuple)) and len(linha) >= 2):
            continue
        rotulo = _txt(linha[0])
        if not rotulo.startswith("MapBiomas"):
            continue
        nome_classe = rotulo.split("—", 1)[-1].strip() if "—" in rotulo else rotulo
        ha = re.search(r"([\d.]+)\s*ha", _txt(linha[1]))
        pct = re.search(r"([\d.]+)\s*%", _txt(linha[1]))
        if ha:
            classes.append({"classe": nome_classe, "ha": _num_en(ha.group(1)),
                            "pct": _num_en(pct.group(1)) if pct else None})
    nome = ctx.fontes.nome(r"MapBiomas Brasil", "MapBiomas Brasil — Cobertura")
    if not (ctx.fontes.respondeu(r"MapBiomas Brasil") and _txt(past.get("state")) == "ready" and classes):
        return envelope("pendente", fonte_id="mapbiomas", fonte_nome=nome, consultado_em=ctx.emitido)
    ano = _num(past.get("year"))
    colecao = re.search(r"Cole(ç|c)(ã|a)o\s*(\d+)", _txt(past.get("source")))
    return envelope("encontrado",
                    {"classes": sorted(classes, key=lambda x: -(x["ha"] or 0)), "resolucao_m": 30,
                     "ano": int(ano) if ano else None,
                     "colecao": int(colecao.group(3)) if colecao else None,
                     "vegetacao_nativa_pct": _num(past.get("native_vegetation_share_pct")),
                     "parte_pasto_pct": _num(past.get("pasture_share_pct")),
                     "parte_agropecuaria_pct": _num(past.get("agriculture_and_pasture_share_pct"))},
                    unidade="ha", fonte_id="mapbiomas", fonte_nome=nome,
                    data_base=str(int(ano)) if ano else None, consultado_em=ctx.emitido,
                    limites=("classificação de satélite a 30 m, não levantamento de campo",))


# ==================================================================================================
# A terra
# ==================================================================================================
@transicao("relevo")
def _t_relevo(ctx: Contexto) -> dict:
    """Relevo medido dentro do CAR: classes da Embrapa em % de inclinação (camada T1)."""
    t = ctx.em("productive.terrain_srtm")
    if not isinstance(t, dict):
        t = ctx.result.get("terrain_srtm") if isinstance(ctx.result.get("terrain_srtm"), dict) else {}
    fonte = dict(fonte_id="srtm_horn",
                 fonte_nome=ctx.fontes.nome(r"SRTM", "SRTM 1 arc-second — altitude e inclinação do terreno"))
    limites = ("modelo de elevação de ~30 m pelo método de Horn; não substitui topografia de campo",)
    if not (t.get("ok") and _txt(t.get("slope_unit")) == "%" and t.get("slope_classes")):
        if _txt(t.get("state")) == "not_found":
            return envelope("fora_desta_edicao", **fonte,
                            limites=("o modelo de elevação não cobre um imóvel deste tamanho",))
        return envelope("pendente", **fonte, consultado_em=ctx.emitido, limites=limites)
    classes = [{"classe": _txt(r.get("class")), "faixa": _txt(r.get("range")),
                "pct": _num(r.get("share_pct")) or 0.0}
               for r in t.get("slope_classes") or [] if isinstance(r, dict)]
    return envelope("encontrado",
                    {"modelo": _txt(t.get("source")) or "SRTM ~30 m", "classes_embrapa_pct": classes,
                     "inclinacao_mediana_pct": _num(t.get("slope_median_pct")),
                     "inclinacao_media_pct": _num(t.get("slope_mean_pct")),
                     "inclinacao_p90_pct": _num(t.get("slope_p90_pct")),
                     "altitude_min_m": _num(t.get("elevation_min_m")),
                     "altitude_mediana_m": _num(t.get("elevation_median_m")),
                     "altitude_max_m": _num(t.get("elevation_max_m")),
                     "pct_25_45_graus": _num(t.get("slope_25_45deg_share_pct")),
                     "pct_acima_45_graus": _num(t.get("slope_gt_45deg_share_pct")),
                     "pixels": _num(t.get("slope_sample_pixels")) or _num(t.get("elevation_sample_pixels"))},
                    unidade="%", **fonte, consultado_em=ctx.emitido, limites=limites)


@transicao("solo_mapa")
def _t_solo_mapa(ctx: Contexto) -> dict:
    texto = ctx.linha_por_rotulo("productive.soil_rows", "Tipo de solo")
    escala = ctx.linha_por_rotulo("productive.soil_rows", "Escala do mapa")
    nome = ctx.fontes.nome(r"Pedologia", "IBGE — Base Contínua de Pedologia")
    limites = ("mapa regional de 1:250.000: descreve a região, não mede o seu solo",)
    if not texto:
        if not ctx.fontes.achar(r"Pedologia"):
            return envelope("pendente", fonte_id="ibge_pedologia", fonte_nome=nome, consultado_em=ctx.emitido)
        return envelope("pendente", fonte_id="ibge_pedologia", fonte_nome=nome, consultado_em=ctx.emitido,
                        limites=limites)
    unidades = [{"descricao": p.strip()} for p in texto.split(";") if p.strip()]
    return envelope("encontrado", {"unidades": unidades, "escala": "1:250.000", "nota_escala": escala},
                    fonte_id="ibge_pedologia", fonte_nome=nome, consultado_em=ctx.emitido, limites=limites)


@transicao("textura_solo")
def _t_textura(ctx: Contexto) -> dict:
    tex = ctx.em("productive.solo_textura_t1")
    if not isinstance(tex, dict):
        tex = {}
    partes = tex.get("group_shares_pct")
    texto = ctx.linha_por_rotulo("productive.soil_rows", "Textura de 0 a 30")
    nome = ctx.fontes.nome(r"MapBiomas Solo", "MapBiomas Solo — granulometria")
    limites = ("estimativa de modelo a 30 m, não análise de laboratório",)
    if not isinstance(partes, dict) or not partes:
        if not texto:
            return envelope("pendente", fonte_id="mapbiomas_solo", fonte_nome=nome, consultado_em=ctx.emitido)
        return envelope("encontrado", {"texto": texto}, fonte_id="mapbiomas_solo", fonte_nome=nome,
                        consultado_em=ctx.emitido, limites=limites)
    grupos = {_txt(k): _num(v) or 0.0 for k, v in partes.items()}
    # A classe textural é a do grupo que cobre a maior parte do imóvel — leitura do próprio modelo,
    # não uma conversão nossa de argila e areia (essa fica na segunda opinião, em `soilgrids`).
    dominante = max(grupos, key=lambda k: grupos[k]) if grupos else None
    return envelope("encontrado",
                    {"grupos_pct": grupos, "classe_textural": dominante,
                     "regra_classe": "grupo textural que cobre a maior parte do imóvel",
                     "resolucao_m": _num(tex.get("resolution_m")) or 30,
                     "modelo": _txt(tex.get("source")) or "MapBiomas Solo", "texto": texto,
                     "pixels": _num(tex.get("pixels"))},
                    unidade="%", fonte_id="mapbiomas_solo", fonte_nome=nome,
                    consultado_em=ctx.emitido, limites=limites)


@transicao("soilgrids")
def _t_soilgrids(ctx: Contexto) -> dict:
    texto = ctx.linha_por_rotulo("productive.soil_rows", "Argila, areia e silte")
    nome = ctx.fontes.nome(r"granulometria|SoilGrids", "MapBiomas Solo — granulometria")
    if not texto:
        return envelope("pendente", fonte_id="soilgrids", fonte_nome=nome, consultado_em=ctx.emitido)
    def _parte(rotulo: str) -> float | None:
        achado = re.search(rotulo + r"\s*([\d.,]+)\s*%", texto)
        return _num(achado.group(1)) if achado else None
    argila, areia, silte = _parte("argila"), _parte("areia"), _parte("silte")
    if argila is None:
        return envelope("pendente", fonte_id="soilgrids", fonte_nome=nome, consultado_em=ctx.emitido)
    return envelope("encontrado",
                    {"argila_pct": argila, "areia_pct": areia, "silte_pct": silte, "texto": texto,
                     "profundidade_cm": "0–30"},
                    unidade="%", fonte_id="soilgrids", fonte_nome=nome, consultado_em=ctx.emitido,
                    limites=("estimativa de modelo com amostras brasileiras, não análise do seu solo",))


@transicao("aptidao")
def _t_aptidao(ctx: Contexto) -> dict:
    classes = []
    nota = None
    for linha in ctx.linhas("productive.aptitude_rows"):
        if not (isinstance(linha, (list, tuple)) and len(linha) >= 2):
            continue
        if _txt(linha[0]).strip().lower().startswith("escala"):
            nota = _txt(linha[1])
            continue
        classes.append({"descricao": _txt(linha[0]), "medida": _txt(linha[1])})
    nome = ctx.fontes.nome(r"aptid(ã|a)o", "Embrapa Solos — aptidão agrícola")
    if not classes:
        return envelope("pendente", fonte_id="embrapa_aptidao", fonte_nome=nome, consultado_em=ctx.emitido)
    return envelope("encontrado", {"classes": classes, "nota_escala": nota},
                    fonte_id="embrapa_aptidao", fonte_nome=nome, consultado_em=ctx.emitido,
                    limites=("classe de um mapa regional; o nome da classe é oficial e não é juízo sobre o imóvel",))


@transicao("erosao")
def _t_erosao(ctx: Contexto) -> dict:
    texto = ctx.linha_por_rotulo("productive.soil_rows", "Erodibilidade")
    if not texto:
        return envelope("fora_desta_edicao", fonte_id="embrapa_erodibilidade",
                        fonte_nome=ctx.fontes.nome(r"erodibilidade", "Embrapa Solos — erodibilidade"))
    return envelope("encontrado", {"texto": texto}, fonte_id="embrapa_erodibilidade",
                    fonte_nome=ctx.fontes.nome(r"erodibilidade", "Embrapa Solos — erodibilidade"),
                    consultado_em=ctx.emitido,
                    limites=("fragilidade do solo no mapa regional, não medição de perda de solo",))


# ==================================================================================================
# Produção e entorno
# ==================================================================================================
@transicao("ppm")
def _t_ppm(ctx: Contexto) -> dict:
    liv = ctx.em("agropecuaria.livestock_municipal") or {}
    if not isinstance(liv, dict):
        liv = {}
    rebanhos = []
    for linha in ctx.linhas("productive.agro_rows"):
        if not (isinstance(linha, (list, tuple)) and len(linha) >= 3):
            continue
        if "IBGE/PPM" not in _txt(linha[2]):
            continue
        cab = re.search(r"([\d.]+)\s*cabe", _txt(linha[1]))
        ano = re.search(r"\b(19|20)\d{2}\b", _txt(linha[1]))
        if cab:
            rebanhos.append({"rebanho": _txt(linha[0]), "cabecas": _num_en(cab.group(1)),
                             "ano": int(ano.group()) if ano else None})
    nome = ctx.fontes.nome(r"PPM", "IBGE / PPM — Pecuária Municipal")
    if not (liv.get("ok") is True and ctx.fontes.respondeu(r"PPM") and rebanhos):
        return envelope("pendente", fonte_id="ibge_ppm", fonte_nome=nome, consultado_em=ctx.emitido)
    anos = [r["ano"] for r in rebanhos if r["ano"]]
    return envelope("encontrado",
                    {"municipio": ctx.municipio, "uf": ctx.uf, "ano": max(anos) if anos else None,
                     "rebanhos": sorted(rebanhos, key=lambda r: -(r["cabecas"] or 0))},
                    unidade="cabeças", fonte_id="ibge_ppm", fonte_nome=nome,
                    data_base=str(max(anos)) if anos else None, consultado_em=ctx.emitido,
                    limites=("número do município inteiro: não é a produção deste imóvel",))


@transicao("safras")
def _t_safras(ctx: Contexto) -> dict:
    """Sem o ano da safra, o bloco não existe (§2.0.2): número sem ano não diz de quando é."""
    bruto = ctx.result.get("safras") if isinstance(ctx.result.get("safras"), dict) else None
    if not isinstance(bruto, dict) or bruto.get("ok") is not True:
        return envelope("fora_desta_edicao", fonte_id="ibge_pam",
                        fonte_nome=ctx.fontes.nome(r"Produ(ç|c)(ã|a)o Agr(í|i)cola Municipal",
                                                   "IBGE / SIDRA — Produção Agrícola Municipal"))
    ano = _num(bruto.get("year")) or _num(bruto.get("ano"))
    culturas = []
    for item in bruto.get("crops") or bruto.get("culturas") or []:
        if not isinstance(item, dict):
            continue
        ha = _num(item.get("area_ha")) or _num(item.get("ha"))
        if ha is None:
            continue
        culturas.append({"cultura": _txt(item.get("crop") or item.get("cultura")),
                         "ha": ha,
                         "toneladas": _num(item.get("production_t")) or _num(item.get("toneladas"))})
    if ano is None or not culturas:
        return envelope("fora_desta_edicao", fonte_id="ibge_pam",
                        fonte_nome=ctx.fontes.nome(r"Produ(ç|c)(ã|a)o Agr(í|i)cola Municipal",
                                                   "IBGE / SIDRA — Produção Agrícola Municipal"))
    return envelope("encontrado",
                    {"municipio": ctx.municipio, "uf": ctx.uf, "year": int(ano),
                     "culturas": sorted(culturas, key=lambda c: -(c["ha"] or 0))},
                    unidade="ha", fonte_id="ibge_pam",
                    fonte_nome=ctx.fontes.nome(r"Produ(ç|c)(ã|a)o Agr(í|i)cola Municipal",
                                               "IBGE / SIDRA — Produção Agrícola Municipal"),
                    data_base=str(int(ano)), consultado_em=ctx.emitido,
                    limites=("número do município inteiro: não é a produção deste imóvel",))


@transicao("sif")
def _t_sif(ctx: Contexto) -> dict:
    """D3: só a categoria do estabelecimento, nunca a empresa (pessoas nunca aparecem)."""
    cadeia = ctx.em("agropecuaria.sif_chain") or {}
    if not isinstance(cadeia, dict):
        cadeia = {}
    estabelecimentos = cadeia.get("establishments")
    nome = ctx.fontes.nome(r"SIGSIF", "MAPA / SIGSIF — Estabelecimentos registrados no SIF")
    if not isinstance(estabelecimentos, list) or not estabelecimentos:
        return envelope("fora_desta_edicao", fonte_id="mapa_sigsif", fonte_nome=nome)
    contagem: dict[str, int] = {}
    for est in estabelecimentos:
        if not isinstance(est, dict):
            continue
        cat = _txt(est.get("category") or est.get("categoria") or est.get("atividade")).strip().lower()
        if cat:
            contagem[cat] = contagem.get(cat, 0) + 1
    if not contagem:
        return envelope("fora_desta_edicao", fonte_id="mapa_sigsif", fonte_nome=nome)
    return envelope("encontrado",
                    {"municipio": ctx.municipio, "uf": ctx.uf,
                     "categorias": [{"categoria": k, "quantidade": v}
                                    for k, v in sorted(contagem.items(), key=lambda kv: -kv[1])]},
                    fonte_id="mapa_sigsif", fonte_nome=nome, consultado_em=ctx.emitido,
                    limites=("estabelecimentos registrados no município, só pela categoria",))


@transicao("armazens")
def _t_armazens(ctx: Contexto) -> dict:
    """CONAB: só o que não identifica ninguém (tipo, município, capacidade e distância)."""
    bloco = ctx.em("infrastructure.warehouses_f2")
    nome = ctx.fontes.nome(r"CONAB", "CONAB — Cadastro Nacional de Unidades Armazenadoras")
    if not isinstance(bloco, dict):
        return envelope("fora_desta_edicao", fonte_id="conab", fonte_nome=nome)
    estado = _estado_f2(bloco)
    raio = _num(bloco.get("radius_km")) or 50
    valor = None
    if estado in ("encontrado", "nao_encontrado"):
        itens = []
        for item in bloco.get("items") or []:
            if not isinstance(item, dict):
                continue
            limpo = {
                "tipo": _txt(item.get("tipo") or item.get("type")) or None,
                "municipio": _txt(item.get("municipio") or item.get("city")) or None,
                "uf": _txt(item.get("uf")) or None,
                "capacidade_t": _num(item.get("capacidade_t") or item.get("capacity_t")),
                "distancia_km": _num(item.get("distancia_km") or item.get("distance_km")),
            }
            itens.append({k: v for k, v in limpo.items() if v is not None})
        distancias = [x["distancia_km"] for x in itens if x.get("distancia_km") is not None]
        valor = {"quantidade": len(itens), "itens": itens,
                 "mais_perto_km": min(distancias) if distancias else None,
                 "raio_km": raio}
    return envelope(estado, valor, unidade="armazéns", fonte_id="conab", fonte_nome=nome,
                    data_base=_txt(bloco.get("base_date")) or ctx.fontes.data_base(r"CONAB"),
                    consultado_em=ctx.emitido,
                    limites=(f"cadastrados na CONAB até {int(raio)} km do imóvel",
                             "o livro não mostra o nome da empresa do armazém"))


@transicao("aerodromo")
def _t_aerodromo(ctx: Contexto) -> dict:
    """A distância de hoje sai do centro do imóvel; a linha do livro pede a distância da divisa."""
    return envelope("fora_desta_edicao", fonte_id="anac_siros",
                    fonte_nome=ctx.fontes.nome(r"Aer(ó|o)dromo", "ANAC / SIROS — Aeródromos"))


# ==================================================================================================
# Leitura
# ==================================================================================================
def envelope_valido(e: Any) -> bool:
    return isinstance(e, dict) and isinstance(e.get("estado"), str) and "valor" in e


def ler_fatos(payload: dict, result: dict | None = None) -> dict[str, dict]:
    """Devolve ``fato_id -> envelope`` para todos os fatos do livro (§2.0.2).

    Ordem: envelope `_v51` no payload; senão a transição; senão ``fora_desta_edicao``. Os fatos de
    ``FATOS_SEMPRE_FORA`` (matrícula, CCIR, embargos estaduais) saem sempre fora da edição.
    """
    ctx = Contexto(payload, result)
    fatos: dict[str, dict] = {}
    for fato_id, caminho in FATO_CHAVES.items():
        bruto = _cam(payload, caminho)
        if envelope_valido(bruto):
            e = dict(bruto)
            e["valor"] = limpar_pessoas(e.get("valor"))
            e.setdefault("limites", [])
            e["origem"] = "v51"
            fatos[fato_id] = e
            continue
        fn = _TRANSICOES.get(fato_id)
        fatos[fato_id] = fn(ctx) if fn else envelope("fora_desta_edicao")
    for fato_id in FATOS_SEMPRE_FORA:
        fatos[fato_id] = envelope("fora_desta_edicao")
    return fatos


def gravar_fatos(payload: dict, result: dict | None = None) -> dict:
    """Escreve no payload, nos caminhos de ``FATO_CHAVES``, os envelopes que ainda não existem.

    Não toca em envelope que já esteja lá (a faixa dona manda) nem em nenhum campo antigo: o
    gerador atual e o portal continuam lendo o que liam.
    """
    fatos = ler_fatos(payload, result)
    for fato_id, caminho in FATO_CHAVES.items():
        if envelope_valido(_cam(payload, caminho)):
            continue
        partes = caminho.split(".")
        alvo = payload
        for parte in partes[:-1]:
            prox = alvo.get(parte)
            if not isinstance(prox, dict):
                prox = {}
                alvo[parte] = prox
            alvo = prox
        alvo[partes[-1]] = fatos[fato_id]
    return payload


# Fatos em que "encontrado" nunca pede conferência: ter água, ter dado de solo ou de clima não é
# problema (§2.0.2, coluna `atencao`). Lista pela propriedade, não pelo que apareceu num imóvel.
SEM_ATENCAO: frozenset[str] = frozenset({
    "car", "car_ultima_alteracao", "composicao_car", "sigef", "snci", "bioma", "regra_credito",
    "outorga", "pocos", "pivo", "climatologia", "janela_chuva", "sentinel", "mapbiomas", "relevo",
    "solo_mapa", "textura_solo", "soilgrids", "aptidao", "erosao", "ppm", "safras", "sif",
    "armazens", "aerodromo",
})


def atencao(fato_id: str, fatos: dict[str, dict]) -> bool:
    """``atencao`` da tabela §2.0.2: só o que pede conferência, nunca por palavra-chave."""
    e = fatos.get(fato_id) or {}
    if e.get("estado") != "encontrado":
        return False
    if fato_id in SEM_ATENCAO:
        return False
    valor = e.get("valor")
    if fato_id == "prodes":
        for o in (valor or {}).get("occurrences") or []:
            if not isinstance(o, dict):
                continue
            if o.get("periodo_legal") == "depois_2019_07_31":
                return True
            if o.get("classe") == "dentro" and o.get("periodo_legal") != "ate_2008_07_22":
                return True
        return False
    if fato_id == "sitio_arqueologico":
        return bool((valor or {}).get("dentro"))
    if fato_id == "alertas_mensais":
        return (_num((valor or {}).get("quantidade")) or 0) > 0
    if isinstance(valor, dict) and "quantidade" in valor:
        return (_num(valor.get("quantidade")) or 0) > 0
    if isinstance(valor, list):
        return len(valor) > 0
    return True


print("RX_BOOK_FATOS=transicao_2_0_2_carregada", flush=True)
