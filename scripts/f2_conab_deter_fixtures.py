"""Regrava as fixtures do gate F2 (CONAB armazéns e alertas DETER) a partir das fontes reais.

Uso (precisa de rede; o gate em si roda offline):
  PYTHONPATH=. python scripts/f2_conab_deter_fixtures.py

O que é gravado em tests/fixtures/f2_conab_deter/:
* ``respostas.json``: cada requisição que os módulos ``conab_armazens`` e
  ``deter_alertas`` fizeram (url, parâmetros, status, cabeçalhos úteis, tempo) e o
  arquivo com o corpo. O gate responde a partir deste índice.
* CONAB: recorte do arquivo real (mesmos bytes ISO-8859-1, CRLF e colunas), só com
  as linhas usadas nos testes. Dados pessoais nunca entram: e-mail e endereço de
  TODAS as linhas e o nome de toda linha que não seja um dos dois armazéns da prova
  são trocados por marcadores fictícios (que o gate prova que o módulo descarta).
* O CAR de teste vem do WFS público do SICAR (sem dado pessoal: código, área, município).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from shapely.geometry import box, shape  # noqa: E402

import conab_armazens as ca  # noqa: E402
import deter_alertas as da  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "f2_conab_deter"
CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
PROVA_CDAS = ("54.7645.0001-4", "54.0472.0006-4")  # armazéns da prova de Curvelo (pessoa jurídica)
CAIXAS = {
    # quadrado sobre o alerta real recente deter_cerrado.64722_curr (21/08/2026, Curvelo)
    "controle_alerta_recente_cerrado": box(-44.2045, -18.5455, -44.1955, -18.5385),
    # quadrado sobre o alerta real deter_cerrado.2061786_hist (09/12/2024), ~18 km a oeste do imóvel
    "controle_alerta_18km_cerrado": box(-44.36, -18.92, -44.34, -18.90),
    # quadrado sobre o alerta real deter_amz.5491_curr (29/09/2025, Santa Carmem/MT)
    "controle_alerta_amazonia": box(-55.36, -12.05, -55.33, -12.035),
    # São Paulo capital: fora do Cerrado e da Amazônia Legal
    "controle_fora_cobertura": box(-46.64, -23.56, -46.62, -23.54),
}
HEADERS_UTEIS = ("etag", "last-modified", "content-type", "content-encoding")

indice: list[dict] = []
arquivos: dict[str, bytes] = {}


def gravador(nome_cenario: str, transformar=None):
    def get(url, *, params=None, headers=None):
        t0 = time.perf_counter()
        resp = httpx.get(url, params=params, headers={**(headers or {}), "User-Agent": "Raio-X-Territorial/F2 fixtures"},
                         timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True)
        ms = round((time.perf_counter() - t0) * 1000)
        corpo = resp.content
        if transformar:
            corpo = transformar(url, params, resp, corpo)
        digest = hashlib.sha1(json.dumps([url, params, headers], sort_keys=True, default=str).encode()).hexdigest()[:10]
        ext = ".csv" if (params or {}).get("outputFormat") == "csv" else ".xml" if (params or {}).get("resultType") == "hits" else ".json"
        if "ArmazensCadastrados" in url:
            ext = ".txt.gz"
        nome = f"respostas/{nome_cenario}_{digest}{ext}"
        arquivos[nome] = gzip.compress(corpo, mtime=0) if ext == ".txt.gz" else corpo
        indice.append({
            "cenario": nome_cenario, "url": url, "params": params or {}, "headers_enviados": headers or {},
            "status": resp.status_code, "headers": {k: resp.headers[k] for k in HEADERS_UTEIS if k in resp.headers},
            "arquivo": nome, "ms": ms,
        })
        return resp
    return get


def recorte_conab(url, params, resp, corpo):
    if resp.status_code != 200:
        return corpo
    texto, _ = ca.decodificar(corpo)
    linhas_txt = texto.split("\r\n")
    cabecalho, dados = linhas_txt[0], [x for x in linhas_txt[1:] if x.strip()]
    col = {c: i for i, c in enumerate(ca.COLUNAS)}
    linhas, _ = ca.ler_arquivo_armazens(corpo)
    car_geom = shape(json.loads((OUT / "car_curvelo.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"])
    perto = {r["cda"] for r in ca.candidates_in_radius(linhas, car_geom, 80.0)}
    escolhidos: dict[str, str] = {}
    contagem: dict[str, int] = {}
    pf = me = zero = fora = 0
    for bruta in dados:
        cda = bruta.split(";")[col["identificacao_armazem"]].strip()
        contagem[cda] = contagem.get(cda, 0) + 1
    duplicado = sorted(k for k, v in contagem.items() if v > 1)[0]  # todas as linhas de UM CDA repetido
    mal_localizado = recorte_conab.mal_localizado
    saida = []
    for bruta in dados:
        f = bruta.split(";")
        cda = f[col["identificacao_armazem"]].strip()
        tp = ca._ascii_upper(f[col["dsc_tipo_pessoa"]])
        nome = f[col["nome_armazenador"]]
        motivo = None
        if cda in perto:
            motivo = "ate_80km_de_curvelo"
        elif cda == duplicado:
            motivo = "cda_duplicado"
        elif cda == mal_localizado:
            motivo = "ponto_fora_do_municipio_declarado"
        elif ca._coord(f[col["latitude"]], f[col["longitude"]]) is None and fora < 1:
            motivo, fora = "coordenada_fora_do_brasil", fora + 1
        elif ca._num_br(f[col["qtd_capacidade_estatica(t)"]]) == 0 and zero < 1:
            motivo, zero = "capacidade_zero", zero + 1
        elif "FISICA" in tp and pf < 2 and "MG" in f[col["uf"]]:
            motivo, pf = "pessoa_fisica", pf + 1
        elif "JURIDICA" in tp and me < 2 and ca.nome_publicavel(nome, f[col["dsc_tipo_pessoa"]], f[col["dsc_tipo_entidade"]]) is None:
            motivo, me = "empresario_individual", me + 1
        if not motivo:
            continue
        # LGPD: nada pessoal no fixture.
        f[col["endereco"]] = "ENDERECO REMOVIDO DO FIXTURE"
        f[col["email"]] = "removido@example.invalid"
        if cda not in PROVA_CDAS:
            if "FISICA" in tp:
                f[col["nome_armazenador"]] = "PESSOA FISICA FICTICIA"
            elif ca.nome_publicavel(nome, f[col["dsc_tipo_pessoa"]], f[col["dsc_tipo_entidade"]]):
                f[col["nome_armazenador"]] = "EMPRESA FICTICIA DE TESTE LTDA"
            else:
                f[col["nome_armazenador"]] = "EMPRESARIO FICTICIO DE TESTE ME"
        saida.append(";".join(f))
        escolhidos.setdefault(cda, motivo)
    recorte_conab.escolhidos = escolhidos
    return ("\r\n".join([cabecalho, *saida]) + "\r\n").encode("latin-1")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    capturado = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # CAR de teste (SICAR público). O servidor do SICAR recusa o TLS do httpx; o
    # portal usa curl (deploy_app._curl) e aqui também.
    import subprocess
    from urllib.parse import urlencode

    q = {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "sicar:sicar_imoveis_mg",
         "outputFormat": "application/json", "CQL_FILTER": f"cod_imovel IN ('{CAR}')"}
    p = subprocess.run(["curl", "-k", "-sS", "--max-time", "60", "-A", "Raio-X-Territorial/F2 fixtures",
                        "https://geoserver.car.gov.br/geoserver/sicar/ows?" + urlencode(q)], capture_output=True, check=True)
    car_fc = json.loads(p.stdout.decode("utf-8"))
    assert car_fc["features"][0]["properties"]["cod_imovel"] == CAR
    (OUT / "car_curvelo.geojson").write_text(json.dumps(car_fc, ensure_ascii=False), encoding="utf-8", newline="\n")
    car_geom = car_fc["features"][0]["geometry"]

    # Linha real com ponto fora do município declarado (Belo Horizonte declarado, ponto longe)
    bruto = httpx.get(ca.URL_ARMAZENS, timeout=60).content
    linhas, _ = ca.ler_arquivo_armazens(bruto)
    bh = ca.fetch_municipality_mesh("3106200", OUT / "_tmp", http_get=gravador("ibge_malha_bh"))
    candidatos_bh = [x for x in linhas if x["cod_ibge"] == "3106200" and x["lat"] is not None
                     and ca.point_matches_municipality(x["lat"], x["lon"], bh, 10.0) is False]
    candidatos_bh.sort(key=lambda x: x["cda"])
    recorte_conab.mal_localizado = candidatos_bh[0]["cda"]
    ponto = candidatos_bh[0]

    # CONAB: download real (200) e condicional (304) exatamente como o módulo faz
    tmp = OUT / "_tmp"
    ca.sync_conab_warehouses(tmp, http_get=gravador("conab_download", recorte_conab), forcar=True, min_linhas=1)
    meta_real = next(x for x in indice if x["cenario"] == "conab_download")["headers"]
    ca.sync_conab_warehouses(tmp, http_get=gravador("conab_condicional"), forcar=True, min_linhas=1)
    ca.fetch_municipality_mesh("3120904", tmp, http_get=gravador("ibge_malha_curvelo"))

    # DETER: cada cenário com cache limpo, para gravar todas as consultas
    cenarios = {"curvelo": car_geom, **{k: v for k, v in CAIXAS.items()}}
    for nome, geom in cenarios.items():
        da._CACHE.clear()
        da.query_deter_live(geom, http_get=gravador(f"deter_{nome}"))
    # PRODES Cerrado sobre o alerta de ~18 km, no mesmo formato do prodes_fast_v24 (JSON, bbox)
    g = CAIXAS["controle_alerta_18km_cerrado"]
    gravador("prodes_controle_18km")("https://terrabrasilis.dpi.inpe.br/geoserver/prodes-cerrado-nb/ows", params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": "prodes-cerrado-nb:yearly_deforestation",
        "srsName": "EPSG:4674", "bbox": f"{g.bounds[0]},{g.bounds[1]},{g.bounds[2]},{g.bounds[3]},EPSG:4674",
        "count": "2000", "outputFormat": "application/json"})

    for nome, corpo in arquivos.items():
        destino = OUT / nome
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(corpo)
    proveniencia = {
        "capturado_em_utc": capturado,
        "car": CAR,
        "conab_last_modified": meta_real.get("last-modified"),
        "conab_linhas_escolhidas": recorte_conab.escolhidos,
        "conab_mal_localizado": {"cda": ponto["cda"], "municipio_declarado": "3106200"},
        "lgpd": "e-mail e endereço de todas as linhas e nomes fora dos dois armazéns da prova trocados por marcadores fictícios",
        "respostas": indice,
    }
    (OUT / "respostas.json").write_text(json.dumps(proveniencia, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"F2_FIXTURES_OK respostas={len(indice)} arquivos={len(arquivos)} destino={OUT}")


if __name__ == "__main__":
    main()
