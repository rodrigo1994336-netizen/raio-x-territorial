"""Portão P0 do relatório-livro v10: a fixture confere com o contrato (SPEC §9.3 e §8.1).

Uso:
    python scripts/book_v10_schema_check.py                      # confere a F1
    python scripts/book_v10_schema_check.py caminho/fixture.json  # confere outra fixture
    python scripts/book_v10_schema_check.py --autoteste           # prova que o portão reprova defeitos

Confere: hash congelado (F1), envelope de cada Fato `_v51`, regras de estado, datas, dado pessoal,
textos-base (sem m², sem "(s)", números pt-BR, fonte só no apêndice quando pedido), PRODES pelas três
condições, regra do crédito pela tabela, e o `book_v10` (só desenho) com as imagens ao lado.
Só biblioteca padrão: roda no Windows e no CI.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import report_book_v10_contracts as C  # noqa: E402

FIX_DIR = ROOT / "tests" / "fixtures" / "book"
F1 = FIX_DIR / "curvelo_corrigido.json"
F1_SHA = FIX_DIR / "curvelo_corrigido.sha256"

CLASSES_DO_CONTRATO = (
    "Fato", "Imagem", "Geometria", "Identidade", "Resposta", "ItemConferencia", "Cabeca", "OcorrenciaProdes",
    "CapDesmatamento", "Achado", "CapRestricoes", "CapDocumentos", "CapAgua", "CapTerraUso", "CapTerraChao",
    "CapClima", "CapEntorno", "Apendice", "LinhaFonte", "LivroModelo", "Bloco", "FolhaSpec", "Plano",
    "BookAssetsMissing", "BookPayloadMissing", "LayoutError", "RelatorioIndisponivel",
)
TEXTO_PROIBIDO = ("m²", "(s)", "(is)", "(ões)", "sem pendências", "regularizad", "retificação", "Landsat8", "N/D")
NUMERO_EN = re.compile(r"(?<![\d.])\d+\.\d{1,2}(?![\d/])")          # "14.28" (ponto decimal); "5.303/2026" e "1.500" passam
PII = {
    "cpf": re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    "cnpj": re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "telefone": re.compile(r"\(\d{2}\)\s?\d{4,5}-\d{4}"),
}
ISO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_HORA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
CREDITO = (  # (limite superior de módulos fiscais, faixa, início) — SPEC §2.0.4, Res. CMN 5.303/2026
    (4.0, "ate_4", "2028-01-03"),
    (15.0, "de_4_a_15", "2027-07-01"),
    (float("inf"), "acima_15", "2027-01-04"),
)
LEITURAS_CHUVA = ("periodo_normalmente_seco", "abaixo_do_normal", "normal", "acima_do_normal")


# ------------------------------------------------------------------------------------------ utilidades

def resolver(dados: dict, caminho: str):
    cur = dados
    for k in caminho.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def data_valida(v) -> bool:
    if not isinstance(v, str) or not ISO_DATA.match(v):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def hora_valida(v) -> bool:
    if not isinstance(v, str) or not ISO_HORA.match(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def tamanho_jpeg(dados: bytes):
    """(largura, altura) lidas do marcador SOF do JPEG, sem PIL."""
    if dados[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(dados):
        if dados[i] != 0xFF:
            i += 1
            continue
        marca = dados[i + 1]
        if marca in (0xD8, 0x01) or 0xD0 <= marca <= 0xD7:
            i += 2
            continue
        seg = int.from_bytes(dados[i + 2:i + 4], "big")
        if marca in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            altura = int.from_bytes(dados[i + 5:i + 7], "big")
            largura = int.from_bytes(dados[i + 7:i + 9], "big")
            return largura, altura
        i += 2 + seg
    return None


def classe_prodes(o: dict) -> str:
    divisa = (o["inner_mean_width_m"] < C.PRODES_DIVISA_LARGURA_MAX_M
              and o["share_of_feature_pct"] < C.PRODES_DIVISA_PARTE_MAX_PCT
              and o["share_in_30m_band_pct"] >= C.PRODES_DIVISA_FAIXA_MIN_PCT)
    return "divisa" if divisa else "dentro"


def periodo_legal(image_date: str) -> str:
    if image_date <= C.PRODES_MARCO_2008:
        return "ate_2008_07_22"
    if image_date <= C.PRODES_MARCO_2019:
        return "ate_2019_07_31"
    return "depois_2019_07_31"


def textos(o, caminho=""):
    """Todos os textos (str) do objeto, com caminho."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield from textos(v, f"{caminho}.{k}" if caminho else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from textos(v, f"{caminho}[{i}]")
    elif isinstance(o, str):
        yield caminho, o


def chaves(o, caminho=""):
    if isinstance(o, dict):
        for k, v in o.items():
            atual = f"{caminho}.{k}" if caminho else k
            yield atual, k
            yield from chaves(v, atual)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from chaves(v, f"{caminho}[{i}]")


# ------------------------------------------------------------------------------------------ envelope

def erros_envelope(env, fato_id: str, caminho: str) -> list[str]:
    e: list[str] = []
    pre = f"[envelope] {fato_id} ({caminho})"
    if not isinstance(env, dict):
        return [f"{pre}: envelope ausente ou não é objeto"]
    for campo in C.ENVELOPE_V51_CAMPOS:
        if campo not in env:
            e.append(f"{pre}: falta o campo '{campo}'")
    permitidos = set(C.ENVELOPE_V51_CAMPOS) | set(C.ENVELOPE_V51_OPCIONAIS) | set(C.ENVELOPE_V51_EXTRAS.get(caminho, ()))
    for campo in env:
        if campo not in permitidos:
            e.append(f"{pre}: campo desconhecido '{campo}'")
    estado = env.get("estado")
    if estado not in C.ESTADOS:
        e.append(f"{pre}: estado inválido {estado!r}")
    valor = env.get("valor")
    if estado == "pendente":
        if valor is not None:
            e.append(f"{pre}: [estado] pendente com valor (tem de ser null)")
        if env.get("motivo") not in C.MOTIVOS:
            e.append(f"{pre}: [estado] pendente sem motivo válido")
    elif env.get("motivo") is not None:
        e.append(f"{pre}: [estado] motivo só existe em pendente")
    if estado in ("fora_desta_edicao", "nao_se_aplica") and valor is not None:
        e.append(f"{pre}: [estado] {estado} com valor (tem de ser null)")
    if estado in ("encontrado", "nao_encontrado") and valor is None:
        e.append(f"{pre}: [estado] {estado} sem valor")
    if estado == "nao_encontrado" and not (env.get("data_base") or env.get("consultado_em")):
        e.append(f"{pre}: [estado] ausência sem data (zero sempre com alcance)")
    fonte = env.get("fonte")
    if not isinstance(fonte, dict):
        e.append(f"{pre}: [fonte] ausente")
    else:
        for campo in C.FONTE_CAMPOS:
            if not isinstance(fonte.get(campo), str) or not fonte.get(campo).strip():
                e.append(f"{pre}: [fonte] '{campo}' vazio")
        for campo in fonte:
            if campo not in C.FONTE_CAMPOS + C.FONTE_OPCIONAIS:
                e.append(f"{pre}: [fonte] campo desconhecido '{campo}'")
        if "somente_apendice" in fonte and not isinstance(fonte["somente_apendice"], bool):
            e.append(f"{pre}: [fonte] somente_apendice não é booleano")
    if env.get("data_base") is not None and not data_valida(env.get("data_base")):
        e.append(f"{pre}: [data] data_base inválida {env.get('data_base')!r}")
    if env.get("consultado_em") is not None and not hora_valida(env.get("consultado_em")):
        e.append(f"{pre}: [data] consultado_em inválido {env.get('consultado_em')!r}")
    limites = env.get("limites")
    if not isinstance(limites, list) or not all(isinstance(x, str) and x.strip() for x in limites):
        e.append(f"{pre}: limites tem de ser lista de textos")
    if env.get("unidade") is not None and not isinstance(env.get("unidade"), str):
        e.append(f"{pre}: unidade tem de ser texto ou null")
    texto = env.get("texto_base")
    if not isinstance(texto, str) or not texto.strip():
        e.append(f"{pre}: [texto] texto_base vazio")
    else:
        for proibido in TEXTO_PROIBIDO:
            if proibido.lower() in texto.lower():
                e.append(f"{pre}: [texto] texto_base com '{proibido}'")
        achado = NUMERO_EN.search(texto)
        if achado:
            e.append(f"{pre}: [texto] número fora do padrão pt-BR '{achado.group(0)}'")
        if isinstance(fonte, dict) and fonte.get("somente_apendice"):
            # nomes próprios da fonte (siglas e palavras com maiúscula) não podem aparecer no corpo
            for token in re.findall(r"[A-Za-zÀ-ÿ]{4,}", fonte.get("nome_publico", "")):
                if token[0].isupper() and re.search(rf"\b{re.escape(token)}\b", texto):
                    e.append(f"{pre}: [texto] fonte só do apêndice aparece no texto ('{token}')")
    return e


# ------------------------------------------------------------------------------------------ regras por fato

def erros_prodes(env) -> list[str]:
    e: list[str] = []
    if not isinstance(env, dict) or env.get("estado") != "encontrado":
        return e
    occ = (env.get("valor") or {}).get("occurrences")
    if not isinstance(occ, list) or not occ:
        return ["[prodes] encontrado sem ocorrências"]
    ids = [o.get("id") for o in occ]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        e.append("[prodes] ids ausentes ou repetidos")
    campos = ("id", "year", "image_date", "prev_image_date", "area_inside_ha", "feature_area_ha", "inner_mean_width_m",
              "share_of_feature_pct", "share_in_30m_band_pct", "classe", "periodo_legal", "layer_kind")
    soma = 0.0
    for o in occ:
        pre = f"[prodes] {o.get('year')}"
        faltam = [c for c in campos if c not in o]
        if faltam:
            e.append(f"{pre}: faltam {faltam}")
            continue
        if "satellite" in o or "sensor" in o:
            e.append(f"{pre}: satélite/sensor do atributo da fonte nunca entra")
        if str(o["layer_kind"]).startswith("accumulated"):
            e.append(f"{pre}: máscara acumulada não é ocorrência")
        if o["classe"] not in C.PRODES_CLASSES or o["classe"] != classe_prodes(o):
            e.append(f"{pre}: [classe] {o['classe']!r} não confere com as três condições ({classe_prodes(o)})")
        if not data_valida(o["image_date"]) or o["periodo_legal"] != periodo_legal(o["image_date"]):
            e.append(f"{pre}: [periodo] {o['periodo_legal']!r} não confere com a data da imagem")
        if o["prev_image_date"] is not None and (not data_valida(o["prev_image_date"]) or o["prev_image_date"] >= o["image_date"]):
            e.append(f"{pre}: imagem anterior inválida")
        if not (0 < o["area_inside_ha"] <= o["feature_area_ha"]):
            e.append(f"{pre}: área dentro maior que a mancha")
        soma += o["area_inside_ha"]
    uniao = (env.get("valor") or {}).get("union_inside_ha")
    maior = max((o.get("area_inside_ha") or 0) for o in occ)
    if not isinstance(uniao, (int, float)) or not (maior - 1e-9 <= uniao <= soma + 1e-9):
        e.append("[prodes] union_inside_ha fora do intervalo [maior recorte, soma dos recortes]")
    return e


def erros_credito(env, dados: dict) -> list[str]:
    if not isinstance(env, dict) or env.get("estado") != "encontrado":
        return []
    v = env.get("valor") or {}
    mf = v.get("modulos_fiscais")
    if mf is None:
        return ["[credito] encontrado sem módulos fiscais (tem de ser pendente)"]
    if mf != (dados.get("car") or {}).get("fiscal_modules"):
        return ["[credito] módulos fiscais diferentes de car.fiscal_modules"]
    for teto, faixa, inicio in CREDITO:
        if mf <= teto:
            break
    if v.get("tipo_car") in ("AST", "PCT"):
        faixa, inicio = v.get("faixa_modulos"), "2028-01-03"
    e = []
    if v.get("faixa_modulos") != faixa or v.get("inicio_exigencia") != inicio:
        e.append(f"[credito] faixa/início {v.get('faixa_modulos')}/{v.get('inicio_exigencia')} ≠ tabela {faixa}/{inicio}")
    if v.get("marco") != C.PRODES_MARCO_2019:
        e.append("[credito] marco diferente de 31/07/2019")
    return e


def erros_especificos(dados: dict, fatos: dict) -> list[str]:
    e: list[str] = []
    e += erros_prodes(resolver(dados, fatos.get("prodes", "")))
    e += erros_credito(resolver(dados, fatos.get("regra_credito", "")), dados)
    chuva = resolver(dados, fatos.get("janela_chuva", ""))
    if isinstance(chuva, dict) and chuva.get("estado") == "encontrado":
        v = chuva["valor"]
        if v.get("leitura") not in LEITURAS_CHUVA:
            e.append("[chuva] leitura fora da lista")
        if not all(isinstance(v.get(k), (int, float)) for k in ("observado_mm", "esperado_mm")):
            e.append("[chuva] sem observado ou esperado")
        elif (v.get("esperado_mm") < 30) != bool(v.get("esperado_abaixo_30mm")):
            e.append("[chuva] esperado_abaixo_30mm não confere com o esperado")
    outorga = resolver(dados, fatos.get("outorga", ""))
    if isinstance(outorga, dict) and outorga.get("estado") == "encontrado":
        for g in outorga.get("valor") or []:
            vz = g.get("vazao")
            if vz is not None and not (isinstance(vz, dict) and vz.get("unidade")):
                e.append("[outorga] vazão sem unidade")
            if g.get("validade") is not None and not data_valida(g.get("validade")):
                e.append("[outorga] validade inválida")
    armazens = resolver(dados, fatos.get("armazens", ""))
    if isinstance(armazens, dict) and armazens.get("estado") == "encontrado":
        for a in armazens.get("valor") or []:
            if not (0 <= float(a.get("distancia_divisa_km", 999)) <= 50):
                e.append("[armazens] fora do raio de 50 km")
    comp = resolver(dados, fatos.get("composicao_car", ""))
    if isinstance(comp, dict) and comp.get("estado") == "encontrado" and not comp.get("data_base"):
        e.append("[composicao] composição declarada sem data da base")
    return e


# ------------------------------------------------------------------------------------------ book_v10

def erros_book(dados: dict, base_dir: Path, fatos: dict) -> list[str]:
    e: list[str] = []
    b = dados.get("book_v10")
    if not isinstance(b, dict):
        return ["[book_v10] ausente"]
    if b.get("schema") != C.BOOK_V10_SCHEMA:
        e.append(f"[book_v10] schema {b.get('schema')!r} ≠ {C.BOOK_V10_SCHEMA}")
    g = b.get("geometry") or {}
    for campo in ("crs", "car_m", "bbox_m", "centroid_lonlat", "short_side_m", "area_ha", "uf"):
        if campo not in g:
            e.append(f"[book_v10] geometry sem '{campo}'")
    if g.get("crs") != "EPSG:31983" and g.get("uf") == "MG":
        e.append("[book_v10] geometria de MG fora do EPSG:31983")
    if (g.get("car_m") or {}).get("type") not in ("Polygon", "MultiPolygon"):
        e.append("[book_v10] car_m não é polígono")
    for nome, minimo, enq in (("base_image", C.BOOK_V10_CAIXA_VIZINHANCA_MIN_M, "vizinhanca"),
                              ("sentinel_image", C.BOOK_V10_RECORTE_MEDIO_MIN_M, "medio"),
                              ("ndvi_image", C.BOOK_V10_RECORTE_MEDIO_MIN_M, "medio")):
        img = b.get(nome)
        if img is None or not img.get("ok"):
            continue
        pre = f"[imagem] {nome}"
        if img.get("enquadramento") != enq:
            e.append(f"{pre}: enquadramento {img.get('enquadramento')!r} ≠ {enq!r}")
        if img.get("crs") != g.get("crs"):
            e.append(f"{pre}: crs diferente da geometria")
        bx = img.get("bounds_m")
        px = img.get("px")
        if not (isinstance(bx, list) and len(bx) == 4 and isinstance(px, list) and len(px) == 2):
            e.append(f"{pre}: bounds_m ou px ausentes")
            continue
        lado = min(bx[2] - bx[0], bx[3] - bx[1])
        if lado + 1e-6 < minimo:
            e.append(f"{pre}: caixa de {lado:.0f} m menor que {minimo:.0f} m")
        if abs(((bx[2] - bx[0]) / (bx[3] - bx[1])) / (px[0] / px[1]) - 1) > 0.005:
            e.append(f"{pre}: proporção da caixa ≠ proporção dos pixels")
        gb = g.get("bbox_m") or [0, 0, 0, 0]
        if not (bx[0] <= gb[0] and bx[1] <= gb[1] and bx[2] >= gb[2] and bx[3] >= gb[3]):
            e.append(f"{pre}: a imagem não cobre o imóvel inteiro")
        for chave_arq in ("path", "path_muted"):
            rel = img.get(chave_arq)
            if rel is None:
                continue
            arq = base_dir / rel
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                e.append(f"{pre}: caminho tem de ser relativo à pasta da fixture")
                continue
            if not arq.is_file():
                e.append(f"{pre}: arquivo {rel} não existe")
                continue
            conteudo = arq.read_bytes()
            if len(conteudo) > C.BOOK_V10_IMAGEM_MAX_BYTES:
                e.append(f"{pre}: {rel} com {len(conteudo)} bytes (> {C.BOOK_V10_IMAGEM_MAX_BYTES})")
            if tamanho_jpeg(conteudo) != tuple(px):
                e.append(f"{pre}: {rel} tem {tamanho_jpeg(conteudo)} px, contrato diz {tuple(px)}")
    prodes = resolver(dados, fatos.get("prodes", ""))
    if isinstance(prodes, dict) and prodes.get("estado") == "encontrado":
        ids = {o.get("id") for o in (prodes.get("valor") or {}).get("occurrences") or []}
        figs = set((b.get("prodes_figuras") or {}).keys())
        if ids != figs:
            e.append(f"[book_v10] prodes_figuras ≠ ocorrências (faltam {sorted(ids - figs)}, sobram {sorted(figs - ids)})")
    fm = b.get("fiscal_module_ha")
    if fm is not None and fm.get("origin") not in ("conta_car", "tabela_incra"):
        e.append("[book_v10] fiscal_module_ha com origem desconhecida")
    return e


# ------------------------------------------------------------------------------------------ fixture inteira

def verificar(dados: dict, base_dir: Path) -> list[str]:
    e: list[str] = []
    meta = dados.get("_fixture")
    if not isinstance(meta, dict):
        return ["[fixture] sem _fixture"]
    fatos = meta.get("fatos")
    if not isinstance(fatos, dict) or not fatos:
        return ["[fixture] _fixture.fatos vazio"]
    ausentes = meta.get("ausentes_de_proposito") or {}
    for fato_id, caminho in fatos.items():
        if C.FATO_CHAVES.get(fato_id) != caminho:
            e.append(f"[fixture] {fato_id}: caminho {caminho!r} ≠ contrato {C.FATO_CHAVES.get(fato_id)!r}")
        e += erros_envelope(resolver(dados, caminho), fato_id, caminho)
    for fato_id, caminho in C.FATO_CHAVES.items():
        if fato_id not in fatos and caminho not in ausentes:
            e.append(f"[fixture] {fato_id} ({caminho}) nem está na fixture nem em ausentes_de_proposito")
    e += erros_especificos(dados, fatos)
    for caminho, chave in chaves(dados):
        if chave.lower() in C.CAMPOS_PROIBIDOS:
            e.append(f"[pessoal] campo proibido '{chave}' em {caminho}")
    for caminho, texto in textos(dados):
        for nome, rx in PII.items():
            if rx.search(texto):
                e.append(f"[pessoal] {nome} em {caminho}")
    e += erros_book(dados, base_dir, fatos)
    return e


def verificar_hash(sha_path: Path) -> tuple[list[str], set[str]]:
    if not sha_path.is_file():
        return [f"[hash] {sha_path.name} não existe"], set()
    e: list[str] = []
    linhas = [l for l in sha_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    vistos = set()
    for linha in linhas:
        m = re.match(r"^([0-9a-f]{64}) [ *](\S+)$", linha)
        if not m:
            e.append(f"[hash] linha fora do formato sha256sum: {linha!r}")
            continue
        esperado, rel = m.groups()
        arq = sha_path.parent / rel
        vistos.add(rel)
        if not arq.is_file():
            e.append(f"[hash] {rel} não existe")
        elif hashlib.sha256(arq.read_bytes()).hexdigest() != esperado:
            e.append(f"[hash] {rel} mudou depois de congelado")
    return e, vistos


def verificar_arquivo(fixture: Path, sha_path: Path | None) -> list[str]:
    e: list[str] = []
    bruto = fixture.read_bytes()
    if b"\r" in bruto:
        e.append("[arquivo] fixture com CRLF (tem de ser LF)")
    try:
        dados = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"[arquivo] não é JSON UTF-8: {exc}"]
    e += verificar(dados, fixture.parent)
    if sha_path is not None:
        erros_hash, vistos = verificar_hash(sha_path)
        e += erros_hash
        esperados = {fixture.name}
        b = dados.get("book_v10") or {}
        for nome in ("base_image", "sentinel_image", "ndvi_image"):
            for chave_arq in ("path", "path_muted"):
                if (b.get(nome) or {}).get(chave_arq):
                    esperados.add(b[nome][chave_arq])
        for rel in sorted(esperados - vistos):
            e.append(f"[hash] {rel} fora do {sha_path.name}")
    return e


# ------------------------------------------------------------------------------------------ autoteste

def autoteste() -> list[str]:
    """Cada defeito injetado precisa reprovar com a etiqueta certa (controle positivo do portão)."""
    dados = json.loads(F1.read_text(encoding="utf-8"))
    base = F1.parent
    limpo = verificar(dados, base)
    falhas = [f"F1 original já reprova: {limpo[:3]}"] if limpo else []
    fatos = dados["_fixture"]["fatos"]

    def env(fid, d):
        return resolver(d, fatos[fid])

    def m_pendente_com_valor(d): env("anm", d).update(estado="pendente", motivo="timeout")
    def m_cpf(d): d["agropecuaria"]["sif_chain"]["establishments"][0]["cpf"] = "x"
    def m_cnpj_texto(d): env("sif", d)["texto_base"] += " CNPJ 12.345.678/0001-90."
    def m_classe(d): env("prodes", d)["valor"]["occurrences"][3]["classe"] = "dentro"
    def m_periodo(d): env("prodes", d)["valor"]["occurrences"][3]["periodo_legal"] = "ate_2019_07_31"
    def m_numero_en(d): env("prodes", d)["texto_base"] = "Dentro do imóvel: 14.28 ha."
    def m_m2(d): env("composicao_car", d)["texto_base"] += " São 12.570 m²."
    def m_data(d): env("sigef", d)["data_base"] = "2026-13-01"
    def m_fonte_no_corpo(d): env("alertas_mensais", d)["texto_base"] = "Nenhum alerta do DETER sobre o imóvel."
    def m_credito(d): env("regra_credito", d)["valor"]["inicio_exigencia"] = "2027-01-04"
    def m_satelite(d): env("prodes", d)["valor"]["occurrences"][0]["satellite"] = "Landsat8"
    def m_figura(d): d["book_v10"]["prodes_figuras"].pop(next(iter(d["book_v10"]["prodes_figuras"])))
    def m_px(d): d["book_v10"]["base_image"]["px"] = [1000, 800]
    def m_caixa(d):  # caixa de 2.000 × 1.587 m, mesma proporção dos pixels, ainda cobrindo o imóvel
        bx = d["book_v10"]["base_image"]["bounds_m"]
        d["book_v10"]["base_image"]["bounds_m"] = [bx[0] + 260, bx[1] + 206.35, bx[2] - 260, bx[3] - 206.35]
    def m_ausencia_sem_data(d): env("ti", d).update(data_base=None, consultado_em=None)
    def m_caminho(d): fatos_d = d["_fixture"]["fatos"]; fatos_d["outorga"] = "water.grants"
    def m_fora_com_valor(d): env("car_ultima_alteracao", d)["valor"] = "2025-03-07"
    def m_fato_sumido(d): d["_fixture"]["fatos"].pop("pivo")
    def m_uniao(d): env("prodes", d)["valor"]["union_inside_ha"] = 20.0

    casos = [
        (m_pendente_com_valor, "pendente com valor"), (m_cpf, "campo proibido 'cpf'"), (m_cnpj_texto, "[pessoal] cnpj"),
        (m_classe, "[classe]"), (m_periodo, "[periodo]"), (m_numero_en, "fora do padrão pt-BR"), (m_m2, "com 'm²'"),
        (m_data, "data_base inválida"), (m_fonte_no_corpo, "fonte só do apêndice"), (m_credito, "[credito] faixa/início"),
        (m_satelite, "satélite/sensor"), (m_figura, "prodes_figuras ≠ ocorrências"), (m_px, "contrato diz"),
        (m_caixa, "menor que"), (m_ausencia_sem_data, "ausência sem data"), (m_caminho, "≠ contrato"),
        (m_fora_com_valor, "fora_desta_edicao com valor"), (m_fato_sumido, "nem está na fixture"),
        (m_uniao, "union_inside_ha"),
    ]
    for mutacao, trecho in casos:
        d = copy.deepcopy(dados)
        mutacao(d)
        erros = verificar(d, base)
        if not any(trecho in x for x in erros):
            falhas.append(f"{mutacao.__name__}: não reprovou com \"{trecho}\" (erros: {erros[:2]})")
    # hash: um byte a mais na cópia tem de mudar o sha256 congelado
    linhas = F1_SHA.read_text(encoding="utf-8").splitlines() if F1_SHA.is_file() else []
    congelado = next((l.split()[0] for l in linhas if l.split()[-1].lstrip("*") == F1.name), None)
    if congelado is None or hashlib.sha256(F1.read_bytes() + b" ").hexdigest() == congelado:
        falhas.append("hash: alteração da F1 não seria percebida")
    print(f"autoteste: {len(casos) + 1} defeitos injetados, {len(falhas)} passaram sem reprovar")
    return falhas


def main(argv: list[str]) -> int:
    faltam = [n for n in CLASSES_DO_CONTRATO if not hasattr(C, n)]
    if faltam:
        print(f"FALHA: contrato sem {faltam}")
        return 1
    if "--autoteste" in argv:
        falhas = autoteste()
        for f in falhas:
            print(f"FALHA: {f}")
        print("OK: portão reprova todos os defeitos injetados" if not falhas else "FALHA: autoteste")
        return 1 if falhas else 0
    alvo = Path(argv[0]) if argv else F1
    sha = F1_SHA if alvo.resolve() == F1.resolve() else None
    erros = verificar_arquivo(alvo, sha)
    for x in erros:
        print(f"FALHA: {x}")
    if erros:
        print(f"FALHA: {len(erros)} problema(s) em {alvo.name}")
        return 1
    dados = json.loads(alvo.read_text(encoding="utf-8"))
    estados: dict[str, int] = {}
    for caminho in dados["_fixture"]["fatos"].values():
        est = resolver(dados, caminho)["estado"]
        estados[est] = estados.get(est, 0) + 1
    print(f"OK: {alvo.name} confere com o contrato ({len(dados['_fixture']['fatos'])} Fatos: {estados})"
          + ("; hash congelado confere" if sha else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
