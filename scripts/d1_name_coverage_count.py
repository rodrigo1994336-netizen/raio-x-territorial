from __future__ import annotations

import json
import os
from pathlib import Path

from google.cloud import bigquery

PROJECT = os.environ["RX_BIGQUERY_PROJECT"]
TABLE = "basedosdados.br_sfb_sicar.area_imovel"
MAX_BYTES_BILLED = 1024 ** 3  # 1 GiB hard cap, after reviewed dry-run ~0.45 GiB

sql = f"""
WITH latest AS (
  SELECT sigla_uf, MAX(data_extracao) AS snapshot
  FROM `{TABLE}`
  WHERE sigla_uf IS NOT NULL
  GROUP BY sigla_uf
)
SELECT a.sigla_uf, l.snapshot,
       COUNT(DISTINCT a.id_imovel) AS car_count
FROM `{TABLE}` AS a
JOIN latest AS l
  ON a.sigla_uf=l.sigla_uf AND a.data_extracao=l.snapshot
WHERE a.id_imovel IS NOT NULL
GROUP BY a.sigla_uf, l.snapshot
ORDER BY a.sigla_uf
"""

client = bigquery.Client(project=PROJECT)
dataset = client.get_dataset("basedosdados.br_sfb_sicar")
table = client.get_table(TABLE)
columns = [f.name for f in table.schema]
name_columns = [c for c in columns if c in {
    "nome_imovel", "denominacao", "nome_area", "nom_imovel",
    "nome_fazenda", "fazenda", "nome_propriedade",
}]
assert not name_columns, f"schema gained name columns; reassess D1 before counting: {name_columns}"

cfg = bigquery.QueryJobConfig(
    use_query_cache=False,
    maximum_bytes_billed=MAX_BYTES_BILLED,
)
job = client.query(sql, job_config=cfg, location=dataset.location)
rows = [dict(r.items()) for r in job.result()]
assert len(rows) == 27, f"expected 27 UFs, got {len(rows)}"
assert all(int(r["car_count"] or 0) > 0 for r in rows), rows

total = sum(int(r["car_count"]) for r in rows)
out = {
    "query_executed": True,
    "dataset_location": dataset.location,
    "table": TABLE,
    "name_columns": name_columns,
    "population_definition": "distinct id_imovel at latest area_imovel snapshot independently per UF",
    "uf_count": len(rows),
    "national_car_population": total,
    "car_records_with_public_name_field": 0,
    "car_records_without_public_name_field": total,
    "public_car_name_field_coverage_pct": 0.0,
    "public_car_name_field_absence_pct": 100.0,
    "basis": "schema-level absence of any property-name column; not an estimate and not auxiliary-name coverage",
    "maximum_bytes_billed": MAX_BYTES_BILLED,
    "total_bytes_billed": int(job.total_bytes_billed or 0),
    "total_bytes_processed": int(job.total_bytes_processed or 0),
    "ufs": rows,
}
Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/d1_name_coverage_count.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
)
print(f"RX_D1_COUNT_UFS={len(rows)}")
print(f"RX_D1_COUNT_NATIONAL_CARS={total}")
print("RX_D1_PUBLIC_CAR_NAME_COLUMNS=NONE")
print("RX_D1_PUBLIC_CAR_NAME_FIELD_ABSENCE_PCT=100.000000")
print(f"RX_D1_COUNT_BYTES_PROCESSED={int(job.total_bytes_processed or 0)}")
print(f"RX_D1_COUNT_BYTES_BILLED={int(job.total_bytes_billed or 0)}")
for r in rows:
    print(f"RX_D1_COUNT_{r['sigla_uf']}=snapshot:{r['snapshot']} cars:{r['car_count']}")