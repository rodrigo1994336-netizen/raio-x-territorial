"""`payload["book_v10"]`: só o que o livro precisa para DESENHAR (SPEC §9.3).

Nada daqui vira afirmação ao cliente: os números do texto saem dos Fatos
(``report_book_v10_fatos``). Aqui mora a geometria projetada em metros, a caixa do mapa, a imagem
de satélite limpa e o recorte das manchas do PRODES.

Decisões desta parte:

- **Projeção métrica por imóvel.** O limite do CAR vem em graus; o mapa do livro mede em metros.
  A zona UTM sai do centro do imóvel (SIRGAS 2000), e todo desenho usa a mesma zona, para que
  escala, barra de escala e largura de faixa fiquem certas em qualquer canto do país.
- **Imagem sem nada escrito** (regra 5 do dono). A base é o mosaico público de alta resolução da
  Esri (World Imagery), pedido já na projeção métrica do imóvel, sem camada de rótulo. As imagens
  que o relatório de hoje grava têm texto e o limite do CAR desenhados por cima: não servem ao
  livro, que desenha o limite por conta própria.
- **Sem data inventada.** O mosaico da Esri não publica, nesta chamada, a data da cena. A legenda
  então não traz data — em vez de trazer uma data errada. Quando o Sentinel-2 responde, a data e a
  nuvem do dia vão para o apêndice pelo Fato ``sentinel``, que tem data de verdade.
- **Falhou a imagem, o livro continua.** Sem imagem, ``base_image.ok`` é falso e a p. 1 desenha a
  planta do imóvel (§2.0.1). Nenhuma página depende de a imagem existir.

Dependências: ``pyproj`` e ``shapely``, que o serviço já usa. A imagem usa só ``urllib`` da
biblioteca padrão. Nenhuma fonte paga, nenhuma chave de acesso.
"""
from __future__ import annotations

import io
import math
import os
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from report_book_v10_contracts import (
    BOOK_V10_CAIXA_VIZINHANCA_MIN_M,
    BOOK_V10_RECORTE_MEDIO_MIN_M,
    BOOK_V10_SCHEMA,
)

ESRI_EXPORT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
ESRI_CREDITO = "Esri, Maxar, Earthstar Geographics e a comunidade de usuários do GIS"
ESRI_TIMEOUT_S = float(os.getenv("RX_LIVRO_IMAGEM_TIMEOUT_S", "9"))
ESRI_LADO_PX = 1100          # medido: 1100 px responde em ~3 s; 1500 px chega a passar de 30 s
IMAGEM_MIN_BYTES = 15_000    # abaixo disto o serviço devolveu um quadrado liso, não uma cena

# SIRGAS 2000 / UTM: zonas 11N–22N (EPSG 31965–31976) e 11S–25S (EPSG 31977–31985 a partir de 17S).
_EPSG_SUL = {17: 31977, 18: 31978, 19: 31979, 20: 31980, 21: 31981, 22: 31982, 23: 31983,
             24: 31984, 25: 31985}
_EPSG_NORTE = {18: 31972, 19: 31973, 20: 31974, 21: 31975, 22: 31976}


def zona_utm(lon: float, lat: float) -> int:
    """EPSG da zona UTM SIRGAS 2000 do ponto. Fora das zonas do Brasil, cai na mais perto."""
    zona = int(math.floor((lon + 180) / 6) + 1)
    tabela = _EPSG_NORTE if lat >= 0 else _EPSG_SUL
    if zona in tabela:
        return tabela[zona]
    mais_perto = min(tabela, key=lambda z: abs(z - zona))
    return tabela[mais_perto]


def _poligonos(geom: Any):
    from shapely.geometry import shape
    g = shape(geom)
    if not g.is_valid:
        g = g.buffer(0)
    return g


def _geojson(g) -> dict:
    from shapely.geometry import mapping
    return mapping(g)


def _lado_menor(g) -> float:
    """Menor lado da caixa do imóvel, em metros: decide o enquadramento da p. 1."""
    minx, miny, maxx, maxy = g.bounds
    return min(maxx - minx, maxy - miny)


# Imagem já baixada nesta emissão, pela chave do pedido (mesma caixa, mesma projeção, mesmo tamanho).
# Serve para o adiantamento: quem busca antes deixa pronto para quem desenha depois.
_CACHE_IMAGEM: dict[str, bytes] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 4


def _baixar(url: str) -> bytes | None:
    with _CACHE_LOCK:
        pronto = _CACHE_IMAGEM.get(url)
    if pronto is not None:
        return pronto
    req = urllib.request.Request(url, headers={"User-Agent": "Raio-X-Territorial/livro-v10"})
    try:
        with urllib.request.urlopen(req, timeout=ESRI_TIMEOUT_S) as r:
            if r.status != 200:
                return None
            tipo = (r.headers.get("content-type") or "").lower()
            dados = r.read()
    except Exception:
        return None
    if "image" not in tipo or len(dados) < IMAGEM_MIN_BYTES:
        return None
    with _CACHE_LOCK:
        if len(_CACHE_IMAGEM) >= _CACHE_MAX:
            _CACHE_IMAGEM.clear()        # instância pequena: a lembrança não pode crescer sozinha
        _CACHE_IMAGEM[url] = dados
    return dados


def adiantar_imagem(payload: dict, result: dict | None) -> None:
    """Busca a imagem da p. 1 em segundo plano, assim que o limite do CAR já é conhecido.

    O desenho do livro acontece no fim da emissão; a imagem não depende de nada que venha depois.
    Buscar antes tira o tempo dela do caminho do cliente. Falha aqui não faz nada: quem desenha
    tenta de novo e, sem imagem, desenha a planta do imóvel.
    """
    def tarefa():
        try:
            montar_book_v10(dict(payload), result, Path(tempfile.gettempdir()) / "rx_livro_adiantado",
                            com_imagem=True)
        except Exception:
            pass

    threading.Thread(target=tarefa, name="rx-livro-imagem", daemon=True).start()


def baixar_imagem(caixa_m: tuple[float, float, float, float], epsg: int, destino,
                  lado_px: int = ESRI_LADO_PX) -> dict | None:
    """Recorte do mosaico de alta resolução, já na projeção métrica do imóvel e sem rótulo.

    ``caixa_m`` entra e sai igual: a imagem cobre exatamente essa caixa, e é isso que deixa o
    limite do CAR cair no lugar certo sobre a foto. Devolve None quando a fonte não responde.
    """
    largura_m = caixa_m[2] - caixa_m[0]
    altura_m = caixa_m[3] - caixa_m[1]
    if largura_m <= 0 or altura_m <= 0:
        return None
    if largura_m >= altura_m:
        px_l = lado_px
        px_a = max(1, round(lado_px * altura_m / largura_m))
    else:
        px_a = lado_px
        px_l = max(1, round(lado_px * largura_m / altura_m))
    params = {
        "bbox": ",".join(f"{v:.3f}" for v in caixa_m),
        "bboxSR": str(epsg), "imageSR": str(epsg),
        "size": f"{px_l},{px_a}", "format": "jpg", "transparent": "false", "f": "image",
    }
    inicio = time.monotonic()
    url = ESRI_EXPORT + "?" + urllib.parse.urlencode(params)
    # Reaproveita o arquivo já baixado SÓ quando o pedido é idêntico (mesma caixa, mesma projeção,
    # mesmo tamanho). Chave diferente baixa de novo: imagem antiga no lugar errado seria dado errado.
    marca = destino.with_suffix(".pedido.txt")
    if destino.is_file() and marca.is_file() and destino.stat().st_size >= IMAGEM_MIN_BYTES:
        try:
            if marca.read_text(encoding="utf-8").strip() == url:
                print("RX_BOOK_IMAGEM=reaproveitada", flush=True)
                return _descricao(destino, caixa_m, epsg, px_l, px_a, largura_m)
        except OSError:
            pass
    dados = _baixar(url)
    if dados is None:
        # Regra do dono: a fonte que não respondeu tenta de novo sozinha antes de desistir. A segunda
        # tentativa pede uma imagem menor: quando o serviço está devagar, o recorte menor costuma vir,
        # e uma foto com menos pontos é melhor que nenhuma foto.
        px_l, px_a = max(400, round(px_l * 0.6)), max(400, round(px_a * 0.6))
        params["size"] = f"{px_l},{px_a}"
        url = ESRI_EXPORT + "?" + urllib.parse.urlencode(params)
        dados = _baixar(url)
    if dados is None:
        print(f"RX_BOOK_IMAGEM=falhou:{round((time.monotonic() - inicio) * 1000)}ms", flush=True)
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)
    try:
        marca.write_text(url, encoding="utf-8")
    except OSError:
        pass
    print(f"RX_BOOK_IMAGEM=ok:{round((time.monotonic() - inicio) * 1000)}ms:{len(dados) // 1024}KB", flush=True)
    return _descricao(destino, caixa_m, epsg, px_l, px_a, largura_m)


def _descricao(destino, caixa_m, epsg: int, px_l: int, px_a: int, largura_m: float) -> dict:
    return {"ok": True, "kind": "esri_tiles", "path": str(destino), "crs": f"EPSG:{epsg}",
            "bounds_m": [round(v, 2) for v in caixa_m], "px": [px_l, px_a],
            "box_side_m": round(largura_m, 1), "date": None,
            "resolution_m": round(largura_m / px_l, 2),
            "attribution": ESRI_CREDITO}


def esmaecer(origem, destino, forca: float = 0.42) -> str | None:
    """Versão clara da mesma imagem, para o mapa das manchas ficar legível por cima dela."""
    try:
        from PIL import Image, ImageEnhance
    except Exception:
        return None
    try:
        img = Image.open(origem).convert("RGB")
        img = ImageEnhance.Color(img).enhance(0.35)
        img = ImageEnhance.Brightness(img).enhance(1 + forca)
        img = ImageEnhance.Contrast(img).enhance(0.72)
        img.save(destino, "JPEG", quality=82, optimize=True)
    except Exception:
        return None
    return str(destino)


def _caixa(centro: tuple[float, float], lado_m: float) -> tuple[float, float, float, float]:
    meio = lado_m / 2
    return (centro[0] - meio, centro[1] - meio, centro[0] + meio, centro[1] + meio)


def _figuras_prodes(result: dict, transformar, fatos: dict | None, car_m=None) -> dict:
    """Recorte de cada mancha do PRODES na projeção do imóvel, pela chave do Fato (id curto).

    Duas geometrias por mancha: a mancha inteira (desenhada tracejada, para mostrar que ela segue
    fora do imóvel) e a parte dentro do limite do CAR (preenchida). A parte de dentro é recortada
    aqui com a mesma interseção exata que o relatório usa para medir.
    """
    prodes = (result or {}).get("prodes")
    if not isinstance(prodes, dict):
        return {}
    quero: set[str] = set()
    e = (fatos or {}).get("prodes") or {}
    for o in ((e.get("valor") or {}).get("occurrences") or []):
        if isinstance(o, dict) and o.get("id"):
            quero.add(str(o["id"]))
    if not quero:
        return {}
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    figuras: dict[str, dict] = {}
    for hit in prodes.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        for feicao in hit.get("features") or []:
            if not isinstance(feicao, dict):
                continue
            curto = str(feicao.get("id") or "").split(".", 1)[-1]
            if curto not in quero or curto in figuras:
                continue
            try:
                g = shape(feicao.get("geometry"))
                if not g.is_valid:
                    g = g.buffer(0)
                projetada = shp_transform(transformar, g).simplify(0.5, preserve_topology=True)
                if not projetada.is_valid:
                    projetada = projetada.buffer(0)
            except Exception:
                continue
            if projetada.is_empty:
                continue
            figura = {"feature_geom_m": _geojson(projetada)}
            if car_m is not None:
                try:
                    dentro = projetada.intersection(car_m)
                    if not dentro.is_empty:
                        figura["inside_geom_m"] = _geojson(dentro)
                except Exception:
                    pass
            figura.setdefault("inside_geom_m", figura["feature_geom_m"])
            figuras[curto] = figura
    return figuras


def modulo_fiscal(payload: dict) -> dict | None:
    """Hectares de 1 módulo fiscal, pela conta do próprio CAR (área ÷ módulos declarados)."""
    car = payload.get("car") or {}
    prop = payload.get("property") or {}
    try:
        modulos = float(car.get("fiscal_modules"))
        area = float(prop.get("area_ha"))
    except (TypeError, ValueError):
        return None
    if modulos <= 0 or area <= 0:
        return None
    return {"value": round(area / modulos, 4), "origin": "conta_car"}


def montar_book_v10(payload: dict, result: dict | None, pasta, *, fatos: dict | None = None,
                    com_imagem: bool = True) -> dict:
    """Monta ``payload["book_v10"]``. Nunca levanta: o que falhar vira ``erros`` e o livro adapta."""
    erros: list[str] = []
    saida: dict[str, Any] = {"schema": BOOK_V10_SCHEMA, "geometry": None, "base_image": {"ok": False},
                             "sentinel_image": None, "ndvi_image": None, "prodes_figuras": {},
                             "achados_figuras": {}, "fiscal_module_ha": modulo_fiscal(payload),
                             "erros": erros}
    car = (result or {}).get("car") or {}
    geom = car.get("geometry") if isinstance(car, dict) else None
    if not geom:
        erros.append("sem o limite do CAR: o livro sai sem mapa")
        return saida
    try:
        from pyproj import Transformer
        from shapely.ops import transform as shp_transform
        g_graus = _poligonos(geom)
        centro = g_graus.representative_point()
        epsg = zona_utm(centro.x, centro.y)
        para_metros = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
        g_m = shp_transform(para_metros, g_graus)
        simples = g_m.simplify(1.0, preserve_topology=True)
        if simples.is_empty:
            simples = g_m
    except Exception as exc:
        erros.append(f"projeção do limite do CAR falhou: {type(exc).__name__}")
        return saida
    minx, miny, maxx, maxy = simples.bounds
    saida["geometry"] = {
        "crs": f"EPSG:{epsg}", "car_m": _geojson(simples),
        "bbox_m": [round(v, 2) for v in (minx, miny, maxx, maxy)],
        "centroid_lonlat": [round(centro.x, 6), round(centro.y, 6)],
        "short_side_m": round(_lado_menor(simples), 1),
        "area_ha": round(simples.area / 10_000, 4),
        "uf": str((payload.get("property") or {}).get("uf") or "").upper(),
    }
    saida["prodes_figuras"] = _figuras_prodes(result or {}, para_metros, fatos, simples)
    if not com_imagem:
        return saida
    meio = ((minx + maxx) / 2, (miny + maxy) / 2)
    maior_lado = max(maxx - minx, maxy - miny)
    # vizinhança: o imóvel ocupa ~45% da largura do quadro, com o piso do contrato
    lado_vizinhanca = max(BOOK_V10_CAIXA_VIZINHANCA_MIN_M, maior_lado / 0.45)
    base = baixar_imagem(_caixa(meio, lado_vizinhanca), epsg, pasta / "livro_base.jpg")
    if base is None:
        erros.append("a imagem de satélite não respondeu nesta emissão")
        return saida
    base["enquadramento"] = "vizinhanca"
    base["legenda_sem_data"] = True
    muted = esmaecer(pasta / "livro_base.jpg", pasta / "livro_base_muted.jpg")
    if muted:
        base["path_muted"] = muted
    saida["base_image"] = base
    # O recorte médio sai da MESMA imagem, cortado: uma chamada só à fonte por relatório. Pedir
    # duas custava ~14 s e desenhava a mesma cena duas vezes.
    lado_medio = max(BOOK_V10_RECORTE_MEDIO_MIN_M, maior_lado * 1.15)
    saida["sentinel_image"] = _recorte(base, _caixa(meio, min(lado_medio, lado_vizinhanca)),
                                       pasta / "livro_medio.jpg") or dict(base, enquadramento="medio")
    return saida


def _recorte(base: dict, caixa_m: tuple[float, float, float, float], destino) -> dict | None:
    """Corta a caixa pedida da imagem já baixada, sem nova chamada à fonte."""
    try:
        from PIL import Image
    except Exception:
        return None
    limites = base.get("bounds_m") or []
    if len(limites) != 4:
        return None
    try:
        img = Image.open(base["path"]).convert("RGB")
        sx = img.width / (limites[2] - limites[0])
        sy = img.height / (limites[3] - limites[1])
        janela = (max(0, round((caixa_m[0] - limites[0]) * sx)),
                  max(0, round((limites[3] - caixa_m[3]) * sy)),
                  min(img.width, round((caixa_m[2] - limites[0]) * sx)),
                  min(img.height, round((limites[3] - caixa_m[1]) * sy)))
        if janela[2] - janela[0] < 80 or janela[3] - janela[1] < 80:
            return None
        img.crop(janela).save(destino, "JPEG", quality=88, optimize=True)
    except Exception:
        return None
    largura_m = caixa_m[2] - caixa_m[0]
    px_l = janela[2] - janela[0]
    return {"ok": True, "kind": "esri_tiles", "enquadramento": "medio", "path": str(destino),
            "crs": base.get("crs"), "bounds_m": [round(v, 2) for v in caixa_m],
            "px": [px_l, janela[3] - janela[1]], "box_side_m": round(largura_m, 1), "date": None,
            "resolution_m": round(largura_m / px_l, 2), "attribution": base.get("attribution")}


print("RX_BOOK_DADOS=geometria_e_imagem_livre", flush=True)
