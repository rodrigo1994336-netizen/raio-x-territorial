"""V50 report presentation: every text that reaches the PDF is shown in pt-BR.

Adapters build report text with Python f-strings ("14.795 ha", "0.3699",
"2026-09-13T21:16:00+00:00", "rain_30d_mm: 31.46"). The owner's first rule is
that the system never shows wrong information: in Portuguese "14.795 ha" reads
as fourteen thousand hectares. Instead of chasing formatting through twenty
adapter layers, this module normalises the text at the single point where all
of it becomes PDF: reportlab's Paragraph.

Only unambiguous patterns are rewritten (a number followed by a unit, compact
or ISO dates, whole-cell SICAR codes, known internal keys, technical error
tails). CAR codes, process numbers, portarias and scene identifiers are left
untouched because they never match those patterns.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

BRT = timezone(timedelta(hours=-3))

_UNITS = (
    r"ha|%|mm/dia|mm|m³/h|cmol\(c\)/kg|g/kg|°C|°|km|m|t|cabeças|"
    r"Mil litros|Mil dúzias|Mil Reais|Toneladas|Hectares|cmol/kg"
)
# A Python-formatted decimal (single dot, no comma) immediately followed by a unit.
_DEC_UNIT = re.compile(r"(?<![\w.,/:\-])(-?\d+)\.(\d+)(\s?)(" + _UNITS + r")(?![\w²³])")
# A Python-formatted decimal inside "key: value" style text with no unit (e.g. "0.3699", "média: 0.339").
_BARE_DEC = re.compile(r"(?<![\w.,/:\-])(-?\d+)\.(\d+)(?![\w/\-%°]|[.,]\d)")
_ISO_DT = re.compile(r"(?<![\w])(20\d{2})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")
_ISO_D = re.compile(r"(?<![\w/])(20\d{2})-(\d{2})-(\d{2})(?![\w:])")
_COMPACT_D = re.compile(r"(?<![\w/])(20\d{2})(\d{2})(\d{2})(?![\w/])")
_TECH_TAIL = re.compile(r"\s*[:—-]?\s*(?:[A-Za-z]+(?:Error|Exception|Timeout)|Traceback)\b.*$", re.S)
_OFF = re.compile(r"\s*[—-]\s*OFF\b")

_CODES = {
    "AT": "Ativo", "PE": "Pendente", "SU": "Suspenso", "CA": "Cancelado",
    "IRU": "Imóvel Rural", "AST": "Assentamento", "PCT": "Povos e Comunidades Tradicionais",
}
_MONTHS = {
    "JAN": "JAN", "FEB": "FEV", "MAR": "MAR", "APR": "ABR", "MAY": "MAI", "JUN": "JUN",
    "JUL": "JUL", "AUG": "AGO", "SEP": "SET", "OCT": "OUT", "NOV": "NOV", "DEC": "DEZ",
}
_KEYS = {
    "rain_30d_mm": "chuva em 30 dias (mm)", "temp_avg_c": "temperatura média (°C)",
    "baixo_pct": "vigor baixo (%)", "médio_pct": "vigor médio (%)", "medio_pct": "vigor médio (%)",
    "alto_pct": "vigor alto (%)", "área_ha": "área (ha)", "area_ha": "área (ha)",
    "área_somada_ha": "área somada (ha)", "area_somada_ha": "área somada (ha)",
    "mediana_graus": "mediana (graus)", "p90_graus": "90% abaixo de (graus)",
    "declive_ate_8_pct": "declive até 8° (%)", "resolução_m": "resolução (m)", "resolucao_m": "resolução (m)",
    "inside": "no imóvel", "near": "na vizinhança", "interseções": "interseções",
}
_KEY_RX = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(_KEYS, key=len, reverse=True)) + r")\s*:")
_KEY_VALUE_RX = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(_KEYS, key=len, reverse=True)) + r")\s*:\s*(-?\d+)(?:\.(\d+))?(?![\d.,])")
_IDE_LAYER = re.compile(r"IDE:ide_[a-z0-9_]+")
# Internal engineering sentences that reached the client; replaced by what the reader needs.
_PHRASES = {
    "Completar a consulta SNCI/INCRA quando o conector público/autenticado estiver disponível.":
        "Consultar a certificação do imóvel no SNCI/INCRA.",
    "A reconciliação final remove placeholders antigos quando o conector efetivamente respondeu; um dado só permanece indisponível/restrito quando essa é a situação real desta emissão.":
        "Cada fonte é consultada de novo a cada emissão; uma consulta pendente é refeita na emissão seguinte.",
    "CONSULTADA, PARCIAL, INDISPONÍVEL, RESTRITA ou NÃO EXECUTADA":
        "CONSULTADA, PARCIAL, CONSULTA PENDENTE ou NÃO CONSULTADA",
}
_INPE_FILE = re.compile(r"focos_10min_(20\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})\.csv")


def _grouped(value: Decimal, digits: int) -> str:
    quant = Decimal(1).scaleb(-digits)
    text = f"{value.quantize(quant, rounding=ROUND_HALF_UP):,.{digits}f}"
    return text.translate(str.maketrans(",.", ".,"))


def format_decimal(value, digits: int) -> str:
    """pt-BR number with half-up rounding of the value as written (14.795 -> "14,80")."""
    return _grouped(Decimal(str(value)), digits)


def _ptbr_number(int_part: str, frac: str, unit: str) -> str:
    value = Decimal(f"{int_part}.{frac}")
    if unit in ("cabeças", "Mil litros", "Mil dúzias", "Mil Reais", "Toneladas", "Hectares") and value == value.to_integral_value():
        return _grouped(value, 0)
    if unit in ("m", "km", "t") and value == value.to_integral_value():
        return _grouped(value, 0)
    if unit == "ha" and 0 < abs(value) < 1:
        digits = min(4, max(2, len(frac)))
    else:
        digits = min(2, len(frac)) if unit not in ("ha", "%") else 2
    if value != 0 and abs(value) < Decimal(1).scaleb(-digits):
        return "< " + _grouped(Decimal(1).scaleb(-digits), digits)
    return _grouped(value, digits)


def _dec_unit(m: re.Match) -> str:
    return f"{_ptbr_number(m.group(1), m.group(2), m.group(4))}{m.group(3)}{m.group(4)}"


def _bare_dec(m: re.Match) -> str:
    int_part, frac = m.group(1), m.group(2)
    # "7.830" may already be pt-BR thousands (a decree number); only rewrite
    # what cannot be pt-BR: not three decimals, a leading zero or a long integer part.
    if len(frac) == 3 and int_part.lstrip("-") != "0" and len(int_part.lstrip("-")) <= 3:
        return m.group(0)
    value = Decimal(f"{int_part}.{frac}")
    if frac.strip("0") == "":
        return _grouped(value, 0)
    return _grouped(value, len(frac))


def _key_value(m: re.Match) -> str:
    key, int_part, frac = m.group(1), m.group(2), m.group(3) or ""
    unit = "ha" if key.endswith("_ha") else "%" if key.endswith("_pct") else ""
    if not frac:
        number = _grouped(Decimal(int_part), 0)
    elif unit:
        number = _ptbr_number(int_part, frac, unit)
    else:
        number = _grouped(Decimal(f"{int_part}.{frac}"), min(2, len(frac)))
    return f"{_KEYS[key]}: {number}"


def _iso_dt(m: re.Match) -> str:
    y, mo, d, hh, mi, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh), int(mi), tzinfo=timezone.utc if tz else BRT)
        if tz and tz not in ("Z",):
            sign = 1 if tz[0] == "+" else -1
            digits = tz[1:].replace(":", "")
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:] or 0))))
        local = dt.astimezone(BRT)
        return local.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return m.group(0)


def _iso_d(m: re.Match) -> str:
    y, mo, d = m.groups()
    try:
        datetime(int(y), int(mo), int(d))
    except ValueError:
        return m.group(0)
    return f"{d}/{mo}/{y}"


_TAG = re.compile(r"(<[^>]*>)")


def _normalize_segment(out: str) -> str:
    out = _OFF.sub("", out)
    out = out.replace("INTEGRAÇÃO PREPARADA", "NÃO ATIVADA NESTA VERSÃO").replace("INTEGRAÇÃO RESTRITA", "NÃO ATIVADA NESTA VERSÃO")
    out = re.sub(r"\bClimatologia (JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b", lambda m: "Climatologia " + _MONTHS[m.group(1)], out)
    out = _KEY_VALUE_RX.sub(_key_value, out)
    out = _KEY_RX.sub(lambda m: _KEYS[m.group(1)] + ":", out)
    out = re.sub(r"IDE-Sisema(?:[;,]\s*IDE-Sisema)+", "IDE-Sisema", _IDE_LAYER.sub("IDE-Sisema", out))
    for old, new in _PHRASES.items():
        out = out.replace(old, new)
    out = _INPE_FILE.sub(lambda m: f"arquivo de 10 minutos de {m.group(3)}/{m.group(2)}/{m.group(1)} {m.group(4)}:{m.group(5)}", out)
    out = _ISO_DT.sub(_iso_dt, out)
    out = _ISO_D.sub(_iso_d, out)
    out = _COMPACT_D.sub(lambda m: _iso_d(m) if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31 else m.group(0), out)
    out = _DEC_UNIT.sub(_dec_unit, out)
    return _BARE_DEC.sub(_bare_dec, out)


def _drop_tail(text: str, start: int, replacement: str) -> str:
    # Keep closing tags of the dropped tail so Paragraph markup stays balanced.
    closing = "".join(re.findall(r"</[^>]+>", text[start:]))
    return text[:start] + replacement + closing


def normalize_text(text):
    """Return text rewritten for a Portuguese reader. Non-strings pass through.

    Paragraph markup is preserved: only the text between tags is rewritten.
    """
    if not isinstance(text, str) or not text:
        return text
    stripped = text.strip()
    if stripped in _CODES:
        return text.replace(stripped, _CODES[stripped])
    out = text
    marker = out.find("Não respondeu nesta emissão")
    if marker >= 0:
        out = _drop_tail(out, marker, "Não respondeu nesta emissão.")
    else:
        tech = _TECH_TAIL.search(out)
        if tech:
            out = _drop_tail(out, tech.start(), ".")
    return "".join(part if part.startswith("<") and part.endswith(">") else _normalize_segment(part) for part in _TAG.split(out))


_PENDING_TEXT = "Esta consulta não pôde ser confirmada nesta emissão. Isso não é tratado como ausência de ocorrência; a consulta é refeita na próxima emissão."
# F2: a count taken from the PAMGIA mirror envelope is never the property's certification.
_LAND_SUMMARY = (
    "Certificação SIGEF e SNCI (INCRA): consulta pendente. "
    "Matrícula, ônus e titularidade dependem de certidão do cartório de registro de imóveis e não são inferidos do CAR."
)
# A row still built from the PAMGIA mirror means the official base was not asked in this emission: never "não respondeu".
_MIRROR_CERT_ROW = ["SIGEF", "CONSULTA PENDENTE", "—", "Consulta ao INCRA não realizada nesta emissão; isso não indica ausência de certificação."]


def _not_activated(status) -> bool:
    text = str(status or "").upper()
    return text.startswith(("INTEGRAÇÃO PREPARADA", "INTEGRAÇÃO RESTRITA", "NÃO ATIVADA"))


def client_payload(payload: dict) -> dict:
    """Copy of the report payload with only what a client should read.

    Connectors that are not active are internal roadmap, not information about the
    property, so their rows are left out. A source that did not answer is shown as a
    quiet pending consultation, never with the technical failure text.
    """
    import copy

    out = copy.deepcopy(payload or {})
    sources = []
    for src in out.get("sources") or []:
        if not isinstance(src, dict):
            sources.append(src)
            continue
        if _not_activated(src.get("status")) or "não ativado" in str(src.get("description") or "").lower():
            continue
        status = str(src.get("status") or "").upper()
        if status in ("INDISPONÍVEL", "INDISPONIVEL", "FALHOU", "ERRO"):
            src = {**src, "status": "CONSULTA PENDENTE", "description": _PENDING_TEXT}
        elif str(src.get("description") or "").startswith(". Origem usada nesta emissão"):
            src = {**src, "status": "CONSULTADA", "level": "ok",
                   "description": "O SICAR não publica denominação para este imóvel; ele é identificado pelo código do CAR."}
        sources.append(src)
    if "sources" in out:
        out["sources"] = sources
    # H1: a layer or check that did not answer (or whose base cannot prove absence)
    # reads as a quiet pending consultation, never as a loud "fonte indisponível".
    env = out.get("environment")
    if isinstance(env, dict) and isinstance(env.get("layer_rows"), list):
        env["layer_rows"] = [
            [r[0], "CONSULTA PENDENTE", *r[2:]] if isinstance(r, (list, tuple)) and len(r) > 1 and "FONTE INDISPONÍVEL" in str(r[1]).upper() else r
            for r in env["layer_rows"]
        ]
    for item in out.get("compliance") or []:
        if isinstance(item, dict) and "fonte indisponível" in str(item.get("text") or "").lower():
            item["text"] = "Consulta pendente."
            item["badge"] = "CONSULTA PENDENTE"
            item["level"] = "neutral"
    enf = out.get("enforcement")
    if isinstance(enf, dict):
        icmbio_pending = any(isinstance(i, dict) and i.get("label") == "Embargos ICMBio" and i.get("badge") == "CONSULTA PENDENTE" for i in out.get("compliance") or [])
        ibama_pending = bool(enf.get("embargo_pending"))
        if ibama_pending and str(enf.get("embargo_count") or 0) == "0":
            enf["embargo_count"] = "PENDENTE"
        if ibama_pending and icmbio_pending:
            enf["embargo_sources_label"] = "consulta pendente"
        elif ibama_pending:
            enf["embargo_sources_label"] = "ICMBio · IBAMA pendente"
        elif icmbio_pending:
            enf["embargo_sources_label"] = "IBAMA · ICMBio pendente"
    car = out.get("car")
    if isinstance(car, dict) and isinstance(car.get("fields"), list):
        # An empty field is not shown.
        car["fields"] = [r for r in car["fields"] if not (isinstance(r, (list, tuple)) and len(r) > 1 and str(r[1] if r[1] is not None else "").strip() == "")]
    land = out.get("land")
    if isinstance(land, dict):
        for key in ("certifications", "matrix"):
            rows = land.get(key)
            if isinstance(rows, list):
                land[key] = [r for r in rows if not (isinstance(r, (list, tuple)) and len(r) > 1 and _not_activated(r[1]))]
        certs = land.get("certifications")
        if isinstance(certs, list):
            land["certifications"] = [
                list(_MIRROR_CERT_ROW) if isinstance(r, (list, tuple)) and len(r) > 3 and str(r[0]).strip().upper() == "SIGEF" and "espelho" in str(r[3]).lower() else r
                for r in certs
            ]
        summary = str(land.get("summary") or "")
        # F2 + H1: a summary built from the PAMGIA mirror (with or without a parcel count) is never the certification.
        if "permanecem preparadas para ativação" in summary or re.search(r"\d+ parcela\(s\) candidata", summary) or summary.startswith("SIGEF público: consulta pendente"):
            land["summary"] = _LAND_SUMMARY
    return out


_INSTALLED = False


def install() -> None:
    """Normalise every Paragraph created by the report engines (idempotent)."""
    global _INSTALLED
    if _INSTALLED:
        return
    from reportlab.platypus import paragraph as rl_paragraph

    original = rl_paragraph.Paragraph.__init__

    def __init__(self, text, *args, **kwargs):
        return original(self, normalize_text(text), *args, **kwargs)

    rl_paragraph.Paragraph.__init__ = __init__
    rl_paragraph.Paragraph.__rx_ptbr_v50__ = True
    _INSTALLED = True
    print("RX_REPORT_PTBR_V50=paragraph_text_normalized", flush=True)
