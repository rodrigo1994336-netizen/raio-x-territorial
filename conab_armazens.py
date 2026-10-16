"""F2 · CONAB — armazéns cadastrados (SICARM) em até 50 km do imóvel.

Fonte: arquivo aberto ``ArmazensCadastrados.txt`` do Portal de Informações da
CONAB (base do Cadastro Nacional de Unidades Armazenadoras, SICARM). Sem chave,
sem cadastro. Provado ao vivo em 13/09/2026 para o CAR de teste de Curvelo/MG:
os mesmos 2 armazéns e as mesmas distâncias até a BORDA do polígono que o
concorrente mostra (31,1 km e 47,1 km).

Regras que este módulo garante (conferidas por ``scripts/f2_conab_deter_gate.py``):

* LGPD: e-mail, endereço e nome de pessoa física são descartados ANTES de gravar
  em disco. A razão social só é guardada para pessoa jurídica com forma
  societária explícita (LTDA, S.A., CIA, cooperativa, órgão oficial) e sem marca
  de empresário individual (ME, MEI, EI, EIRELI, EPP, SLU ou dígitos, que é como
  a Receita grava MEI). O arquivo não traz CPF/CNPJ.
* Codificação lida pelo conteúdo: o servidor declara utf-8, o arquivo é ISO-8859-1.
* Sincronização barata: o proxy da CONAB acrescenta ``-gzip`` ao ETag quando
  comprime e só devolve 304 com o ETag original. Enviamos If-None-Match sem o
  sufixo e If-Modified-Since (medido em 13/09/2026: 304 em 0,17–0,85 s; com o
  ETag ``-gzip`` a resposta foi 200 com o arquivo inteiro).
* Arquivo novo com cabeçalho diferente ou com menos da metade das linhas da
  cópia anterior não substitui a cópia anterior.
* Distância geodésica (GRS80) até a borda do polígono; zero se o armazém está
  dentro do imóvel.
* 4,9 % dos pontos de MG caem a mais de 10 km do município declarado (até 411 km).
  Por isso cada armazém candidato é conferido contra a malha do IBGE do município
  que ele mesmo declara (tolerância de 2 km). Ponto fora não é listado; malha que
  não respondeu deixa o armazém sem conferência e a lista vira "pelo menos N".
* Estados: ``found`` / ``not_found`` / ``pending``. Base nunca baixada e download
  falhou = ``pending`` (nunca "nenhum armazém"). Com armazém sem conferência de
  localização, o texto não afirma qual é o mais próximo.
* Sincronização não trava o relatório: falha fica guardada e só se tenta de novo
  depois de um intervalo; se outra linha de execução já está baixando e existe
  cópia boa, responde com a cópia. ``aquecer_em_segundo_plano`` baixa no arranque.
  A malha do IBGE que falhou também fica guardada (15 min) para não repetir a espera.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from external_process_lifecycle import in_current_scope
from functools import lru_cache
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from pyproj import Geod, Transformer
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, transform

from report_ptbr_v50 import format_decimal

URL_ARMAZENS = "https://portaldeinformacoes.conab.gov.br/downloads/arquivos/ArmazensCadastrados.txt"
URL_MALHA_IBGE = "https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{codigo}"
PARAMS_MALHA_IBGE = {"formato": "application/vnd.geo+json", "qualidade": "intermediaria"}
COLUNAS = (
    "identificacao_armazem", "dsc_especie_armazem", "dsc_tipo_armazem", "dsc_tipo_entidade",
    "dsc_tipo_pessoa", "nom_municipio", "cod_ibge", "uf", "qtd_capacidade_estatica(t)",
    "qtd_capacidade_expedicao(t)", "qtd_capacidade_recepcao(t)", "latitude", "longitude",
    "nome_armazenador", "endereco", "email",
)
RAIO_PADRAO_KM = 50.0
TOLERANCIA_MUNICIPIO_KM = 2.0
SYNC_TTL_S = 6 * 3600
# Depois de uma falha (rede, HTTP ou arquivo recusado), nova tentativa só depois deste intervalo:
# relatórios seguidos não esperam de novo o mesmo tempo limite. Com cópia boa, 15 min; sem cópia
# nenhuma a consulta já é "pendente", então a nova tentativa vem em 1 min.
SYNC_NOVA_TENTATIVA_S = 15 * 60
SYNC_NOVA_TENTATIVA_SEM_BASE_S = 60
MALHA_TTL_S = 180 * 86400
MIN_LINHAS = 5000  # o arquivo nacional tinha 18.847 linhas em 11/09/2026
FORMATO_CACHE = 1
FONTE = "CONAB — Cadastro Nacional de Unidades Armazenadoras (SICARM)"
USER_AGENT = "Raio-X-Territorial/F2 conab-armazens"
BRT = timezone(timedelta(hours=-3))
_GEOD = Geod(ellps="GRS80")
_SYNC_LOCK = threading.Lock()
_FALHAS: dict[str, float] = {}
_MEM_LOCK = threading.Lock()
_MEMORIA: dict[str, Any] = {}

HttpGet = Callable[..., Any]


# ---------------------------------------------------------------- utilidades

def cache_dir(base: str | os.PathLike | None = None) -> Path:
    root = base or os.environ.get("RX_DATA_CACHE_DIR") or Path(tempfile.gettempdir()) / "raio-x-data"
    return Path(root)


def _http_get(url: str, *, params: dict | None = None, headers: dict | None = None):
    import httpx

    # Mesmo padrão de tempo do prodes_fast_v24 (connect 6 s, leitura 16 s); do Render
    # ainda não foi medido. Daqui: arquivo com gzip 0,6–1,4 s; malha IBGE 0,2–0,8 s.
    timeout = httpx.Timeout(16.0, connect=6.0)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        return client.get(url, params=params, headers=headers)


def _escrever_atomico(path: Path, data: bytes) -> None:
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


def _ler_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=4096)
def _ascii_upper(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").upper()


def _geometria(obj: Any) -> BaseGeometry:
    if isinstance(obj, BaseGeometry):
        return obj
    if isinstance(obj, dict):
        if obj.get("type") == "FeatureCollection":
            return shape(obj["features"][0]["geometry"])
        if obj.get("type") == "Feature":
            return shape(obj["geometry"])
        return shape(obj)
    raise TypeError("geometria do CAR ausente")


def _num_br(text: str) -> float | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        return float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)
    except ValueError:
        return None


def _coord(lat_txt: str, lon_txt: str) -> tuple[float, float] | None:
    try:
        lat, lon = float(lat_txt.strip()), float(lon_txt.strip())
    except (ValueError, AttributeError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-34.0 < lat < 6.0 and -74.5 < lon < -28.5):
        return None
    return lat, lon


@lru_cache(maxsize=512)
def _frase(text: str) -> str | None:
    """"GRANEL SÓLIDO" -> "Granel sólido"; "NÃO INFORMADO" some (campo vazio não aparece)."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s or _ascii_upper(s) in ("NAO INFORMADO", "NAO INFORMADA"):
        return None
    return s[:1].upper() + s[1:].lower()


_PARTICULAS = {"DE", "DA", "DO", "DAS", "DOS", "E", "DEL"}


@lru_cache(maxsize=8192)
def _municipio_uf(nom_municipio: str, uf: str) -> tuple[str | None, str | None]:
    s = re.sub(r"\s+", " ", (nom_municipio or "").strip())
    uf = (uf or "").strip().upper() or None
    m = re.match(r"^(.*?)-([A-Z]{2})$", s)
    if m:
        s, uf = m.group(1).strip(), uf or m.group(2)
    if not s:
        return None, uf
    palavras = []
    for i, w in enumerate(s.split(" ")):
        partes = []
        for p in w.split("-"):
            up = _ascii_upper(p)
            if i > 0 and up in _PARTICULAS:
                partes.append(p.lower())
            elif up.startswith("D'") and len(p) > 2:
                partes.append("d'" + p[2:3].upper() + p[3:].lower())
            else:
                partes.append(p[:1].upper() + p[1:].lower())
        palavras.append("-".join(partes))
    return " ".join(palavras), uf


_FORMA_SOCIETARIA = re.compile(r"\b(LTDA|S A|SA|CIA|COMPANHIA|COOPERATIVA|COOP|ASSOCIACAO|FUNDACAO|SINDICATO|CONAB)\b")
_EMPRESARIO_INDIVIDUAL = re.compile(r"\d|\b(MEI|EIRELI|SLU)\b|\b(ME|EI|EPP)$")


def nome_publicavel(nome: str, tipo_pessoa: str, entidade: str) -> str | None:
    """Razão social que pode ser mostrada, ou None.

    Pessoa física: nunca. Pessoa jurídica: só com forma societária explícita (ou
    entidade cooperativa/oficial) e sem marca de empresário individual, cujo nome
    empresarial é o nome da pessoa.
    """
    if "JURIDICA" not in _ascii_upper(tipo_pessoa):
        return None
    limpo = re.sub(r"\s+", " ", (nome or "").strip())
    if not limpo:
        return None
    chave = re.sub(r"\s+", " ", re.sub(r"[./\\,;:()\-]", " ", _ascii_upper(limpo))).strip()
    if _EMPRESARIO_INDIVIDUAL.search(chave):
        return None
    ent = _ascii_upper(entidade)
    if not (_FORMA_SOCIETARIA.search(chave) or "COOPERATIVA" in ent or "OFICIAL" in ent):
        return None
    return limpo


# ---------------------------------------------------------------- leitura do arquivo

def decodificar(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def ler_arquivo_armazens(raw: bytes) -> tuple[list[dict], dict]:
    """Lê o arquivo da CONAB e devolve só campos não pessoais (função pura).

    Levanta ValueError se o cabeçalho não for o esperado.
    """
    texto, codificacao = decodificar(raw)
    leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=";")
    try:
        cabecalho = tuple(c.strip() for c in next(leitor))
    except StopIteration:
        raise ValueError("arquivo_vazio") from None
    if cabecalho != COLUNAS:
        raise ValueError("cabecalho_inesperado")
    idx = {c: i for i, c in enumerate(COLUNAS)}
    linhas: list[dict] = []
    vistos: set[str] = set()
    stats = {"codificacao": codificacao, "linhas_arquivo": 0, "duplicados": 0, "coord_invalidas": 0, "linhas_curtas": 0}
    for campos in leitor:
        if not any(x.strip() for x in campos):
            continue
        stats["linhas_arquivo"] += 1
        if len(campos) < len(COLUNAS):
            stats["linhas_curtas"] += 1
            continue
        cda = campos[idx["identificacao_armazem"]].strip()
        if not cda:
            stats["linhas_curtas"] += 1
            continue
        if cda in vistos:
            stats["duplicados"] += 1
            continue
        vistos.add(cda)
        coord = _coord(campos[idx["latitude"]], campos[idx["longitude"]])
        if coord is None:
            stats["coord_invalidas"] += 1
        municipio, uf = _municipio_uf(campos[idx["nom_municipio"]], campos[idx["uf"]])
        pessoa = _ascii_upper(campos[idx["dsc_tipo_pessoa"]])
        linhas.append({
            "cda": cda,
            "especie": _frase(campos[idx["dsc_especie_armazem"]]),
            "tipo": _frase(campos[idx["dsc_tipo_armazem"]]),
            "entidade": _frase(campos[idx["dsc_tipo_entidade"]]),
            "pessoa": "juridica" if "JURIDICA" in pessoa else "fisica" if "FISICA" in pessoa else "nao_informado",
            # e-mail e endereço nunca são lidos para fora daqui; nome só se publicável.
            "nome": nome_publicavel(campos[idx["nome_armazenador"]], campos[idx["dsc_tipo_pessoa"]], campos[idx["dsc_tipo_entidade"]]),
            "municipio": municipio,
            "uf": uf,
            "cod_ibge": re.sub(r"\D", "", campos[idx["cod_ibge"]]) or None,
            "capacidade_t": _num_br(campos[idx["qtd_capacidade_estatica(t)"]]),
            "lat": coord[0] if coord else None,
            "lon": coord[1] if coord else None,
        })
    stats["linhas"] = len(linhas)
    return linhas, stats


_CAMPOS_CACHE = ("cda", "especie", "tipo", "entidade", "pessoa", "nome", "municipio", "uf", "cod_ibge", "capacidade_t", "lat", "lon")


def _serializar_base(linhas: list[dict], meta: dict) -> bytes:
    corpo = {"formato": FORMATO_CACHE, "meta": meta, "campos": list(_CAMPOS_CACHE),
             "linhas": [[r.get(k) for k in _CAMPOS_CACHE] for r in linhas]}
    return json.dumps(corpo, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------- sincronização

def cabecalhos_condicionais(meta: dict | None) -> dict:
    headers: dict[str, str] = {}
    if not meta:
        return headers
    etag = str(meta.get("etag") or "").strip()
    if etag.startswith("W/"):
        etag = etag[2:]
    etag = re.sub(r'-gzip"$', '"', etag)
    if etag:
        headers["If-None-Match"] = etag
    if meta.get("last_modified"):
        headers["If-Modified-Since"] = str(meta["last_modified"])
    return headers


def _data_base(last_modified: str | None) -> str | None:
    if not last_modified:
        return None
    try:
        return parsedate_to_datetime(last_modified).astimezone(BRT).date().isoformat()
    except (TypeError, ValueError):
        return None


def _caminhos(base: str | os.PathLike | None) -> tuple[Path, Path]:
    d = cache_dir(base) / "conab"
    return d / "armazens.json", d / "armazens.meta.json"


def sync_conab_warehouses(base_dir: str | os.PathLike | None = None, *, http_get: HttpGet | None = None,
                          agora: float | None = None, forcar: bool = False, min_linhas: int | None = None) -> dict:
    """Atualiza a cópia local do arquivo da CONAB. Nunca apaga a última cópia boa."""
    min_linhas = MIN_LINHAS if min_linhas is None else min_linhas
    arq_base, arq_meta = _caminhos(base_dir)
    now = time.time() if agora is None else agora
    if not _SYNC_LOCK.acquire(blocking=False):
        meta = _ler_json(arq_meta) or {}
        if arq_base.is_file() and meta:
            # outra linha de execução já está baixando: responde com a cópia boa, sem fila
            return {"estado": "em_andamento", "tem_base": True, "meta": meta}
        _SYNC_LOCK.acquire()  # sem cópia nenhuma: esperar o download em curso é melhor que pendente
    try:
        return _sincronizar(arq_base, arq_meta, now, http_get, forcar, min_linhas)
    finally:
        _SYNC_LOCK.release()


def falha_recente(chave: str, now: float, tem_base: bool) -> bool:
    falhou = _FALHAS.get(chave)
    espera = SYNC_NOVA_TENTATIVA_S if tem_base else SYNC_NOVA_TENTATIVA_SEM_BASE_S
    return falhou is not None and 0 <= now - falhou < espera


def _sincronizar(arq_base: Path, arq_meta: Path, now: float, http_get: HttpGet | None, forcar: bool, min_linhas: int) -> dict:
    chave = str(arq_base)
    meta = _ler_json(arq_meta) or {}
    tem_base = arq_base.is_file() and bool(meta)
    if tem_base and not forcar and now - float(meta.get("conferido_em") or 0) < SYNC_TTL_S:
        return {"estado": "fresco", "tem_base": True, "meta": meta}
    if not forcar and falha_recente(chave, now, tem_base):
        return {"estado": "falhou_recentemente", "tem_base": tem_base, "meta": meta}

    def falhou(estado: str, erro: str) -> dict:
        _FALHAS[chave] = now
        return {"estado": estado, "tem_base": tem_base, "meta": meta, "erro": erro}

    try:
        resp = (http_get or _http_get)(URL_ARMAZENS, headers=cabecalhos_condicionais(meta if tem_base else None))
    except Exception as exc:  # rede: mantém a cópia anterior
        return falhou("falhou", type(exc).__name__)
    status = int(getattr(resp, "status_code", 0) or 0)
    if status == 304 and tem_base:
        _FALHAS.pop(chave, None)
        meta["conferido_em"] = now
        _escrever_atomico(arq_meta, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        return {"estado": "sem_mudanca", "tem_base": True, "meta": meta}
    if status != 200:
        return falhou("falhou", f"http_{status}")
    try:
        linhas, stats = ler_arquivo_armazens(resp.content)
    except ValueError as exc:
        return falhou("invalido", str(exc))
    anterior = int(meta.get("linhas") or 0) if tem_base else 0
    if len(linhas) < min_linhas or (anterior and len(linhas) < anterior / 2):
        return falhou("invalido", "linhas_insuficientes")
    headers = getattr(resp, "headers", {}) or {}
    novo = {
        "etag": headers.get("etag"),
        "last_modified": headers.get("last-modified"),
        "data_base": _data_base(headers.get("last-modified")),
        "baixado_em": now,
        "conferido_em": now,
        **stats,
    }
    _escrever_atomico(arq_base, _serializar_base(linhas, novo))
    _escrever_atomico(arq_meta, json.dumps(novo, ensure_ascii=False).encode("utf-8"))
    _FALHAS.pop(chave, None)
    return {"estado": "baixado", "tem_base": True, "meta": novo}


def aquecer_em_segundo_plano(base_dir: str | os.PathLike | None = None, *, http_get: HttpGet | None = None) -> threading.Thread:
    """Baixa e lê a base numa linha de execução própria (chamar no arranque do serviço)."""
    def trabalho() -> None:
        try:
            sync_conab_warehouses(base_dir, http_get=http_get)
            load_conab_warehouses(base_dir)
        except Exception:
            pass  # o relatório tenta de novo; falha aqui nunca derruba o arranque

    t = threading.Thread(target=trabalho, name="conab-aquecer", daemon=True)
    t.start()
    return t


def load_conab_warehouses(base_dir: str | os.PathLike | None = None) -> tuple[list[dict] | None, dict]:
    """Lê a base do disco uma vez e mantém em memória até o arquivo mudar."""
    arq_base, _ = _caminhos(base_dir)
    try:
        st = arq_base.stat()
    except OSError:
        return None, {}
    assinatura = (str(arq_base), st.st_mtime_ns, st.st_size)
    with _MEM_LOCK:
        if _MEMORIA.get("assinatura") == assinatura:
            return _MEMORIA["linhas"], _MEMORIA["meta"]
        corpo = _ler_json(arq_base)
        if not corpo or corpo.get("formato") != FORMATO_CACHE:
            return None, {}
        campos = corpo.get("campos") or list(_CAMPOS_CACHE)
        pool: dict[str, str] = {}  # tipo, entidade, município e UF se repetem: uma cópia de cada texto
        linhas = [dict(zip(campos, (pool.setdefault(v, v) if isinstance(v, str) and k != "cda" else v for k, v in zip(campos, r))))
                  for r in corpo.get("linhas") or []]
        _MEMORIA.update(assinatura=assinatura, linhas=linhas, meta=corpo.get("meta") or {})
        return linhas, _MEMORIA["meta"]


def fetch_municipality_mesh(codigo: str, base_dir: str | os.PathLike | None = None, *,
                            http_get: HttpGet | None = None, agora: float | None = None) -> dict | None:
    """Malha do município no IBGE (qualidade intermediária), com cópia em disco."""
    codigo = re.sub(r"\D", "", str(codigo or ""))
    if len(codigo) != 7:
        return None
    arq = cache_dir(base_dir) / "ibge_malhas" / f"{codigo}.json"
    now = time.time() if agora is None else agora
    salvo = _ler_json(arq)
    if salvo and now - float(salvo.get("baixado_em") or 0) < MALHA_TTL_S:
        return salvo.get("geometry")
    chave = f"malha:{arq}"
    if falha_recente(chave, now, True):
        # IBGE caiu há pouco: não espera de novo o tempo limite; sem cópia o armazém fica sem conferência
        return salvo.get("geometry") if salvo else None
    try:
        resp = (http_get or _http_get)(URL_MALHA_IBGE.format(codigo=codigo), params=dict(PARAMS_MALHA_IBGE))
        if int(getattr(resp, "status_code", 0) or 0) != 200:
            raise ValueError("http")
        dados = json.loads(resp.content)
        feats = [f for f in dados.get("features") or [] if str((f.get("properties") or {}).get("codarea")) == codigo]
        if len(feats) != 1 or not feats[0].get("geometry"):
            raise ValueError("malha_inesperada")
        geom = feats[0]["geometry"]
        shape(geom)  # valida
    except Exception:
        _FALHAS[chave] = now
        return salvo.get("geometry") if salvo else None
    _FALHAS.pop(chave, None)
    try:
        _escrever_atomico(arq, json.dumps({"codigo": codigo, "baixado_em": now, "geometry": geom}).encode("utf-8"))
    except OSError:
        pass  # disco sem gravação: a malha vale para esta consulta e é baixada de novo na próxima
    return geom


# ---------------------------------------------------------------- consulta (pura)

def _aeqd(lon: float, lat: float) -> tuple[Callable, Callable]:
    crs = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +ellps=GRS80 +units=m +no_defs"
    ida = Transformer.from_crs("EPSG:4674", crs, always_xy=True).transform
    volta = Transformer.from_crs(crs, "EPSG:4674", always_xy=True).transform
    return ida, volta


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, h)))


def distance_to_edge_km(geometria_car: Any, lat: float, lon: float) -> float:
    """Distância geodésica GRS80 do ponto até a borda do polígono (0 dentro dele)."""
    geom = _geometria(geometria_car)
    c = geom.centroid
    ida, volta = _aeqd(c.x, c.y)
    geom_m = transform(ida, geom)
    p_m = Point(ida(lon, lat))
    if geom_m.contains(p_m):
        return 0.0
    q_m = nearest_points(geom_m, p_m)[0]
    qlon, qlat = volta(q_m.x, q_m.y)
    return abs(_GEOD.inv(lon, lat, qlon, qlat)[2]) / 1000.0


def candidates_in_radius(linhas: Iterable[dict], geometria_car: Any, raio_km: float = RAIO_PADRAO_KM) -> list[dict]:
    geom = _geometria(geometria_car)
    c = geom.centroid
    cx, cy = c.x, c.y
    ida, volta = _aeqd(cx, cy)
    geom_m = transform(ida, geom)
    raio_poligono_km = geom_m.hausdorff_distance(Point(0, 0)) / 1000.0
    limite = raio_km + raio_poligono_km + 1.0
    dlat = limite / 110.0
    saida = []
    for r in linhas:
        lat = r.get("lat")
        if lat is None or abs(lat - cy) > dlat:
            continue
        lon = r.get("lon")
        if lon is None or _haversine_km(cy, cx, lat, lon) > limite:
            continue
        p_m = Point(ida(lon, lat))
        if geom_m.contains(p_m):
            dist = 0.0
        else:
            q_m = nearest_points(geom_m, p_m)[0]
            qlon, qlat = volta(q_m.x, q_m.y)
            dist = abs(_GEOD.inv(lon, lat, qlon, qlat)[2]) / 1000.0
        if dist <= raio_km:
            saida.append({**r, "distancia_km": dist})
    saida.sort(key=lambda x: (x["distancia_km"], x["cda"]))
    return saida


def point_matches_municipality(lat: float, lon: float, malha: dict | None, tolerancia_km: float = TOLERANCIA_MUNICIPIO_KM) -> bool | None:
    """True/False se o ponto cai no município declarado (com tolerância); None sem malha."""
    if not malha:
        return None
    geom = shape(malha)
    ida, _ = _aeqd(lon, lat)
    return transform(ida, geom).distance(Point(0, 0)) <= tolerancia_km * 1000.0


def query_conab_warehouses(linhas: Iterable[dict], geometria_car: Any, malhas: dict[str, dict | None],
                           raio_km: float = RAIO_PADRAO_KM, candidatos: list[dict] | None = None) -> dict:
    """Consulta pura: armazéns no raio com ponto conferido no município declarado."""
    cands = candidates_in_radius(linhas, geometria_car, raio_km) if candidatos is None else candidatos
    itens, excluidos, sem_conferencia = [], [], []
    for c in cands:
        ok = point_matches_municipality(c["lat"], c["lon"], malhas.get(c.get("cod_ibge") or ""))
        if ok is True:
            itens.append(c)
        elif ok is False:
            excluidos.append(c["cda"])
        else:
            sem_conferencia.append(c["cda"])
    return {"raio_km": raio_km, "itens": itens, "excluidos_localizacao": excluidos, "sem_conferencia": sem_conferencia,
            "candidatos": len(cands)}


# ---------------------------------------------------------------- payload

def _texto_num(v: float, casas: int) -> str:
    return format_decimal(v, casas)


def _data_br(iso: str | None) -> str | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else None


def _rotulo(item: dict) -> str:
    if item.get("nome"):
        return item["nome"]
    base = item.get("tipo") or item.get("especie") or "Armazém"
    ent = item.get("entidade")
    return f"{base} ({ent.lower()})" if ent else base


def _capacidade_texto(v: float | None) -> str | None:
    if v is None or v <= 0:
        return None
    casas = 0 if float(v).is_integer() else 1
    # "toneladas" por extenso: "15.930 t" seria reescrito para "15,93 t" pelo
    # normalizador do PDF (report_ptbr_v50); o gate confere que o texto sobrevive.
    return f"{_texto_num(v, casas)} toneladas"


def build_warehouses_payload(resultado: dict | None, meta: dict | None, *, pendente: bool = False) -> dict:
    meta = meta or {}
    raio = float((resultado or {}).get("raio_km") or RAIO_PADRAO_KM)
    raio_txt = _texto_num(raio, 0 if raio.is_integer() else 1)
    data_base = meta.get("data_base")
    data_txt = _data_br(data_base)
    fonte_txt = f"{FONTE}, base de {data_txt}" if data_txt else FONTE
    base = {"source": "conab_armazens", "radius_km": raio, "base_date": data_base, "base_date_text": data_txt,
            "source_text": fonte_txt}
    if pendente or resultado is None:
        return {**base, "state": "pending", "complete": False, "items": [], "table_rows": [], "text": "Consulta pendente.",
                "audit": {}}
    itens = []
    for c in resultado["itens"]:
        dist_txt = f"{_texto_num(round(c['distancia_km'], 1), 1)} km"
        muni = f"{c['municipio']}/{c['uf']}" if c.get("municipio") and c.get("uf") else c.get("municipio")
        item = {
            "cda": c["cda"], "name": c.get("nome"), "label": _rotulo(c), "type": c.get("tipo"), "species": c.get("especie"),
            "entity": c.get("entidade"), "municipality_uf": muni, "capacity_t": c.get("capacidade_t"),
            "capacity_text": _capacidade_texto(c.get("capacidade_t")), "distance_km": round(c["distancia_km"], 3),
            "distance_text": dist_txt,
        }
        itens.append({k: v for k, v in item.items() if v is not None})
    excl, sem = resultado["excluidos_localizacao"], resultado["sem_conferencia"]
    completa = not excl and not sem
    n = len(itens)
    if n:
        estado = "found"
        palavra = "armazém cadastrado" if n == 1 else "armazéns cadastrados"
        prefixo = "" if completa else "Pelo menos "
        if sem:
            # armazém sem localização conferida pode ser o mais próximo: o texto não afirma distância
            texto = f"{prefixo}{n} {palavra} na CONAB em até {raio_txt} km do imóvel."
        elif n == 1:
            texto = f"{prefixo}1 {palavra} na CONAB em até {raio_txt} km do imóvel, a {itens[0]['distance_text']}."
        else:
            texto = f"{prefixo}{n} {palavra} na CONAB em até {raio_txt} km do imóvel; o mais próximo fica a {itens[0]['distance_text']}."
        texto = texto[:1].upper() + texto[1:]
    elif sem:
        return {**base, "state": "pending", "complete": False, "items": [], "table_rows": [], "text": "Consulta pendente.",
                "audit": {"candidates": resultado["candidatos"], "location_unverified": len(sem), "location_mismatch": len(excl)}}
    else:
        estado = "not_found"
        conferida = " com localização conferida" if excl else ""
        texto = f"Nenhum armazém cadastrado na CONAB{conferida} em até {raio_txt} km do imóvel."
    return {
        **base, "state": estado, "complete": completa, "items": itens,
        "table_rows": [[i["label"], i.get("municipality_uf") or "", i.get("capacity_text") or "", i["distance_text"]] for i in itens],
        "text": texto,
        "audit": {"candidates": resultado["candidatos"], "location_mismatch": len(excl), "location_unverified": len(sem)},
    }


def conab_warehouses_payload(geometria_car: Any, *, raio_km: float = RAIO_PADRAO_KM, base_dir: str | os.PathLike | None = None,
                             http_get: HttpGet | None = None, agora: float | None = None) -> dict:
    """Payload do relatório: sincroniza (barato), consulta e confere a localização."""
    sync = sync_conab_warehouses(base_dir, http_get=http_get, agora=agora)
    linhas, meta = load_conab_warehouses(base_dir)
    if not linhas:
        out = build_warehouses_payload(None, meta, pendente=True)
        out["audit"] = {"sync": sync.get("estado")}
        return out
    cands = candidates_in_radius(linhas, geometria_car, raio_km)
    codigos = sorted({c["cod_ibge"] for c in cands if c.get("cod_ibge")})
    malhas: dict[str, dict | None] = {}
    if codigos:
        with ThreadPoolExecutor(max_workers=min(6, len(codigos))) as pool:
            for codigo, malha in zip(codigos, pool.map(in_current_scope(
                    lambda k: fetch_municipality_mesh(k, base_dir, http_get=http_get, agora=agora)), codigos)):
                malhas[codigo] = malha
    resultado = query_conab_warehouses(linhas, geometria_car, malhas, raio_km, candidatos=cands)
    out = build_warehouses_payload(resultado, meta)
    out["audit"]["sync"] = sync.get("estado")
    return out
