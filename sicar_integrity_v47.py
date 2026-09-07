from __future__ import annotations

"""V47 CAR integrity/composition engine backed by Base dos Dados BigQuery.

Truth rules:
- The environmental source is only ``basedosdados.br_sfb_sicar``.
- Query/configuration failure is never converted to a zero occurrence/area.
- Declared area, geometry-contained area and Raio-X ellipsoidal measurement are
  separate facts.
- GRS80 ellipsoidal measurement is used directly; no Curvelo-specific factor.
- Regeneration is only a geometric residual and is never promoted to a
  biological/legal conclusion.
- Municipality/UF boundary checks are fail-closed until an administrative mesh
  is explicitly configured. No centroid/attribute shortcut is permitted.
"""

import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pyproj import Geod
from shapely.geometry import GeometryCollection, mapping, shape
from shapely.ops import unary_union
try:
    from shapely import make_valid as _make_valid
except Exception:  # shapely < 2 compatibility
    from shapely.validation import make_valid as _make_valid


DATASET = "basedosdados.br_sfb_sicar"
GEOD = Geod(ellps="GRS80")
TABLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
CAR_RE = re.compile(r"^[A-Z]{2}-\d{7}-[A-F0-9]{32}$", re.I)

THEMES: dict[str, dict[str, Any]] = {
    "vegetacao_nativa": {"label": "Vegetação nativa", "table": "vegetacao_nativa", "declared_area": True},
    "reserva_legal": {"label": "Reserva legal", "table": "reserva_legal", "declared_area": True},
    "app": {"label": "APP", "table": "app", "declared_area": True, "app_total": True},
    "uso_restrito": {"label": "Uso restrito", "table": "uso_restrito", "declared_area": True},
    "area_consolidada": {"label": "Área consolidada", "table": "area_consolidada", "declared_area": True},
    # The current treated SICAR hydrology schema publishes geometry but does not
    # expose a reliable declared-area attribute for this V47 contract.
    "hidrografia": {"label": "Hidrografia", "table": "hidrografia", "declared_area": False},
    # Not displayed directly; it participates in the frozen residual equation.
    "area_pousio": {"label": "Área de pousio", "table": "area_pousio", "declared_area": True, "hidden": True},
}
EXCLUSIVE_FOR_RESIDUAL = ("area_consolidada", "vegetacao_nativa", "hidrografia", "area_pousio")
DISPLAY_ORDER = ("vegetacao_nativa", "reserva_legal", "app", "uso_restrito", "area_consolidada", "hidrografia")


@dataclass(frozen=True)
class BigQueryRuntime:
    project_id: str | None
    configured: bool
    detail: str


def runtime_state() -> BigQueryRuntime:
    project = (
        os.getenv("RX_BIGQUERY_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or ""
    ).strip()
    if not project:
        return BigQueryRuntime(None, False, "bigquery_project_not_configured")
    return BigQueryRuntime(project, True, "configured")


def _client():
    state = runtime_state()
    if not state.configured:
        raise RuntimeError(state.detail)
    try:
        from google.cloud import bigquery
    except Exception as exc:
        raise RuntimeError("google_cloud_bigquery_dependency_missing") from exc

    raw = (os.getenv("RX_BIGQUERY_SERVICE_ACCOUNT_JSON") or "").strip()
    if raw:
        try:
            from google.oauth2 import service_account
            info = json.loads(raw)
            credentials = service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            return bigquery.Client(project=state.project_id, credentials=credentials)
        except Exception as exc:
            raise RuntimeError("bigquery_service_account_configuration_invalid") from exc
    try:
        # Application Default Credentials are the preferred production path.
        return bigquery.Client(project=state.project_id)
    except Exception as exc:
        raise RuntimeError("bigquery_application_default_credentials_unavailable") from exc


def _query_parameters(params: dict[str, Any]):
    try:
        from google.cloud import bigquery
    except Exception as exc:
        raise RuntimeError("google_cloud_bigquery_dependency_missing") from exc
    qparams = []
    for key, value in params.items():
        if isinstance(value, bool):
            typ = "BOOL"
        elif isinstance(value, int):
            typ = "INT64"
        elif isinstance(value, float):
            typ = "FLOAT64"
        elif isinstance(value, dt.datetime):
            typ = "TIMESTAMP"
        elif isinstance(value, dt.date):
            typ = "DATE"
        else:
            typ = "STRING"
        qparams.append(bigquery.ScalarQueryParameter(key, typ, value))
    return qparams


def _query(client: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from google.cloud import bigquery
    except Exception as exc:
        raise RuntimeError("google_cloud_bigquery_dependency_missing") from exc
    cfg = bigquery.QueryJobConfig(query_parameters=_query_parameters(params), use_legacy_sql=False)
    max_bytes = (os.getenv("RX_BIGQUERY_MAX_BYTES_BILLED") or "").strip()
    if max_bytes:
        try:
            cfg.maximum_bytes_billed = int(max_bytes)
        except Exception as exc:
            raise RuntimeError("invalid_bigquery_max_bytes_billed") from exc
    rows = client.query(sql, job_config=cfg).result(timeout=45)
    return [dict(row.items()) for row in rows]


def _dry_run_bytes(client: Any, sql: str, params: dict[str, Any]) -> int:
    """Return BigQuery's byte estimate without executing the query."""
    try:
        from google.cloud import bigquery
    except Exception as exc:
        raise RuntimeError("google_cloud_bigquery_dependency_missing") from exc
    cfg = bigquery.QueryJobConfig(
        query_parameters=_query_parameters(params),
        use_legacy_sql=False,
        dry_run=True,
        use_query_cache=False,
    )
    job = client.query(sql, job_config=cfg)
    return int(job.total_bytes_processed or 0)


def _safe_geom(value: Any):
    if not value:
        return None
    try:
        if isinstance(value, str):
            value = json.loads(value)
        geom = shape(value)
        if geom.is_empty:
            return None
        if not geom.is_valid:
            geom = _make_valid(geom)
        if geom.is_empty:
            return None
        return geom
    except Exception:
        return None


def _union(geoms: Iterable[Any]):
    clean = [g for g in geoms if g is not None and not g.is_empty]
    if not clean:
        return GeometryCollection()
    try:
        out = unary_union(clean)
        if not out.is_valid:
            out = _make_valid(out)
        return out
    except Exception:
        return GeometryCollection()


def area_ha_grs80(geom: Any) -> float | None:
    if geom is None or geom.is_empty:
        return 0.0
    try:
        area_m2, _ = GEOD.geometry_area_perimeter(geom)
        return abs(float(area_m2)) / 10000.0
    except Exception:
        # GeometryCollections can contain linework mixed with polygons. Measure
        # polygonal members independently without fabricating area for lines.
        try:
            total = 0.0
            for item in getattr(geom, "geoms", []):
                value = area_ha_grs80(item)
                if value is not None:
                    total += value
            return total
        except Exception:
            return None


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def _difference_label(declared: float | None, inside: float | None) -> str | None:
    if declared is None or inside is None:
        return None
    delta = float(inside) - float(declared)
    # Avoid promoting normal topology/rounding noise to an alert. The Etapa 3
    # benchmark already accepted differences in the tens of square metres.
    tolerance = max(0.01, abs(float(declared)) * 0.005)
    if abs(delta) < tolerance:
        return None
    if delta < 0:
        return f"Déficit: {abs(delta):.2f} ha"
    return f"Excedente geométrico: {delta:.2f} ha"


def _theme_row(key: str, record: dict[str, Any], property_geom: Any) -> dict[str, Any]:
    spec = THEMES[key]
    snapshot_available = bool(record.get("snapshot_available"))
    row_count = int(record.get("row_count") or 0)
    geometry_count = int(record.get("geometry_count") or 0)
    geom = _safe_geom(record.get("geometry_geojson"))

    if not snapshot_available:
        return {
            "key": key,
            "label": spec["label"],
            "state": "source_snapshot_unavailable",
            "declared_ha": None,
            "inside_ha": None,
            "inside_pct": None,
            "measured_ha": None,
            "row_count": row_count,
            "geometry_count": geometry_count,
            "discrepancy": None,
            "note": "Snapshot da camada não disponível para a UF/data do imóvel.",
        }

    declared = None
    if spec.get("declared_area") and row_count:
        raw = record.get("declared_area_ha")
        try:
            declared = float(raw) if raw is not None else None
        except Exception:
            declared = None

    if row_count == 0:
        return {
            "key": key,
            "label": spec["label"],
            "state": "not_declared_or_not_found",
            "declared_ha": None,
            "inside_ha": None,
            "inside_pct": None,
            "measured_ha": None,
            "row_count": 0,
            "geometry_count": 0,
            "discrepancy": None,
            "note": "Nenhuma feição localizada neste snapshot; não exibido como zero declarado.",
        }

    if geom is None:
        return {
            "key": key,
            "label": spec["label"],
            "state": "geometry_unavailable",
            "declared_ha": _round(declared),
            "inside_ha": None,
            "inside_pct": None,
            "measured_ha": None,
            "row_count": row_count,
            "geometry_count": geometry_count,
            "discrepancy": None,
            "note": "Há registro declaratório, mas a geometria tratada não está disponível.",
        }

    measured = area_ha_grs80(geom)
    try:
        inside_geom = _make_valid(geom.intersection(property_geom))
    except Exception:
        inside_geom = geom.intersection(property_geom)
    inside = area_ha_grs80(inside_geom)
    inside_pct = None
    if measured is not None and measured > 0 and inside is not None:
        inside_pct = max(0.0, min(100.0, inside / measured * 100.0))
    return {
        "key": key,
        "label": spec["label"],
        "state": "measured",
        "declared_ha": _round(declared),
        "inside_ha": _round(inside),
        "inside_pct": _round(inside_pct, 4),
        "measured_ha": _round(measured),
        "row_count": row_count,
        "geometry_count": geometry_count,
        "discrepancy": _difference_label(declared, inside),
        "note": (
            "Área declarada é atributo SICAR; contenção e medição são cálculos geométricos GRS80."
            if spec.get("declared_area")
            else "A tabela não fornece área declarada confiável para este contrato; apenas a geometria é medida."
        ),
        "_geometry": geom,
    }


def _residual_row(property_geom: Any, rows_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    blockers = []
    exclusive = []
    for key in EXCLUSIVE_FOR_RESIDUAL:
        row = rows_by_key.get(key) or {}
        state = row.get("state")
        if state == "source_snapshot_unavailable" or state == "geometry_unavailable":
            blockers.append(key)
            continue
        if state == "measured" and row.get("_geometry") is not None:
            exclusive.append(row["_geometry"])
        # not_declared_or_not_found is a known empty set for this snapshot.
    if blockers:
        return {
            "key": "regeneracao",
            "label": "Regeneração",
            "state": "not_computable",
            "declared_ha": None,
            "inside_ha": None,
            "inside_pct": None,
            "measured_ha": None,
            "row_count": None,
            "geometry_count": None,
            "discrepancy": None,
            "note": "Residual não calculado porque uma ou mais classes exclusivas estão indisponíveis.",
        }
    occupied = _union(exclusive)
    try:
        residual = _make_valid(property_geom.difference(occupied))
    except Exception:
        residual = property_geom.difference(occupied)
    measured = area_ha_grs80(residual)
    property_area = area_ha_grs80(property_geom)
    share = None
    if property_area and measured is not None:
        share = max(0.0, min(100.0, measured / property_area * 100.0))
    return {
        "key": "regeneracao",
        "label": "Regeneração",
        "state": "geometric_residual",
        "declared_ha": None,
        "inside_ha": _round(measured),
        "inside_pct": _round(share, 4),
        "measured_ha": _round(measured),
        "row_count": None,
        "geometry_count": None,
        "discrepancy": None,
        "note": "Residual geométrico do método congelado; não comprova regeneração biológica nem regularidade legal.",
        "_geometry": residual,
    }


def _boundary_check(property_geom: Any, boundary_record: dict[str, Any] | None, label: str) -> dict[str, Any]:
    if not boundary_record:
        return {"state": "not_configured", "outside_ha": None, "outside_pct": None, "label": label}
    if not boundary_record.get("configured", True):
        return {"state": "not_configured", "outside_ha": None, "outside_pct": None, "label": label}
    if boundary_record.get("query_ok") is False:
        return {"state": "unavailable", "outside_ha": None, "outside_pct": None, "label": label, "detail": boundary_record.get("detail")}
    boundary = _safe_geom(boundary_record.get("geometry_geojson"))
    if boundary is None:
        return {"state": "unavailable", "outside_ha": None, "outside_pct": None, "label": label, "detail": "boundary_geometry_unavailable"}
    try:
        outside = _make_valid(property_geom.difference(boundary))
    except Exception:
        outside = property_geom.difference(boundary)
    outside_ha = area_ha_grs80(outside)
    property_ha = area_ha_grs80(property_geom)
    outside_pct = None if not property_ha or outside_ha is None else outside_ha / property_ha * 100.0
    return {
        "state": "checked",
        "outside_ha": _round(outside_ha),
        "outside_pct": _round(outside_pct, 4),
        "label": label,
        "has_outside_area": bool(outside_ha and outside_ha > 0.000001),
    }


def build_integrity_from_records(
    car_code: str,
    property_record: dict[str, Any],
    theme_records: dict[str, dict[str, Any]],
    overlap_record: dict[str, Any] | None = None,
    municipality_boundary: dict[str, Any] | None = None,
    uf_boundary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    property_geom = _safe_geom(property_record.get("geometry_geojson"))
    if property_geom is None:
        return {
            "ok": False,
            "state": "unavailable",
            "car_code": car_code,
            "detail": "property_geometry_unavailable_in_basedosdados",
            "source": DATASET,
        }

    property_measured = area_ha_grs80(property_geom)
    rows_by_key: dict[str, dict[str, Any]] = {}
    for key in THEMES:
        rows_by_key[key] = _theme_row(key, theme_records.get(key) or {}, property_geom)
    residual = _residual_row(property_geom, rows_by_key)
    rows = [rows_by_key[key] for key in DISPLAY_ORDER] + [residual]

    overlap = {
        "state": "unavailable",
        "distinct_car_count": None,
        "union_area_ha": None,
        "property_pct": None,
        "detail": "overlap_query_not_returned",
    }
    if overlap_record is not None:
        if overlap_record.get("query_ok") is False:
            overlap = {
                "state": "unavailable",
                "distinct_car_count": None,
                "union_area_ha": None,
                "property_pct": None,
                "detail": overlap_record.get("detail") or "overlap_query_failed",
            }
        else:
            count = int(overlap_record.get("distinct_car_count") or 0)
            og = _safe_geom(overlap_record.get("geometry_geojson"))
            union_area = area_ha_grs80(og) if og is not None else (0.0 if count == 0 else None)
            pct = None
            if property_measured and union_area is not None:
                pct = union_area / property_measured * 100.0
            overlap = {
                "state": "checked",
                "distinct_car_count": count,
                "union_area_ha": _round(union_area),
                "property_pct": _round(pct, 4),
                "detail": "Interseções de área positiva; próprio CAR e meros contatos de borda excluídos.",
            }

    municipality = _boundary_check(property_geom, municipality_boundary, "Município codificado no CAR")
    uf = _boundary_check(property_geom, uf_boundary, "UF cadastrada no CAR")

    # Internal geometries are not exposed in the API payload.
    public_rows = []
    for row in rows:
        clean = {k: v for k, v in row.items() if not k.startswith("_")}
        public_rows.append(clean)

    snapshot = property_record.get("data_extracao")
    composition_complete = all(
        r.get("state") not in {"source_snapshot_unavailable", "geometry_unavailable", "not_computable"}
        for r in public_rows
    )
    integrity_complete = (
        composition_complete
        and overlap.get("state") == "checked"
        and municipality.get("state") == "checked"
        and uf.get("state") == "checked"
    )
    return {
        "ok": True,
        "state": "checked" if integrity_complete else "partial",
        "car_code": car_code,
        "source": DATASET,
        "snapshot": str(snapshot) if snapshot is not None else None,
        "property": {
            "declared_area_ha": _round(_as_float(property_record.get("area"))),
            "measured_area_ha": _round(property_measured),
            "id_municipio": property_record.get("id_municipio"),
            "sigla_uf": property_record.get("sigla_uf"),
        },
        "composition_rows": public_rows,
        "overlap": overlap,
        "municipality_boundary": municipality,
        "uf_boundary": uf,
        "method": {
            "measurement": "GRS80 ellipsoidal / pyproj.Geod",
            "residual": "property - union(area_consolidada, vegetacao_nativa, hidrografia, area_pousio)",
            "regeneration_warning": "Residual geométrico; não comprova regeneração biológica nem conclusão jurídica.",
            "absence_rule": "Fonte/campo indisponível não é convertido em zero.",
        },
    }


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def _fetch_property(client: Any, car_code: str, uf: str) -> dict[str, Any] | None:
    sql = f"""
    WITH candidates AS (
      SELECT data_extracao, sigla_uf, id_municipio, id_imovel, area,
             data_atualizacao, geometria
      FROM `{DATASET}.area_imovel`
      WHERE sigla_uf=@uf AND id_imovel=@car_code
    ), latest AS (
      SELECT MAX(data_extracao) AS data_extracao FROM candidates
    )
    SELECT data_extracao, sigla_uf, id_municipio, id_imovel, area,
           ST_ASGEOJSON(geometria) AS geometry_geojson
    FROM candidates
    WHERE data_extracao=(SELECT data_extracao FROM latest)
      AND geometria IS NOT NULL
    ORDER BY data_atualizacao DESC NULLS LAST
    LIMIT 1
    """
    rows = _query(client, sql, {"uf": uf, "car_code": car_code})
    return rows[0] if rows else None


def _fetch_theme(client: Any, car_code: str, uf: str, snapshot: Any, key: str) -> dict[str, Any]:
    spec = THEMES[key]
    table = f"{DATASET}.{spec['table']}"
    if spec.get("declared_area"):
        app_declared = """
          CASE WHEN COUNTIF(UPPER(COALESCE(tipo,''))='APP_TOTAL')>0
               THEN SUM(IF(UPPER(COALESCE(tipo,''))='APP_TOTAL',SAFE_CAST(area AS FLOAT64),NULL))
               ELSE SUM(SAFE_CAST(area AS FLOAT64)) END
        """ if spec.get("app_total") else "SUM(SAFE_CAST(area AS FLOAT64))"
        declared_sql = f"CAST(({app_declared}) AS FLOAT64)"
    else:
        declared_sql = "CAST(NULL AS FLOAT64)"
    sql = f"""
    WITH raw AS (
      SELECT * FROM `{table}`
      WHERE sigla_uf=@uf AND data_extracao=@snapshot AND id_imovel=@car_code
    )
    SELECT
      EXISTS(SELECT 1 FROM `{table}` WHERE sigla_uf=@uf AND data_extracao=@snapshot LIMIT 1) AS snapshot_available,
      COUNT(*) AS row_count,
      COUNTIF(geometria IS NOT NULL) AS geometry_count,
      {declared_sql} AS declared_area_ha,
      ST_ASGEOJSON(ST_UNION_AGG(geometria)) AS geometry_geojson
    FROM raw
    """
    rows = _query(client, sql, {"uf": uf, "snapshot": snapshot, "car_code": car_code})
    return rows[0] if rows else {"snapshot_available": False, "row_count": 0, "geometry_count": 0}


def _fetch_themes(client: Any, car_code: str, uf: str, snapshot: Any) -> dict[str, dict[str, Any]]:
    fragments = []
    for key, spec in THEMES.items():
        table = f"{DATASET}.{spec['table']}"
        if spec.get("declared_area"):
            if spec.get("app_total"):
                declared_sql = """CAST((CASE WHEN COUNTIF(UPPER(COALESCE(tipo,''))='APP_TOTAL')>0 THEN SUM(IF(UPPER(COALESCE(tipo,''))='APP_TOTAL',SAFE_CAST(area AS FLOAT64),NULL)) ELSE SUM(SAFE_CAST(area AS FLOAT64)) END) AS FLOAT64)"""
            else:
                declared_sql = "CAST(SUM(SAFE_CAST(area AS FLOAT64)) AS FLOAT64)"
        else:
            declared_sql = "CAST(NULL AS FLOAT64)"
        fragments.append(f"""
        SELECT '{key}' AS theme_key,
          EXISTS(SELECT 1 FROM `{table}` WHERE sigla_uf=@uf AND data_extracao=@snapshot LIMIT 1) AS snapshot_available,
          COUNT(*) AS row_count,
          COUNTIF(geometria IS NOT NULL) AS geometry_count,
          {declared_sql} AS declared_area_ha,
          ST_ASGEOJSON(ST_UNION_AGG(geometria)) AS geometry_geojson
        FROM `{table}`
        WHERE sigla_uf=@uf AND data_extracao=@snapshot AND id_imovel=@car_code
        """)
    sql = "\nUNION ALL\n".join(fragments)
    rows = _query(client, sql, {"uf": uf, "snapshot": snapshot, "car_code": car_code})
    found = {str(row.get("theme_key")): row for row in rows}
    return {key: found.get(key) or {"snapshot_available": False, "row_count": 0, "geometry_count": 0} for key in THEMES}


def _overlap_sql() -> str:
    return f"""
    WITH target AS (
      SELECT geometria
      FROM `{DATASET}.area_imovel`
      WHERE sigla_uf=@uf AND data_extracao=@snapshot
        AND id_imovel=@car_code AND geometria IS NOT NULL
      LIMIT 1
    ), hits AS (
      SELECT other.id_imovel,
             ST_INTERSECTION(target.geometria,other.geometria) AS inter
      FROM target
      JOIN `{DATASET}.area_imovel` AS other
        ON other.sigla_uf=@uf
       AND other.data_extracao=@snapshot
       AND other.id_imovel!=@car_code
       AND other.geometria IS NOT NULL
       AND ST_INTERSECTS(target.geometria,other.geometria)
      WHERE ST_AREA(ST_INTERSECTION(target.geometria,other.geometria))>0
    ), per_car AS (
      SELECT id_imovel, ST_UNION_AGG(inter) AS inter
      FROM hits
      GROUP BY id_imovel
    )
    SELECT COUNT(*) AS distinct_car_count,
           ST_ASGEOJSON(ST_UNION_AGG(inter)) AS geometry_geojson
    FROM per_car
    """


def _fetch_overlap(client: Any, car_code: str, uf: str, snapshot: Any) -> dict[str, Any]:
    sql = _overlap_sql()
    rows = _query(client, sql, {"uf": uf, "snapshot": snapshot, "car_code": car_code})
    out = rows[0] if rows else {"distinct_car_count": 0, "geometry_geojson": None}
    out["query_ok"] = True
    return out


def _configured_boundary_table(env_name: str) -> str | None:
    value = (os.getenv(env_name) or "").strip()
    if not value:
        return None
    if not TABLE_ID_RE.match(value):
        raise RuntimeError(f"invalid_admin_boundary_table:{env_name}")
    return value


def _boundary_sql(table: str, key_column: str) -> str:
    if key_column not in {"id_municipio", "sigla_uf"}:
        raise RuntimeError("invalid_admin_boundary_key")
    if not TABLE_ID_RE.match(table):
        raise RuntimeError("invalid_admin_boundary_table")
    extra = "AND id_municipio=@boundary_key" if key_column == "id_municipio" else ""
    return f"""
    SELECT ST_ASGEOJSON(ST_UNION_AGG(geometria)) AS geometry_geojson
    FROM `{table}`
    WHERE sigla_uf=@uf {extra} AND geometria IS NOT NULL
    """


def _fetch_boundary(client: Any, table: str | None, key_column: str, key_value: str, uf: str) -> dict[str, Any]:
    if not table:
        return {"configured": False}
    sql = _boundary_sql(table, key_column)
    params = {"uf": uf}
    if key_column == "id_municipio":
        params["boundary_key"] = str(key_value)
    rows = _query(client, sql, params)
    return {"configured": True, "query_ok": True, "geometry_geojson": (rows[0].get("geometry_geojson") if rows else None)}


def query_car_integrity_v47(car_code: str, *, client: Any | None = None) -> dict[str, Any]:
    code = str(car_code or "").strip().upper()
    if not CAR_RE.match(code):
        return {"ok": False, "state": "invalid", "car_code": code, "detail": "invalid_car_format", "source": DATASET}
    uf = code[:2]
    own_client = client
    try:
        if own_client is None:
            own_client = _client()
        property_record = _fetch_property(own_client, code, uf)
        if not property_record:
            return {"ok": False, "state": "unavailable", "car_code": code, "detail": "car_not_found_in_basedosdados", "source": DATASET}
        snapshot = property_record.get("data_extracao")
        if snapshot is None:
            return {"ok": False, "state": "unavailable", "car_code": code, "detail": "snapshot_missing", "source": DATASET}
        themes = _fetch_themes(own_client, code, uf, snapshot)
        overlap = _fetch_overlap(own_client, code, uf, snapshot)
        municipality_table = _configured_boundary_table("RX_ADMIN_MUNICIPALITY_TABLE")
        uf_table = _configured_boundary_table("RX_ADMIN_UF_TABLE")
        municipality = _fetch_boundary(
            own_client, municipality_table, "id_municipio",
            str(property_record.get("id_municipio") or ""), uf,
        )
        uf_boundary = _fetch_boundary(own_client, uf_table, "sigla_uf", uf, uf)
        out = build_integrity_from_records(code, property_record, themes, overlap, municipality, uf_boundary)
        out["runtime"] = {"project_id_configured": True, "admin_mesh_configured": bool(municipality_table and uf_table)}
        return out
    except Exception as exc:
        detail = f"{type(exc).__name__}:{str(exc)[:220]}"
        return {
            "ok": False,
            "state": "unavailable",
            "car_code": code,
            "detail": detail,
            "source": DATASET,
            "runtime": {
                "project_id_configured": runtime_state().configured,
                "admin_mesh_configured": bool(os.getenv("RX_ADMIN_MUNICIPALITY_TABLE") and os.getenv("RX_ADMIN_UF_TABLE")),
            },
        }


def panel_table_rows(result: dict[str, Any]) -> list[dict[str, str]]:
    if not result.get("ok"):
        return []
    out = []
    for row in result.get("composition_rows") or []:
        state = row.get("state")
        declared = row.get("declared_ha")
        inside = row.get("inside_ha")
        pct = row.get("inside_pct")
        measured = row.get("measured_ha")
        if row.get("key") == "hidrografia":
            declared_text = "Campo não publicado"
        elif row.get("key") == "regeneracao":
            declared_text = "Não é campo declarado"
        elif declared is None:
            declared_text = "Não declarado/localizado" if state == "not_declared_or_not_found" else "Indisponível"
        else:
            declared_text = f"{declared:.4f} ha"
        if inside is None:
            inside_text = "Indisponível" if state not in {"not_declared_or_not_found"} else "Sem feição localizada"
        else:
            inside_text = f"{inside:.4f} ha" + (f" · {pct:.2f}%" if pct is not None else "")
        measured_text = "Indisponível" if measured is None else f"{measured:.4f} ha"
        if row.get("discrepancy"):
            inside_text += f" · {row['discrepancy']}"
        out.append({
            "key": str(row.get("key") or ""),
            "information": str(row.get("label") or ""),
            "declared": declared_text,
            "inside": inside_text,
            "measured": measured_text,
            "state": str(state or "unknown"),
            "note": str(row.get("note") or ""),
        })
    return out


print("RX_SICAR_INTEGRITY_V47=basedosdados_bigquery_grs80_fail_closed", flush=True)