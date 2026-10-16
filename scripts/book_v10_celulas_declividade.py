"""Células de inclinação de Curvelo para o mapa da p. 7 da amostra do livro v10 (complemento da F1).

A F1 (``tests/fixtures/book/curvelo_corrigido.json``) guarda só as partes por classe de relevo
(``productive.relief_v51``). O mapa de inclinação da amostra precisa de ONDE cada parte fica. Este script refaz,
célula a célula, a mesma conta que gerou a F1 (``plano/terra_scripts/p3_relevo.py``): Copernicus DEM GLO-30 (AWS
Open Data), ``np.gradient`` com o passo do grau convertido em metros, inclinação em %, célula que entra quando o
centro cai no limite do CAR. Grava ``curvelo_declividade_celulas.json`` e o ``.sha256`` ao lado.

O limite entra em graus, como o SICAR publica (``--car-geojson``): a F1 só tem o limite projetado em UTM, arredondado
ao centímetro, e a volta para graus muda uma célula de divisa (centro a 6 cm do limite original), o que já tira a conta
da F1. O limite em graus usado fica gravado na saída.

Sai com erro se o número de células ou as partes por classe não baterem com a F1: o mapa nunca pode contradizer os
números impressos. Precisa de rede (leitura por faixa do COG na AWS, ~4 s).

Uso:
    python scripts/book_v10_celulas_declividade.py --car-geojson car_geom.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.geometry import mapping, shape
from shapely.ops import transform as sh_transform

ROOT = Path(__file__).resolve().parents[1]
PASTA = ROOT / "tests" / "fixtures" / "book"
DESTINO = PASTA / "curvelo_declividade_celulas.json"
COG = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_S19_00_W045_00_DEM/"
       "Copernicus_DSM_COG_10_S19_00_W045_00_DEM.tif")
CLASSES = (("plano", 0, 3), ("suave ondulado", 3, 8), ("ondulado", 8, 20), ("forte ondulado", 20, 45),
           ("montanhoso", 45, 75), ("escarpado", 75, 1e9))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10))[0])
    ap.add_argument("--car-geojson", required=True, type=Path, help="limite do CAR em graus (EPSG:4326), do SICAR")
    args = ap.parse_args(argv)
    f1 = json.loads((PASTA / "curvelo_corrigido.json").read_text(encoding="utf-8"))
    relevo = f1["productive"]["relief_v51"]["valor"]
    para_utm = Transformer.from_crs("EPSG:4326", "EPSG:31983", always_xy=True)
    bruto = json.loads(args.car_geojson.read_text(encoding="utf-8"))
    car = shape(bruto.get("geometry", bruto))
    # o limite em graus tem de ser o mesmo da F1: projetado, cai sobre o car_m (diferença de até 2 m, da projeção)
    desvio = sh_transform(para_utm.transform, car).hausdorff_distance(shape(f1["book_v10"]["geometry"]["car_m"]))
    if desvio > 2:
        raise SystemExit(f"o limite em graus não é o da F1: desvio de {desvio:.2f} m")
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", VSI_CACHE="TRUE")
    with rasterio.Env(**env), rasterio.open("/vsicurl/" + COG) as ds:
        janela = from_bounds(*car.buffer(0.002).bounds, ds.transform).round_offsets().round_lengths()
        alt = ds.read(1, window=janela).astype("float64")
        tr = ds.window_transform(janela)
    passo = abs(tr.a)
    dy = passo * 111132.0
    dx = passo * 111320.0 * math.cos(math.radians(car.centroid.y))
    gy, gx = np.gradient(alt, dy, dx)
    pct = np.hypot(gx, gy) * 100
    dentro = geometry_mask([mapping(car)], out_shape=alt.shape, transform=tr, invert=True)
    celulas = []
    for lin, col in zip(*np.nonzero(dentro)):
        x0, y0 = tr * (col, lin)
        x1, y1 = tr * (col + 1, lin + 1)
        anel = [para_utm.transform(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        p = float(pct[lin, col])
        celulas.append({"anel_m": [[round(e, 2), round(n, 2)] for e, n in anel], "inclinacao_pct": round(p, 1),
                        "classe": next(nome for nome, de, ate in CLASSES if de <= p < ate)})
    total = len(celulas)
    partes = {nome: Decimal(sum(1 for c in celulas if c["classe"] == nome) * 100) / total for nome, _, _ in CLASSES}
    for linha in relevo["classes_embrapa_pct"]:
        feito = partes[linha["classe"]].quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if feito != Decimal(str(linha["pct"])):
            raise SystemExit(f"{linha['classe']}: células dão {feito}%, a F1 diz {linha['pct']}%")
    if total != relevo["pixels"]:
        raise SystemExit(f"{total} células, a F1 diz {relevo['pixels']}")
    saida = {
        "complementa": "tests/fixtures/book/curvelo_corrigido.json · productive.relief_v51",
        "modelo": relevo["modelo"], "fonte": COG, "crs": "EPSG:31983", "celulas_no_car": total,
        "car_graus": mapping(car),
        "metodo": "inclinação em % por np.gradient (grau convertido em metros: 111132 m na latitude, 111320 × cos(lat) "
                  "na longitude); célula entra quando o centro cai no limite do CAR; mesmo método da F1",
        "celulas": celulas,
    }
    DESTINO.write_text(json.dumps(saida, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    soma = hashlib.sha256(DESTINO.read_bytes()).hexdigest()
    DESTINO.with_suffix(".sha256").write_text(f"{soma}  {DESTINO.name}\n", encoding="utf-8", newline="\n")
    print(f"RX_CELULAS={total} {DESTINO.name} {soma[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
