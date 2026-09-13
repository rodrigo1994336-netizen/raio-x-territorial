from __future__ import annotations

import json
import os
from pathlib import Path

from google.cloud import bigquery

PROJECT = os.environ["RX_BIGQUERY_PROJECT"]
DATASET = "basedosdados.br_sfb_sicar"
TABLE = f"{DATASET}.area_imovel"
LIST_PRICE_USD_PER_TIB = 6.25
NAME_CANDIDATES = {
    "nome_imovel", "denominacao", "nome_area", "nom_imovel",
    "nome_fazenda", "fazenda", "nome_propriedade",
}

client = bigquery.Client(project=PROJECT)
dataset = client.get_dataset(DATASET)
table = client.get_table(TABLE)
columns = [f.name for f in table.schema]
name_columns = sorted(NAME_CANDIDATES.intersection(columns))

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

cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
job = client.query(sql, job_config=cfg, location=dataset.location)
processed = int(job.total_bytes_processed or 0)
tib = processed / (1024 ** 4)
list_price = tib * LIST_PRICE_USD_PER_TIB

out = {
    "project": PROJECT, "dataset": DATASET, "dataset_location": dataset.location,
    "table": TABLE, "schema_columns": columns, "name_columns": name_columns,
    "query_executed": False, "dry_run": True,
    "total_bytes_processed": processed, "tib_processed": tib,
    "list_price_usd_per_tib": LIST_PRICE_USD_PER_TIB,
    "list_price_upper_bound_usd": list_price,
    "free_tier_not_assumed": True, "query": sql.strip(),
}
Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/d1_name_coverage_dryrun.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("RX_D1_NAME_SCHEMA_COLUMNS=" + ",".join(columns))
print("RX_D1_NAME_COLUMNS=" + (",".join(name_columns) if name_columns else "NONE"))
print(f"RX_D1_DATASET_LOCATION={dataset.location}")
print(f"RX_D1_DRY_RUN_BYTES={processed}")
print(f"RX_D1_DRY_RUN_TIB={tib:.9f}")
print(f"RX_D1_DRY_RUN_LIST_PRICE_USD={list_price:.6f}")
print("RX_D1_DRY_RUN_QUERY_EXECUTED=NO")