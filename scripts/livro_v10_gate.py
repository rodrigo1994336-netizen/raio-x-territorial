"""Portão do relatório-livro v10: regras do dono, diagramação e ligação ao serviço.

Uso:
    PYTHONPATH=. python scripts/livro_v10_gate.py

O portão roda em Linux e no Windows, sem rede: desenha o livro a partir de payloads de teste e
confere o PDF que saiu, não a intenção do código.

**Cada regra tem controle positivo por mutação.** Para cada regra, o portão quebra de propósito o
dado (ou o código) que a regra protege e exige que ela REPROVE. Regra que não vê o defeito não prova
nada — foi assim que portões verdes conviveram com defeito no ar (observações 2, 26, 30 e 32 do
acervo). O controle executa o caminho real da regra, nunca um ajudante abaixo dela.

Regras conferidas:

1. **Contrato do livro** — todo fato sai com um dos cinco estados; ``pendente`` nunca traz valor;
   nenhuma chave proibida (CPF, CNPJ, nome, razão social) entra em Fato nem em ``book_v10``.
2. **Zero com alcance** — fato ``nao_encontrado`` sempre traz pelo menos um limite escrito.
3. **Fonte que não respondeu nunca vira ausência** — fonte INDISPONÍVEL na lista produz ``pendente``,
   nunca ``nao_encontrado``.
4. **Nada inventado** — tirar um campo do payload tira a afirmação do PDF; não aparece número no
   lugar dela.
5. **Português do cliente** — o texto do livro passa pelo lint do R1 (scripts/r1_report_ptbr_gate.py)
   e pelas palavras proibidas do guia de redação (§2.0.3).
6. **Ocupação das folhas** — nenhuma folha do corpo abaixo do piso; nenhuma estourando a área útil.
7. **Ligação ao serviço** — o livro é o formato padrão, e ``RX_RELATORIO_LIVRO=off`` devolve o
   gerador antigo.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("RX_LIVRO_IMAGEM", "off")      # o portão não vai à rede
os.environ.setdefault("RX_LIVRO_SITE", "raioxterritorial.com.br")

import report_book_v10 as livro  # noqa: E402
import report_book_v10_dados as dados  # noqa: E402
import report_book_v10_fatos as fatos  # noqa: E402
from report_book_v10_contracts import CAMPOS_PROIBIDOS, ESTADOS, FATO_CHAVES  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "livro"
PISO_OCUPACAO_CORPO = 70      # % da área útil; folha de corpo abaixo disto está vazia demais
FALHAS: list[str] = []


# ---------------------------------------------------------------------------------------------
# Payloads de teste (sem dado pessoal, escritos aqui para o portão não depender de rede)
# ---------------------------------------------------------------------------------------------
def payload_base() -> dict:
    return {
        "report_id": "RX-20260301T120000Z-TESTE001",
        "generated_at": "2026-03-01T12:00:00+00:00",
        "property": {"car_code": "MG-3120904-TESTE0000000000000000000000000001",
                     "municipality": "Curvelo", "uf": "MG", "area_ha": 120.5, "name": "",
                     "name_source": "SICAR sem denominação pública"},
        "car": {"status": "AT", "type": "IRU", "fiscal_modules": 3.0,
                "analysis_status": "Aguardando análise", "areas": [], "environmental_composition": []},
        "land": {"certifications": []},
        "incra_acervo_f2": {
            "sigef": {"state": "not_found", "items": [], "complete": True, "car_share": 0.0},
            "snci": {"state": "not_found", "items": [], "complete": True, "car_share": 0.0}},
        "environment": {
            "prodes": {"count": 0},
            "layer_rows": [["Terra Indígena", "0 ocorrência(s) • 0.0 ha", "FUNAI"],
                           ["Unidade de Conservação", "0 ocorrência(s) • 0.0 ha", "CNUC"],
                           ["Território Quilombola", "0 ocorrência(s) • 0.0 ha", "INCRA"],
                           ["Assentamento", "0 ocorrência(s) • 0.0 ha", "INCRA"],
                           ["Floresta Pública — SFB", "0 ocorrência(s) • 0.0 ha", "SFB"]],
            "iphan_f2": {"state": "found",
                         "inside": {"state": "not_found", "count": 0, "rows": []},
                         "near": {"state": "not_found", "count": 0, "rows": []}},
            "deforestation_alerts": {"state": "not_found", "items": [], "event_count": 0,
                                     "area_union_ha": 0.0, "recent_after": "2025-08-01",
                                     "complete": True}},
        "enforcement": {"embargo_count": 0, "embargo_pending": False, "embargo_area_ha": 0.0,
                        "embargo_items": [], "auto_count": 0, "autos": []},
        "mining": {"process_count": 0, "overlap_area_ha": 0.0, "processes": []},
        "water": {"grant_count": 0, "pivot_count": 0,
                  "grants_f2": {"state": "not_found", "items": [], "rows": [], "headers": [],
                                "count": 0, "other_count": 0, "notes": [], "complete": True},
                  "groundwater": {"ok": True, "well_count": 12, "radius_km": 20.0,
                                  "static_level_median_m": 18.0, "static_level_count": 9},
                  "drought_screening": {"rain_sum_mm": 44.2, "days": 30, "period_start": "20260201",
                                        "period_end": "20260302"},
                  "rain_rows": [["Dias secos (< 1 mm)", "12"]] + [
                      [f"Climatologia {m}", f"chuva ≈ {mm} mm no mês • temperatura média 23,0 °C"]
                      for m, mm in zip(fatos.MESES_EN,
                                       (180, 140, 150, 60, 25, 8, 6, 9, 35, 90, 170, 240))]},
        "productive": {
            "landcover_rows": [["MapBiomas — Pastagem", "90.5 ha • 75.10% do imóvel"],
                               ["MapBiomas — Formação savânica", "30.0 ha • 24.90% do imóvel"]],
            "soil_rows": [["Tipo de solo no mapa oficial", "Latossolo Vermelho em todo o imóvel."],
                          ["Textura de 0 a 30 cm (estimativa no imóvel)", "Textura média em todo o imóvel."],
                          ["Argila, areia e silte (valor típico no imóvel)",
                           "argila 28% (de 24% a 33% em 8 de cada 10 pontos) · areia 45% · silte 27%."],
                          ["Erodibilidade do solo (mapa oficial)", "Erodibilidade média em todo o imóvel."],
                          ["Escala do mapa", "Mapa regional (1:250.000): descreve a região do imóvel."]],
            "aptitude_rows": [["2 — aptidão regular para lavouras", "Em todo o imóvel"]],
            "solo_textura_t1": {"group_shares_pct": {"média": 100.0}, "resolution_m": 30,
                                "source": "MapBiomas Solo, coleção 2"},
            "terrain_srtm": {"ok": True, "state": "found", "slope_unit": "%",
                             "source": "SRTM 1 arc-second (~30 m)",
                             "elevation_min_m": 600.0, "elevation_median_m": 640.0,
                             "elevation_max_m": 690.0, "slope_median_pct": 5.0,
                             "slope_mean_pct": 5.4, "slope_p90_pct": 11.0,
                             "slope_25_45deg_share_pct": 0.0, "slope_gt_45deg_share_pct": 0.0,
                             "slope_sample_pixels": 1200,
                             "slope_classes": [{"class": "plano", "range": "0–3%", "share_pct": 30.0},
                                               {"class": "suave ondulado", "range": "3–8%", "share_pct": 50.0},
                                               {"class": "ondulado", "range": "8–20%", "share_pct": 20.0}]},
            "agro_rows": [["Bovino", "95000.0 cabeças • 2025 • variação +1.09% vs 2024", "MUNICÍPIO — IBGE/PPM"]]},
        "infrastructure": {"airports": [], "warehouses": [], "iphan": [],
                           "warehouses_f2": {"state": "not_found", "items": [], "complete": True,
                                             "radius_km": 50.0, "base_date": "2026-02-10"}},
        "agropecuaria": {"municipality": "Curvelo", "uf": "MG",
                         "livestock_municipal": {"ok": True},
                         "pasture": {"state": "ready", "year": 2025, "pasture_share_pct": 75.1,
                                     "native_vegetation_share_pct": 24.9,
                                     "source": "MapBiomas Brasil — Cobertura 30 m — Coleção 11"},
                         "sif_chain": {"ok": True, "establishments": []}},
        "satellite_imagery": {"sentinel": {"ok": False}},
        "sources": [
            {"name": "SICAR", "status": "CONSULTADA"},
            {"name": "INPE / TerraBrasilis / PRODES", "status": "CONSULTADA"},
            {"name": "IBAMA — áreas embargadas", "status": "CONSULTADA"},
            {"name": "IBAMA — autos de infração", "status": "CONSULTADA"},
            {"name": "Terra Indígena", "status": "CONSULTADA"},
            {"name": "Unidade de Conservação", "status": "CONSULTADA"},
            {"name": "Território Quilombola", "status": "CONSULTADA"},
            {"name": "Assentamento", "status": "CONSULTADA"},
            {"name": "Floresta Pública — SFB", "status": "CONSULTADA"},
            {"name": "ANM / SIGMINE", "status": "CONSULTADA"},
            {"name": "Patrimônio arqueológico — IPHAN", "status": "CONSULTADA"},
            {"name": "Outorgas de água — IGAM/ANA", "status": "CONSULTADA"},
            {"name": "SGB / SIAGAS — Água subterrânea", "status": "CONSULTADA"},
            {"name": "ANA / SNIRH - Pivôs Centrais 2022", "status": "CONSULTADA"},
            {"name": "NASA POWER - Agroclimatologia", "status": "CONSULTADA"},
            {"name": "NASA POWER — climatologia mensal", "status": "CONSULTADA"},
            {"name": "MapBiomas Brasil — Cobertura/Pastagem Coleção 11", "status": "CONSULTADA"},
            {"name": "SRTM 1 arc-second — altitude e inclinação do terreno", "status": "CONSULTADA"},
            {"name": "IBGE — Base Contínua de Pedologia (BDiA)", "status": "CONSULTADA"},
            {"name": "MapBiomas Solo, coleção 2 — granulometria", "status": "CONSULTADA"},
            {"name": "Embrapa Solos — aptidão agrícola do Brasil", "status": "CONSULTADA"},
            {"name": "Embrapa Solos — erodibilidade dos solos do Brasil", "status": "CONSULTADA"},
            {"name": "IBGE / PPM — Pecuária Municipal", "status": "CONSULTADA"},
            {"name": "CONAB — Cadastro Nacional de Unidades Armazenadoras (SICARM), base de 10/02/2026",
             "status": "CONSULTADA"},
            {"name": "INCRA — Acervo Fundiário (SIGEF)", "status": "CONSULTADA"},
            {"name": "INCRA — Acervo Fundiário (SNCI)", "status": "CONSULTADA"},
        ],
    }


def geometria_quadrada(lado_m: float = 1100.0) -> dict:
    """Um quadrado simples em graus, para o livro ter geometria sem depender do SICAR."""
    lon, lat = -44.18, -18.89
    d = lado_m / 111_320.0
    anel = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
    return {"type": "Polygon", "coordinates": [anel]}


def result_base() -> dict:
    """A análise ao vivo como o serviço entrega: o PRODES respondeu e não achou nada neste imóvel."""
    return {
        "car": {"geometry": geometria_quadrada(), "properties": {"area": 120.5}},
        "prodes": {"ok": True, "hits": [], "reading": {
            "version": "f2-prodes-leitura-2", "state": "not_found", "complete": True,
            "car_area_ha": 120.5,
            "criterion": {"pixel_m": 30.0, "inside_when": "mancha inteira no CAR"},
            "inside": {"state": "not_found", "count": 0, "area_ha": 0.0, "pct_car": 0.0,
                       "years": [], "occurrences": []},
            "boundary": {"state": "not_found", "count": 0, "area_ha": 0.0, "years": [],
                         "occurrences": []},
            "post_cutoff_inside": {"state": "not_found", "count": 0, "area_ha": 0.0, "occurrences": []},
            "accumulated": {"state": "not_found", "count": 0, "area_ha": 0.0, "occurrences": []},
            "other_classes": {"state": "not_found", "count": 0, "area_ha": 0.0, "occurrences": []}}},
    }


def result_com_desmatamento() -> dict:
    """A mesma análise, com uma mancha dentro e uma na divisa: o caminho do capítulo com achado."""
    base = result_base()
    leitura = base["prodes"]["reading"]
    leitura["state"] = "found"
    leitura["inside"] = {"state": "found", "count": 1, "area_ha": 12.5, "pct_car": 10.4,
                         "years": [2021], "occurrences": [
                             {"id": "yearly_deforestation.aaaa1111", "layer": "prodes-cerrado-nb:yearly",
                              "biome": "Cerrado", "year": 2021, "image_date": "2021-08-10",
                              "period_label": "entre 2020 e a imagem de 10/08/2021", "area_ha": 12.5,
                              "feature_area_ha": 20.0, "share_of_feature_pct": 62.5,
                              "placement": "inside", "max_width_m": None, "post_cutoff": True}]}
    leitura["boundary"] = {"state": "found", "count": 1, "area_ha": 0.04, "years": [2006],
                           "max_width_m": 11.0, "post_cutoff_count": 0, "occurrences": [
                               {"id": "yearly_deforestation.bbbb2222", "layer": "prodes-cerrado-nb:yearly",
                                "biome": "Cerrado", "year": 2006, "image_date": "2006-07-21",
                                "period_label": "entre 2004 e a imagem de 21/07/2006", "area_ha": 0.04,
                                "feature_area_ha": 8.0, "share_of_feature_pct": 0.5,
                                "placement": "boundary", "max_width_m": 11.0, "post_cutoff": False}]}
    return base


def desenhar(payload: dict, result: dict | None = None) -> tuple[Path, str, list[str]]:
    """Desenha o livro num arquivo temporário e devolve (pdf, texto, problemas de redação)."""
    from pypdf import PdfReader

    pasta = Path(tempfile.mkdtemp(prefix="livro_gate_"))
    pdf = pasta / "livro.pdf"
    trabalho = copy.deepcopy(payload)
    fatos.gravar_fatos(trabalho, result)
    trabalho["book_v10"] = dados.montar_book_v10(trabalho, result, pasta, com_imagem=False)
    livro.gerar_livro(pdf, trabalho, result, pasta_imagens=pasta)
    texto = "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)
    return pdf, texto, livro.problemas_de_redacao()


def falhar(regra: str, detalhe: str) -> None:
    FALHAS.append(f"{regra}: {detalhe}")
    print(f"FALHA {regra}: {detalhe}")


def exige(condicao: bool, regra: str, detalhe: str) -> bool:
    if not condicao:
        falhar(regra, detalhe)
    return bool(condicao)


# ---------------------------------------------------------------------------------------------
# 1 · contrato dos fatos
# ---------------------------------------------------------------------------------------------
def regra_contrato(payload: dict, result: dict) -> list[str]:
    """Todo fato tem estado válido; ``pendente`` nunca traz valor; nada de dado pessoal."""
    problemas: list[str] = []
    lidos = fatos.ler_fatos(payload, result)
    for chave in FATO_CHAVES:
        if chave not in lidos:
            problemas.append(f"fato {chave} não foi lido")
            continue
        e = lidos[chave]
        if e.get("estado") not in ESTADOS:
            problemas.append(f"fato {chave} com estado inválido: {e.get('estado')!r}")
        if e.get("estado") == "pendente" and e.get("valor") is not None:
            problemas.append(f"fato {chave} pendente com valor")
        for proibida in chaves_de(e.get("valor")):
            if proibida.casefold() in CAMPOS_PROIBIDOS:
                problemas.append(f"fato {chave} carrega campo proibido {proibida}")
    return problemas


def chaves_de(valor) -> list[str]:
    saida: list[str] = []
    if isinstance(valor, dict):
        for k, v in valor.items():
            saida.append(str(k))
            saida += chaves_de(v)
    elif isinstance(valor, list):
        for v in valor:
            saida += chaves_de(v)
    return saida


# ---------------------------------------------------------------------------------------------
# 2 · zero sempre com alcance
# ---------------------------------------------------------------------------------------------
def regra_zero_com_alcance(payload: dict, result: dict) -> list[str]:
    problemas = []
    for chave, e in fatos.ler_fatos(payload, result).items():
        if e.get("estado") == "nao_encontrado" and not (e.get("limites") or []):
            problemas.append(f"fato {chave} diz 'nenhum registro' sem escrever o alcance")
    return problemas


# ---------------------------------------------------------------------------------------------
# 3 · fonte sem resposta nunca vira ausência
# ---------------------------------------------------------------------------------------------
def regra_fonte_caida(payload: dict, result: dict) -> list[str]:
    """Com a fonte INDISPONÍVEL, o fato tem de sair ``pendente`` — nunca ``nao_encontrado``."""
    problemas = []
    for nome_fonte, chave in (("ANM / SIGMINE", "anm"),
                              ("IBAMA — áreas embargadas", "embargo_ibama"),
                              ("Terra Indígena", "ti")):
        doente = copy.deepcopy(payload)
        for linha in doente["sources"]:
            if linha.get("name") == nome_fonte:
                linha["status"] = "INDISPONÍVEL"
        estado = fatos.ler_fatos(doente, result)[chave].get("estado")
        if estado != "pendente":
            problemas.append(f"fonte {nome_fonte} indisponível virou {estado} em {chave}")
    return problemas


# ---------------------------------------------------------------------------------------------
# 4 · nada inventado
# ---------------------------------------------------------------------------------------------
def regra_sem_invencao(payload: dict, result: dict) -> list[str]:
    """Tirando o campo do payload, a afirmação some do PDF: nada entra no lugar dela."""
    problemas = []
    sem_relevo = copy.deepcopy(payload)
    sem_relevo["productive"]["terrain_srtm"] = {"ok": False, "state": "pending"}
    _pdf, texto, _ = desenhar(sem_relevo, result)
    plano = re.search(r"(\d{1,3}(?:,\d)?)% da área tem até 8% de inclinação", texto)
    if plano:
        problemas.append(f"sem o modelo de elevação, o livro ainda afirma inclinação: {plano.group()}")
    if "Consulta pendente" not in texto:
        problemas.append("sem o modelo de elevação, o livro não diz que a consulta ficou pendente")
    return problemas


# ---------------------------------------------------------------------------------------------
# 5 · português do cliente
# ---------------------------------------------------------------------------------------------
def lint_r1():
    """O lint do R1, carregado do próprio script do portão R1 — nunca uma cópia das regras aqui."""
    import importlib.util

    caminho = ROOT / "scripts" / "r1_report_ptbr_gate.py"
    spec = importlib.util.spec_from_file_location("r1_report_ptbr_gate", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.lint_text


def regra_portugues(texto: str, problemas_redacao: list[str]) -> list[str]:
    problemas = list(problemas_redacao)
    for nome, achados in lint_r1()(texto).items():
        problemas.append(f"lint R1 {nome}: {achados[:3]}")
    return problemas


# ---------------------------------------------------------------------------------------------
# 6 · ocupação das folhas
# ---------------------------------------------------------------------------------------------
def regra_ocupacao(saida_log: list[str], folhas_corpo: int) -> list[str]:
    problemas = []
    for linha in saida_log:
        achado = re.match(r"RX_BOOK_OCUPACAO_P(\d+)=(\d+)", linha)
        if not achado:
            continue
        pagina, pct = int(achado.group(1)), int(achado.group(2))
        if pagina <= folhas_corpo and pct < PISO_OCUPACAO_CORPO:
            problemas.append(f"folha {pagina} do corpo com {pct}% de ocupação (piso {PISO_OCUPACAO_CORPO}%)")
    return problemas


# ---------------------------------------------------------------------------------------------
# 7 · ligação ao serviço
# ---------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------
# 8 · o que as frentes F2 apuraram chega ao livro
# ---------------------------------------------------------------------------------------------
# A regra R2 do portão F2 (scripts/f2_relatorio_v20_gate.py) mede o desenho ANTIGO. Esta é a mesma
# proteção no desenho novo: trocar o formato não pode apagar, em silêncio, o que as frentes apuraram.
RESPOSTAS_F2 = (
    ("armazém da CONAB", r"armaz(é|e)m|armaz(é|e)ns"),
    ("alerta recente de desmatamento", r"alerta"),
    ("outorga com vazão e validade", r"Vaz(ã|a)o autorizada"),
    ("regime de captação da outorga", r"Regime de capta(ç|c)(ã|a)o"),
    ("validade da outorga", r"Validade"),
    ("sítio arqueológico do IPHAN", r"IPHAN"),
    ("certificação do INCRA", r"INCRA"),
)


def payload_com_frentes_f2(payload: dict) -> dict:
    """O mesmo payload, com as frentes F2 respondendo — como o serviço entrega num dia normal."""
    doente = copy.deepcopy(payload)
    doente["water"]["grant_count"] = 1
    doente["water"]["grants_f2"] = {
        "state": "found", "complete": True, "count": 1, "other_count": 2,
        "near_text": "Outras 2 captações registradas a até 5 km.",
        "headers": ["Uso", "Ato", "Situação", "Vazão autorizada", "Regime de captação", "Finalidade",
                    "Validade"],
        "rows": [["Poço tubular", "Portaria 123/2025", "Deferido", "10 m³/h",
                  "8 h por dia · o ano todo", "irrigação", "válida até 15/12/2030"]],
        "items": [{"authority": "IGAM", "kind": "captacao", "portaria": "123/2025"}],
        "notes": ["Vazão outorgada não é água garantida."]}
    doente["infrastructure"]["warehouses_f2"] = {
        "state": "found", "complete": True, "radius_km": 50.0, "base_date": "2026-02-10",
        "items": [{"tipo": "Bateria de silos", "name": "EMPRESA TESTE LTDA", "municipio": "Curvelo",
                   "uf": "MG", "capacidade_t": 18500.0, "distancia_km": 22.4}]}
    doente["environment"]["deforestation_alerts"] = {
        "state": "found", "complete": True, "event_count": 1, "area_union_ha": 2.5,
        "recent_after": "2025-08-01",
        "items": [{"data": "2026-01-15", "area_ha": 2.5}]}
    doente["environment"]["iphan_f2"]["near"] = {
        "state": "found", "count": 2, "rows": [["Sítio A", "bem arqueológico", "6,7 km"]]}
    doente["incra_acervo_f2"]["sigef"] = {
        "state": "found", "complete": True, "car_share": 98.0,
        "items": [{"parcel_code": "abc", "layer": "particular"}]}
    doente["land"]["certifications"] = [["SIGEF (INCRA)", "CERTIFICADA", "1", "1 parcela"]]
    return doente


def regra_frentes_no_livro(texto: str) -> list[str]:
    problemas = []
    for nome, padrao in RESPOSTAS_F2:
        if not re.search(padrao, texto, re.IGNORECASE):
            problemas.append(f"o livro não mostra {nome}")
    if re.search(r"EMPRESA TESTE LTDA", texto):
        problemas.append("o livro mostrou o nome da empresa do armazém")
    return problemas


# ---------------------------------------------------------------------------------------------
# 9 · as decisões da camada T1 (terra-verdade) valem no desenho novo
# ---------------------------------------------------------------------------------------------
# A regra `relatorio_real` do portão T1 mede o desenho ANTIGO. Esta é a mesma proteção no livro.
def regra_terra_verdade(texto: str) -> list[str]:
    problemas = []
    # relevo em %, com as classes da Embrapa — nunca em graus
    if not re.search(r"suave ondulado", texto, re.IGNORECASE):
        problemas.append("o livro não mostra as classes de relevo da Embrapa")
    if not re.search(r"inclina(ç|c)(ã|a)o[^.]{0,40}%", texto, re.IGNORECASE):
        problemas.append("o livro não mostra a inclinação em porcentagem")
    if re.search(r"\d+[,.]\d+°\s*(mediana|de inclina)", texto) or "Declive" in texto:
        problemas.append("o livro mostra inclinação em graus")
    # nenhum "máximo" de inclinação (o antigo era um percentil disfarçado de máximo)
    if re.search(r"(?i)m[áa]xim\w*[^.]{0,40}(inclina|decliv|relevo)", texto):
        problemas.append("o livro diz 'máximo' de inclinação")
    # solo pela base nacional, sem as camadas que a T1 tirou do cliente
    if "SoilGrids" in texto:
        problemas.append("o livro cita SoilGrids")
    if re.search(r"IDE-Sisema", texto):
        problemas.append("o livro cita camada estadual que a T1 tirou do cliente")
    if "1:250.000" not in texto:
        problemas.append("o livro não diz a escala do mapa regional de solo")
    # erodibilidade com o conceito, nunca a classe solta
    if "Erodibilidade" in texto and "fragilidade" not in texto:
        problemas.append("o livro diz a erodibilidade sem explicar o que ela é")
    # chuva recente nunca comparada com o normal
    if re.search(r"(?i)(acima|abaixo) do normal|da m[ée]dia da [ée]poca|dentro do normal", texto):
        problemas.append("o livro compara a chuva recente com o normal do lugar")
    if "estimativa em grade" not in texto and "grade de cerca de 50 km" not in texto:
        problemas.append("o livro mostra a chuva recente sem dizer que é estimativa em grade")
    return problemas


# ---------------------------------------------------------------------------------------------
# 10 · a frase concorda com o número que ela mesma traz
# ---------------------------------------------------------------------------------------------
# Defeito achado olhando a folha: "A maior parte do imóvel é plana: 38% da área...". O texto era
# fixo e o número vinha do dado, então a frase afirmava o contrário da medida.
QUANTIFICADORES = (
    (r"(?:a\s+)?maior parte[^.]{0,60}?(\d{1,3}(?:,\d)?)\s*%", 50.0, "mais da metade"),
    (r"(?:em\s+)?todo o im[óo]vel[^.]{0,40}?(\d{1,3}(?:,\d)?)\s*%", 99.0, "praticamente tudo"),
    (r"quase toda a [áa]rea[^.]{0,60}?(\d{1,3}(?:,\d)?)\s*%", 85.0, "quase tudo"),
)


def regra_quantificador(texto: str) -> list[str]:
    problemas = []
    plano = " ".join(texto.split())
    for padrao, piso, nome in QUANTIFICADORES:
        for achado in re.finditer(padrao, plano, re.IGNORECASE):
            valor = float(achado.group(1).replace(",", "."))
            if valor < piso:
                problemas.append(f"a frase diz '{nome}' com {valor}%: {achado.group()[:90]}")
    return problemas


def regra_ligacao_em(fonte_patch: str, fonte_site: str) -> list[str]:
    """O livro é o padrão e ``RX_RELATORIO_LIVRO=off`` devolve o gerador antigo.

    A regra mora aqui, sobre textos dados, e o caso real só entrega os arquivos: assim o controle por
    mutação exercita ESTA função, e não uma cópia parecida dela.
    """
    problemas = []
    if 'os.getenv("RX_RELATORIO_LIVRO", "on")' not in fonte_patch:
        problemas.append("o padrão do livro não é 'on' em report_livro_patch")
    if "_render_anterior(path, payload)" not in fonte_patch:
        problemas.append("report_livro_patch não volta para o gerador antigo quando o livro falha")
    if "import report_livro_patch" not in fonte_site:
        problemas.append("sitecustomize não carrega report_livro_patch")
    elif fonte_site.index("import report_v20_patch") > fonte_site.index("import report_livro_patch"):
        problemas.append("o livro é carregado antes do V20 e seria sobrescrito por ele")
    # O livro troca o DESENHO do PDF; os nomes pelos quais outros portões provam a cadeia continuam
    # apontando para o V20. Trocar `report_api.generate_live_report` derrubava o v47-block2-contract
    # e o f2_relatorio_v20 — ganho nenhum, porque o gancho do adiantamento cabe em `start_queries`.
    for proibido in ("_api.generate_live_report =", "report_api.generate_live_report =",
                     "v20.generate_live_report =", "_v13.generate_live_report ="):
        if proibido in fonte_patch:
            problemas.append(f"report_livro_patch troca um nome que os portões usam para provar a "
                             f"cadeia: {proibido.strip(' =')}")
    return problemas


def regra_ligacao() -> list[str]:
    return regra_ligacao_em((ROOT / "report_livro_patch.py").read_text(encoding="utf-8"),
                            (ROOT / "sitecustomize.py").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------
# Controles positivos por mutação
# ---------------------------------------------------------------------------------------------
def controle(nome: str, regra, deve_reprovar: bool = True) -> None:
    """Roda a regra sobre o caso quebrado e exige que ela aponte problema."""
    try:
        problemas = regra()
    except Exception as erro:               # a regra morrer no defeito também é reprovar
        problemas = [f"{type(erro).__name__}: {erro}"]
    if bool(problemas) != deve_reprovar:
        falhar("controle positivo", f"{nome}: a regra {'não viu' if deve_reprovar else 'viu'} o defeito")
    else:
        print(f"  controle ok · {nome}")


def main() -> int:
    payload = payload_base()
    result = result_base()

    print("== regras sobre o livro desenhado ==")
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _pdf, texto, problemas_redacao = desenhar(payload, result)
    log = buffer.getvalue().splitlines()
    # o próprio motor diz quantas folhas são de corpo; contar "total menos 3" errava quando o
    # apêndice B precisava de uma segunda folha
    folhas_corpo = 0
    for linha in log:
        if linha.startswith("RX_BOOK_FOLHAS_CORPO="):
            folhas_corpo = int(linha.split("=", 1)[1])
    if not folhas_corpo:
        falhar("6 ocupação das folhas", "o motor não disse quantas folhas são de corpo")

    for nome, problemas in (
            ("1 contrato dos fatos", regra_contrato(payload, result)),
            ("2 zero com alcance", regra_zero_com_alcance(payload, result)),
            ("3 fonte caída nunca vira ausência", regra_fonte_caida(payload, result)),
            ("4 nada inventado", regra_sem_invencao(payload, result)),
            ("5 português do cliente", regra_portugues(texto, problemas_redacao)),
            ("6 ocupação das folhas", regra_ocupacao(log, folhas_corpo)),
            ("7 ligação ao serviço", regra_ligacao()),
            ("9 decisões da camada T1 no livro", regra_terra_verdade(texto)),
            ("10 a frase concorda com o número", regra_quantificador(texto)),
    ):
        if problemas:
            for p in problemas:
                falhar(nome, p)
        else:
            print(f"  ok · {nome}")

    # O mesmo livro com achado: o capítulo com mancha dentro e na divisa tem de fechar também.
    print("== o livro do imóvel com desmatamento ==")
    buffer2 = io.StringIO()
    with redirect_stdout(buffer2):
        _pdf2, texto2, problemas2 = desenhar(payload, result_com_desmatamento())
    log2 = buffer2.getvalue().splitlines()
    corpo2 = next((int(l.split("=", 1)[1]) for l in log2 if l.startswith("RX_BOOK_FOLHAS_CORPO=")), 0)
    for nome, problemas in (("5 português do cliente (com achado)", regra_portugues(texto2, problemas2)),
                            ("6 ocupação das folhas (com achado)", regra_ocupacao(log2, corpo2)),
                            ("10 a frase concorda com o número (com achado)", regra_quantificador(texto2))):
        if problemas:
            for p in problemas:
                falhar(nome, p)
        else:
            print(f"  ok · {nome}")
    if "31/07/2019" not in texto2:
        falhar("regra do crédito", "mancha posterior ao marco sem a frase da Resolução CMN 5.303/2026")

    print("== o que as frentes F2 apuraram chega ao livro ==")
    com_f2 = payload_com_frentes_f2(payload)
    buffer3 = io.StringIO()
    with redirect_stdout(buffer3):
        _pdf3, texto3, problemas3 = desenhar(com_f2, result)
    for nome, problemas in (("8 frentes F2 no livro", regra_frentes_no_livro(texto3)),
                            ("5 português do cliente (com frentes F2)",
                             regra_portugues(texto3, problemas3))):
        if problemas:
            for p in problemas:
                falhar(nome, p)
        else:
            print(f"  ok · {nome}")

    print("== controles positivos por mutação ==")

    def com_estado_invalido():
        doente = copy.deepcopy(payload)
        original = fatos.estado_por_contagem

        def quebrado(fonte_ok, n, zero_vale, completa=True):
            return "quase_certo"            # estado que não existe no contrato
        fatos.estado_por_contagem = quebrado
        try:
            return regra_contrato(doente, result)
        finally:
            fatos.estado_por_contagem = original

    controle("estado fora do contrato", com_estado_invalido)

    def com_pendente_que_traz_valor():
        original = fatos.envelope

        def quebrado(estado, valor=None, **kw):
            e = original(estado, valor, **kw)
            if estado == "pendente":
                e["valor"] = {"quantidade": 0}   # o defeito que a regra existe para pegar
            return e
        fatos.envelope = quebrado
        try:
            return regra_contrato(payload, result)
        finally:
            fatos.envelope = original

    controle("pendente com valor", com_pendente_que_traz_valor)

    def com_zero_sem_alcance():
        original = fatos.envelope

        def quebrado(estado, valor=None, **kw):
            kw["limites"] = ()
            return original(estado, valor, **kw)
        fatos.envelope = quebrado
        try:
            return regra_zero_com_alcance(payload, result)
        finally:
            fatos.envelope = original

    controle("zero sem alcance escrito", com_zero_sem_alcance)

    def com_fonte_caida_virando_zero():
        original = fatos.estado_por_contagem

        def quebrado(fonte_ok, n, zero_vale, completa=True):
            return original(True, n if n is not None else 0.0, zero_vale, completa)
        fatos.estado_por_contagem = quebrado
        try:
            return regra_fonte_caida(payload, result)
        finally:
            fatos.estado_por_contagem = original

    controle("fonte caída virando 'nenhum registro'", com_fonte_caida_virando_zero)

    def com_dado_pessoal():
        """Desliga a limpeza e põe dado pessoal no payload: a regra 1 tem de ver o campo proibido.

        Sem desligar ``limpar_pessoas`` o controle só provava que a limpeza funciona — e passaria
        verde mesmo com a regra 1 cega, que é o defeito que ele existe para pegar.
        """
        doente = copy.deepcopy(payload)
        doente["enforcement"]["embargo_count"] = 1
        doente["enforcement"]["embargo_items"] = [
            {"tad": "AAA", "razao_social": "FAZENDA TESTE LTDA", "cpf": "000", "date": "01/01/2026"}]
        original = fatos.limpar_pessoas
        fatos.limpar_pessoas = lambda valor: valor
        try:
            return regra_contrato(doente, result)
        finally:
            fatos.limpar_pessoas = original

    controle("dado pessoal no fato", com_dado_pessoal)

    def com_limpeza_desligada_e_dado_limpo():
        """Controle negativo: sem dado pessoal, a regra 1 não pode inventar problema."""
        original = fatos.limpar_pessoas
        fatos.limpar_pessoas = lambda valor: valor
        try:
            return regra_contrato(payload, result)
        finally:
            fatos.limpar_pessoas = original

    controle("payload limpo não vira falso positivo", com_limpeza_desligada_e_dado_limpo,
             deve_reprovar=False)

    def com_palavra_proibida():
        original = livro.frase_significado_p1
        livro.frase_significado_p1 = lambda L: "O imóvel está regular e sem pendências."
        try:
            _pdf, texto_doente, problemas_doente = desenhar(payload, result)
            return regra_portugues(texto_doente, problemas_doente)
        finally:
            livro.frase_significado_p1 = original

    controle("palavra proibida no texto do cliente", com_palavra_proibida)

    def com_numero_americano():
        original = livro.f_ha
        livro.f_ha = lambda x: f"{float(x)} ha"      # 14.795 ha à americana, o defeito do R1
        try:
            _pdf, texto_doente, problemas_doente = desenhar(payload, result)
            return regra_portugues(texto_doente, problemas_doente)
        finally:
            livro.f_ha = original

    controle("número em formato americano", com_numero_americano)

    def com_invencao():
        """Sem o modelo de elevação, o motor ainda afirma a inclinação: a regra 4 tem de ver."""
        original = livro.pagina_8

        def pagina_mentirosa(c, L, grade):
            L.fatos["relevo"] = fatos.envelope(
                "encontrado",
                {"classes_embrapa_pct": [{"classe": "plano", "faixa": "0–3%", "pct": 100.0}],
                 "inclinacao_mediana_pct": 2.0, "altitude_mediana_m": 600},
                fonte_nome="inventada")
            return original(c, L, grade)
        livro.pagina_8 = pagina_mentirosa
        try:
            return regra_sem_invencao(payload, result)
        finally:
            livro.pagina_8 = original

    controle("afirmação sem fonte respondida", com_invencao)

    def com_folha_vazia():
        return regra_ocupacao(["RX_BOOK_OCUPACAO_P2=31"], folhas_corpo=5)

    controle("folha de corpo vazia demais", com_folha_vazia)

    def com_frente_f2_apagada():
        """Desligando o bloco F2 das outorgas, a regra 8 tem de notar que a resposta sumiu do livro."""
        sem_outorga = payload_com_frentes_f2(payload)
        sem_outorga["water"]["grants_f2"] = {"state": "not_found", "items": [], "rows": [],
                                             "headers": [], "count": 0, "complete": True}
        sem_outorga["water"]["grant_count"] = 0
        with redirect_stdout(io.StringIO()):
            _p, texto_doente, _ = desenhar(sem_outorga, result)
        return regra_frentes_no_livro(texto_doente)

    controle("resposta da frente F2 apagada do livro", com_frente_f2_apagada)

    def com_nome_de_empresa():
        """Desligando a limpeza, o nome da empresa do armazém chega ao PDF: a regra 8 tem de pegar."""
        original = fatos.limpar_pessoas
        fatos.limpar_pessoas = lambda valor: valor
        original_itens = livro.pagina_10
        try:
            com_nome = payload_com_frentes_f2(payload)
            com_nome["infrastructure"]["warehouses_f2"]["items"][0]["tipo"] = "EMPRESA TESTE LTDA"
            with redirect_stdout(io.StringIO()):
                _p, texto_doente, _ = desenhar(com_nome, result)
            return regra_frentes_no_livro(texto_doente)
        finally:
            fatos.limpar_pessoas = original
            livro.pagina_10 = original_itens

    controle("nome de empresa no livro", com_nome_de_empresa)

    def com_relevo_em_graus():
        """Relevo escrito em graus: a regra 9 tem de ver (a T1 decidiu que o cliente lê %)."""
        original = livro.f_pct
        livro.f_pct = lambda x: f"{livro._ptbr(livro._q(livro.Decimal(str(x)), 1))}° mediana"
        try:
            with redirect_stdout(io.StringIO()):
                _p, texto_doente, _ = desenhar(payload, result)
            return regra_terra_verdade(texto_doente)
        finally:
            livro.f_pct = original

    controle("relevo escrito em graus", com_relevo_em_graus)

    def com_chuva_comparada():
        """Chuva recente comparada ao normal: a T1 tirou isso do cliente, e a regra 9 guarda."""
        original = livro.significado_agua
        livro.significado_agua = lambda L: "A chuva recente ficou acima do normal para a época."
        try:
            with redirect_stdout(io.StringIO()):
                _p, texto_doente, _ = desenhar(payload, result)
            return regra_terra_verdade(texto_doente)
        finally:
            livro.significado_agua = original

    controle("chuva recente comparada ao normal", com_chuva_comparada)

    def com_frase_que_contradiz_o_numero():
        """O defeito real: texto fixo "a maior parte" com um número que diz o contrário."""
        return regra_quantificador(
            "A maior parte do imóvel é plana ou pouco inclinada: 38% da área tem até 8% de inclinação.")

    controle("frase que contradiz o próprio número", com_frase_que_contradiz_o_numero)

    def com_frase_que_concorda():
        """Controle negativo: com 74%, a mesma frase é verdadeira e a regra não pode reprovar."""
        return regra_quantificador(
            "A maior parte do imóvel é plana ou pouco inclinada: 74% da área tem até 8% de inclinação.")

    controle("frase que concorda com o número", com_frase_que_concorda, deve_reprovar=False)

    def com_ligacao_quebrada():
        return regra_ligacao_em(
            (ROOT / "report_livro_patch.py").read_text(encoding="utf-8").replace(
                'os.getenv("RX_RELATORIO_LIVRO", "on")', 'os.getenv("RX_RELATORIO_LIVRO", "off")'),
            (ROOT / "sitecustomize.py").read_text(encoding="utf-8"))

    controle("padrão do livro trocado para desligado", com_ligacao_quebrada)

    def com_nome_da_cadeia_trocado():
        """Trocar report_api.generate_live_report derruba dois portões: a regra 7 tem de pegar antes."""
        return regra_ligacao_em(
            (ROOT / "report_livro_patch.py").read_text(encoding="utf-8")
            + "\n_api.generate_live_report = generate_live_report\n",
            (ROOT / "sitecustomize.py").read_text(encoding="utf-8"))

    controle("nome da cadeia trocado pelo livro", com_nome_da_cadeia_trocado)

    def cadeia_real_intacta():
        """A cadeia de verdade, com o livro ligado: o livro desenha E o contrato do V20 continua.

        Esta regra existe porque o jeito óbvio de adiantar a imagem — embrulhar
        ``report_api.generate_live_report`` — passava em tudo aqui e derrubava dois portões
        vizinhos, que provam a cadeia justamente por aquele nome.
        """
        import importlib

        problemas = []
        try:
            report_api = importlib.import_module("report_api")
            importlib.import_module("report_v20_patch")
            importlib.import_module("report_livro_patch")
            v20 = importlib.import_module("live_report_adapter_v20")
            v18 = importlib.import_module("live_report_adapter_v18")
            if getattr(report_api.generate_live_report, "__module__", "") != "live_report_adapter_v20":
                problemas.append("report_api.generate_live_report deixou de ser do live_report_adapter_v20 "
                                 "(quebra o v47-block2-contract e o f2_relatorio_v20)")
            if getattr(v20.render_v20, "__module__", "") != "report_livro_patch":
                problemas.append("o livro não está desenhando o PDF na cadeia real")
            if getattr(v18.build_premium_property_report_v8, "__module__", "") != "report_livro_patch":
                problemas.append("o V18 continua chamando o desenho antigo")
            if str(report_api.APP_VERSION) != "0.50.0-f2-verdade-e-lacunas":
                problemas.append(f"a versão do serviço mudou: {report_api.APP_VERSION}")
        except Exception as erro:
            problemas.append(f"a cadeia real não montou: {type(erro).__name__}: {erro}")
        return problemas

    print("== a cadeia real, com o livro ligado ==")
    problemas = cadeia_real_intacta()
    if problemas:
        for p in problemas:
            falhar("7 ligação ao serviço (cadeia real)", p)
    else:
        print("  ok · 7 ligação ao serviço (cadeia real)")

    print()
    if FALHAS:
        print(f"RX_LIVRO_GATE=FAIL ({len(FALHAS)} problema(s))")
        for f in FALHAS:
            print(" -", f)
        return 1
    print("RX_LIVRO_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
