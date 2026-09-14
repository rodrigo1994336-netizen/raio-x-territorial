"""F2 · Alertas recentes de desmatamento do INPE (DETER) sobre o imóvel.

Fonte: INPE/TerraBrasilis, WFS público sem chave (licença CC BY-SA 4.0):
``deter-cerrado-nb:deter_cerrado`` (Cerrado, alertas a partir de 3 ha) e
``deter-amz:deter_amz`` (Amazônia Legal, a partir de 6,25 ha). MapBiomas Alerta
fica FORA: a licença dele proíbe revenda e a decisão é do dono.

Como a consulta é feita (provado ao vivo em 13/09/2026):

* Cobertura: ``CQL_FILTER=INTERSECTS/CONTAINS(geom, SRID=4674;<envoltória do CAR>)``
  na borda do bioma (``prodes-cerrado-nb:biome_border``) e no limite da Amazônia
  Legal (``prodes-legal-amz:brazilian_legal_amazon``), com ``resultType=hits``.
  Sem o prefixo ``SRID=4674;`` o servidor devolve 0 sem erro (armadilha medida:
  Curvelo × Cerrado = 0 sem SRID, 1 com SRID). Fora da cobertura o estado é
  ``not_covered`` — "não coberto por este sistema", nunca "sem alerta".
* Alertas: ``bbox=minx,miny,maxx,maxy,EPSG:4674`` em lon/lat, saída ``csv`` (que
  traz a geometria em WKT com precisão total; o JSON do servidor arredonda para
  4 casas) e interseção exata com o polígono do CAR feita aqui.
* Página de 2000: resposta cheia é tratada como incompleta (200 não é todos).

Regra para não contar a mesma área duas vezes com o PRODES (relatório de fontes
03, seção 5), aplicada por ``deter_alerts_in_property``:

1. Corte por data. O PRODES é o registro anual oficial. Alerta com data de imagem
   até o fim do último ano PRODES publicado (hoje 31/07/2025) não é "recente": não
   entra nesta seção, fica só na auditoria. Amazônia: ``deter-amz:prodes_reference``.
   Cerrado: 31/07 do último ano de ``prodes-cerrado-nb:yearly_deforestation``,
   conferido com a maior data dos alertas ``_hist``; se discordarem, vale a mais
   antiga e a discordância é registrada. O corte anda sozinho quando sai o PRODES.
2. Agrupar por lugar, não por fonte: alertas recentes que se tocam dentro do imóvel
   (inclusive Cerrado × Amazônia Legal, que se sobrepõem em MT/TO/MA) viram um evento.
3. Área = união das interseções dentro do CAR, medida por nós (GRS80). Nunca soma
   de áreas entre fontes.
4. Evento recente sobre área que o PRODES já marcou: diz quantos hectares já
   constavam no mapa anual, em vez de somar.
5. Crédito rural (MCR 2-9, 31/07/2019): alerta recente não entra na conta pós-2019
   do PRODES; vira linha à parte ("ainda não confirmado pelo mapa anual").

Texto fixo sempre que houver alerta: alerta não é multa nem auto de infração.
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Callable, Iterable

from pyproj import Geod
from shapely import wkt as shapely_wkt
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from report_ptbr_v50 import format_decimal

URL_TB = "https://terrabrasilis.dpi.inpe.br/geoserver/{ws}/ows"
PAGINA = 2000
# Menor área afirmada: um pixel Landsat de 30 m (0,09 ha). Abaixo disso a sobreposição é
# menor que a resolução dos mapas (DETER usa pixel de 64 m; PRODES, 30 m) e vem de divisas
# desenhadas em escalas diferentes. Medido: o alerta 2061786 toca polígonos PRODES de 2002 e
# 2017 em lascas de 0,001 a 0,007 ha, e o de 2025 em 12,03 ha.
AREA_MINIMA_LEITURA_HA = 0.09
CORTE_TTL_S = 24 * 3600
ULTIMA_IMAGEM_TTL_S = 6 * 3600
USER_AGENT = "Raio-X-Territorial/F2 deter-alertas"
FRASE_ALERTA = (
    "Alerta não é multa nem auto de infração. É um aviso de satélite de mudança na vegetação, "
    "que pode ter autorização. O mapa oficial anual (PRODES) confirma ou não."
)
NOTA_CREDITO = "Há alerta recente ainda não confirmado pelo mapa anual do PRODES."
NOTA_COBERTURA_PARCIAL = "Parte do imóvel fica fora da área monitorada por este sistema do INPE."
FONTE_TEXTO = "INPE/TerraBrasilis — DETER (licença CC-BY-SA-4.0)"
TEXTO_PENDENTE = "Consulta pendente."
TEXTO_NAO_COBERTO = "Não coberto por este sistema."

SISTEMAS: dict[str, dict[str, Any]] = {
    "cerrado": {
        "ws": "deter-cerrado-nb", "camada": "deter-cerrado-nb:deter_cerrado", "coluna_geom": "st_multi",
        "cobertura_ws": "prodes-cerrado-nb", "cobertura_camada": "prodes-cerrado-nb:biome_border",
        "area_minima_ha": 3.0, "rotulo": "Cerrado",
    },
    "amazonia": {
        "ws": "deter-amz", "camada": "deter-amz:deter_amz", "coluna_geom": "geom",
        "cobertura_ws": "prodes-legal-amz", "cobertura_camada": "prodes-legal-amz:brazilian_legal_amazon",
        "area_minima_ha": 6.25, "rotulo": "Amazônia Legal",
    },
}
CLASSES = {
    "DESMATAMENTO_CR": "Desmatamento (corte raso)",
    "DESMATAMENTO_VEG": "Desmatamento com vegetação",
    "MINERACAO": "Mineração",
    "DEGRADACAO": "Degradação",
    "CICATRIZ_DE_QUEIMADA": "Cicatriz de incêndio",
    "CS_DESORDENADO": "Corte seletivo desordenado",
    "CS_GEOMETRICO": "Corte seletivo geométrico",
}
_GEOD = Geod(ellps="GRS80")
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()

HttpGet = Callable[..., Any]


# ---------------------------------------------------------------- utilidades

def _http_get(url: str, *, params: dict | None = None, headers: dict | None = None):
    import httpx

    # Mesmo padrão de tempo do prodes_fast_v24 (connect 6 s, leitura 16 s). Medido daqui:
    # cobertura 0,2–1,4 s; alertas por bbox 0,5–1,6 s; corte/última imagem 1,8–2,8 s.
    timeout = httpx.Timeout(16.0, connect=6.0)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        return client.get(url, params=params, headers=headers)


def _geometria(obj: Any) -> BaseGeometry:
    if isinstance(obj, BaseGeometry):
        geom = obj
    elif isinstance(obj, dict):
        if obj.get("type") == "FeatureCollection":
            geom = shape(obj["features"][0]["geometry"])
        elif obj.get("type") == "Feature":
            geom = shape(obj["geometry"])
        else:
            geom = shape(obj)
    else:
        raise TypeError("geometria do CAR ausente")
    return geom if geom.is_valid else make_valid(geom)


def area_ha(geom: BaseGeometry | None) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    return abs(_GEOD.geometry_area_perimeter(geom)[0]) / 10000.0


def _data(text: Any) -> date | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(text or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _data_br(d: date | None) -> str | None:
    return d.strftime("%d/%m/%Y") if d else None


def _ha_texto(v: float) -> str:
    if 0 < v < 0.01:
        return "menos de 0,01 ha"
    return f"{format_decimal(round(v, 2), 2)} ha"


def _classe(codigo: str | None) -> str | None:
    c = (codigo or "").strip()
    if not c:
        return None
    return CLASSES.get(c.upper()) or c.replace("_", " ").strip().capitalize()


def _satelite(sat: str | None, sensor: str | None) -> str | None:
    sat, sensor = (sat or "").strip(), (sensor or "").strip()
    if not sat:
        return None
    return f"{sat} ({sensor})" if sensor else sat


def _cache_get(chave: str, ttl: float, agora: float) -> Any:
    with _CACHE_LOCK:
        item = _CACHE.get(chave)
        return item[1] if item and agora - item[0] < ttl else None


def _cache_put(chave: str, valor: Any, agora: float) -> None:
    with _CACHE_LOCK:
        _CACHE[chave] = (agora, valor)


# ---------------------------------------------------------------- consultas ao servidor

def envelope_wkt(geom: BaseGeometry) -> str:
    """Polígono que CONTÉM o CAR: envoltória convexa ou, se muito detalhada, retângulo mínimo."""
    hull = geom.convex_hull
    # envoltória longa estoura o tamanho de URL do GET; o retângulo mínimo também contém o CAR
    if hull.geom_type != "Polygon" or len(hull.exterior.coords) > 60:
        hull = geom.minimum_rotated_rectangle
    return hull.wkt


def params_coverage(sistema: str, geom: BaseGeometry, predicado: str) -> dict:
    cfg = SISTEMAS[sistema]
    return {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": cfg["cobertura_camada"],
        "resultType": "hits", "CQL_FILTER": f"{predicado}(geom,SRID=4674;{envelope_wkt(geom)})",
    }


def params_alerts(sistema: str, geom: BaseGeometry) -> dict:
    cfg = SISTEMAS[sistema]
    minx, miny, maxx, maxy = geom.bounds
    return {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": cfg["camada"],
        "srsName": "EPSG:4674", "bbox": f"{minx},{miny},{maxx},{maxy},EPSG:4674", "outputFormat": "csv",
        "count": str(PAGINA),
    }


def _number_matched(texto: str) -> int:
    m = re.search(r'numberMatched="(\d+)"', texto or "")
    if not m:
        raise ValueError("resposta_sem_numberMatched")
    return int(m.group(1))


def _get_ok(http_get: HttpGet, url: str, params: dict):
    resp = http_get(url, params=params)
    if int(getattr(resp, "status_code", 0) or 0) != 200:
        raise ValueError(f"http_{getattr(resp, 'status_code', None)}")
    return resp


def query_coverage(sistema: str, geom: BaseGeometry, http_get: HttpGet) -> str:
    """'total' | 'parcial' | 'nenhuma' (levanta exceção se o servidor não respondeu)."""
    url = URL_TB.format(ws=SISTEMAS[sistema]["cobertura_ws"])
    if _number_matched(_get_ok(http_get, url, params_coverage(sistema, geom, "INTERSECTS")).text) == 0:
        return "nenhuma"
    return "total" if _number_matched(_get_ok(http_get, url, params_coverage(sistema, geom, "CONTAINS")).text) > 0 else "parcial"


def parse_alerts_csv(texto: str, sistema: str) -> tuple[list[dict], int]:
    """Alertas do CSV do GeoServer e o número de linhas lidas (com ou sem geometria)."""
    col = SISTEMAS[sistema]["coluna_geom"]
    # polígono grande em WKT passa do limite padrão do csv (131.072 caracteres)
    csv.field_size_limit(max(csv.field_size_limit(), 1 << 28))
    leitor = csv.DictReader(io.StringIO((texto or "").lstrip("﻿"), newline=""))
    campos = leitor.fieldnames or []
    if col not in campos or "view_date" not in campos:
        raise ValueError("csv_sem_colunas_esperadas")
    saida = []
    linhas = 0
    for row in leitor:
        linhas += 1
        bruto = (row.get(col) or "").strip()
        if not bruto:
            continue
        geom = shapely_wkt.loads(bruto)
        if not geom.is_valid:
            geom = make_valid(geom)
        saida.append({
            "id": row.get("FID") or row.get("gid"), "sistema": sistema, "classe": row.get("classname"),
            "data_imagem": row.get("view_date"), "satelite": row.get("satellite"), "sensor": row.get("sensor"),
            "geometria": geom,
        })
    return saida, linhas


def query_alerts(sistema: str, geom: BaseGeometry, http_get: HttpGet) -> tuple[list[dict], bool]:
    resp = _get_ok(http_get, URL_TB.format(ws=SISTEMAS[sistema]["ws"]), params_alerts(sistema, geom))
    alertas, linhas = parse_alerts_csv(resp.text, sistema)
    return alertas, linhas >= PAGINA


def _um_valor(http_get: HttpGet, ws: str, params: dict, campo: str) -> str | None:
    resp = _get_ok(http_get, URL_TB.format(ws=ws), params)
    feats = (resp.json() or {}).get("features") or []
    return str((feats[0].get("properties") or {}).get(campo)) if feats else None


def query_cutoff(sistema: str, http_get: HttpGet, agora: float | None = None) -> dict:
    """Fim do último ano PRODES publicado, lido da fonte (cache de 24 h)."""
    now = time.time() if agora is None else agora
    chave = f"corte:{sistema}"
    salvo = _cache_get(chave, CORTE_TTL_S, now)
    if salvo is not None:
        return salvo
    base = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "outputFormat": "application/json"}
    if sistema == "amazonia":
        d = _data(_um_valor(http_get, "deter-amz", {**base, "typeNames": "deter-amz:prodes_reference"}, "end_date"))
        out = {"data": d.isoformat() if d else None, "origem": "deter-amz:prodes_reference", "discordancia": None}
    else:
        candidatos: dict[str, date] = {}
        try:
            ano = _um_valor(http_get, "prodes-cerrado-nb", {**base, "typeNames": "prodes-cerrado-nb:yearly_deforestation",
                                                           "count": "1", "sortBy": "year D", "propertyName": "year"}, "year")
            if ano and re.fullmatch(r"\d{4}", ano):
                candidatos["prodes_ultimo_ano"] = date(int(ano), 7, 31)
        except Exception:
            pass
        try:
            d = _data(_um_valor(http_get, "deter-cerrado-nb", {**base, "typeNames": "deter-cerrado-nb:deter_cerrado", "count": "1",
                                                               "sortBy": "view_date D", "propertyName": "view_date",
                                                               "CQL_FILTER": "gid LIKE '%_hist'"}, "view_date"))
            if d:
                candidatos["deter_hist_maior_data"] = d
        except Exception:
            pass
        if not candidatos:
            raise ValueError("corte_indisponivel")
        escolhido = min(candidatos.values())
        out = {"data": escolhido.isoformat(), "origem": "+".join(sorted(candidatos)),
               "discordancia": {k: v.isoformat() for k, v in candidatos.items()} if len(set(candidatos.values())) > 1 else None}
    if not out["data"]:
        raise ValueError("corte_indisponivel")
    _cache_put(chave, out, now)
    return out


def query_latest_image(sistema: str, http_get: HttpGet, agora: float | None = None) -> str | None:
    now = time.time() if agora is None else agora
    chave = f"ultima:{sistema}"
    salvo = _cache_get(chave, ULTIMA_IMAGEM_TTL_S, now)
    if salvo is not None:
        return salvo
    base = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "outputFormat": "application/json"}
    if sistema == "amazonia":
        # a ordenação na camada da Amazônia levou 16,8 s; a tabela de atualização responde em 0,2 s
        d = _data(_um_valor(http_get, "deter-amz", {**base, "typeNames": "deter-amz:updated_date"}, "updated_date"))
    else:
        d = _data(_um_valor(http_get, "deter-cerrado-nb", {**base, "typeNames": "deter-cerrado-nb:deter_cerrado", "count": "1",
                                                           "sortBy": "view_date D", "propertyName": "view_date"}, "view_date"))
    valor = d.isoformat() if d else None
    if valor:
        _cache_put(chave, valor, now)
    return valor


def query_deter_live(geometria_car: Any, *, http_get: HttpGet | None = None, agora: float | None = None) -> dict:
    """Consulta os dois sistemas e devolve as respostas cruas (falha fica registrada, nunca vira zero)."""
    get = http_get or _http_get
    geom = _geometria(geometria_car)
    respostas: dict[str, dict] = {k: {"cobertura": None, "alertas": None, "truncado": False, "corte": None,
                                      "ultima_imagem": None, "erros": []} for k in SISTEMAS}

    def guarda(sistema: str, etapa: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:
            respostas[sistema]["erros"].append(f"{etapa}:{type(exc).__name__}")
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        cob = {k: pool.submit(guarda, k, "cobertura", lambda k=k: query_coverage(k, geom, get)) for k in SISTEMAS}
        for k in SISTEMAS:
            respostas[k]["cobertura"] = cob[k].result()
        tarefas = {}
        for k in SISTEMAS:
            if respostas[k]["cobertura"] == "nenhuma":
                continue
            tarefas[(k, "alertas")] = pool.submit(guarda, k, "alertas", lambda k=k: query_alerts(k, geom, get))
            tarefas[(k, "corte")] = pool.submit(guarda, k, "corte", lambda k=k: query_cutoff(k, get, agora))
            tarefas[(k, "ultima")] = pool.submit(guarda, k, "ultima_imagem", lambda k=k: query_latest_image(k, get, agora))
        for (k, etapa), fut in tarefas.items():
            valor = fut.result()
            if etapa == "alertas" and valor is not None:
                respostas[k]["alertas"], respostas[k]["truncado"] = valor
            elif etapa == "corte" and valor is not None:
                respostas[k]["corte"] = valor.get("data")
                respostas[k]["corte_detalhe"] = valor
            elif etapa == "ultima":
                respostas[k]["ultima_imagem"] = valor
    return respostas


# ---------------------------------------------------------------- regra (pura)

def prodes_features_from_result(prodes_result: dict | None) -> list[dict]:
    """Feições do PRODES já consultadas pelo relatório (``result['prodes']['hits'][*]['features']``)."""
    saida = []
    for hit in (prodes_result or {}).get("hits") or []:
        for f in hit.get("features") or []:
            if isinstance(f, dict) and f.get("geometry"):
                saida.append(f)
    return saida


def _ano_prodes(props: dict) -> int | None:
    for chave in ("year", "ano", "year_prodes"):
        v = props.get(chave)
        if v is not None and re.fullmatch(r"\d{4}", str(v)[:4]):
            return int(str(v)[:4])
    m = re.fullmatch(r"d(\d{4})", str(props.get("class_name") or ""))
    return int(m.group(1)) if m else None


def _grupos_que_se_tocam(geoms: list[BaseGeometry]) -> list[list[int]]:
    pai = list(range(len(geoms)))

    def raiz(i: int) -> int:
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i

    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            if geoms[i].intersects(geoms[j]):
                pai[raiz(i)] = raiz(j)
    grupos: dict[int, list[int]] = {}
    for i in range(len(geoms)):
        grupos.setdefault(raiz(i), []).append(i)
    return list(grupos.values())


def deter_alerts_in_property(geometria_car: Any, respostas: dict[str, dict], prodes_features: Iterable[dict] | None = None) -> dict:
    """Aplica cobertura, corte do PRODES, agrupamento e união. Não faz rede."""
    geom = _geometria(geometria_car)
    sistemas_out: dict[str, dict] = {}
    recentes: list[dict] = []
    historicos = 0
    encostados = 0
    for nome, cfg in SISTEMAS.items():
        r = respostas.get(nome) or {}
        cobertura = r.get("cobertura")
        info = {"label": cfg["rotulo"], "coverage": cobertura, "min_area_ha": cfg["area_minima_ha"],
                "cutoff": r.get("corte"), "latest_image": r.get("ultima_imagem"), "errors": list(r.get("erros") or [])}
        if (r.get("corte_detalhe") or {}).get("discordancia"):
            info["cutoff_disagreement"] = r["corte_detalhe"]["discordancia"]
        if cobertura == "nenhuma":
            sistemas_out[nome] = {**info, "state": "not_covered", "text": TEXTO_NAO_COBERTO}
            continue
        alertas = r.get("alertas")
        if alertas is None or r.get("truncado"):
            sistemas_out[nome] = {**info, "state": "pending", "text": TEXTO_PENDENTE}
            continue
        dentro = []
        for a in alertas:
            inter = geom.intersection(a["geometria"])
            ha = area_ha(inter)
            if ha >= AREA_MINIMA_LEITURA_HA:
                dentro.append({**a, "intersecao": inter, "area_no_imovel_ha": ha})
            elif ha > 0:
                encostados += 1
        if not dentro and cobertura is None:
            sistemas_out[nome] = {**info, "state": "pending", "text": TEXTO_PENDENTE}
            continue
        corte = _data(r.get("corte"))
        if dentro and corte is None:
            sistemas_out[nome] = {**info, "state": "pending", "text": TEXTO_PENDENTE}
            continue
        rec = [a for a in dentro if (_data(a["data_imagem"]) or date.min) > corte] if dentro else []
        historicos += len(dentro) - len(rec)
        recentes.extend(rec)
        sistemas_out[nome] = {**info, "state": "found" if rec else "not_found", "recent_alerts": len(rec),
                              "alerts_up_to_cutoff": len(dentro) - len(rec)}

    prodes = []
    for f in prodes_features or []:
        try:
            g = shape(f["geometry"])
            prodes.append((_ano_prodes(f.get("properties") or {}), g if g.is_valid else make_valid(g)))
        except Exception:
            continue

    eventos = []
    for grupo in _grupos_que_se_tocam([a["intersecao"] for a in recentes]):
        membros = [recentes[i] for i in grupo]
        uniao = unary_union([m["intersecao"] for m in membros])
        datas = sorted(d for d in (_data(m["data_imagem"]) for m in membros) if d)
        sobre: dict[int | None, BaseGeometry] = {}
        for ano, g in prodes:
            if g.intersects(uniao):
                parte = g.intersection(uniao)
                sobre[ano] = unary_union([sobre[ano], parte]) if ano in sobre else parte
        sobre = {ano: g for ano, g in sobre.items() if area_ha(g) >= AREA_MINIMA_LEITURA_HA}
        sobre_total = area_ha(unary_union(list(sobre.values()))) if sobre else 0.0
        area = area_ha(uniao)
        classes = list(dict.fromkeys(c for c in (_classe(m.get("classe")) for m in membros) if c))
        satelites = list(dict.fromkeys(s for s in (_satelite(m.get("satelite"), m.get("sensor")) for m in membros) if s))
        partes_linha = [_data_br(datas[0]) if datas else None, " / ".join(classes) or None,
                        f"{_ha_texto(area)} dentro do imóvel", ("satélite " + ", ".join(satelites)) if satelites else None]
        anos = sorted(a for a in sobre if a is not None)
        if sobre_total >= AREA_MINIMA_LEITURA_HA:
            ref = f"no mapa anual do PRODES de {', '.join(str(a) for a in anos)}" if anos else "no mapa do PRODES"
            partes_linha.append(f"{_ha_texto(sobre_total)} já constavam {ref}")
        eventos.append({
            "first_seen": datas[0].isoformat() if datas else None, "first_seen_text": _data_br(datas[0]) if datas else None,
            "last_seen": datas[-1].isoformat() if datas else None, "classes": classes, "satellites": satelites,
            "systems": sorted({m["sistema"] for m in membros}), "alert_ids": [m["id"] for m in membros],
            "area_in_property_ha": round(area, 4), "area_in_property_text": _ha_texto(area),
            "prodes_overlap_ha": round(sobre_total, 4), "prodes_overlap_years": anos,
            "line": " · ".join(p for p in partes_linha if p), "_geom": uniao,
        })
    eventos.sort(key=lambda e: (e["first_seen"] or "", e["alert_ids"][0] or ""))
    area_uniao = area_ha(unary_union([e.pop("_geom") for e in eventos])) if eventos else 0.0
    soma_por_fonte = sum(a["area_no_imovel_ha"] for a in recentes)

    estados = [s["state"] for s in sistemas_out.values()]
    if "found" in estados:
        estado = "found"
    elif "pending" in estados:
        estado = "pending"
    elif "not_found" in estados:
        estado = "not_found"
    else:
        estado = "not_covered"
    completa = estado in ("found", "not_found", "not_covered") and "pending" not in estados

    ativos = {k: s for k, s in sistemas_out.items() if s["state"] in ("found", "not_found")}
    cortes = sorted(d for d in (_data(s.get("cutoff")) for s in ativos.values()) if d)
    ultimas = sorted(d for d in (_data(s.get("latest_image")) for s in ativos.values()) if d)
    limites = sorted({s["min_area_ha"] for s in ativos.values()})
    limite_txt = " ou ".join(f"{format_decimal(v, 0 if float(v).is_integer() else 2)} ha" for v in limites)
    detalhes = [f"alertas a partir de {limite_txt}"] if limites else []
    if ultimas:
        detalhes.append(f"imagens até {_data_br(ultimas[0])}")
    detalhe_txt = f" ({'; '.join(detalhes)})" if detalhes else ""

    notas = []
    if estado == "found":
        n = len(eventos)
        palavra = "alerta recente de satélite" if n == 1 else "alertas recentes de satélite"
        prefixo = "" if completa else "Pelo menos "
        if n == 1:
            texto = f"{prefixo}1 {palavra} do INPE sobre o imóvel: {eventos[0]['area_in_property_text']}, visto em {eventos[0]['first_seen_text']}."
        else:
            texto = f"{prefixo}{n} {palavra} do INPE sobre o imóvel, somando {_ha_texto(area_uniao)} sem sobreposição."
        texto = texto[:1].upper() + texto[1:]
        notas = [FRASE_ALERTA, NOTA_CREDITO]
    elif estado == "not_found":
        desde = f" desde {_data_br(cortes[0] + timedelta(days=1))}" if cortes else ""
        recente = "recente " if cortes else ""
        texto = f"Nenhum alerta {recente}de satélite do INPE sobre o imóvel{desde}{detalhe_txt}."
    elif estado == "not_covered":
        texto = "O sistema de alertas de satélite do INPE não cobre a região deste imóvel."
    else:
        texto = TEXTO_PENDENTE
    if any(s.get("coverage") == "parcial" for s in ativos.values()):
        notas.append(NOTA_COBERTURA_PARCIAL)

    return {
        "source": "inpe_deter", "state": estado, "complete": completa, "title": "Alertas recentes de desmatamento",
        "text": texto, "notes": notas, "events": eventos, "area_union_ha": round(area_uniao, 4),
        "cutoff": cortes[0].isoformat() if cortes else None, "systems": sistemas_out, "source_text": FONTE_TEXTO,
        "audit": {"alerts_up_to_cutoff_in_property": historicos, "alerts_below_one_pixel": encostados,
                  "sum_by_source_ha": round(soma_por_fonte, 4)},
    }


def deter_alerts_payload(geometria_car: Any, prodes_result: dict | None = None, *, http_get: HttpGet | None = None,
                         agora: float | None = None) -> dict:
    """Payload do relatório (rede + regra)."""
    try:
        respostas = query_deter_live(geometria_car, http_get=http_get, agora=agora)
    except Exception as exc:
        respostas = {k: {"erros": [f"geral:{type(exc).__name__}"]} for k in SISTEMAS}
    return deter_alerts_in_property(geometria_car, respostas, prodes_features_from_result(prodes_result))
