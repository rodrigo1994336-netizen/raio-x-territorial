"""Vazão das outorgas de água que caem dentro do imóvel, sem dado pessoal.

Minas Gerais: IDE-Sisema, as duas camadas que ``water_mg`` já consulta —
IGAM (estaduais) e ANA (federais em MG). Os campos de vazão chegavam ao
servidor, mas as horas por dia (``tcap*_4``) e a data da base (``datverbase``)
eram cortados e a formatação só mostrava processo, portaria, situação, uso e
posição; a camada federal não era lida.

Fora de MG: ANA CNARH (dados abertos), atrás da chave ``RX_OUTORGA_ANA_CNARH``
(desligada por padrão). A unidade da vazão só foi provada na camada de
subterrâneas estaduais (volume anual = vazão × horas × 365 em 4 de 4 registros
conferidos com o IGAM, 13/09/2026); nas superficiais a vazão fica escondida.

Regras: nunca CPF/CNPJ, nome de titular, de responsável ou de empreendimento;
unidade vem do registro (sem unidade, a vazão não aparece) — na camada federal
da ANA em MG a unidade (m³/h) é provada registro a registro pelo volume anual;
o volume do IGAM não é usado (inconsistente com vazão × horas); "vazão
outorgada não é água garantida"; fonte que não respondeu por inteiro nunca vira
"nenhuma outorga".

Tipo de uso (14/09/2026, 2.258 outorgas do IGAM e 3.000 da ANA em MG): só
captação e explotação são "captação" e só elas "autorizam captar". Barramento
sem captação, dragagem, desvio, canalização, hidrelétrica e travessia têm texto
próprio, sem vazão. Lançamento de efluente e ponto de referência não entram na
leitura. Tipo desconhecido nunca vira captação. Outorga preventiva, revogada,
de uso de pouca expressão ou com situação desconhecida não "autoriza captar".
"""
from __future__ import annotations

import math
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

STATE_FOUND = "found"
STATE_NOT_FOUND = "not_found"
STATE_PENDING = "pending"
STATE_NOT_COVERED = "not_covered"
STATE_DISABLED = "disabled"

KIND_CAPTURE = "captacao"
KIND_DAM = "barramento"
KIND_DREDGE = "dragagem"
KIND_CHANNEL = "intervencao_curso"
KIND_HYDRO = "hidreletrica"
KIND_CROSSING = "travessia"
KIND_DISCHARGE = "lancamento"
KIND_REFERENCE = "referencia"
KIND_OTHER = "outro"
# Lançamento de efluente e ponto de referência não são uso de água do imóvel: ficam fora da leitura.
EXCLUDED_KINDS = frozenset({KIND_DISCHARGE, KIND_REFERENCE})

NOTE_FLOW = (
    "Vazão outorgada não é água garantida: é o que o órgão autorizou retirar, e não a água que a "
    "captação entrega hoje nem a garantia de que ela vai existir."
)
NOTE_POSITION = (
    "O registro indica onde fica o ponto outorgado; ele não diz quem é o titular nem que a outorga pertence a este imóvel."
)
NOTE_PARTIAL = "Parte do cadastro de outorgas não respondeu nesta emissão; pode haver outros registros não listados."

_MONTHS = (
    ("jan", 31), ("fev", 28), ("mar", 31), ("abr", 30), ("mai", 31), ("jun", 30),
    ("jul", 31), ("ago", 31), ("set", 30), ("out", 31), ("nov", 30), ("dez", 31),
)
# A camada do IGAM grafa julho como "vazjulh_4"; aceita as duas formas.
_FLOW_KEYS = {m: (f"vaz{m}_4",) if m != "jul" else ("vazjul_4", "vazjulh_4") for m, _ in _MONTHS}
# Camada federal da ANA no IDE-Sisema: nomes cortados em 10 letras pelo shapefile.
# Outubro a dezembro: vazão_10_/dias_mês1/horas_di_1 … (conferido pelo volume anual).
_ANA_MG_MONTH_KEYS = (
    ("vazão_1__", "dia_mês1", "horas_dia1"), ("vazão_2__", "dia_mês2", "horas_dia2"),
    *[(f"vazão_{i}__", f"dias_mês{i}", f"horas_dia{i}") for i in range(3, 10)],
    ("vazão_10_", "dias_mês1", "horas_di_1"), ("vazão_11_", "dias_mê_1", "horas_di_2"), ("vazão_12_", "dias_mê_2", "horas_di_3"),
)
ANA_MG_VOLUME_TOLERANCE = 0.05

IGAM_FIELDS = (
    "objectid", "id", "numpa_4", "numport_4", "statuspa_4", "moduso_4", "tipouso_4", "unvazao_4",
    "finuso1_4", "diavenc_4", "mesvenc_4", "anovenc_4", "aririgha_4", "datverbase", "datpub_4", "muncap_4",
    *[k for keys in _FLOW_KEYS.values() for k in keys],
    *[f"tcap{m}_4" for m, _ in _MONTHS],
    *[f"dia_{m}_4" for m, _ in _MONTHS],
)
ANA_MG_FIELDS = (
    "tipo_inter", "categoria", "resolucao", "numero_pro", "finalidade", "data_de_ve", "data_de_pu", "volumeanua", "uf",
    *[k for keys in _ANA_MG_MONTH_KEYS for k in keys],
)
ANA_FIELDS = (
    "objectid", "int_cd", "int_qt_vazaomedia", "int_qt_vazaomaxima", "org_nm", "org_uf", "out_nu_ato",
    "out_nu_processo", "out_tp_ato", "out_dt_outorgainicial", "out_dt_outorgafinal", "outorga_valida",
    "tsp_ds", "tfn_ds", "tin_ds", "tpo_ds", "tch_ds", "tdm_ds", "ing_sg_ufmunicipio",
)
PII_FIELD_PARTS = ("cpf", "cnpj", "nome", "nm_", "empto", "empreend", "respons", "titular", "requer", "usuario", "usuário", "email", "fone", "ender")

_UNITS = {
    "m³/h": ("m³/h", "m³ por hora"), "m3/h": ("m³/h", "m³ por hora"),
    "l/s": ("l/s", "litros por segundo"),
    "m³/s": ("m³/s", "m³ por segundo"), "m3/s": ("m³/s", "m³ por segundo"),
    "l/h": ("l/h", "litros por hora"),
    "m³/dia": ("m³/dia", "m³ por dia"), "m3/dia": ("m³/dia", "m³ por dia"), "m³/d": ("m³/dia", "m³ por dia"),
}


# ------------------------------------------------------------ campos seguros --
def _is_pii_field(name: str) -> bool:
    low = str(name).lower()
    return any(part in low for part in PII_FIELD_PARTS)


def safe_grant_props(props: dict[str, Any]) -> dict[str, Any]:
    """Lista explícita de campos da outorga (IGAM, ANA em MG e ANA CNARH), sem nenhum dado pessoal."""
    out: dict[str, Any] = {}
    for key in (*IGAM_FIELDS, *ANA_MG_FIELDS, *ANA_FIELDS):
        if key in (props or {}) and not _is_pii_field(key):
            value = props[key]
            if value in (None, "") or isinstance(value, (dict, list)):
                continue
            out[key] = value
    return out


# ----------------------------------------------------------------- números --
def _num(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def format_number(value: Decimal | float | int) -> str:
    """pt-BR sem arredondar o valor do ato além de 4 casas: 10 -> "10"; 44.4 -> "44,4"; 30660 -> "30.660"."""
    d = Decimal(str(value)).quantize(Decimal("0.0001"))
    text = f"{d:,.4f}".rstrip("0").rstrip(".")
    return text.translate(str.maketrans(",.", ".,"))


def _minutes(value: Any) -> int | None:
    text = str(value or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    number = _num(text)
    if number is None:
        return None
    return int((number * 60).to_integral_value())


def format_duration(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


def _unit(value: Any) -> tuple[str, str] | None:
    key = str(value or "").strip().lower().replace(" ", "")
    return _UNITS.get(key)


def _date_from_parts(day: Any, month: Any, year: Any) -> date | None:
    try:
        return date(int(str(year).strip()), int(str(month).strip()), int(str(day).strip()))
    except Exception:
        return None


def _date_any(value: Any) -> date | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 10**11:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
    text = str(value or "").strip()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        return _date_from_parts(m.group(1), m.group(2), m.group(3))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return _date_from_parts(m.group(3), m.group(2), m.group(1))
    return None


def _date_br(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def base_date(props: dict[str, Any]) -> str | None:
    """``GEIRH_v1_17-10-2025`` -> ``17/10/2025``."""
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", str(props.get("datverbase") or ""))
    if not m:
        return None
    d = _date_from_parts(m.group(1), m.group(2), m.group(3))
    return _date_br(d) if d else None


def _purposes(value: Any) -> str | None:
    items: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,;]", str(value or "")):
        item = " ".join(raw.split())
        if not item or item.lower() in ("na", "ni", "texto"):
            continue
        low = item.lower()
        if low in seen:
            continue
        seen.add(low)
        items.append(low)
    if not items:
        return None
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " e " + items[-1]


def _sentence_case(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text[:1].upper() + text[1:].lower()


# ------------------------------------------------------------ tipo de uso --
def igam_kind(value: Any) -> tuple[str, str | None]:
    """Tipo de uso do IGAM (``moduso_4``) -> (tipo, nome curto). Desconhecido nunca é captação."""
    text = " ".join(str(value or "").split())
    low = text.lower()
    if not low:
        return KIND_OTHER, None
    if "sem captação" in low or "sem captacao" in low:
        return (KIND_DAM, "barramento sem captação") if "barramento" in low else (KIND_OTHER, None)
    if "dragagem" in low:
        return KIND_DREDGE, ("dragagem para extração mineral" if "mineral" in low else "dragagem")
    if "hidrel" in low:
        return KIND_HYDRO, "aproveitamento hidrelétrico"
    if "desvio" in low:
        return KIND_CHANNEL, "desvio de curso d'água"
    if "canaliza" in low or "retifica" in low:
        return KIND_CHANNEL, "canalização de curso d'água"
    if "travessia" in low:
        return KIND_CROSSING, "travessia de curso d'água"
    if "lançamento" in low or "lancamento" in low:
        return KIND_DISCHARGE, None
    if low.startswith(("captação", "captacao")) or "explotação" in low or "explotacao" in low:
        if "poço tubular" in low:
            short = "poço tubular"
        elif "poço manual" in low or "cisterna" in low or "cacimba" in low:
            short = "poço manual"
        elif "surgência" in low or "nascente" in low:
            short = "captação em nascente"
        elif "barramento" in low:
            short = "captação em barramento"
        elif "corpo de água" in low or "curso de água" in low or "curso d" in low or re.search(r"\brios?\b", low):
            short = "captação em curso d'água"
        elif "subterr" in low or "explota" in low:
            short = "captação subterrânea"
        else:
            short = "captação"
        return KIND_CAPTURE, short
    if "barramento" in low:
        # Barramento que não diz "sem captação" (ex.: para regularização de vazão) também não é captação.
        return KIND_DAM, ("barramento para regularização de vazão" if "regulariza" in low else "barramento")
    return KIND_OTHER, None


def ana_kind(value: Any) -> tuple[str, str | None]:
    """Tipo de interferência da ANA (``tipo_inter`` / ``tin_ds``). Desconhecido nunca é captação."""
    low = " ".join(str(value or "").split()).lower()
    if "capta" in low:
        return KIND_CAPTURE, "captação"
    if "lança" in low or "lanca" in low:
        return KIND_DISCHARGE, None
    if "barrag" in low or "barrament" in low:
        return KIND_DAM, "barragem"
    if "refer" in low:
        return KIND_REFERENCE, None
    return KIND_OTHER, None


_KIND_FALLBACK_LABEL = {
    KIND_CAPTURE: "captação",
    KIND_DAM: "barramento",
    KIND_DREDGE: "dragagem",
    KIND_CHANNEL: "intervenção em curso d'água",
    KIND_HYDRO: "aproveitamento hidrelétrico",
    KIND_CROSSING: "travessia de curso d'água",
    KIND_OTHER: "outro uso de recurso hídrico",
}


# ------------------------------------------------------ situação da outorga --
def igam_status(value: Any) -> tuple[str | None, bool]:
    """(situação para o cliente, autoriza o uso?). Só deferida, renovada ou retificada autoriza."""
    text = _sentence_case(value)
    low = (text or "").lower()
    authorizes = bool(re.fullmatch(r"(outorga )?(deferid[ao]|renovad[ao]|retificad[ao])", low))
    return text, authorizes


def ana_mg_status(value: Any) -> tuple[str | None, bool, str | None]:
    """Categoria da ANA em MG -> (situação, autoriza o uso?, expressão para a frase)."""
    low = " ".join(str(value or "").split()).lower()
    if low == "direito de uso":
        return "Direito de uso", True, None
    if low.startswith("conversão de drdh") or low.startswith("conversao de drdh"):
        return "Direito de uso", True, None
    if "preventiva" in low:
        return "Outorga preventiva", False, "com outorga preventiva"
    if "pouca express" in low:
        return "Uso de pouca expressão", False, "de uso de pouca expressão, no cadastro"
    if "revoga" in low or "regova" in low:  # a camada grafa "Regovação"
        return "Revogada", False, "com outorga revogada"
    return _sentence_case(value), False, "no cadastro de outorgas"


def ana_cnarh_status(value: Any) -> tuple[str | None, bool]:
    text = _sentence_case(value)
    return text, bool(text and "outorgad" in text.lower())


# ---------------------------------------------------------------- vazão --
def _flow_summary(months: list[dict[str, Any]], unit: tuple[str, str]) -> dict[str, Any]:
    flows = [m["flow"] for m in months]
    lo, hi = min(flows), max(flows)
    unit_short, unit_long = unit
    if lo == hi:
        flow_text = f"{format_number(lo)} {unit_short}"
        flow_sentence = f"{format_number(lo)} {unit_long}"
    else:
        flow_text = f"de {format_number(lo)} a {format_number(hi)} {unit_short} conforme o mês"
        flow_sentence = f"de {format_number(lo)} a {format_number(hi)} {unit_long}, conforme o mês"
    minutes = [m["minutes"] for m in months if m["minutes"] is not None]
    hours_text = None
    if minutes and len(minutes) == len(months):
        mlo, mhi = min(minutes), max(minutes)
        hours_text = f"{format_duration(mlo)} por dia" if mlo == mhi else f"de {format_duration(mlo)} a {format_duration(mhi)} por dia, conforme o mês"
    days_text = None
    days = [m["days"] for m in months if m["days"] is not None]
    if days and len(days) == len(months):
        full = all(m["days"] >= m["days_in_month"] for m in months)
        if not full:
            dlo, dhi = min(days), max(days)
            days_text = f"{dlo} dias por mês" if dlo == dhi else f"de {dlo} a {dhi} dias por mês"
    n = len(months)
    months_text = "o ano todo" if n == 12 else ("1 mês por ano" if n == 1 else f"{n} meses por ano")
    return {
        "state": STATE_FOUND,
        "unit": unit_short,
        "min": float(lo),
        "max": float(hi),
        "flow_text": flow_text,
        "flow_sentence": flow_sentence,
        "hours_text": hours_text,
        "days_text": days_text,
        "months_count": n,
        "months_text": months_text,
    }


def igam_flow(props: dict[str, Any]) -> dict[str, Any]:
    """Vazão, horas por dia, dias e meses a partir dos campos mensais do IGAM."""
    unit = _unit(props.get("unvazao_4"))
    months: list[dict[str, Any]] = []
    for month, days_in_month in _MONTHS:
        flow = next((_num(props.get(k)) for k in _FLOW_KEYS[month] if props.get(k) not in (None, "")), None)
        minutes = _minutes(props.get(f"tcap{month}_4"))
        days = _num(props.get(f"dia_{month}_4"))
        if flow is None or flow <= 0:
            continue
        if minutes is not None and minutes <= 0:
            continue
        if days is not None and days <= 0:
            continue
        months.append({"month": month, "flow": flow, "minutes": minutes, "days": int(days) if days is not None else None, "days_in_month": days_in_month})
    if not months:
        return {"state": STATE_NOT_FOUND}
    if unit is None:
        # Sem unidade no registro a vazão não aparece (a unidade muda de registro para registro).
        return {"state": "unit_missing", "months_count": len(months)}
    return _flow_summary(months, unit)


def ana_mg_volume_matches(props: dict[str, Any], months: list[dict[str, Any]]) -> bool:
    """Prova da unidade (m³/h) registro a registro: volume anual = Σ vazão × horas × dias."""
    volume = _num(props.get("volumeanua"))
    if volume is None or volume <= 0 or not months:
        return False
    total = sum(m["flow"] * Decimal(m["minutes"]) / 60 * m["days"] for m in months)
    if total <= 0:
        return False
    return abs(volume - total) / volume <= Decimal(str(ANA_MG_VOLUME_TOLERANCE))


def ana_mg_flow(props: dict[str, Any]) -> dict[str, Any]:
    """Camada federal da ANA no IDE-Sisema: vazão mensal só com a unidade provada pelo volume."""
    months: list[dict[str, Any]] = []
    for (month, days_in_month), (flow_key, days_key, hours_key) in zip(_MONTHS, _ANA_MG_MONTH_KEYS):
        flow, days, hours = _num(props.get(flow_key)), _num(props.get(days_key)), _num(props.get(hours_key))
        if flow is None or flow <= 0 or days is None or days <= 0 or hours is None or hours <= 0:
            continue
        months.append({"month": month, "flow": flow, "minutes": int((hours * 60).to_integral_value()), "days": int(days), "days_in_month": days_in_month})
    if not months:
        return {"state": STATE_NOT_FOUND}
    if not ana_mg_volume_matches(props, months):
        return {"state": "unit_unproven", "months_count": len(months)}
    return _flow_summary(months, ("m³/h", "m³ por hora"))


# ---------------------------------------------------------------- uma outorga --
def _authority(item: dict[str, Any], props: dict[str, Any]) -> str | None:
    label = str(item.get("authority") or props.get("org_nm") or "").strip()
    if not label:
        return None
    return label.split(" - ")[0].split(" — ")[0].strip() or None


def today_brasilia() -> date:
    """Hoje no horário de Brasília. O servidor roda em UTC: das 21h à meia-noite o date.today() dele já é
    amanhã, e uma outorga que vence hoje sairia como vencida três horas antes."""
    from report_ptbr_v50 import BRT

    return datetime.now(BRT).date()


def grant_view(item: dict[str, Any], today: date | None = None, layer_unit: str | None = None) -> dict[str, Any]:
    """Uma outorga pronta para o cliente. ``layer_unit`` só para camadas da ANA CNARH com unidade provada."""
    today = today or today_brasilia()
    props = item.get("properties") or {}
    authority = _authority(item, props)
    phrase = None
    base = None
    if "tipo_inter" in props:  # ANA federal em MG (IDE-Sisema)
        source = "ana_mg"
        act_type = "Resolução"
        portaria = str(props.get("resolucao") or "").strip() or None
        processo = str(props.get("numero_pro") or "").strip().lstrip("#") or None
        situacao, authorizes, phrase = ana_mg_status(props.get("categoria"))
        purposes = _purposes(props.get("finalidade"))
        kind, mode_short = ana_kind(props.get("tipo_inter"))
        mode_full = _sentence_case(props.get("tipo_inter"))
        valid_until = _date_any(props.get("data_de_ve"))
        flow = ana_mg_flow(props)
        base = base_date(props)
    elif "out_nu_ato" in props or "tin_ds" in props or "int_cd" in props:  # ANA CNARH
        source = "ana_cnarh"
        act_type = _sentence_case(props.get("out_tp_ato")) or "Ato"
        portaria = str(props.get("out_nu_ato") or "").strip() or None
        processo = str(props.get("out_nu_processo") or "").strip() or None
        situacao, authorizes = ana_cnarh_status(props.get("tsp_ds"))
        purposes = _purposes(props.get("tfn_ds"))
        kind, _ = ana_kind(props.get("tin_ds"))
        mode_short = (item.get("mode_hint") or "captação") if kind == KIND_CAPTURE else None
        if kind == KIND_DAM:
            mode_short = "barragem"
        mode_full = _sentence_case(props.get("tin_ds"))
        valid_until = _date_any(props.get("out_dt_outorgafinal"))
        flow = {"state": STATE_NOT_FOUND}
        value = _num(props.get("int_qt_vazaomedia"))
        unit = _unit(layer_unit) if layer_unit else None
        if value is not None and value > 0:
            if unit is None:
                flow = {"state": "unit_unproven"}
            else:
                flow = {"state": STATE_FOUND, "unit": unit[0], "min": float(value), "max": float(value),
                        "flow_text": f"{format_number(value)} {unit[0]}", "flow_sentence": f"{format_number(value)} {unit[1]}",
                        "hours_text": None, "days_text": None, "months_count": None, "months_text": None}
    else:  # IGAM
        source = "igam"
        act_type = "Portaria"
        portaria = str(props.get("numport_4") or "").strip() or None
        processo = str(props.get("numpa_4") or "").strip() or None
        situacao, authorizes = igam_status(props.get("statuspa_4"))
        purposes = _purposes(props.get("finuso1_4"))
        kind, mode_short = igam_kind(props.get("moduso_4"))
        mode_full = _sentence_case(props.get("moduso_4"))
        valid_until = _date_from_parts(props.get("diavenc_4"), props.get("mesvenc_4"), props.get("anovenc_4"))
        flow = igam_flow(props)
        base = base_date(props)
    if kind != KIND_CAPTURE or not authorizes:
        # Vazão só aparece como "autorizada" em captação com outorga que autoriza o uso.
        if flow.get("state") == STATE_FOUND:
            flow = {"state": "not_authorized" if kind == KIND_CAPTURE else "not_capture"}
    if valid_until:
        validity = f"válida até {_date_br(valid_until)}" if valid_until >= today else f"vencida em {_date_br(valid_until)}"
    else:
        validity = None
    return {
        "source": source,
        "authority": authority,
        "kind": kind,
        "ato_tipo": act_type,
        "portaria": portaria,
        "processo": processo,
        "situacao": situacao,
        "autoriza": authorizes,
        "situacao_frase": phrase,
        "modo": mode_short,
        "modo_completo": mode_full,
        "finalidade": purposes,
        "validade": validity,
        "vencimento": _date_br(valid_until) if valid_until else None,
        "vencida": bool(valid_until and valid_until < today),
        "vazao": flow,
        "base_data": base,
        "distancia_m": item.get("distance_m"),
        "dentro": bool(item.get("inside")),
    }


def item_kind(item: dict[str, Any]) -> str:
    """Tipo de uso de um item bruto do ``water_mg`` / ANA CNARH (para contar a vizinhança)."""
    props = item.get("properties") or {}
    if "tipo_inter" in props:
        return ana_kind(props.get("tipo_inter"))[0]
    if "out_nu_ato" in props or "tin_ds" in props or "int_cd" in props:
        return ana_kind(props.get("tin_ds"))[0]
    return igam_kind(props.get("moduso_4"))[0]


_AUTHORITY_MASC = {"IGAM", "INEMA", "IAT", "IMASUL", "INEA", "IPAAM", "IMAC", "IGARN", "IEMA", "DAEE", "IDAF", "INMET"}
_AUTHORITY_FEM = {"ANA", "SEMA", "SEMAD", "SEMARH", "SEMACE", "SEMAS", "SEDAM", "ADASA", "AGERH", "APAC", "AESA", "COGERH", "SRH", "FEMARH", "CPRH", "SDS", "SEMADESC", "SEMAR"}


def _of_authority(authority: str | None) -> str:
    """"do IGAM" / "da ANA"; órgão sem gênero conhecido vai entre parênteses."""
    if not authority:
        return ""
    key = authority.split("(")[0].strip().upper()
    if key in _AUTHORITY_MASC:
        return f" do {authority}"
    if key in _AUTHORITY_FEM:
        return f" da {authority}"
    return f" ({authority})"


def grant_sentence(view: dict[str, Any]) -> str:
    """"Poço tubular com outorga do IGAM: autoriza captar 10 m³ por hora, 8 h 22 min por dia, o ano todo, ..." """
    kind = view.get("kind") or KIND_OTHER
    subject = view.get("modo") or _KIND_FALLBACK_LABEL.get(kind, _KIND_FALLBACK_LABEL[KIND_OTHER])
    subject = subject[:1].upper() + subject[1:]
    if kind == KIND_OTHER and view.get("modo_completo"):
        subject += f" ({view['modo_completo'].lower()})"
    if kind == KIND_CAPTURE and not view.get("autoriza"):
        head = f"{subject} {view.get('situacao_frase') or 'no cadastro de outorgas'}{_of_authority(view.get('authority'))}"
    else:
        # "Barramento sem captação, com outorga": sem a vírgula, lê-se "sem captação com outorga".
        sep = ", " if subject.lower().endswith("sem captação") else " "
        head = f"{subject}{sep}com outorga{_of_authority(view.get('authority'))}"
    flow = view.get("vazao") or {}
    clauses: list[str] = []
    if kind == KIND_CAPTURE and view.get("autoriza") and flow.get("state") == STATE_FOUND:
        verb = "autorizava captar" if view.get("vencida") else "autoriza captar"
        pieces = [f"{verb} {flow['flow_sentence']}"]
        pieces += [p for p in (flow.get("hours_text"), flow.get("days_text"), flow.get("months_text")) if p]
        clauses.append(", ".join(pieces))
    if view.get("finalidade"):
        clauses.append(f"para {view['finalidade']}")
    text = head + (": " + ", ".join(clauses) if clauses else "") + "."
    tail = []
    if view.get("portaria"):
        tail.append(f"{view.get('ato_tipo') or 'Ato'} {view['portaria']}")
    if view.get("validade"):
        tail.append(view["validade"])
    if tail:
        text += " " + ", ".join(tail) + "."
    return text


def _flow_shown(view: dict[str, Any]) -> bool:
    flow = view.get("vazao") or {}
    return view.get("kind") == KIND_CAPTURE and bool(view.get("autoriza")) and flow.get("state") == STATE_FOUND


def grant_row(view: dict[str, Any]) -> list[str]:
    flow = view.get("vazao") or {}
    shown = _flow_shown(view)
    regime = " · ".join(p for p in (flow.get("hours_text"), flow.get("days_text"), flow.get("months_text")) if p) if shown else ""
    uso = view.get("modo") or _KIND_FALLBACK_LABEL.get(view.get("kind") or KIND_OTHER, _KIND_FALLBACK_LABEL[KIND_OTHER])
    act = f"{view.get('ato_tipo') or 'Ato'} {view['portaria']}" if view.get("portaria") else (f"Processo {view['processo']}" if view.get("processo") else "")
    return [
        uso[:1].upper() + uso[1:],
        act,
        view.get("situacao") or "",
        flow.get("flow_text") if shown else "",
        regime,
        view.get("finalidade") or "",
        view.get("validade") or "",
    ]


GRANT_HEADERS = ["Uso", "Ato", "Situação", "Vazão autorizada", "Regime de captação", "Finalidade", "Validade"]


# ------------------------------------------------------- conferência IGAM×ANA --
def cross_check(views: list[dict[str, Any]], ana_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mesma portaria com vazão diferente no IGAM e na ANA: a vazão não aparece."""
    ana_by_act: dict[str, Decimal] = {}
    for item in ana_items or []:
        props = item.get("properties") or {}
        act = str(props.get("out_nu_ato") or "").strip()
        value = _num(props.get("int_qt_vazaomedia"))
        if act and value is not None:
            ana_by_act[act] = value
    out = []
    for view in views:
        flow = view.get("vazao") or {}
        act = view.get("portaria")
        if act in ana_by_act and flow.get("state") == STATE_FOUND:
            value = float(ana_by_act[act])
            if not (flow["min"] - 1e-9 <= value <= flow["max"] + 1e-9):
                view = {**view, "vazao": {"state": "divergent", "detail": "IGAM e ANA divergem para a mesma portaria"}}
        out.append(view)
    return out


# ---------------------------------------------------------------- payload --
def _layers_complete(water: dict[str, Any]) -> bool:
    layers = water.get("layers")
    if not isinstance(layers, dict) or not layers:
        return False
    for meta in layers.values():
        if not (meta or {}).get("ok"):
            return False
        # water_mg pede no máximo 3000 feições por camada; bater no teto é resposta possivelmente cortada.
        if int((meta or {}).get("feature_count_bbox") or 0) >= 3000:
            return False
    return True


def is_cut(source: dict[str, Any], key: str) -> bool:
    """O ``water_mg`` corta ``inside`` em 150 e ``near`` em 300; a contagem total fica em ``*_count``."""
    listed = len(source.get(key) or [])
    total = source.get(f"{key}_count")
    try:
        return total is not None and int(total) > listed
    except (TypeError, ValueError):
        return True


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _captures_phrase(n: int, at_least: bool = False) -> str:
    base = f"{n} {_plural(n, 'captação registrada', 'captações registradas')} no cadastro de outorgas"
    return ("ao menos " + base) if at_least else base


def water_grants_payload(
    water: dict[str, Any] | None,
    uf: str | None = None,
    today: date | None = None,
    ana: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Campos novos das outorgas dentro do imóvel, com estado explícito.

    ``state``: ``found`` · ``not_found`` (todas as camadas responderam por inteiro)
    · ``pending`` (fonte não respondeu; nada é afirmado) · ``not_covered``
    (imóvel fora de MG sem a camada nacional: a seção não aparece).
    """
    today = today or today_brasilia()
    uf_norm = str(uf or "").strip().upper() or None
    title = "Outorgas de água dentro do imóvel"
    ana_ok = bool(ana and ana.get("ok"))
    if uf_norm and uf_norm != "MG" and not ana_ok:
        if ana and ana.get("state") not in (None, STATE_DISABLED):
            return _pending_payload(title)
        return {"state": STATE_NOT_COVERED, "title": title, "rows": [], "items": [], "count": None,
                "headline": None, "near_text": None, "notes": [], "level": "neutral", "source_row": None, "compliance_row": None}
    source = ana if (uf_norm and uf_norm != "MG") else water
    if not source or not source.get("ok"):
        return _pending_payload(title)
    inside = list(source.get("inside") or [])
    layer_units = (source.get("layer_units") or {}) if source is ana else {}
    views = [grant_view(it, today, layer_units.get(it.get("layer"))) for it in inside]
    if source is water and ana_ok:
        views = cross_check(views, list(ana.get("inside") or []))
    views = [v for v in views if v["kind"] not in EXCLUDED_KINDS]
    captures = [v for v in views if v["kind"] == KIND_CAPTURE]
    others = [v for v in views if v["kind"] != KIND_CAPTURE]
    inside_cut = is_cut(source, "inside")
    complete = _layers_complete(source) and not inside_cut
    if not views and not complete:
        return _pending_payload(title)
    authority_names = sorted({v["authority"] for v in views if v.get("authority")}) or (["IGAM", "ANA"] if source is water else ["ANA"])
    n = len(captures)
    sentences: list[str] = []
    if captures:
        sentences.append(f"Há {_captures_phrase(n, inside_cut)} dentro do imóvel.")
        sentences += [grant_sentence(v) for v in captures]
    elif complete:
        where = "do IGAM e da ANA" if source is water else "nacional da ANA"
        sentences.append(f"Nenhuma captação registrada no cadastro de outorgas {where} dentro do imóvel.")
    if others:
        m = len(others)
        sentences.append(f"{'Há também' if captures else 'Há'} {m} {_plural(m, 'registro', 'registros')} de outorga de outro tipo dentro do imóvel.")
        sentences += [grant_sentence(v) for v in others]
    headline = " ".join(sentences)
    near_captures = [x for x in (source.get("near") or []) if not x.get("inside") and item_kind(x) == KIND_CAPTURE]
    near_cut = is_cut(source, "near")
    radius = source.get("radius_km") or 5
    radius_txt = f"{float(radius):g}".replace(".", ",")
    if near_captures:
        nearest = min(float(x.get("distance_m") or 0) for x in near_captures)
        km = format_number(Decimal(str(round(nearest / 1000, 1))))
        k = len(near_captures)
        lead = "Ao menos outras" if near_cut else ("Outra" if k == 1 else "Outras")
        near_text = (f"{lead} {k} {_plural(k, 'captação registrada', 'captações registradas')} no cadastro de outorgas "
                     f"a até {radius_txt} km; a mais próxima a {km} km.")
    else:
        near_text = (f"Nenhuma outra captação registrada no cadastro de outorgas a até {radius_txt} km."
                     if complete and not near_cut else None)
    notes = []
    if any(_flow_shown(v) for v in views):
        notes.append(NOTE_FLOW)
    if views:
        notes.append(NOTE_POSITION)
    bases: dict[str, str] = {}
    for v in views:
        if v.get("base_data") and v.get("authority"):
            bases.setdefault(v["authority"], v["base_data"])
    for authority, base in sorted(bases.items()):
        notes.append(f"Situação conforme o cadastro{_of_authority(authority)} de {base}.")
    if views and not complete:
        notes.append(NOTE_PARTIAL)
    authority_txt = " e ".join(authority_names)
    if n:
        count_txt = f"{_captures_phrase(n, inside_cut)} dentro do imóvel"
    elif complete:
        count_txt = "nenhuma captação registrada no cadastro de outorgas dentro do imóvel"
    else:
        count_txt = "captações dentro do imóvel: consulta parcial"
    if others:
        count_txt += f"; {len(others)} {_plural(len(others), 'registro', 'registros')} de outro tipo"
    return {
        "state": STATE_FOUND if views else STATE_NOT_FOUND,
        "title": title,
        "count": n,
        "count_is_minimum": inside_cut,
        "other_count": len(others),
        "items": views,
        "headers": GRANT_HEADERS,
        "rows": [grant_row(v) for v in views],
        "headline": headline,
        "near_text": near_text,
        "notes": notes,
        "level": "neutral",
        "complete": complete,
        "source_row": {
            "name": "Outorgas de água — IGAM/ANA" if source is water else "Outorgas de água — ANA (CNARH)",
            "description": f"Cadastro de outorgas ({authority_txt}) consultado nesta emissão: {count_txt}.",
            "status": "CONSULTADA" if complete else "PARCIAL",
            "level": "ok",
        },
        "compliance_row": {"label": "Outorgas de uso de água", "text": count_txt[:1].upper() + count_txt[1:] + ".", "badge": "CONSULTADA" if complete else "PARCIAL", "level": "neutral"},
    }


def _pending_payload(title: str) -> dict[str, Any]:
    return {
        "state": STATE_PENDING, "title": title, "count": None, "count_is_minimum": False, "other_count": None, "items": [],
        "headers": GRANT_HEADERS, "rows": [],
        "headline": "Outorgas de água: consulta pendente.", "near_text": None, "notes": [], "level": "neutral", "complete": False,
        "source_row": {"name": "Outorgas de água — IGAM/ANA", "description": "A fonte não respondeu nesta emissão.", "status": "INDISPONÍVEL", "level": "neutral"},
        "compliance_row": {"label": "Outorgas de uso de água", "text": "Consulta pendente.", "badge": "CONSULTA PENDENTE", "level": "neutral"},
    }


# ------------------------------------------------- ANA CNARH (fora de MG) --
ANA_CNARH_FLAG = "RX_OUTORGA_ANA_CNARH"
ANA_CNARH_LAYERS: dict[str, dict[str, Any]] = {
    "ana_estaduais_subterraneas": {
        "url": "https://portal1.snirh.gov.br/server/rest/services/Hosted/outorgas_estaduais_subterraneas/FeatureServer/0",
        "out_fields": ("objectid", "int_cd", "int_qt_vazaomedia", "org_nm", "org_uf", "out_nu_ato", "out_tp_ato",
                       "out_dt_outorgainicial", "out_dt_outorgafinal", "tfn_ds", "tin_ds", "tpo_ds", "tsp_ds",
                       "ing_sg_ufmunicipio"),
        "flow_unit": "m³/h",
        "unit_evidence": "volume anual = vazão × horas por dia × 365 em 4 de 4 registros conferidos com o IGAM (13/09/2026)",
        "mode_hint": "captação subterrânea",
    },
    "ana_estaduais_superficiais": {
        "url": "https://portal1.snirh.gov.br/arcgis/rest/services/DADOSABERTOS/outorgas_estaduais_superficial/MapServer/0",
        "out_fields": ("objectid", "int_cd", "int_qt_vazaomedia", "int_qt_vazaomaxima", "org_nm", "org_uf", "out_nu_ato",
                       "out_nu_processo", "out_tp_ato", "out_dt_outorgainicial", "out_dt_outorgafinal", "outorga_valida",
                       "tfn_ds", "tin_ds", "tch_ds", "tdm_ds"),
        "flow_unit": None,  # unidade não provada: vazão escondida
        "unit_evidence": "não provada (dicionário da ANA não respondeu)",
        "mode_hint": "captação superficial",
    },
    "ana_federais_superficiais": {
        "url": "https://portal1.snirh.gov.br/arcgis/rest/services/DADOSABERTOS/outorgas_federais_superficial/MapServer/4",
        "out_fields": ("objectid", "int_cd", "int_qt_vazaomedia", "int_qt_vazaomaxima", "org_nm", "org_uf", "out_nu_ato",
                       "out_nu_processo", "out_tp_ato", "out_dt_outorgainicial", "out_dt_outorgafinal", "outorga_valida",
                       "tfn_ds", "tin_ds", "tch_ds", "tdm_ds"),
        "flow_unit": None,
        "unit_evidence": "não provada (dicionário da ANA não respondeu)",
        "mode_hint": "captação superficial",
    },
}


def ana_enabled(env: dict[str, str] | None = None) -> bool:
    value = (env if env is not None else os.environ).get(ANA_CNARH_FLAG, "")
    return str(value).strip().lower() in ("1", "true", "on", "sim")


def ana_query_params(layer_key: str, envelope: list[float]) -> dict[str, str]:
    """Consulta ArcGIS com ``outFields`` explícito: nunca ``*`` (a camada tem CPF/CNPJ e nomes)."""
    layer = ANA_CNARH_LAYERS[layer_key]
    fields = [f for f in layer["out_fields"] if not _is_pii_field(f)]
    return {
        "f": "geojson", "where": "1=1", "geometry": ",".join(str(v) for v in envelope),
        "geometryType": "esriGeometryEnvelope", "inSR": "4674", "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(fields), "returnGeometry": "true", "outSR": "4674", "resultRecordCount": "2000",
    }


def ana_parse(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("features"), list):
        return {"ok": False, "detail": "resposta_invalida"}
    exceeded = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
    if exceeded:
        return {"ok": False, "detail": "resposta_truncada"}
    return {"ok": True, "features": data["features"]}


def ana_items(car: Any, features: list[dict[str, Any]], layer_key: str, radius_km: float = 5.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mesmo formato de item do ``water_mg`` (distância, dentro, propriedades seguras)."""
    from pyproj import CRS, Transformer
    from shapely.geometry import shape
    from shapely.ops import transform

    car_geom = shape(car) if isinstance(car, dict) else car
    c = car_geom.centroid
    tr = Transformer.from_crs("EPSG:4674", CRS.from_proj4(f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m +no_defs"), always_xy=True)
    car_m = transform(tr.transform, car_geom)
    layer = ANA_CNARH_LAYERS[layer_key]
    inside, near = [], []
    for feature in features or []:
        try:
            g = shape(feature.get("geometry"))
        except Exception:
            continue
        dist = float(car_m.distance(transform(tr.transform, g)))
        org = str((feature.get("properties") or {}).get("org_nm") or "").strip()
        item = {"distance_m": round(dist, 1), "inside": bool(car_geom.intersects(g)), "authority": org or "ANA",
                "layer": layer_key, "mode_hint": layer.get("mode_hint"), "properties": safe_grant_props(feature.get("properties") or {})}
        if item["inside"]:
            inside.append(item)
        if dist <= radius_km * 1000:
            near.append(item)
    near.sort(key=lambda x: x["distance_m"])
    return inside, near


def query_outorgas_ana_cnarh(car_geometry: dict[str, Any], bbox: list[float], radius_km: float = 5.0,
                            enabled: bool | None = None, client: Any = None) -> dict[str, Any]:
    """Caminho nacional pela ANA CNARH. Desligado por padrão (``RX_OUTORGA_ANA_CNARH``)."""
    if not (ana_enabled() if enabled is None else enabled):
        return {"ok": False, "state": STATE_DISABLED, "source": "ANA CNARH"}
    import httpx
    from shapely.geometry import shape

    car = shape(car_geometry)
    c = car.centroid
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.2, abs(math.cos(math.radians(c.y)))))
    xmin, ymin, xmax, ymax = bbox
    envelope = [xmin - dlon, ymin - dlat, xmax + dlon, ymax + dlat]
    own = client is None
    http = client or httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": "Raio-X-Territorial/ana-cnarh"})
    layers: dict[str, Any] = {}
    inside_all: list[dict[str, Any]] = []
    near_all: list[dict[str, Any]] = []
    try:
        for key, layer in ANA_CNARH_LAYERS.items():
            parsed: dict[str, Any] = {"ok": False, "detail": "nao_consultado"}
            for attempt in range(2):
                try:
                    r = http.get(layer["url"] + "/query", params=ana_query_params(key, envelope))
                    parsed = ana_parse(r.json()) if r.status_code == 200 else {"ok": False, "detail": f"http_{r.status_code}"}
                except Exception as exc:
                    parsed = {"ok": False, "detail": type(exc).__name__}
                if parsed.get("ok"):
                    break
            if parsed.get("ok"):
                inside, near = ana_items(car, parsed["features"], key, radius_km)
                inside_all += inside
                near_all += near
                layers[key] = {"ok": True, "feature_count_bbox": len(parsed["features"])}
            else:
                layers[key] = {"ok": False, "detail": parsed.get("detail")}
    finally:
        if own:
            http.close()
    near_all.sort(key=lambda x: x["distance_m"])
    ok = all(v.get("ok") for v in layers.values())
    return {
        "ok": ok, "state": "answered" if ok else STATE_PENDING, "source": "ANA CNARH (dados abertos)", "radius_km": radius_km,
        "inside": inside_all, "near": near_all, "layers": layers,
        "inside_count": len(inside_all), "near_count": len(near_all),
        "layer_units": {k: v.get("flow_unit") for k, v in ANA_CNARH_LAYERS.items()},
    }


print("RX_OUTORGA_VAZAO=igam_ana_mg_flow_hours_validity_kinds ana_cnarh:" + ("on" if ana_enabled() else "off"), flush=True)
