"""R1 gate: the PDF report speaks Portuguese and never prints wrong-looking data.

Usage:
  PYTHONPATH=. python scripts/r1_report_ptbr_gate.py            # deterministic contract + synthetic PDF
  PYTHONPATH=. python scripts/r1_report_ptbr_gate.py --pdf X.pdf  # lint a real emitted report

The lint rules are checked against the raw adapter strings first (positive
control): a rule that cannot see the old defect proves nothing.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

UNIT = r"(?:ha|%|m|mm(?:/dia)?|°C|°|cmol\(c\)/kg|g/kg|cabeças|m³/h|km|t|Mil litros|Mil dúzias|Mil Reais|Toneladas|Hectares)"
RULES = {
    # 14.795 ha (Python float) reads as fourteen thousand hectares; pt-BR groups always have 3 digits.
    "numero_formato_americano": re.compile(r"(?<![\w.,/-])(?:0|\d{4,}|\d{1,3})\.(?:\d{1,2}|\d{4,})\s?" + UNIT + r"(?![\w])|(?<![\w.,/-])0\.\d{3}\s?" + UNIT + r"(?![\w])"),
    "inteiro_com_ponto_zero": re.compile(r"(?<![\w.,])\d+\.0\s?(?:cabeças|Mil|Toneladas|Hectares|m\b)"),
    "data_iso_ou_compacta": re.compile(r"\b20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2})?\b|(?<![\w/])20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?![\w/])"),
    "codigo_cru_status_tipo": re.compile(r"(?m)^(?:AT|PE|CA|SU|IRU|AST|PCT)$"),
    "cidade_como_nome": re.compile(r"Imóvel rural\s*[—·-]\s*[A-ZÀ-Ú][\wÀ-ú ]+/[A-Z]{2}"),
    "erro_tecnico": re.compile(r"HTTPStatusError|Traceback|Server error|https?://\S*(?:export|query|bbox)|[A-Za-z]+(?:Error|Exception)\b"),
    "chave_interna": re.compile(r"\b[a-z]+(?:_[a-z0-9]+){2,}\b|\b[a-z]+(?:_[a-z0-9]+)+\s*:\s*[-\d.]"),
    "mes_em_ingles": re.compile(r"\bClimatologia (?:FEB|APR|MAY|AUG|SEP|OCT|DEC)\b"),
    "jargao_integracao": re.compile(r"—\s*OFF\b|INTEGRAÇÃO (?:PREPARADA|RESTRITA)|NÃO ATIVADA|preparada na arquitetura|credencial|Nenhum zero foi inferido|conector|placeholder"),
    "ano_impossivel": re.compile(r"·\s*(?:20[3-9]\d|2[1-9]\d{2})\s*·"),
}

RAW_DEFECTS = {
    "numero_formato_americano": "O imóvel de 14.795 ha tem 14.496704 ha (97.98%)",
    "inteiro_com_ponto_zero": "2077.0 cabeças",
    "data_iso_ou_compacta": "Emitido em 2026-09-13T21:27:57.820727+00:00",
    "codigo_cru_status_tipo": "AT",
    "cidade_como_nome": "Imóvel rural — Curvelo/MG",
    "erro_tecnico": "Não respondeu nesta emissão: HTTPStatusError:Server error '500' for url 'https://server/export?bbox=1'",
    "chave_interna": "Sem imagem aberta: no_public_street_imagery_near_property",
    "mes_em_ingles": "Climatologia FEB",
    "jargao_integracao": "INTEGRAÇÃO PREPARADA — OFF",
    "ano_impossivel": "2.077 cabeças · 2077 · variação",
}


def lint_text(text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for name, rx in RULES.items():
        found = [m.group(0) for m in rx.finditer(text)]
        if found:
            hits[name] = found
    return hits


def test_rules_see_the_old_defects() -> None:
    for name, sample in RAW_DEFECTS.items():
        assert RULES[name].search(sample), f"positive control failed: {name} does not see {sample!r}"
    clean = "Área 14,80 ha · 1.243,57 ha · 2.077 cabeças · 2024 · Decreto 7.830/2012 · CAR MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F · Climatologia JAN"
    assert not lint_text(clean), lint_text(clean)


def test_normalize_text() -> None:
    from report_ptbr_v50 import normalize_text as n

    cases = {
        "14.795 ha": "14,80 ha",
        "14.496704 ha de interseção única (97.98% do CAR)": "14,50 ha de interseção única (97,98% do CAR)",
        "0.027909 ha": "0,0279 ha",
        "nuvens 0.000727%.": "nuvens < 0,01%.",
        "0.3699": "0,3699",
        "média 0.339; mediana 0.327.": "média 0,339; mediana 0,327.",
        "2077.0 cabeças": "2.077 cabeças",
        "Altitude mediana 712.0 m": "Altitude mediana 712 m",
        "chuva 6.08 mm/dia • média 23.97 °C": "chuva 6,08 mm/dia • média 23,97 °C",
        "cena Sentinel-2 datada de 2026-08-28": "cena Sentinel-2 datada de 28/08/2026",
        "Emitido em 2026-09-13T21:27:57.820727+00:00": "Emitido em 13/09/2026 18:27 (horário de Brasília)",
        "20260828": "28/08/2026",
        "AT": "Ativo",
        " IRU ": " Imóvel Rural ",
        "Climatologia FEB": "Climatologia FEV",
        "rain_30d_mm: 31.46": "chuva em 30 dias (mm): 31,46",
        "Não respondeu nesta emissão: HTTPStatusError: Server error for url 'https://x/export?bbox=1'": "Não respondeu nesta emissão.",
        # T2: o </b> do rabo cortado já estava fechado antes do corte; devolvê-lo deixava marcação solta.
        "<b>Esri</b> Não respondeu nesta emissão: ReadTimeout x</b>": "<b>Esri</b> Não respondeu nesta emissão.",
        "<b>Solo</b> 0 interseção(ões). TimeoutExpired:Command '['curl', '-sS']' timed out": "<b>Solo</b> 0 interseção(ões).",
        '<b>Rua:</b> <link href="https://maps.google.com/?q=1,2" color="#0E603B">abrir</link>':
            '<b>Rua:</b> <link href="https://maps.google.com/?q=1,2" color="#0E603B">abrir</link>',
        '<font size="7.5">14.795 ha</font>': '<font size="7.5">14,80 ha</font>',
        # Already Portuguese or identifiers: untouched.
        "1.243,57 ha": "1.243,57 ha",
        "R$ 1.234,56": "R$ 1.234,56",
        "Decreto 7.830/2012": "Decreto 7.830/2012",
        "Lei 12.651, de 2012": "Lei 12.651, de 2012",
        "Portaria 123.456 de 2020": "Portaria 123.456 de 2020",
        "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F": "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F",
        "Cena S2B_23KNV_20260828_0_L2A": "Cena S2B_23KNV_20260828_0_L2A",
        "anos identificados: 2004, 2006, 2014, 2021.": "anos identificados: 2004, 2006, 2014, 2021.",
        "V47.2": "V47.2",
        "Camadas: IDE:ide_2103_a_pto; IDE:ide_2103_b_pto.": "Camadas: IDE-Sisema.",
        "Completar a consulta SNCI/INCRA quando o conector público/autenticado estiver disponível.": "Consultar a certificação do imóvel no SNCI/INCRA.",
        "camada: IDE:ide_1502_mg_mapa_solos_pol": "camada: IDE-Sisema",
        "área_somada_ha: 14.804099; baixo_pct: 12.3456": "área somada (ha): 14,80; vigor baixo (%): 12,35",
        "Último arquivo processado: focos_10min_20260913_1820.csv.": "Último arquivo processado: arquivo de 10 minutos de 13/09/2026 15:20 (horário de Brasília).",
    }
    for raw, expected in cases.items():
        got = n(raw)
        assert got == expected, f"normalize_text({raw!r}) = {got!r}, expected {expected!r}"
    assert n(None) is None and n("") == ""


def test_client_payload() -> None:
    from report_ptbr_v50 import client_payload

    payload = {
        "sources": [
            {"name": "SICAR", "description": "ok", "status": "CONSULTADA"},
            {"name": "Busca por titular / CPF / CNPJ", "description": "Integração já preparada na arquitetura", "status": "INTEGRAÇÃO PREPARADA — OFF"},
            {"name": "MapBiomas vigor", "description": "credencial GEE", "status": "INTEGRAÇÃO PREPARADA — CREDENCIAL GEE"},
            {"name": "SNCI", "description": "Conector ainda não ativado nesta emissão.", "status": "NÃO CONSULTADA"},
            {"name": "Esri", "description": "Não respondeu nesta emissão: HTTPStatusError", "status": "INDISPONÍVEL", "level": "attention"},
            {"name": "Identidade/denominação do imóvel", "description": ". Origem usada nesta emissão: SICAR sem denominação pública.", "status": "PARCIAL"},
        ],
        "land": {
            "summary": "SIGEF público consultado nesta emissão: 0 parcela(s) candidata(s) no envelope do imóvel. SNCI/CCIR, matrícula, ônus e titularidade são integrações registrais/cadastrais separadas e permanecem preparadas para ativação por fonte legalmente habilitada; não são inferidas do CAR.",
            "certifications": [["SIGEF", "CONSULTADO", "0", "Espelho"], ["SNCI", "INTEGRAÇÃO PREPARADA — OFF", "—", "Ativação depende"]],
            "matrix": [["CAR", "AT", "14.795 ha", "Cadastro"], ["Matrícula", "INTEGRAÇÃO RESTRITA — OFF", "—", "conector"]],
        },
    }
    payload["car"] = {"fields": [["Denominação do imóvel", ""], ["Código CAR", "MG-1"], ["Condição", None]]}
    out = client_payload(payload)
    assert out["car"]["fields"] == [["Código CAR", "MG-1"]], out["car"]["fields"]
    names = [s["name"] for s in out["sources"]]
    assert names == ["SICAR", "Esri", "Identidade/denominação do imóvel"], names
    esri = out["sources"][1]
    assert esri["status"] == "CONSULTA PENDENTE" and "HTTPStatusError" not in esri["description"], esri
    ident = out["sources"][2]
    assert ident["status"] == "CONSULTADA" and "código do CAR" in ident["description"], ident
    assert [r[0] for r in out["land"]["certifications"]] == ["SIGEF"]
    # F2: the PAMGIA mirror envelope count never reaches the client as the certification.
    assert out["land"]["certifications"][0][1] == "CONSULTA PENDENTE", out["land"]["certifications"]
    assert [r[0] for r in out["land"]["matrix"]] == ["CAR"]
    assert "preparadas para ativação" not in out["land"]["summary"] and "parcela" not in out["land"]["summary"] and "consulta pendente" in out["land"]["summary"]
    # The saved payload is not mutated.
    assert len(payload["sources"]) == 6 and payload["sources"][4]["status"] == "INDISPONÍVEL"


def test_sidra_period_is_never_the_value() -> None:
    import agropecuaria

    header = {"NC": "Nível Territorial (Código)", "V": "Valor", "D1C": "Município (Código)", "D3C": "Ano (Código)", "D3N": "Ano", "D4N": "Tipo de rebanho"}
    row = {"NC": "6", "V": "2077", "D1C": "3120904", "D3C": "2024", "D3N": "2024", "D4N": "Bovino"}
    assert agropecuaria._sidra_period(row, header) == "2024"
    assert agropecuaria._sidra_period({"V": "2077", "D1C": "3120904"}, {}) is None


def test_municipality_is_never_the_name() -> None:
    import live_report_adapter_v13 as v13

    base = {"car": {"properties": {"municipio": "Curvelo", "uf": "MG"}}}
    assert v13._best_property_name(base) == ("", "SICAR sem denominação pública")
    typed = {**base, "_requested_property_name": "Fazenda digitada na busca"}
    assert v13._best_property_name(typed)[0] == "", "a typed or other-registry name is a reference, not the CAR name"
    unnamed = v13._patch_identity({"property": {"car_code": "MG-1"}, "car": {"status": "AT", "fields": [["Código CAR", "MG-1"]]}}, base, "", "SICAR sem denominação pública")
    assert all(row[0] != "Denominação do imóvel" for row in unnamed["car"]["fields"]), unnamed["car"]["fields"]
    assert unnamed["car"]["summary"].startswith("CAR MG-1"), unnamed["car"]["summary"]
    assert not unnamed["sources"][-1]["description"].startswith("."), unnamed["sources"][-1]
    named = {"car": {"properties": {"nome_imovel": "Fazenda Boa Vista", "municipio": "Curvelo"}}}
    assert v13._best_property_name(named) == ("Fazenda Boa Vista", "SICAR:nome_imovel")


def test_rendered_pdf_is_clean() -> None:
    from PIL import Image
    from pypdf import PdfReader
    from report_engine_v9 import build_premium_property_report_v9

    payload = {
        "report_id": "RX-R1-GATE",
        "generated_at": "2026-09-13T21:27:57.820727+00:00",
        "source_version": "R1 gate",
        "property": {"name": "", "area_ha": 14.795, "municipality": "Curvelo", "uf": "MG", "car_code": "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"},
        "car": {"status": "AT", "analysis_status": "Aguardando análise", "fields": [["Status do imóvel", "AT"], ["Tipo do imóvel", "IRU"], ["Módulos fiscais", "0.3699"]]},
        "land": {"certifications": [["SNCI", "INTEGRAÇÃO PREPARADA — OFF", "—", "Ativação depende de fonte/credencial legalmente habilitada."]], "matrix": [["CAR", "AT", "14.795 ha", "Cadastro ambiental consultado"]]},
        "enforcement": {}, "mining": {}, "productive": {}, "monitoring": {}, "conclusion": {},
        "water": {"rain_rows": [["Climatologia FEB", "chuva 6.08 mm/dia • média 23.97 °C"]]},
        "narrative": {"one_sentence": "O imóvel de 14.795 ha em Curvelo/MG tem 14.496704 ha de interseção (97.98% do CAR).", "what_we_found": ["Cena datada de 2026-08-28; nuvens 0.000727%."]},
        "sources": [
            {"name": "Esri World Imagery", "description": "Não respondeu nesta emissão: HTTPStatusError:Server error for url 'https://server/export?bbox=1'", "status": "INDISPONÍVEL", "level": "attention"},
            {"name": "Busca por titular / CPF / CNPJ", "description": "Integração já preparada na arquitetura; ativação pendente de credencial/habilitação.", "status": "INTEGRAÇÃO PREPARADA — OFF", "level": "neutral"},
            {"name": "IBGE / PPM", "description": "Rebanho 2077.0 cabeças · 2024", "status": "CONSULTADA", "level": "ok"},
        ],
        "agropecuaria": {}, "satellite_imagery": {}, "environment": {},
    }
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        image_path = td / "cover.jpg"
        Image.new("RGB", (32, 32), (240, 240, 240)).save(image_path, format="JPEG")
        payload["satellite_image_path"] = str(image_path)
        pdf = td / "r1_gate.pdf"
        build_premium_property_report_v9(pdf, payload)
        text = "\n".join((page.extract_text() or "") for page in PdfReader(str(pdf)).pages)
    hits = lint_text(text)
    assert not hits, f"PDF still prints defects: {hits}"
    flat = " ".join(text.split())
    for expected in ("14,80 ha", "Ativo", "CONSULTA PENDENTE", "2.077 cabeças"):
        assert expected in flat, f"expected {expected!r} in the rendered PDF"
    assert "CPF / CNPJ" not in text, "inactive connectors must not be listed to the client"


def lint_pdf(path: str) -> int:
    from pypdf import PdfReader

    total = 0
    for number, page in enumerate(PdfReader(path).pages, 1):
        for name, found in lint_text(page.extract_text() or "").items():
            total += len(found)
            print(f"p{number} {name}: {found[:4]}")
    print(f"RX_R1_REPORT_LINT_TOTAL={total}")
    return 1 if total else 0


def main() -> int:
    if "--pdf" in sys.argv:
        return lint_pdf(sys.argv[sys.argv.index("--pdf") + 1])
    test_rules_see_the_old_defects()
    test_normalize_text()
    test_client_payload()
    test_sidra_period_is_never_the_value()
    test_municipality_is_never_the_name()
    test_rendered_pdf_is_clean()
    print("RX_R1_REPORT_PTBR_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
