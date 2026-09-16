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
  Curvelo × Cerrado = 0 sem SRID, 1 com SRID). A envoltória só decide os dois
  casos em que ela é prova: não toca a área monitorada (``nenhuma``) ou está toda
  dentro dela (``total``). Se a envoltória cruza a divisa, a decisão é tomada com o
  polígono REAL do CAR contra a geometria da área monitorada, baixada uma vez
  (CSV, precisão total; medido em 14/09/2026: Cerrado 14 MB e 350 mil vértices,
  Amazônia Legal 18,7 MB e 479 mil vértices, 3 a 10 s cada um conforme a carga da
  máquina) e guardada em disco como WKB (5,6 e 7,7 MB; leitura 0,02–0,16 s). Parte
  dentro abaixo de um pixel = ``nenhuma``; parte fora abaixo de um pixel =
  ``total``. Fora da cobertura o estado é ``not_covered`` — "não coberto por este
  sistema", nunca "sem alerta". Sem a geometria, a cobertura fica sem resposta.
  Imóvel só em parte monitorado (e nenhum sistema cobrindo tudo): "nenhum alerta"
  vale só "sobre a parte monitorada do imóvel", com a nota de cobertura parcial.
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
   UM corte só para o imóvel: com os dois sistemas respondendo (MT/TO/MA), vale o
   mais antigo entre eles e ele é aplicado a TODOS os alertas, para que o "desde"
   do texto seja exatamente o filtro usado.
2. Só DESMATAMENTO_CR e DESMATAMENTO_VEG são desmatamento: recebem as regras 3 a
   5. Degradação, cicatriz de incêndio, corte seletivo e mineração (só existem na
   Amazônia Legal) ficam à parte, como "outra mudança na vegetação", sem a frase de
   confirmação pelo PRODES e sem a nota de crédito rural.
3. Agrupar por lugar, não por fonte: alertas recentes que se tocam dentro do imóvel
   (inclusive Cerrado × Amazônia Legal, que se sobrepõem em MT/TO/MA) viram um evento.
   Área = união das interseções dentro do CAR, medida por nós (GRS80). Nunca soma
   de áreas entre fontes.
4. Evento recente sobre área que o PRODES já marcou: diz quantos hectares já
   constavam no mapa anual, em vez de somar.
5. Crédito rural (MCR 2-9, 31/07/2019): alerta recente não entra na conta pós-2019
   do PRODES; vira linha à parte ("ainda não confirmado pelo mapa anual").

Texto fixo sempre que houver alerta: alerta não é multa nem auto de infração.

Combinação com outras testemunhas (MapBiomas Alerta, frente ``f2/mapbiomas_alerta``):
``deter_combiner_input`` entrega os alertas de desmatamento com geometria no contrato
de ``combine_deforestation_alerts`` a partir das MESMAS respostas (uma consulta só).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from external_process_lifecycle import in_current_scope
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import shapely
from pyproj import Geod
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.errors import GEOSException
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
# Limite do bioma (IBGE) e da Amazônia Legal: camadas estáticas; a cópia em disco vale 30 dias
# e, vencida, continua valendo se a fonte não responder. Falha sem cópia: nova tentativa só
# depois de 15 min, para relatórios seguidos não esperarem o mesmo download que caiu.
COBERTURA_TTL_S = 30 * 86400
COBERTURA_NOVA_TENTATIVA_S = 15 * 60
USER_AGENT = "Raio-X-Territorial/F2 deter-alertas"
FRASE_ALERTA = (
    "Alerta não é multa nem auto de infração. É um aviso de satélite de mudança na vegetação, "
    "que pode ter autorização."
)
FRASE_PRODES = "O mapa oficial anual (PRODES) confirma ou não o desmatamento."
NOTA_CREDITO = "Há alerta recente de desmatamento ainda não confirmado pelo mapa anual do PRODES."
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
# Só estas são desmatamento (as que o PRODES pode confirmar). Medido em 14/09/2026: o DETER
# Cerrado só tem DESMATAMENTO_CR (133.097); o da Amazônia tem também degradação, cicatriz de
# incêndio, corte seletivo e mineração. Classe desconhecida nunca vira desmatamento.
CLASSES_DESMATAMENTO = frozenset({"DESMATAMENTO_CR", "DESMATAMENTO_VEG"})
_GEOD = Geod(ellps="GRS80")
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()
_COB_LOCKS = {k: threading.Lock() for k in SISTEMAS}
_COB_FALHA: dict[str, float] = {}
_COB_MEMORIA: dict[str, BaseGeometry] = {}  # só quando o disco não aceita gravação

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


def cache_dir(base: str | os.PathLike | None = None) -> Path:
    return Path(base or os.environ.get("RX_DATA_CACHE_DIR") or Path(tempfile.gettempdir()) / "raio-x-data")


def params_coverage_geometry(sistema: str) -> dict:
    return {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": SISTEMAS[sistema]["cobertura_camada"],
            "srsName": "EPSG:4674", "outputFormat": "csv"}


_ANEL_WKT = re.compile(rb"\(([^()]*)\)")


def poligonal_de_wkt(wkt: bytes, ini: int = 0, fim: int | None = None) -> BaseGeometry:
    """POLYGON/MULTIPOLYGON 2D em WKT → geometria, anel por anel (sem o pico do leitor WKT do GEOS).

    Cada grupo de parênteses mais interno é um anel; entre dois anéis, um ")" no intervalo
    quer dizer polígono novo. Qualquer forma diferente (Z, EMPTY, número ímpar) levanta
    ValueError, e quem chama usa o leitor do GEOS. ``ini``/``fim`` delimitam o WKT dentro de
    ``wkt`` sem copiar o trecho.
    """
    fim = len(wkt) if fim is None else fim
    cab = wkt[ini:min(fim, ini + 32)].lstrip().upper()
    if not cab.startswith((b"MULTIPOLYGON", b"POLYGON")) or re.match(rb"^\w+\s*(Z|M|ZM|EMPTY)\b", cab):
        raise ValueError("wkt_nao_poligonal_2d")
    poligonos, aneis, fim_anterior = [], [], None
    for m in _ANEL_WKT.finditer(wkt, ini, fim):
        if fim_anterior is not None and b")" in wkt[fim_anterior:m.start()]:
            poligonos.append(shapely.polygons(aneis[0], holes=aneis[1:] or None))
            aneis = []
        texto = m.group(1).replace(b",", b" ").decode("ascii")
        coords = np.fromstring(texto, dtype=np.float64, sep=" ")
        del texto
        if coords.size < 8 or coords.size % 2 or not np.isfinite(coords).all():
            raise ValueError("wkt_anel_invalido")
        aneis.append(shapely.linearrings(coords.reshape(-1, 2)))
        fim_anterior = m.end()
    if aneis:
        poligonos.append(shapely.polygons(aneis[0], holes=aneis[1:] or None))
    if not poligonos or (cab.startswith(b"POLYGON") and len(poligonos) != 1):
        raise ValueError("wkt_sem_poligono")
    return poligonos[0] if cab.startswith(b"POLYGON") else shapely.multipolygons(poligonos)


def parse_coverage_csv(corpo: bytes) -> BaseGeometry:
    """Geometria da área monitorada no CSV do GeoServer (última coluna ``geom``, WKT com precisão total).

    Lê direto dos bytes, sem decodificar o arquivo inteiro nem passar pelo módulo csv, e
    monta a geometria anel por anel. Medido em 14/09/2026 com o arquivo real da Amazônia
    Legal (18,7 MB, 479 mil vértices), pico de memória do processo partindo de ~55–70 MB:
    texto + csv + leitor WKT do GEOS = 429 MB; bytes + leitor do GEOS = 259 MB (o pico está
    dentro do leitor do GEOS); anel por anel = 127 MB, geometria idêntica (equals_exact 0).
    Cerrado: 334 → 109 MB. WKT não tem aspas nem quebra de linha: cada geometria é o
    trecho entre aspas que começa com POLYGON/MULTIPOLYGON.
    """
    corpo = bytes(corpo or b"")
    fim_cab = corpo.find(b"\n")
    if fim_cab < 0 or corpo[:fim_cab].decode("utf-8-sig", "replace").strip().split(",")[-1] != "geom":
        raise ValueError("csv_cobertura_sem_geom")
    partes = []
    pos = fim_cab + 1
    while True:
        achados = [i for i in (corpo.find(b'"MULTIPOLYGON', pos), corpo.find(b'"POLYGON', pos)) if i >= 0]
        if not achados:
            break
        i = min(achados)
        j = corpo.find(b'"', i + 1)
        if j < 0:
            raise ValueError("csv_cobertura_truncado")
        try:
            partes.append(poligonal_de_wkt(corpo, i + 1, j))
        except ValueError:
            partes.append(shapely.from_wkt(corpo[i + 1:j].decode("ascii")))
        pos = j + 1
    linhas = corpo.count(b"\n", fim_cab + 1) + (0 if corpo.endswith(b"\n") else 1)
    if not partes:
        raise ValueError("csv_cobertura_vazio")
    if linhas != len(partes):
        raise ValueError("csv_cobertura_linha_sem_geometria")
    geom = partes[0] if len(partes) == 1 else unary_union(partes)
    geom = geom if geom.is_valid else make_valid(geom)
    if geom.is_empty or area_ha(geom) <= 0:
        raise ValueError("csv_cobertura_sem_area")
    return geom


def _gravar_atomico(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def coverage_geometry(sistema: str, http_get: HttpGet, *, base_dir: str | os.PathLike | None = None,
                      agora: float | None = None) -> BaseGeometry:
    """Área monitorada pelo sistema (cópia em disco). Levanta exceção se não houver geometria confiável."""
    now = time.time() if agora is None else agora
    arq = cache_dir(base_dir) / "deter" / f"cobertura_{sistema}.wkb"
    arq_meta = arq.with_suffix(".meta.json")
    chave = str(arq)
    with _COB_LOCKS[sistema]:  # relatórios simultâneos esperam o mesmo download em vez de repeti-lo
        try:
            meta = json.loads(arq_meta.read_text(encoding="utf-8"))
            salvo = shapely.from_wkb(arq.read_bytes())
        except (OSError, ValueError, GEOSException):
            meta, salvo = {}, _COB_MEMORIA.get(chave)
        if salvo is not None and (not meta or now - float(meta.get("baixado_em") or 0) < COBERTURA_TTL_S):
            return salvo
        falhou = _COB_FALHA.get(chave)
        if falhou is not None and 0 <= now - falhou < COBERTURA_NOVA_TENTATIVA_S:
            if salvo is not None:
                return salvo  # vencida, mas a camada é estática
            raise ValueError("cobertura_falhou_recentemente")
        try:
            resp = _get_ok(http_get, URL_TB.format(ws=SISTEMAS[sistema]["cobertura_ws"]), params_coverage_geometry(sistema))
            geom = parse_coverage_csv(resp.content)
            del resp
        except Exception:
            _COB_FALHA[chave] = now
            if salvo is not None:
                return salvo
            raise
        _COB_FALHA.pop(chave, None)
        try:
            _gravar_atomico(arq, shapely.to_wkb(geom))
            _gravar_atomico(arq_meta, json.dumps({"baixado_em": now, "camada": SISTEMAS[sistema]["cobertura_camada"]}).encode("utf-8"))
        except OSError:
            _COB_MEMORIA[chave] = geom
        return geom


def classify_coverage(geom: BaseGeometry, area_monitorada: BaseGeometry, detalhe: dict | None = None) -> str:
    """Cobertura pelo polígono real: parte dentro/fora abaixo de um pixel não conta."""
    minx, miny, maxx, maxy = geom.bounds
    folga = 0.01
    local = shapely.clip_by_rect(area_monitorada, minx - folga, miny - folga, maxx + folga, maxy + folga)
    if not local.is_valid:
        local = make_valid(local)
    dentro = area_ha(geom.intersection(local)) if not local.is_empty else 0.0
    fora = area_ha(geom.difference(local)) if not local.is_empty else area_ha(geom)
    if detalhe is not None:
        detalhe.update(metodo="poligono_real", dentro_ha=round(dentro, 4), fora_ha=round(fora, 4))
    if dentro < AREA_MINIMA_LEITURA_HA:
        return "nenhuma"
    if fora < AREA_MINIMA_LEITURA_HA:
        return "total"
    return "parcial"


def query_coverage(sistema: str, geom: BaseGeometry, http_get: HttpGet, *, base_dir: str | os.PathLike | None = None,
                   agora: float | None = None, detalhe: dict | None = None) -> str:
    """'total' | 'parcial' | 'nenhuma' (levanta exceção se o servidor não respondeu).

    A envoltória contém o CAR, então ela só prova dois casos: não tocar a área monitorada
    (nenhuma) e estar toda dentro dela (total). Envoltória cruzando a divisa não diz nada
    do imóvel: aí vale o polígono real contra a geometria da área monitorada.
    """
    url = URL_TB.format(ws=SISTEMAS[sistema]["cobertura_ws"])
    if _number_matched(_get_ok(http_get, url, params_coverage(sistema, geom, "INTERSECTS")).text) == 0:
        if detalhe is not None:
            detalhe.update(metodo="envoltoria")
        return "nenhuma"
    if _number_matched(_get_ok(http_get, url, params_coverage(sistema, geom, "CONTAINS")).text) > 0:
        if detalhe is not None:
            detalhe.update(metodo="envoltoria")
        return "total"
    return classify_coverage(geom, coverage_geometry(sistema, http_get, base_dir=base_dir, agora=agora), detalhe)


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


def query_deter_live(geometria_car: Any, *, http_get: HttpGet | None = None, agora: float | None = None,
                     base_dir: str | os.PathLike | None = None) -> dict:
    """Consulta os dois sistemas e devolve as respostas cruas (falha fica registrada, nunca vira zero)."""
    get = http_get or _http_get
    geom = _geometria(geometria_car)
    respostas: dict[str, dict] = {k: {"cobertura": None, "cobertura_detalhe": {}, "alertas": None, "truncado": False,
                                      "corte": None, "ultima_imagem": None, "erros": []} for k in SISTEMAS}

    def guarda(sistema: str, etapa: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:
            respostas[sistema]["erros"].append(f"{etapa}:{type(exc).__name__}")
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        cob = {k: pool.submit(in_current_scope(guarda), k, "cobertura", lambda k=k: query_coverage(k, geom, get, base_dir=base_dir, agora=agora,
                                                                          detalhe=respostas[k]["cobertura_detalhe"]))
               for k in SISTEMAS}
        for k in SISTEMAS:
            respostas[k]["cobertura"] = cob[k].result()
        tarefas = {}
        for k in SISTEMAS:
            if respostas[k]["cobertura"] == "nenhuma":
                continue
            tarefas[(k, "alertas")] = pool.submit(in_current_scope(guarda), k, "alertas", lambda k=k: query_alerts(k, geom, get))
            tarefas[(k, "corte")] = pool.submit(in_current_scope(guarda), k, "corte", lambda k=k: query_cutoff(k, get, agora))
            tarefas[(k, "ultima")] = pool.submit(in_current_scope(guarda), k, "ultima_imagem", lambda k=k: query_latest_image(k, get, agora))
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


def _e_desmatamento(classe: str | None) -> bool:
    return (classe or "").strip().upper() in CLASSES_DESMATAMENTO


def _eventos(recentes: list[dict], prodes: list[tuple[int | None, BaseGeometry]], com_prodes: bool) -> list[dict]:
    """Agrupa por lugar (alertas que se tocam = um evento) e mede a união dentro do imóvel."""
    eventos = []
    for grupo in _grupos_que_se_tocam([a["intersecao"] for a in recentes]):
        membros = [recentes[i] for i in grupo]
        uniao = unary_union([m["intersecao"] for m in membros])
        datas = sorted(d for d in (_data(m["data_imagem"]) for m in membros) if d)
        sobre: dict[int | None, BaseGeometry] = {}
        for ano, g in (prodes if com_prodes else []):
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
        evento = {
            "first_seen": datas[0].isoformat() if datas else None, "first_seen_text": _data_br(datas[0]) if datas else None,
            "last_seen": datas[-1].isoformat() if datas else None, "classes": classes, "satellites": satelites,
            "systems": sorted({m["sistema"] for m in membros}), "alert_ids": [m["id"] for m in membros],
            "area_in_property_ha": round(area, 4), "area_in_property_text": _ha_texto(area),
            "line": " · ".join(p for p in partes_linha if p), "_geom": uniao,
        }
        if com_prodes:
            evento.update(prodes_overlap_ha=round(sobre_total, 4), prodes_overlap_years=anos)
        eventos.append(evento)
    eventos.sort(key=lambda e: (e["first_seen"] or "", e["alert_ids"][0] or ""))
    return eventos


def _frase_eventos(eventos: list[dict], area_uniao: float, desmatamento: bool, prefixo: str) -> str:
    n = len(eventos)
    if desmatamento:
        nome = "alerta recente de desmatamento" if n == 1 else "alertas recentes de desmatamento"
    else:
        classes = list(dict.fromkeys(c for e in eventos for c in e["classes"]))
        tipo = f" ({', '.join(c[:1].lower() + c[1:] for c in classes)})" if classes else ""
        nome = f"alerta recente de outra mudança na vegetação{tipo}" if n == 1 else f"alertas recentes de outras mudanças na vegetação{tipo}"
    if n == 1:
        return f"{prefixo}1 {nome} do INPE sobre o imóvel: {eventos[0]['area_in_property_text']}, visto em {eventos[0]['first_seen_text']}."
    return f"{prefixo}{n} {nome} do INPE sobre o imóvel, somando {_ha_texto(area_uniao)} sem sobreposição."


def deter_alerts_in_property(geometria_car: Any, respostas: dict[str, dict], prodes_features: Iterable[dict] | None = None) -> dict:
    """Aplica cobertura, corte único do PRODES, classes, agrupamento e união. Não faz rede."""
    geom = _geometria(geometria_car)
    sistemas_out: dict[str, dict] = {}
    lidos: dict[str, tuple[list[dict], date | None]] = {}
    encostados = 0
    for nome, cfg in SISTEMAS.items():
        r = respostas.get(nome) or {}
        cobertura = r.get("cobertura")
        info = {"label": cfg["rotulo"], "coverage": cobertura, "min_area_ha": cfg["area_minima_ha"],
                "cutoff": r.get("corte"), "latest_image": r.get("ultima_imagem"), "errors": list(r.get("erros") or [])}
        if (r.get("corte_detalhe") or {}).get("discordancia"):
            info["cutoff_disagreement"] = r["corte_detalhe"]["discordancia"]
        if (r.get("cobertura_detalhe") or {}).get("metodo"):
            info["coverage_detail"] = dict(r["cobertura_detalhe"])
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
        proprio = _data(r.get("corte"))
        # alerta sem data ou sistema sem corte: não dá para dizer se é recente
        if dentro and (proprio is None or any(_data(a["data_imagem"]) is None for a in dentro)):
            sistemas_out[nome] = {**info, "state": "pending", "text": TEXTO_PENDENTE}
            continue
        sistemas_out[nome] = info
        lidos[nome] = (dentro, proprio)

    # Um corte só para o imóvel: o mais antigo entre os sistemas que responderam, aplicado a
    # TODOS os alertas. Assim o "desde" do texto é exatamente o filtro usado.
    cortes_por_sistema = {nome: proprio for nome, (_, proprio) in lidos.items() if proprio}
    corte = min(cortes_por_sistema.values()) if cortes_por_sistema else None
    recentes: list[dict] = []
    historicos = 0
    for nome, (dentro, _) in lidos.items():
        rec = [a for a in dentro if _data(a["data_imagem"]) > corte] if dentro else []
        historicos += len(dentro) - len(rec)
        recentes.extend(rec)
        sistemas_out[nome].update(state="found" if rec else "not_found", recent_alerts=len(rec),
                                  alerts_up_to_cutoff=len(dentro) - len(rec))

    prodes = []
    for f in prodes_features or []:
        try:
            g = shape(f["geometry"])
            prodes.append((_ano_prodes(f.get("properties") or {}), g if g.is_valid else make_valid(g)))
        except Exception:
            continue

    desmatamento = [a for a in recentes if _e_desmatamento(a.get("classe"))]
    outros = [a for a in recentes if not _e_desmatamento(a.get("classe"))]
    eventos = _eventos(desmatamento, prodes, True)
    outros_eventos = _eventos(outros, prodes, False)
    area_uniao = area_ha(unary_union([e.pop("_geom") for e in eventos])) if eventos else 0.0
    area_outros = area_ha(unary_union([e.pop("_geom") for e in outros_eventos])) if outros_eventos else 0.0
    soma_por_fonte = sum(a["area_no_imovel_ha"] for a in desmatamento)

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
    ultimas = sorted(d for d in (_data(s.get("latest_image")) for s in ativos.values()) if d)
    limites = sorted({s["min_area_ha"] for s in ativos.values()})
    limite_txt = " ou ".join(f"{format_decimal(v, 0 if float(v).is_integer() else 2)} ha" for v in limites)
    detalhes = [f"alertas a partir de {limite_txt}"] if limites else []
    if ultimas:
        detalhes.append(f"imagens até {_data_br(ultimas[0])}")
    detalhe_txt = f" ({'; '.join(detalhes)})" if detalhes else ""
    desde = f" desde {_data_br(corte + timedelta(days=1))}" if corte else ""
    # Nenhum sistema cobre o imóvel inteiro e algum cobre só parte: "nenhum alerta" vale só para
    # a parte monitorada (medido ao vivo em 14/09/2026: imóvel na divisa do Cerrado em MG com
    # 427 de 1.457 ha dentro da área monitorada).
    so_parte = (not any(s.get("coverage") == "total" for s in ativos.values())
                and any(s.get("coverage") == "parcial" for s in ativos.values()))
    alvo = "sobre a parte monitorada do imóvel" if so_parte else "sobre o imóvel"

    notas = []
    if estado == "found":
        prefixo = "" if completa else "Pelo menos "
        frases = []
        if eventos:
            frases.append(_frase_eventos(eventos, area_uniao, True, prefixo))
        elif completa:
            frases.append(f"Nenhum alerta recente de desmatamento do INPE {alvo}{desde}{detalhe_txt}.")
        if outros_eventos:
            if eventos:
                frases.append("Há também " + _frase_eventos(outros_eventos, area_outros, False, prefixo.lower()))
            else:
                frases.append(_frase_eventos(outros_eventos, area_outros, False, prefixo))
        texto = " ".join(f[:1].upper() + f[1:] for f in frases)
        notas = [FRASE_ALERTA] + ([FRASE_PRODES, NOTA_CREDITO] if eventos else [])
    elif estado == "not_found":
        recente = "recente " if corte else ""
        texto = f"Nenhum alerta {recente}de satélite do INPE {alvo}{desde}{detalhe_txt}."
    elif estado == "not_covered":
        texto = "O sistema de alertas de satélite do INPE não cobre a região deste imóvel."
    else:
        texto = TEXTO_PENDENTE
    if so_parte and estado in ("found", "not_found"):
        notas.append(NOTA_COBERTURA_PARCIAL)

    titulo = "Alertas recentes de desmatamento" + (" e de outras mudanças na vegetação" if outros_eventos else "")
    out = {
        "source": "inpe_deter", "state": estado, "complete": completa, "title": titulo,
        "text": texto, "notes": notas, "events": eventos, "area_union_ha": round(area_uniao, 4),
        "other_events": outros_eventos, "other_area_union_ha": round(area_outros, 4),
        "cutoff": corte.isoformat() if corte else None, "systems": sistemas_out, "source_text": FONTE_TEXTO,
        "audit": {"alerts_up_to_cutoff_in_property": historicos, "alerts_below_one_pixel": encostados,
                  "sum_by_source_ha": round(soma_por_fonte, 4)},
    }
    if len(set(cortes_por_sistema.values())) > 1:
        out["audit"]["cutoff_by_system"] = {k: v.isoformat() for k, v in cortes_por_sistema.items()}
    return out


def deter_combiner_input(geometria_car: Any, respostas: dict[str, dict]) -> dict:
    """Entrada de ``mapbiomas_alerta.combine_deforestation_alerts`` a partir das mesmas respostas.

    Contrato daquela frente: ``{"state": "answered"|"pending"|"not_covered", "features": [GeoJSON],
    "min_area_ha", "latest_image_date"}``, com ``properties.view_date`` e ``properties.gid``. Só entram
    alertas de DESMATAMENTO (o combinador trata toda feição como desmatamento). Qualquer sistema
    que cobre o imóvel sem resposta completa deixa tudo ``pending`` (lista vazia só é resposta
    quando o estado diz ``answered``). ``cutoff`` é o corte único a passar em ``prodes_cutoff``.
    """
    geom = _geometria(geometria_car)
    estados: list[str] = []
    feicoes: list[dict] = []
    cortes: list[date] = []
    ultimas: list[date] = []
    minimos: list[float] = []
    outras = 0
    for nome, cfg in SISTEMAS.items():
        r = respostas.get(nome) or {}
        cobertura = r.get("cobertura")
        if cobertura == "nenhuma":
            estados.append("not_covered")
            continue
        if cobertura is None or r.get("alertas") is None or r.get("truncado"):
            estados.append("pending")
            continue
        proprio = _data(r.get("corte"))
        tocam = [a for a in r["alertas"] if a["geometria"].intersects(geom)]
        if tocam and (proprio is None or any(_data(a["data_imagem"]) is None for a in tocam)):
            estados.append("pending")
            continue
        estados.append("answered")
        minimos.append(cfg["area_minima_ha"])
        if proprio:
            cortes.append(proprio)
        if _data(r.get("ultima_imagem")):
            ultimas.append(_data(r.get("ultima_imagem")))
        for a in tocam:
            if not _e_desmatamento(a.get("classe")):
                outras += 1
                continue
            feicoes.append({"type": "Feature", "geometry": mapping(a["geometria"]),
                            "properties": {"gid": a["id"], "view_date": a["data_imagem"], "classname": a.get("classe"),
                                           "system": nome}})
    if "pending" in estados:
        estado = "pending"
    elif "answered" in estados:
        estado = "answered"
    else:
        estado = "not_covered"
    return {
        "state": estado, "features": feicoes if estado == "answered" else [],
        "min_area_ha": min(minimos) if minimos else None,
        "latest_image_date": min(ultimas).isoformat() if ultimas else None,
        "cutoff": min(cortes).isoformat() if cortes else None,
        "other_classes_excluded": outras,
    }


def _respostas_ou_falha(geometria_car: Any, http_get: HttpGet | None, agora: float | None,
                        base_dir: str | os.PathLike | None) -> dict:
    try:
        return query_deter_live(geometria_car, http_get=http_get, agora=agora, base_dir=base_dir)
    except Exception as exc:
        return {k: {"erros": [f"geral:{type(exc).__name__}"]} for k in SISTEMAS}


def deter_alerts_payload(geometria_car: Any, prodes_result: dict | None = None, *, http_get: HttpGet | None = None,
                         agora: float | None = None, base_dir: str | os.PathLike | None = None) -> dict:
    """Payload do relatório (rede + regra)."""
    respostas = _respostas_ou_falha(geometria_car, http_get, agora, base_dir)
    return deter_alerts_in_property(geometria_car, respostas, prodes_features_from_result(prodes_result))


def deter_alerts_bundle(geometria_car: Any, prodes_result: dict | None = None, *, http_get: HttpGet | None = None,
                        agora: float | None = None, base_dir: str | os.PathLike | None = None) -> dict:
    """Uma consulta só para as duas saídas: ``payload`` (seção do relatório) e ``combiner``.

    ``combiner`` traz geometria (GeoJSON): não gravar no payload.json do relatório.
    """
    respostas = _respostas_ou_falha(geometria_car, http_get, agora, base_dir)
    payload = deter_alerts_in_property(geometria_car, respostas, prodes_features_from_result(prodes_result))
    try:
        combinador = deter_combiner_input(geometria_car, respostas)
    except Exception as exc:
        combinador = {"state": "pending", "features": [], "error_type": type(exc).__name__}
    return {"payload": payload, "combiner": combinador}
