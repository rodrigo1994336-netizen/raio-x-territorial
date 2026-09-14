"""F2 · Chuva recente comparada ao normal da mesma época e climatologia com rótulo certo.

Por que existe: a triagem antiga ("atenção" com menos de 50 mm e 65 % de dias
secos) media a estação e não a anomalia. Em Curvelo/MG, 13/08 a 11/09/2026,
choveu 1,9 vez o normal e ela deu "atenção"; na série de 1991 a 2025 daria
alerta em 34 de 35 anos. A climatologia do NASA POWER também era impressa com
rótulo errado: T2M_MAX/T2M_MIN são o recorde do período 2001–2020, não a média
das máximas, e a chuva vem em mm/dia.

Regra declarada (sem limite fixo de mm, sem dias secos como gatilho):

1. Percentil na série longa (preferido, quando ``history`` é passado).
   Soma da chuva nos mesmos dias do calendário em cada ano da série diária
   NASA POWER (1991 até o último ano completo), no mínimo 20 anos.
   - percentil <= 20 e média da época >= 1 mm/dia -> "abaixo do normal" (alerta);
   - percentil <= 20 e média da época <  1 mm/dia -> "época de pouca chuva" (sem alerta);
   - percentil >= 80 e chuva - mediana >= 10 mm  -> "acima do normal";
   - resto -> "dentro do normal".
2. Razão contra a climatologia mensal 2001–2020 (sem chamada extra), só para
   janelas de 28 a 120 dias. Esperado = soma, dia a dia, do mm/dia do mês.
   - chuva < 40 % do esperado e esperado >= 1,5 mm/dia -> "abaixo do normal" (alerta);
   - chuva < 40 % do esperado e esperado <  1,5 mm/dia -> "época de pouca chuva";
   - chuva > 150 % do esperado e excesso >= 10 mm      -> "acima do normal";
   - resto -> "dentro do normal".
   Limites conferidos contra a série real de Curvelo (gate
   ``scripts/f2_clima_landsat_gate.py``): nenhum "abaixo" pela razão cai acima
   do percentil 30 e nenhum "acima" abaixo do percentil 70.
3. Janela com menos de 28 dias -> ``not_found`` (não se compara uma semana).
   Chuva recente ou referência que não respondeu -> ``pending``; nunca estado
   inventado.
"""
from __future__ import annotations

from array import array
from collections import OrderedDict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
import json
import math
import subprocess
import threading
from typing import Any
from urllib.parse import urlencode

POWER_DAILY = "https://power.larc.nasa.gov/api/temporal/daily/point"
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MONTH_DAYS = (31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

MIN_WINDOW_DAYS = 28
MIN_REFERENCE_YEARS = 20
PCT_LOW = 20.0
PCT_HIGH = 80.0
PCT_DRY_SEASON_MM_DAY = 1.0
RATIO_MAX_WINDOW_DAYS = 120
RATIO_LOW = 0.40
RATIO_HIGH = 1.50
RATIO_DRY_SEASON_MM_DAY = 1.5
MIN_EXCESS_MM = 10.0

STATE_LABELS = {
    "below_normal": "abaixo do normal",
    "normal": "dentro do normal",
    "above_normal": "acima do normal",
    "dry_season": "época de pouca chuva",
}
NOTE = (
    "Compara a chuva do período com o normal dos mesmos dias do calendário na grade NASA POWER "
    "do centróide do imóvel. Não é índice oficial de seca (SPI/SPEI) nem medição da fazenda."
)

# Measured 13/09/2026 from Brazil: 1.035-1.531 s for 1991-2025 PRECTOTCORR (6 calls, 202 kB).
HISTORY_MAX_TIME_S = 12
HISTORY_CONNECT_TIMEOUT_S = 5
_HISTORY_CACHE_MAX = 64
_history_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_history_lock = threading.Lock()


# --------------------------------------------------------------------------- formatting

def fmt_num(value: Any, digits: int = 1) -> str:
    """pt-BR number: 1.234,5 — never the Python dot decimal."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number):
        return ""
    text = f"{number:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return text[1:] if text.startswith("-") and not any(ch in "123456789" for ch in text) else text


def _fmt_date(yyyymmdd: str | None) -> str:
    raw = str(yyyymmdd or "")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[6:8]}/{raw[4:6]}/{raw[:4]}"
    return ""


def _parse_day(yyyymmdd: Any) -> date | None:
    raw = str(yyyymmdd or "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def _valid(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= -900:
        return None
    return number


# --------------------------------------------------------------------------- climatology

def climatology_month_row(month: str, parameter: dict[str, Any], rain_units: str | None = "mm/day") -> dict[str, Any]:
    """One month of the POWER climatology with honest keys.

    ``T2M_MAX``/``T2M_MIN`` of the climatology endpoint are the highest/lowest
    daily value of 2001–2020 (checked against the daily series for all 12
    months in Curvelo), so they are exposed only as records.
    """
    idx = MONTHS.index(month)
    rain_day = _valid((parameter.get("PRECTOTCORR") or {}).get(month))
    if rain_day is not None and str(rain_units or "").strip().lower() not in {"mm/day", "mm day-1", "mm/dia"}:
        rain_day = None  # unit we have not verified: hide rather than mislabel
    return {
        "month": month,
        "rain_mm_day": rain_day,
        # whole mm, rounded once (half-up) so PDF and portal print the same number
        "rain_mm_month": int(Decimal(str(rain_day * MONTH_DAYS[idx])).quantize(Decimal("1"), ROUND_HALF_UP)) if rain_day is not None else None,
        "t_avg_c": _valid((parameter.get("T2M") or {}).get(month)),
        "t_max_record_c": _valid((parameter.get("T2M_MAX") or {}).get(month)),
        "t_min_record_c": _valid((parameter.get("T2M_MIN") or {}).get(month)),
    }


def parse_climatology_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Parse a raw POWER climatology/point JSON into rows + reference period."""
    props = (data or {}).get("properties") or {}
    parameter = props.get("parameter") or {}
    units = (((data or {}).get("parameters") or {}).get("PRECTOTCORR") or {}).get("units")
    rows = [climatology_month_row(m, parameter, units) for m in MONTHS]
    rows = [r for r in rows if any(r.get(k) is not None for k in ("rain_mm_day", "t_avg_c", "t_max_record_c", "t_min_record_c"))]
    header_range = str(((data or {}).get("header") or {}).get("range") or "")
    period = "2001–2020" if "2001" in header_range and "2020" in header_range else None
    return {"months": rows, "period": period}


def climatology_row_text(row: dict[str, Any], period: str | None = None) -> str:
    """PDF text for one climatology month; missing parts are omitted, never '—'."""
    parts = []
    if row.get("rain_mm_day") is not None:
        parts.append(f"chuva ≈ {fmt_num(row.get('rain_mm_month'), 0)} mm no mês ({fmt_num(row.get('rain_mm_day'), 2)} mm/dia)")
    if row.get("t_avg_c") is not None:
        parts.append(f"temperatura média {fmt_num(row.get('t_avg_c'), 1)} °C")
    records = []
    if row.get("t_max_record_c") is not None:
        records.append(f"máx {fmt_num(row.get('t_max_record_c'), 1)} °C")
    if row.get("t_min_record_c") is not None:
        records.append(f"mín {fmt_num(row.get('t_min_record_c'), 1)} °C")
    if records:
        parts.append(f"recorde {period or 'do período'}: " + ", ".join(records))
    return " • ".join(parts)


def climatology_pdf_rows(climatology: dict[str, Any]) -> list[list[str]]:
    period = climatology.get("period")  # only the period the response header declares
    rows = []
    for row in (climatology.get("months") or [])[:12]:
        text = climatology_row_text(row, period)
        if text:
            rows.append([f"Climatologia {row.get('month')}", text])
    return rows


# --------------------------------------------------------------------------- rain history (network)

def _history_key(lon: float, lat: float, first_year: int, last_year: int) -> tuple:
    return (round(lat, 2), round(lon, 2), first_year, last_year)


def compact_history(series: dict[str, Any], first_year: int, last_year: int) -> dict[str, Any]:
    """Store a POWER daily PRECTOTCORR series as one float array from 1 Jan first_year."""
    start = date(first_year, 1, 1)
    days = (date(last_year, 12, 31) - start).days + 1
    values = array("d", [math.nan]) * days
    for key, raw in (series or {}).items():
        d = _parse_day(key)
        v = _valid(raw)
        if d is None or v is None or d < start:
            continue
        i = (d - start).days
        if i < days:
            values[i] = v
    return {"start": start, "values": values}


def _history_value(history: dict[str, Any], d: date) -> float | None:
    start = history.get("start")
    values = history.get("values")
    if start is None or values is None:
        series = history.get("series") or {}
        return _valid(series.get(d.strftime("%Y%m%d")))
    i = (d - start).days
    if i < 0 or i >= len(values):
        return None
    v = values[i]
    return None if math.isnan(v) else v


def query_rain_history_nasa(car_geometry: dict[str, Any], first_year: int = 1991, last_year: int | None = None) -> dict[str, Any]:
    """Daily PRECTOTCORR series at the property centroid (1 call, cached per ~1 km cell)."""
    try:
        from shapely.geometry import shape

        c = shape(car_geometry).centroid
        lon, lat = float(c.x), float(c.y)
    except Exception as exc:  # geometry problem is ours, not the source's
        return {"ok": False, "status": "pending", "source": "NASA POWER - Daily API", "detail": f"centroid:{type(exc).__name__}"}
    last_year = int(last_year or (date.today().year - 1))
    key = _history_key(lon, lat, first_year, last_year)
    with _history_lock:
        hit = _history_cache.get(key)
        if hit is not None:
            _history_cache.move_to_end(key)
            return hit
    params = {
        "parameters": "PRECTOTCORR", "community": "AG", "longitude": lon, "latitude": lat,
        "start": f"{first_year}0101", "end": f"{last_year}1231", "format": "JSON", "time-standard": "UTC",
    }
    try:
        proc = subprocess.run(
            ["curl", "-sS", "--connect-timeout", str(HISTORY_CONNECT_TIMEOUT_S), "--max-time", str(HISTORY_MAX_TIME_S),
             "-A", "Raio-X-Territorial/f2-climate-normal", POWER_DAILY + "?" + urlencode(params)],
            capture_output=True, timeout=HISTORY_MAX_TIME_S + 5,
        )
    except Exception as exc:
        return {"ok": False, "status": "pending", "source": "NASA POWER - Daily API", "detail": f"curl:{type(exc).__name__}"}
    if proc.returncode:
        return {"ok": False, "status": "pending", "source": "NASA POWER - Daily API", "detail": proc.stderr.decode("utf-8", "ignore")[:200]}
    try:
        data = json.loads(proc.stdout.decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "status": "pending", "source": "NASA POWER - Daily API", "detail": f"json:{type(exc).__name__}"}
    result = history_from_power_payload(data, first_year, last_year)
    if result.get("ok"):
        with _history_lock:
            _history_cache[key] = result
            while len(_history_cache) > _HISTORY_CACHE_MAX:
                _history_cache.popitem(last=False)
    return result


def history_from_power_payload(data: dict[str, Any], first_year: int, last_year: int) -> dict[str, Any]:
    series = (((data or {}).get("properties") or {}).get("parameter") or {}).get("PRECTOTCORR") or {}
    units = (((data or {}).get("parameters") or {}).get("PRECTOTCORR") or {}).get("units")
    if not series:
        return {"ok": False, "status": "pending", "source": "NASA POWER - Daily API", "detail": "no_series"}
    if str(units or "mm/day").strip().lower() != "mm/day":
        return {"ok": False, "status": "pending", "source": "NASA POWER - Daily API", "detail": f"units:{units}"}
    compact = compact_history(series, first_year, last_year)
    return {"ok": True, "status": "found", "source": "NASA POWER - Daily API (PRECTOTCORR)", "first_year": first_year,
            "last_year": last_year, **compact}


# --------------------------------------------------------------------------- the rule (pure)

def _recent_days(recent: dict[str, Any]) -> list[tuple[date, float]]:
    out = []
    for item in recent.get("daily") or []:
        d = _parse_day(item.get("date"))
        v = _valid(item.get("rain_mm"))
        if d is not None and v is not None:
            out.append((d, v))
    out.sort()
    return out


def _same_calendar_day(d: date, year: int) -> date:
    try:
        return date(year, d.month, d.day)
    except ValueError:  # 29/02 in a non-leap reference year
        return date(year, 2, 28)


def _reference_sums(days: list[date], history: dict[str, Any]) -> dict[int, float]:
    first = int(history.get("first_year") or 0)
    last = int(history.get("last_year") or 0)
    if not days or not first or not last:
        return {}
    base_year = days[0].year
    sums: dict[int, float] = {}
    for year in range(first, last + 1):
        total = 0.0
        complete = True
        for d in days:
            ref = _same_calendar_day(d, year + (d.year - base_year))
            if ref >= days[0]:  # never compare the window with itself or the future
                complete = False
                break
            v = _history_value(history, ref)
            if v is None:
                complete = False
                break
            total += v
        if complete:
            sums[year] = total
    return sums


def _expected_from_climatology(days: list[date], climatology: dict[str, Any]) -> float | None:
    per_month = {}
    for row in climatology.get("months") or []:
        value = row.get("rain_mm_day")
        if value is None and "rain_mm" in row and "rain_mm_day" not in row:
            value = row.get("rain_mm")  # legacy key: POWER climatology PRECTOTCORR is mm/day
        value = _valid(value)
        if value is not None and row.get("month") in MONTHS:
            per_month[MONTHS.index(row["month"])] = value
    if not days or any((d.month - 1) not in per_month for d in days):
        return None
    return sum(per_month[d.month - 1] for d in days)


def _base(recent: dict[str, Any], status: str, reason: str | None) -> dict[str, Any]:
    return {
        "ok": status == "found", "status": status, "reason": reason, "state": None, "state_code": None, "alert": False,
        "method": None, "rain_sum_mm": None, "normal_mm": None, "percentile": None, "ratio_pct": None,
        "reference": None, "reference_years": None, "days": None,
        "period_start": recent.get("period_start"), "period_end": recent.get("period_end"),
        "summary": None, "source": "NASA POWER", "note": NOTE,
    }


def build_rain_vs_normal(recent: dict[str, Any] | None, climatology: dict[str, Any] | None = None,
                         history: dict[str, Any] | None = None) -> dict[str, Any]:
    """Payload with explicit status: found / not_found / pending (see module docstring)."""
    recent = recent or {}
    climatology = climatology or {}
    if not recent.get("ok"):
        return _base(recent, "pending", "chuva_recente_nao_respondeu")
    pairs = _recent_days(recent)
    if pairs:
        days = [d for d, _ in pairs]
        observed = round(sum(v for _, v in pairs), 2)
    else:
        # summarised recent climate (no daily list): accept only a contiguous, fully valid period
        start, end = _parse_day(recent.get("period_start")), _parse_day(recent.get("period_end"))
        total = _valid(recent.get("rain_sum_mm"))
        span = (end - start).days + 1 if start and end and end >= start else 0
        if not span or total is None or int(recent.get("available_days") or 0) != span:
            return _base(recent, "pending", "chuva_recente_sem_serie_diaria")
        days = [start + timedelta(k) for k in range(span)]
        observed = round(total, 2)
    n = len(days)
    if n < MIN_WINDOW_DAYS:
        out = _base(recent, "not_found", "janela_curta")
        out.update(rain_sum_mm=observed, days=n)
        return out

    out: dict[str, Any] | None = None
    if history and history.get("ok"):
        sums = _reference_sums(days, history)
        if len(sums) >= MIN_REFERENCE_YEARS:
            values = sorted(sums.values())
            below = sum(1 for s in values if s < observed)
            ties = sum(1 for s in values if s == observed)
            pct = round((below + 0.5 * ties) / len(values) * 100, 1)
            median = values[len(values) // 2] if len(values) % 2 else (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2
            mean = sum(values) / len(values)
            if pct <= PCT_LOW:
                code = "below_normal" if mean / n >= PCT_DRY_SEASON_MM_DAY else "dry_season"
            elif pct >= PCT_HIGH and observed - median >= MIN_EXCESS_MM:
                code = "above_normal"
            else:
                code = "normal"
            years = sorted(sums)
            out = _base(recent, "found", None)
            out.update(method="percentile_series", normal_mm=round(median, 1), percentile=pct,
                       reference=f"{years[0]}–{years[-1]}", reference_years=len(years), state_code=code)
    if out is None:
        expected = _expected_from_climatology(days, climatology) if climatology.get("ok", True) else None
        if expected is None:
            return _base(recent, "pending", "normal_da_epoca_nao_respondeu")
        if n > RATIO_MAX_WINDOW_DAYS:
            res = _base(recent, "not_found", "janela_longa_sem_serie_historica")
            res.update(rain_sum_mm=observed, days=n)
            return res
        ratio = observed / expected if expected > 0 else math.inf
        if ratio < RATIO_LOW:
            code = "below_normal" if expected / n >= RATIO_DRY_SEASON_MM_DAY else "dry_season"
        elif ratio > RATIO_HIGH and observed - expected >= MIN_EXCESS_MM:
            code = "above_normal"
        else:
            code = "normal"
        out = _base(recent, "found", None)
        out.update(method="ratio_climatology", normal_mm=round(expected, 1),
                   ratio_pct=round(ratio * 100) if math.isfinite(ratio) else None,
                   reference=climatology.get("period"), state_code=code)

    out.update(rain_sum_mm=observed, days=n, state=STATE_LABELS[out["state_code"]], alert=out["state_code"] == "below_normal")
    out["summary"] = rain_vs_normal_summary(out)
    return out


def rain_vs_normal_summary(item: dict[str, Any]) -> str:
    if item.get("status") != "found":
        return ""
    start, end = _fmt_date(item.get("period_start")), _fmt_date(item.get("period_end"))
    period = f" ({start} a {end})" if start and end else ""
    head = f"{fmt_num(item.get('rain_sum_mm'), 1)} mm em {item.get('days')} dias{period}."
    if item.get("method") == "percentile_series":
        pct = float(item.get("percentile") or 0)
        compare = (f"choveu mais que em {fmt_num(pct, 0)}% dos anos" if pct >= 50
                   else f"choveu menos que em {fmt_num(100 - pct, 0)}% dos anos")
        body = (f" Nos mesmos dias de {item.get('reference')}, a chuva mediana é {fmt_num(item.get('normal_mm'), 1)} mm; "
                f"{compare}.")
    else:
        ratio = item.get("ratio_pct")
        ref = f" {item.get('reference')}" if item.get("reference") else ""
        body = (f" O normal desses dias na climatologia NASA POWER{ref} é "
                f"{fmt_num(item.get('normal_mm'), 1)} mm" + (f" ({fmt_num(ratio, 0)}% do normal)." if ratio is not None else "."))
    tail = " É uma época normalmente de pouca chuva na região; pouca chuva agora é o esperado." if item.get("state_code") == "dry_season" else ""
    return head + body + tail


def rain_vs_normal_pdf_row(item: dict[str, Any]) -> list[str] | None:
    """PDF row, or None: pending/not_found rows do not appear (campo vazio não aparece)."""
    if item.get("status") != "found":
        return None
    label = str(item.get("state") or "")
    return ["Chuva recente comparada ao normal", f"{label[:1].upper()}{label[1:]} — {item.get('summary')}"]
