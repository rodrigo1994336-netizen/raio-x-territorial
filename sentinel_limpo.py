"""Foto de satélite limpa, com data: recorte Sentinel-2 em cor natural e em verdor.

Este módulo é a FONTE da imagem. Ele não escreve nada sobre a imagem: nem data, nem
rótulo, nem limite do imóvel, nem crédito. Quem desenha por cima é o relatório
(``foto_limpa_no_relatorio``), com o limite do CAR e a legenda fora do recorte — a regra
do dono é que nada aparece escrito sobre o mapa, e um pixel de texto gravado na fonte não
tem mais volta.

Por que isso existe: o caminho antigo (``sentinel_cog``) grava faixa de texto e o contorno
amarelo dentro do JPEG, o que serve à tela e não serve ao relatório impresso. Por isso o
relatório-livro estava usando mosaico da Esri, que entrega imagem bonita e NÃO publica a
data da cena. Aqui a data e a nuvem vêm do metadado da própria cena, e voltam no resultado
para quem for imprimir.

Fonte, de graça e sem cadastro: catálogo STAC do Earth Search (Element 84) sobre o acervo
Sentinel-2 L2A em nuvem otimizada (COG) do AWS Open Data. Mesmo caminho que o projeto já
usa em produção — leitura por faixa de bytes, só o recorte do imóvel desce pela rede.
Nenhuma fonte paga, nenhuma chave, nenhuma conta nova entra aqui.

Honestidade sobre a cena: a mais recente que presta pode ser antiga ou nublada. O
resultado sempre diz a data, a nuvem e a idade; e marca ``cena_antiga`` e ``cena_nublada``
para o relatório avisar em vez de esconder. Cena nenhuma não vira imagem vazia: vira
consulta pendente.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image
from pyproj import Transformer
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

# A leitura do COG, a escolha do ativo e o esticamento de contraste têm um dono só: o
# módulo que já está em produção. Duas cópias da mesma leitura é como as duas imagens do
# mesmo imóvel passam a não combinar.
from sentinel_cog import EARTH_SEARCH, _asset, _read

JANELA_DIAS = 420
NUVEM_MAXIMA = 45.0
# Degraus de tolerância: primeiro a mais recente entre as limpas; só depois afrouxa.
DEGRAUS_DE_NUVEM = (12.0, 25.0, NUVEM_MAXIMA)
CENA_ANTIGA_DIAS = 150
CENA_NUBLADA_PCT = 12.0
CANDIDATOS = 2
LARGURA_PADRAO = 1200
PRAZO_BUSCA_S = 12
PRAZO_LEITURA_S = 14

FONTE = ("Copernicus Sentinel-2 L2A (ESA) — recorte lido direto do acervo aberto no AWS "
         "Open Data, pelo catálogo do Earth Search (Element 84)")
RESOLUCAO_M = 10

SEM_LEITURA = (140, 148, 144)
# Classes de verdor, honestas de propósito: faixa declarada, cor e o que ela quer dizer.
# Verdor mede quanto a folhagem reflete na cena; não mede produtividade, espécie nem safra.
CLASSES_DE_VERDOR: tuple[tuple[float, float, tuple[int, int, int], str], ...] = (
    (-1.0, 0.15, (169, 130, 90), "solo exposto, água, construção ou palhada seca"),
    (0.15, 0.30, (203, 191, 114), "vegetação muito rala"),
    (0.30, 0.45, (168, 192, 90), "vegetação rala"),
    (0.45, 0.60, (111, 164, 70), "vegetação média"),
    (0.60, 0.75, (61, 130, 54), "vegetação densa"),
    (0.75, 1.0, (31, 95, 43), "vegetação muito densa"),
)
LEGENDA_NOTA = ("Verdor medido na cena de {data}: mede quanto a folhagem reflete naquele dia. "
                "Não mede produtividade, não diz a espécie e não confirma safra.")


ESCALA_MAXIMA = 2.2  # não ampliar a cena mais do que isso: 10 m por pixel é 10 m por pixel
LARGURA_MINIMA = 560
# Folga em volta do imóvel. 34% de cada lado dá um quadro de 1,68x o lado maior: cobre o
# enquadramento que o relatório-livro pede para a figura do imóvel (imóvel em ~62% da
# largura, ou seja 1,61x) com margem. Recorte mais apertado do que o quadro pedido faria o
# livro cortar fora da imagem.
FOLGA = 0.34
ENQUADRAMENTO_COBERTO = 1 / (1 + 2 * FOLGA)


def esticar_cor_natural(vermelho, verde, azul, valido):
    """Contraste das três bandas pelo MESMO corte, para a cor da terra sair verdadeira.

    Esticar cada banda sozinha (um corte para o vermelho, outro para o verde, outro para o
    azul) é o que deixa pasto roxo e solo rosa: cada banda vira "a mais clara de si mesma"
    e a relação entre elas, que é a cor, se perde. O corte aqui é um só, tirado dos pixels
    válidos das três juntas.
    """
    juntas = np.concatenate([x[valido & np.isfinite(x) & (x > 0)] for x in (vermelho, verde, azul)])
    if juntas.size < 30:
        return np.zeros(vermelho.shape + (3,), dtype="uint8")
    baixo = float(np.percentile(juntas, 2))
    alto = float(np.percentile(juntas, 98))
    if alto <= baixo:
        alto = baixo + 1
    saida = np.zeros(vermelho.shape + (3,), dtype="uint8")
    for indice, banda in enumerate((vermelho, verde, azul)):
        x = np.clip((banda - baixo) / (alto - baixo), 0, 1)
        saida[..., indice] = (np.power(x, 0.82) * 255).astype("uint8")
    return saida


def _cor_hex(cor: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % cor


def legenda_de_verdor(data_br: str | None = None) -> dict[str, Any]:
    """A legenda como dado. Quem desenha é o relatório, fora do recorte."""
    faixas = [
        {"de": de, "ate": ate, "cor": _cor_hex(cor), "rotulo": rotulo}
        for de, ate, cor, rotulo in CLASSES_DE_VERDOR
    ]
    saida: dict[str, Any] = {
        "faixas": faixas,
        "sem_leitura": {"cor": _cor_hex(SEM_LEITURA), "rotulo": "sem leitura nesta cena"},
    }
    if data_br:
        saida["nota"] = LEGENDA_NOTA.format(data=data_br)
    return saida


def _iso_da_cena(propriedades: dict[str, Any]) -> str:
    return str(propriedades.get("datetime") or propriedades.get("start_datetime") or "")


def _nuvem(propriedades: dict[str, Any]) -> float | None:
    valor = propriedades.get("eo:cloud_cover")
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def escolher_cenas(itens: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """A mais recente entre as que prestam, com a tolerância de nuvem dita no resultado.

    Puro de propósito: a regra de escolha se prova sem rede. Cena sem data não entra —
    foto sem data é exatamente o que este módulo existe para não entregar.
    """
    validas = [x for x in itens or [] if _iso_da_cena((x.get("properties") or {}))[:10]]
    for teto in DEGRAUS_DE_NUVEM:
        grupo = [x for x in validas
                 if (_nuvem(x.get("properties") or {}) is not None
                     and _nuvem(x.get("properties") or {}) <= teto)]
        if grupo:
            grupo.sort(key=lambda x: _iso_da_cena(x.get("properties") or {}), reverse=True)
            return grupo[:CANDIDATOS], f"nuvem_ate_{teto:g}"
    return [], "nenhuma_cena"


async def _buscar(geometria: dict[str, Any], dias: int = JANELA_DIAS) -> list[dict[str, Any]]:
    fim = datetime.now(timezone.utc)
    inicio = fim.replace(microsecond=0) - timedelta(days=dias)
    corpo = {
        "collections": ["sentinel-2-l2a"],
        "intersects": geometria,
        "datetime": f'{inicio.isoformat().replace("+00:00", "Z")}/{fim.isoformat().replace("+00:00", "Z")}',
        "limit": 40,
        "query": {"eo:cloud_cover": {"lt": NUVEM_MAXIMA}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    async with httpx.AsyncClient(timeout=PRAZO_BUSCA_S, follow_redirects=True,
                                 headers={"User-Agent": "Raio-X-Territorial/foto-limpa"}) as cliente:
        resposta = await cliente.post(EARTH_SEARCH, json=corpo)
        resposta.raise_for_status()
        return (resposta.json() or {}).get("features") or []


def montar_imagens(vermelho, verde, azul, infravermelho, caminho_cor: Path, caminho_verdor: Path,
                   qualidade: int = 92) -> dict[str, Any]:
    """Grava as duas imagens, sem escrever nada em cima. Sem rede: dá para provar offline."""
    valido = (vermelho > 0) & (verde > 0) & (azul > 0)
    rgb = esticar_cor_natural(vermelho, verde, azul, valido)
    caminho_cor.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, "RGB").save(caminho_cor, "JPEG", quality=qualidade, optimize=True)

    soma = infravermelho + vermelho
    with np.errstate(invalid="ignore", divide="ignore"):
        verdor = np.where(soma > 0, (infravermelho - vermelho) / soma, np.nan)
    pintura = np.zeros(verdor.shape + (3,), dtype="uint8")
    pintura[:] = SEM_LEITURA
    legivel = np.isfinite(verdor) & valido
    for de, ate, cor, _ in CLASSES_DE_VERDOR:
        faixa = legivel & (verdor >= de) & (verdor < ate if ate < 1.0 else verdor <= ate)
        pintura[faixa] = cor
    Image.fromarray(pintura, "RGB").save(caminho_verdor, "PNG", optimize=True)
    return {"verdor": verdor, "legivel": legivel}


def _estatisticas_de_verdor(verdor, dentro) -> dict[str, Any]:
    amostra = verdor[dentro & np.isfinite(verdor)]
    if not amostra.size:
        return {}
    return {
        "verdor_medio": round(float(np.mean(amostra)), 3),
        "verdor_mediana": round(float(np.median(amostra)), 3),
        "verdor_pixels": int(amostra.size),
        "verdor_classes_pct": [
            {"rotulo": rotulo,
             "pct": round(float(np.mean((amostra >= de) & ((amostra < ate) if ate < 1.0 else (amostra <= ate))) * 100), 1)}
            for de, ate, _cor, rotulo in CLASSES_DE_VERDOR
        ],
    }


def limite_em_pixels(meta: dict[str, Any], car_geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """O limite do CAR em coordenada de pixel da imagem, para o relatório desenhar por cima."""
    if not meta.get("ok") or not meta.get("crs") or not meta.get("transform"):
        return []
    transformador = Transformer.from_crs("EPSG:4326", meta["crs"], always_xy=True)
    geometria = shp_transform(transformador.transform, shape(car_geometry))
    a, b, c, d, e, f = meta["transform"]
    # Meio pixel de tolerância: o desenho fica igual na tela e no papel, e o anel deixa de
    # carregar milhares de pontos até o payload do relatório.
    geometria = geometria.simplify(abs(a) * 0.5, preserve_topology=True)
    inversa = ~Affine(a, b, c, d, e, f)
    partes = [geometria] if geometria.geom_type == "Polygon" else list(getattr(geometria, "geoms", []))
    aneis = []
    for parte in partes:
        if parte.geom_type != "Polygon":
            continue
        aneis.append([tuple(inversa * (float(x), float(y))) for x, y in parte.exterior.coords])
    return aneis


def imagem_para_o_livro(meta: dict[str, Any], epsg_do_livro: str | int) -> dict[str, Any] | None:
    """A foto limpa no contrato de imagem do relatório-livro, ou None quando não serve.

    O livro recorta a imagem por caixa em metros, e para isso ela precisa estar na MESMA
    projeção métrica que ele escolheu para o imóvel. A cena do Sentinel-2 quase sempre está
    (as duas são UTM/WGS84 da mesma zona), mas num imóvel na divisa de zona pode não estar:
    nesse caso devolve None, e o livro segue com a imagem que ele já tinha. Melhor sem data
    do que com o limite do CAR caindo no lugar errado.

    Com este dicionário, o livro passa a ter uma figura do imóvel COM data da cena: basta
    preferi-la em ``Leitura.imagem_media`` e abrir a legenda por ``legenda_abertura``, que
    não promete alta resolução onde cada ponto cobre 10 m.
    """
    if not meta.get("ok") or not meta.get("transform") or not meta.get("crs"):
        return None
    alvo = str(epsg_do_livro).upper()
    if not alvo.startswith("EPSG:"):
        alvo = f"EPSG:{alvo}"
    if str(meta["crs"]).upper() != alvo:
        return None
    a, _b, c, _d, e, f = meta["transform"]
    largura, altura = int(meta["largura_px"]), int(meta["altura_px"])
    minx, maxy = float(c), float(f)
    maxx, miny = minx + a * largura, maxy + e * altura
    saida = {
        "ok": True,
        "kind": "sentinel2_limpo",
        "path": meta["cor_natural_path"],
        "crs": meta["crs"],
        "bounds_m": [round(v, 2) for v in (minx, miny, maxx, maxy)],
        "px": [largura, altura],
        "date": meta.get("data_cena"),
        "resolution_m": RESOLUCAO_M,
        "attribution": FONTE,
        "legenda_abertura": "Imagem de satélite",
        "enquadramento_coberto": round(ENQUADRAMENTO_COBERTO, 3),
    }
    if meta.get("nuvem_pct") is not None:
        saida["cloud_pct"] = meta["nuvem_pct"]
    if meta.get("aviso"):  # campo vazio não aparece
        saida["aviso"] = meta["aviso"]
    return saida


def texto_da_cena(meta: dict[str, Any]) -> str:
    """A linha que o relatório imprime embaixo da foto — fora da imagem, nunca dentro."""
    if not meta.get("ok"):
        return ""
    partes = [f"Imagem de satélite de {meta['data_cena_br']}"]
    nuvem = meta.get("nuvem_pct")
    if nuvem is not None:
        partes.append(f"nuvem de {str(round(nuvem, 1)).replace('.', ',')}% na cena")
    partes.append("Sentinel-2 (programa Copernicus, da Agência Espacial Europeia), 10 m por pixel")
    linha = " · ".join(partes) + "."
    if meta.get("aviso"):
        linha += " " + meta["aviso"]
    return linha


def _aviso(idade_dias: int, nuvem: float | None, antiga: bool, nublada: bool) -> str | None:
    if antiga and nublada:
        return ("Esta é a cena mais recente sem nuvem demais sobre o imóvel: ela tem "
                f"{idade_dias} dias e ainda traz nuvem na área.")
    if antiga:
        return f"Esta é a cena limpa mais recente disponível: ela tem {idade_dias} dias."
    if nublada:
        return "A cena mais recente disponível ainda traz nuvem sobre parte da área."
    return None


def pendente(detalhe: str) -> dict[str, Any]:
    """Fonte que não respondeu não vira foto vazia nem data inventada: vira pendência."""
    return {"ok": False, "pendente": True, "fonte": FONTE, "detalhe": detalhe,
            "mensagem": "Imagem de satélite: consulta pendente nesta emissão."}


async def build_clean_sentinel_photo(car_geometry: dict[str, Any], out_dir: str | Path, *,
                                     largura: int = LARGURA_PADRAO,
                                     dias: int = JANELA_DIAS,
                                     prefixo: str = "foto_limpa") -> dict[str, Any]:
    comeco = time.monotonic()
    pasta = Path(out_dir)
    try:
        car = shape(car_geometry)
    except Exception as erro:
        return pendente(f"geometria:{type(erro).__name__}")
    try:
        itens = await _buscar(car_geometry, dias)
    except Exception as erro:
        return pendente(f"catalogo:{type(erro).__name__}")
    candidatos, politica = escolher_cenas(itens)
    if not candidatos:
        return pendente("nenhuma_cena_com_data_na_janela")

    ultimo = "ativos_indisponiveis"
    for item in candidatos:
        propriedades = item.get("properties") or {}
        vermelho = _asset(item, "red", "B04")
        verde = _asset(item, "green", "B03")
        azul = _asset(item, "blue", "B02")
        infra = _asset(item, "nir", "nir08", "B08")
        if not all((vermelho, verde, azul, infra)):
            continue
        try:
            with rasterio.Env(GDAL_HTTP_CONNECTTIMEOUT="5", GDAL_HTTP_TIMEOUT="9",
                              GDAL_HTTP_MAX_RETRY="0", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF"):
                with rasterio.open(vermelho) as referencia:
                    crs = referencia.crs
            transformador = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
            projetada = shp_transform(transformador.transform, car)
            minx, miny, maxx, maxy = projetada.bounds
            dx = max(maxx - minx, 30)
            dy = max(maxy - miny, 30)
            folga = max(dx, dy) * FOLGA
            limites = (minx - folga, miny - folga, maxx + folga, maxy + folga)
            proporcao = max((limites[2] - limites[0]) / (limites[3] - limites[1]), 0.25)
            # Teto honesto de ampliação: o recorte não é esticado muito além do que a cena
            # tem de verdade. Imagem lisa e grande, feita de 10 m por pixel, promete
            # detalhe que o satélite não viu.
            nativo = (limites[2] - limites[0]) / RESOLUCAO_M
            largura = int(min(largura, max(LARGURA_MINIMA, nativo * ESCALA_MAXIMA)))
            altura = max(int(LARGURA_MINIMA / max(proporcao, 1.0)), min(1300, int(largura / proporcao)))
            leituras = await asyncio.wait_for(asyncio.gather(
                asyncio.to_thread(_read, vermelho, limites, altura, largura),
                asyncio.to_thread(_read, verde, limites, altura, largura),
                asyncio.to_thread(_read, azul, limites, altura, largura),
                asyncio.to_thread(_read, infra, limites, altura, largura),
            ), timeout=PRAZO_LEITURA_S)
            (banda_r, transformacao, _), (banda_g, _, _), (banda_b, _, _), (banda_n, _, _) = leituras
            caminho_cor = pasta / f"{prefixo}_cor_natural.jpg"
            caminho_verdor = pasta / f"{prefixo}_verdor.png"
            calculado = montar_imagens(banda_r, banda_g, banda_b, banda_n, caminho_cor, caminho_verdor)
            dentro = geometry_mask([mapping(projetada)], out_shape=(altura, largura),
                                   transform=transformacao, invert=True)
            iso = _iso_da_cena(propriedades)
            data = iso[:10]
            nuvem = _nuvem(propriedades)
            idade = max((datetime.now(timezone.utc) - datetime.fromisoformat(
                iso.replace("Z", "+00:00"))).days, 0)
            antiga = idade > CENA_ANTIGA_DIAS
            nublada = nuvem is not None and nuvem > CENA_NUBLADA_PCT
            meta: dict[str, Any] = {
                "ok": True,
                "cor_natural_path": str(caminho_cor),
                "verdor_path": str(caminho_verdor),
                "cena_id": item.get("id"),
                "data_cena": data,
                "data_cena_br": f"{data[8:10]}/{data[5:7]}/{data[0:4]}",
                "idade_dias": idade,
                "cena_antiga": antiga,
                "cena_nublada": nublada,
                "politica_de_escolha": politica,
                "plataforma": propriedades.get("platform") or "sentinel-2",
                "resolucao_m": RESOLUCAO_M,
                "fonte": FONTE,
                "crs": str(crs),
                "transform": [transformacao.a, transformacao.b, transformacao.c,
                              transformacao.d, transformacao.e, transformacao.f],
                "largura_px": largura,
                "altura_px": altura,
                "legenda_verdor": legenda_de_verdor(f"{data[8:10]}/{data[5:7]}/{data[0:4]}"),
            }
            if nuvem is not None:
                meta["nuvem_pct"] = round(nuvem, 1)
            aviso = _aviso(idade, nuvem, antiga, nublada)
            if aviso:  # campo vazio não aparece
                meta["aviso"] = aviso
            meta.update(_estatisticas_de_verdor(calculado["verdor"], dentro))
            meta["limite_px"] = limite_em_pixels(meta, car_geometry)
            meta["ms"] = round((time.monotonic() - comeco) * 1000)
            return meta
        except Exception as erro:
            ultimo = f"{type(erro).__name__}:{str(erro)[:160]}"
            continue
    return pendente(ultimo)


print("RX_FOTO_LIMPA=sentinel2_cor_natural_e_verdor_sem_nada_escrito", flush=True)
