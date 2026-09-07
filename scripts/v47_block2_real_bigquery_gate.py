from __future__ import annotations

import os
import sys
from typing import Any

import sicar_integrity_v47 as sicar

EXPECTED_UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}
DEFAULT_SAMPLE_UFS = ("AM", "BA", "MT", "MG", "PR")
CURVELO_CAR = "MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F"
ADMIN_DATASET = "basedosdados.br_geobr_mapas"
EXPECTED_MUNICIPALITY_TABLE = "municipio"
EXPECTED_STATE_TABLE = "estado"


def fail(message: str) -> None:
    raise AssertionError(message)


def require_configuration() -> None:
    state = sicar.runtime_state()
    if not state.configured:
        fail("RX_BIGQUERY_PROJECT/GOOGLE_CLOUD_PROJECT não configurado")
    expected = {
        "RX_ADMIN_MUNICIPALITY_TABLE": f"{ADMIN_DATASET}.{EXPECTED_MUNICIPALITY_TABLE}",
        "RX_ADMIN_UF_TABLE": f"{ADMIN_DATASET}.{EXPECTED_STATE_TABLE}",
    }
    for name, value in expected.items():
        actual = (os.getenv(name) or "").strip()
        if actual != value:
            fail(f"{name} deve ser exatamente {value}; recebido {actual or '<ausente>'}")


def inspect_admin_mesh_schema(client: Any) -> dict[str, dict[str, str]]:
    # This exact dataset-level inventory is a hard gate requested by the owner.
    rows = sicar._query(
        client,
        f"SELECT table_name FROM `{ADMIN_DATASET}.INFORMATION_SCHEMA.TABLES` ORDER BY table_name",
        {},
    )
    tables = [str(row.get("table_name") or "") for row in rows]
    print("RX_V47_ADMIN_TABLES", ",".join(tables))
    required = {EXPECTED_MUNICIPALITY_TABLE, EXPECTED_STATE_TABLE}
    missing = sorted(required - set(tables))
    if missing:
        fail(
            "malha administrativa autorizada não encontrada no datalake; "
            f"tabelas ausentes={missing}. Nenhuma fonte alternativa será usada"
        )

    col_rows = sicar._query(
        client,
        f"""
        SELECT table_name, column_name, data_type
        FROM `{ADMIN_DATASET}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name IN ('{EXPECTED_MUNICIPALITY_TABLE}','{EXPECTED_STATE_TABLE}')
        ORDER BY table_name, ordinal_position
        """,
        {},
    )
    schema: dict[str, dict[str, str]] = {name: {} for name in required}
    for row in col_rows:
        table = str(row.get("table_name") or "")
        column = str(row.get("column_name") or "")
        typ = str(row.get("data_type") or "")
        if table in schema and column:
            schema[table][column] = typ
    for table in sorted(schema):
        print(
            "RX_V47_ADMIN_COLUMNS",
            f"table={table}",
            "columns=" + ",".join(f"{k}:{v}" for k, v in schema[table].items()),
        )

    required_columns = {
        EXPECTED_MUNICIPALITY_TABLE: {"id_municipio", "sigla_uf", "geometria"},
        EXPECTED_STATE_TABLE: {"sigla_uf", "geometria"},
    }
    for table, columns in required_columns.items():
        missing_cols = sorted(columns - set(schema[table]))
        if missing_cols:
            fail(
                f"{ADMIN_DATASET}.{table} não possui as colunas obrigatórias {missing_cols}; "
                "nenhuma fonte/coluna alternativa será improvisada"
            )
        if str(schema[table].get("geometria") or "").upper() != "GEOGRAPHY":
            fail(
                f"{ADMIN_DATASET}.{table}.geometria não é GEOGRAPHY "
                f"(tipo={schema[table].get('geometria')}); parar sem improvisar"
            )
    return schema


def source_coverage_sql() -> str:
    return f"""
    SELECT DISTINCT sigla_uf
    FROM `{sicar.DATASET}.area_imovel`
    WHERE sigla_uf IS NOT NULL AND geometria IS NOT NULL
    """


def source_coverage(client: Any) -> set[str]:
    rows = sicar._query(client, source_coverage_sql(), {})
    return {str(row.get("sigla_uf") or "").upper() for row in rows if row.get("sigla_uf")}


def sample_car(client: Any, uf: str) -> str:
    # Keep the expensive spatial work partition-pruned to one UF + one snapshot.
    sql = f"""
    WITH latest AS (
      SELECT MAX(data_extracao) AS snapshot
      FROM `{sicar.DATASET}.area_imovel`
      WHERE sigla_uf=@uf
    )
    SELECT id_imovel
    FROM `{sicar.DATASET}.area_imovel`
    WHERE sigla_uf=@uf
      AND data_extracao=(SELECT snapshot FROM latest)
      AND geometria IS NOT NULL
      AND SAFE_CAST(area AS FLOAT64) BETWEEN 5 AND 500
      AND status IN ('AT','PE','SU')
    ORDER BY id_imovel
    LIMIT 1
    """
    rows = sicar._query(client, sql, {"uf": uf})
    if not rows or not rows[0].get("id_imovel"):
        fail(f"nenhum CAR amostral elegível em {uf}")
    return str(rows[0]["id_imovel"]).upper()


def print_dry_run(name: str, byte_count: int) -> None:
    gib = byte_count / (1024 ** 3)
    tib = byte_count / (1024 ** 4)
    print(
        "RX_V47_DRYRUN",
        f"query={name}",
        f"bytes={byte_count}",
        f"GiB={gib:.6f}",
        f"TiB={tib:.9f}",
    )


def estimate_expensive_queries(client: Any) -> None:
    prop = sicar._fetch_property(client, CURVELO_CAR, "MG")
    if not prop:
        fail("Curvelo: CAR de referência não localizado antes do dry-run")
    snapshot = prop.get("data_extracao")
    id_municipio = str(prop.get("id_municipio") or "")
    if snapshot is None or not id_municipio:
        fail("Curvelo: snapshot/id_municipio indisponível para estimativa de custo")

    estimates = (
        (
            "car_overlap_same_uf_snapshot",
            sicar._overlap_sql(),
            {"uf": "MG", "snapshot": snapshot, "car_code": CURVELO_CAR},
        ),
        (
            "municipality_boundary",
            sicar._boundary_sql(os.environ["RX_ADMIN_MUNICIPALITY_TABLE"], "id_municipio"),
            {"uf": "MG", "boundary_key": id_municipio},
        ),
        (
            "uf_boundary",
            sicar._boundary_sql(os.environ["RX_ADMIN_UF_TABLE"], "sigla_uf"),
            {"uf": "MG"},
        ),
        ("national_source_coverage", source_coverage_sql(), {}),
    )
    for name, sql, params in estimates:
        print_dry_run(name, sicar._dry_run_bytes(client, sql, params))
    print("RX_V47_COST_ESTIMATES_READY=PASS")


def assert_result(car_code: str, result: dict[str, Any]) -> None:
    if not result.get("ok"):
        fail(f"{car_code}: consulta real falhou: {result.get('detail')}")
    if result.get("source") != sicar.DATASET:
        fail(f"{car_code}: fonte ambiental inesperada {result.get('source')}")
    if result.get("state") != "checked":
        fail(f"{car_code}: resultado não está integralmente checado: {result.get('state')}")

    rows = {row.get("key"): row for row in result.get("composition_rows") or []}
    required = set(sicar.DISPLAY_ORDER) | {"regeneracao"}
    if not required.issubset(rows):
        fail(f"{car_code}: linhas ausentes {sorted(required-set(rows))}")
    # Official br_sfb_sicar schema has no `area` field for hydrografia.
    if rows["hidrografia"].get("declared_ha") is not None:
        fail(f"{car_code}: hidrografia ganhou área declarada artificial")
    if rows["regeneracao"].get("state") != "geometric_residual":
        fail(f"{car_code}: residual não calculado com o método congelado")

    ov = result.get("overlap") or {}
    if ov.get("state") != "checked" or ov.get("distinct_car_count") is None or ov.get("union_area_ha") is None:
        fail(f"{car_code}: sobreposição entre CARs não checada")
    for name in ("municipality_boundary", "uf_boundary"):
        boundary = result.get(name) or {}
        if boundary.get("state") != "checked" or boundary.get("outside_ha") is None:
            fail(f"{car_code}: {name} não checado")

    table = sicar.panel_table_rows(result)
    if len(table) != 7:
        fail(f"{car_code}: tabela esperava 7 linhas e recebeu {len(table)}")
    hydro = next(row for row in table if row.get("key") == "hidrografia")
    if hydro.get("declared") != "Campo não publicado":
        fail(f"{car_code}: contrato visual da hidrografia violado")


def assert_curvelo_benchmark(client: Any) -> None:
    result = sicar.query_car_integrity_v47(CURVELO_CAR, client=client)
    assert_result(CURVELO_CAR, result)
    measured = float((result.get("property") or {}).get("measured_area_ha") or 0)
    if abs(measured - 14.804116) > 0.02:
        fail(f"Curvelo: área GRS80 saiu do benchmark: {measured:.6f} ha")
    residual = next(row for row in result.get("composition_rows") or [] if row.get("key") == "regeneracao")
    residual_ha = float(residual.get("measured_ha") or 0)
    if abs(residual_ha - 1.815498) > 0.03:
        fail(f"Curvelo: residual saiu do benchmark aceito: {residual_ha:.6f} ha")
    print(
        "RX_V47_CURVELO_REAL=PASS",
        f"snapshot={result.get('snapshot')}",
        f"area={measured:.6f}",
        f"residual={residual_ha:.6f}",
    )


def main() -> None:
    require_configuration()
    client = sicar._client()

    # Hard stop before the V47 engine uses any administrative boundary.
    inspect_admin_mesh_schema(client)
    print("RX_V47_ADMIN_SCHEMA=PASS")

    # Cost is disclosed by BigQuery dry-run BEFORE the nationwide coverage query
    # or multi-UF spatial sample is executed.
    estimate_expensive_queries(client)

    present = source_coverage(client)
    missing = sorted(EXPECTED_UFS - present)
    if missing:
        fail(f"area_imovel não cobre as 27 UFs esperadas; faltando: {missing}")
    print("RX_V47_NATIONAL_SOURCE_COVERAGE=PASS ufs=27")

    raw = (os.getenv("RX_V47_SAMPLE_UFS") or ",".join(DEFAULT_SAMPLE_UFS)).upper()
    sample_ufs = tuple(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))
    if not sample_ufs or any(uf not in EXPECTED_UFS for uf in sample_ufs):
        fail(f"RX_V47_SAMPLE_UFS inválido: {sample_ufs}")

    for uf in sample_ufs:
        car_code = sample_car(client, uf)
        result = sicar.query_car_integrity_v47(car_code, client=client)
        assert_result(car_code, result)
        print(
            "RX_V47_REAL_SAMPLE=PASS",
            f"uf={uf}",
            f"car={car_code}",
            f"snapshot={result.get('snapshot')}",
            f"overlaps={(result.get('overlap') or {}).get('distinct_car_count')}",
        )

    assert_curvelo_benchmark(client)
    print("RX_V47_BLOCK2_REAL_BIGQUERY_GATE=PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"RX_V47_BLOCK2_REAL_BIGQUERY_GATE=FAIL {type(exc).__name__}:{exc}", file=sys.stderr)
        raise