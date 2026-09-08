from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil
from google.api_core.exceptions import PreconditionFailed
from google.cloud import bigquery, storage
from shapely.geometry import shape

PROJECT = "metodo-afp-plataforma"
DATASET = "basedosdados.br_sfb_sicar"
SOURCE_TABLE = f"{DATASET}.area_imovel"
UF = "ES"
SNAPSHOT = dt.date(2026, 8, 4)
SNAPSHOT_ID = "sicar-2026-08-04"
EXPECTED_FEATURES = 129_496
BUCKET = os.getenv("RX_V48_GCS_BUCKET", "raio-x-territorial-car-metodo-afp-plataforma").strip()
BUCKET_REGION_OWNER_ATTESTED = "southamerica-east1"
MIN_ZOOM = 10
MAX_ZOOM = 16
MAX_BQ_BYTES = 10 * 1024**3
SCHEMA_VERSION = "v48-vector-pilot-metrics-2"


def die(message: str) -> None:
    raise RuntimeError(message)


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(block_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def extraction_sql() -> str:
    # Every row is explicitly pinned to the canonical date. Duplicate source
    # rows for one CAR are unioned inside that same date. There is no MAX(date)
    # or latest-snapshot fallback anywhere in this query.
    return f"""
    WITH source_rows AS (
      SELECT id_imovel, id_municipio, status, area, data_atualizacao, geometria
      FROM `{SOURCE_TABLE}`
      WHERE sigla_uf=@uf
        AND data_extracao=@snapshot
        AND geometria IS NOT NULL
    ), per_car AS (
      SELECT
        id_imovel,
        ARRAY_AGG(
          STRUCT(id_municipio, status, area, data_atualizacao)
          ORDER BY data_atualizacao DESC NULLS LAST,
                   id_municipio ASC,
                   status ASC
          LIMIT 1
        )[OFFSET(0)] AS attrs,
        COUNT(*) AS source_row_count,
        ST_UNION_AGG(geometria) AS geometria
      FROM source_rows
      GROUP BY id_imovel
    )
    SELECT
      id_imovel,
      attrs.id_municipio AS id_municipio,
      attrs.status AS status,
      SAFE_CAST(attrs.area AS FLOAT64) AS area_ha,
      source_row_count,
      ST_GEOMETRYTYPE(geometria) AS geometry_type,
      ST_NUMPOINTS(geometria) AS geometry_points,
      ST_ASGEOJSON(geometria) AS geometry_geojson
    FROM per_car
    WHERE geometria IS NOT NULL
    ORDER BY id_imovel
    """


def assert_static_contract() -> None:
    if PROJECT != "metodo-afp-plataforma":
        die("project_contract_changed")
    if UF != "ES":
        die("pilot_uf_contract_changed")
    if SNAPSHOT.isoformat() != "2026-08-04":
        die("snapshot_contract_changed")
    if BUCKET_REGION_OWNER_ATTESTED != "southamerica-east1":
        die("bucket_region_contract_changed")
    sql = extraction_sql().upper()
    if "DATA_EXTRACAO=@SNAPSHOT" not in sql:
        die("exact_snapshot_predicate_missing")
    if "MAX(DATA_EXTRACAO)" in sql:
        die("latest_snapshot_fallback_detected")


class ResourceSampler:
    def __init__(self, temp_root: Path) -> None:
        self.temp_root = temp_root
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.peak_rss_bytes = 0
        self.peak_system_cpu_percent = 0.0
        self.peak_temp_used_bytes = 0

    def start(self) -> None:
        psutil.cpu_percent(interval=None)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=3)
        self._sample()

    def _run(self) -> None:
        while not self.stop_event.wait(0.5):
            self._sample()

    def _sample(self) -> None:
        try:
            root = psutil.Process(os.getpid())
            processes = [root] + root.children(recursive=True)
            rss = 0
            for proc in processes:
                try:
                    rss += int(proc.memory_info().rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
            self.peak_system_cpu_percent = max(
                self.peak_system_cpu_percent,
                float(psutil.cpu_percent(interval=None)),
            )
            disk = shutil.disk_usage(self.temp_root)
            self.peak_temp_used_bytes = max(
                self.peak_temp_used_bytes,
                int(disk.total - disk.free),
            )
        except Exception:
            pass


def run_tippecanoe(ndjson: Path, output: Path, log_path: Path) -> dict[str, Any]:
    version = subprocess.run(
        ["tippecanoe", "--version"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    command = [
        "tippecanoe",
        "--output", str(output),
        "--layer", "car",
        "--minimum-zoom", str(MIN_ZOOM),
        "--maximum-zoom", str(MAX_ZOOM),
        "--read-parallel",
        "--no-feature-limit",
        "--no-tile-size-limit",
        "--force",
        str(ndjson),
    ]
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        die(f"tippecanoe_failed:{proc.returncode}:{tail}")
    if not output.exists() or output.stat().st_size <= 127:
        die("pmtiles_missing_or_too_small")
    with output.open("rb") as fh:
        if not fh.read(7).startswith(b"PMTiles"):
            die("pmtiles_magic_invalid")
    return {
        "version": version,
        "command": command,
        "elapsed_seconds": round(elapsed, 3),
        "log_bytes": log_path.stat().st_size,
    }


def upload_immutable(
    bucket: storage.Bucket,
    local_path: Path,
    object_name: str,
    metadata: dict[str, str],
    content_type: str,
) -> dict[str, Any]:
    blob = bucket.blob(object_name)
    local_sha256 = sha256_file(local_path)
    metadata = {**metadata, "sha256": local_sha256}
    started = time.perf_counter()
    try:
        blob.metadata = metadata
        blob.upload_from_filename(
            str(local_path),
            content_type=content_type,
            if_generation_match=0,
            timeout=900,
        )
        blob.reload()
        disposition = "created"
    except PreconditionFailed:
        blob.reload()
        remote_meta = blob.metadata or {}
        if remote_meta.get("sha256") != local_sha256:
            die(f"immutable_object_conflict:{object_name}")
        if int(blob.size or -1) != local_path.stat().st_size:
            die(f"immutable_object_size_conflict:{object_name}")
        disposition = "reused_identical"
    elapsed = time.perf_counter() - started
    return {
        "object": object_name,
        "generation": str(blob.generation or ""),
        "size_bytes": int(blob.size or local_path.stat().st_size),
        "sha256": local_sha256,
        "disposition": disposition,
        "elapsed_seconds": round(elapsed, 3),
    }


def row_to_feature(row: bigquery.table.Row) -> tuple[dict[str, Any], int]:
    raw_geometry = row.get("geometry_geojson")
    if not raw_geometry:
        die(f"missing_geometry:{row.get('id_imovel')}")
    geometry = json.loads(str(raw_geometry))
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        die(f"unexpected_geometry_type:{geometry.get('type')}:{row.get('id_imovel')}")
    polygon_parts = 1 if geometry["type"] == "Polygon" else len(geometry.get("coordinates") or [])
    feature = {
        "type": "Feature",
        "properties": {
            "car_code": str(row.get("id_imovel") or ""),
            "id_municipio": str(row.get("id_municipio") or ""),
            "status": str(row.get("status") or ""),
            "area_ha": float(row.get("area_ha") or 0.0),
            "snapshot": SNAPSHOT.isoformat(),
        },
        "geometry": geometry,
    }
    if not feature["properties"]["car_code"].startswith("ES-"):
        die(f"non_es_car_detected:{feature['properties']['car_code']}")
    return feature, polygon_parts


def extract_geojsonl(client: bigquery.Client, destination: Path) -> dict[str, Any]:
    params = [
        bigquery.ScalarQueryParameter("uf", "STRING", UF),
        bigquery.ScalarQueryParameter("snapshot", "DATE", SNAPSHOT),
    ]
    dry_config = bigquery.QueryJobConfig(
        query_parameters=params,
        dry_run=True,
        use_query_cache=False,
    )
    dry_job = client.query(extraction_sql(), job_config=dry_config)
    estimated_bytes = int(dry_job.total_bytes_processed or 0)
    if estimated_bytes > MAX_BQ_BYTES:
        die(f"bigquery_dryrun_guard:{estimated_bytes}>{MAX_BQ_BYTES}")

    config = bigquery.QueryJobConfig(
        query_parameters=params,
        use_legacy_sql=False,
        use_query_cache=False,
        maximum_bytes_billed=MAX_BQ_BYTES,
    )
    started = time.perf_counter()
    job = client.query(extraction_sql(), job_config=config)
    rows = job.result(timeout=3600, page_size=1000)

    fingerprint = hashlib.sha256()
    feature_count = 0
    polygon_count = 0
    duplicate_car_count = 0
    total_geometry_points = 0
    sample: dict[str, Any] | None = None
    previous_car = ""

    with destination.open("wb") as out:
        for row in rows:
            feature, parts = row_to_feature(row)
            car_code = feature["properties"]["car_code"]
            if previous_car and car_code <= previous_car:
                die(f"non_deterministic_car_order:{previous_car}:{car_code}")
            previous_car = car_code
            if int(row.get("source_row_count") or 0) > 1:
                duplicate_car_count += 1
            total_geometry_points += int(row.get("geometry_points") or 0)
            polygon_count += parts

            line = (
                json.dumps(feature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            out.write(line)
            fingerprint.update(line)
            feature_count += 1

            if sample is None:
                geom = shape(feature["geometry"])
                point = geom.representative_point()
                if not geom.is_empty and math.isfinite(point.x) and math.isfinite(point.y):
                    sample = {
                        "car_code": car_code,
                        "id_municipio": feature["properties"]["id_municipio"],
                        "lon": float(point.x),
                        "lat": float(point.y),
                    }

    elapsed = time.perf_counter() - started
    if feature_count != EXPECTED_FEATURES:
        die(f"source_feature_count_changed:{feature_count}!={EXPECTED_FEATURES}")
    if not sample:
        die("no_click_sample_resolved")
    if destination.stat().st_size <= 0:
        die("geojsonl_empty")

    return {
        "dry_run_bytes": estimated_bytes,
        "total_bytes_processed": int(job.total_bytes_processed or 0),
        "total_bytes_billed": int(job.total_bytes_billed or 0),
        "elapsed_seconds": round(elapsed, 3),
        "feature_count": feature_count,
        "polygon_count": polygon_count,
        "duplicate_car_count": duplicate_car_count,
        "total_geometry_points": total_geometry_points,
        "geojsonl_bytes": destination.stat().st_size,
        "fingerprint_sha256": fingerprint.hexdigest(),
        "sample": sample,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def main() -> None:
    assert_static_contract()
    env_project = (
        os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or ""
    ).strip()
    if env_project and env_project != PROJECT:
        die(f"runtime_project_mismatch:{env_project}")
    requested_uf = os.getenv("RX_V48_PILOT_UF", UF).strip().upper()
    requested_snapshot = os.getenv("RX_V48_SNAPSHOT", SNAPSHOT.isoformat()).strip()
    if requested_uf != UF:
        die(f"national_or_other_uf_blocked:{requested_uf}")
    if requested_snapshot != SNAPSHOT.isoformat():
        die(f"noncanonical_snapshot_blocked:{requested_snapshot}")
    if os.getenv("RX_V48_ALLOW_NATIONAL", "false").strip().lower() not in {"", "0", "false", "no"}:
        die("national_generation_flag_must_remain_false")

    base = Path(os.getenv("RX_V48_WORKDIR", "/mnt/disks/rxv48"))
    if not base.exists():
        base = Path("/tmp/rxv48")
    base.mkdir(parents=True, exist_ok=True)
    work = base / f"{SNAPSHOT_ID}-{UF.lower()}"
    work.mkdir(parents=True, exist_ok=True)

    ndjson = work / "ES.geojsonl"
    pmtiles = work / "ES.pmtiles"
    tippecanoe_log = work / "tippecanoe.log"
    manifest_path = work / "pilot-manifest.json"

    sampler = ResourceSampler(base)
    overall_started = time.perf_counter()
    generated_at = utcnow()
    sampler.start()
    try:
        bq = bigquery.Client(project=PROJECT)
        extraction = extract_geojsonl(bq, ndjson)
        fingerprint = extraction["fingerprint_sha256"]

        tippecanoe = run_tippecanoe(ndjson, pmtiles, tippecanoe_log)
        pmtiles_sha256 = sha256_file(pmtiles)
        pmtiles_size = pmtiles.stat().st_size

        # Runtime intentionally has objectCreator + objectViewer only. Bucket
        # existence/location/security were verified by the post-bootstrap gate
        # and owner attestation; do not require storage.buckets.get here.
        storage_client = storage.Client(project=PROJECT)
        bucket = storage_client.bucket(BUCKET)

        object_name = f"car/{SNAPSHOT_ID}/uf/{UF}.pmtiles"
        common_meta = {
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_date": SNAPSHOT.isoformat(),
            "analysis_snapshot": SNAPSHOT.isoformat(),
            "map_snapshot": SNAPSHOT.isoformat(),
            "uf": UF,
            "source_fingerprint": fingerprint,
            "generator": "tippecanoe-2.79.0",
        }
        pmtiles_upload = upload_immutable(
            bucket,
            pmtiles,
            object_name,
            common_meta,
            "application/vnd.pmtiles",
        )

        metrics = {
            "schema_version": SCHEMA_VERSION,
            "status": "pilot-generated",
            "project": PROJECT,
            "bucket": BUCKET,
            "bucket_region_owner_attested": BUCKET_REGION_OWNER_ATTESTED,
            "uf": UF,
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_date": SNAPSHOT.isoformat(),
            "analysis_dataset": DATASET,
            "analysis_snapshot": SNAPSHOT.isoformat(),
            "map_snapshot": SNAPSHOT.isoformat(),
            "map_analysis_snapshot_equal": True,
            "source_fingerprint_sha256": fingerprint,
            "source": extraction,
            "tippecanoe": tippecanoe,
            "pmtiles": {
                "object": object_name,
                "size_bytes": pmtiles_size,
                "sha256": pmtiles_sha256,
                "min_zoom": MIN_ZOOM,
                "max_zoom": MAX_ZOOM,
                "upload": pmtiles_upload,
            },
            "resources": {
                "machine": platform.node(),
                "platform": platform.platform(),
                "cpu_count": psutil.cpu_count(logical=True),
                "peak_rss_bytes": None,
                "peak_system_cpu_percent": None,
                "peak_temp_used_bytes": None,
            },
            "sample_click": extraction["sample"],
            "generated_at": generated_at,
            "finished_at": None,
            "total_elapsed_seconds": None,
            "national_generation_allowed": False,
        }

        sampler.stop()
        metrics["resources"].update(
            {
                "peak_rss_bytes": sampler.peak_rss_bytes,
                "peak_system_cpu_percent": round(sampler.peak_system_cpu_percent, 2),
                "peak_temp_used_bytes": sampler.peak_temp_used_bytes,
            }
        )
        metrics["finished_at"] = utcnow()
        metrics["total_elapsed_seconds"] = round(time.perf_counter() - overall_started, 3)
        write_json(manifest_path, metrics)

        manifest_object = f"car/{SNAPSHOT_ID}/manifests/pilot/{UF}/{fingerprint}.json"
        manifest_upload = upload_immutable(
            bucket,
            manifest_path,
            manifest_object,
            common_meta,
            "application/json",
        )
        metrics["manifest"] = manifest_upload
        write_json(manifest_path, metrics)

        print(f"RX_V48_PILOT_UF={UF}")
        print(f"RX_V48_PILOT_SNAPSHOT={SNAPSHOT.isoformat()}")
        print(f"RX_V48_PILOT_FEATURES={extraction['feature_count']}")
        print(f"RX_V48_PILOT_POLYGONS={extraction['polygon_count']}")
        print(f"RX_V48_PILOT_FINGERPRINT={fingerprint}")
        print(f"RX_V48_PILOT_PMTILES_BYTES={pmtiles_size}")
        print(f"RX_V48_PILOT_PMTILES_SHA256={pmtiles_sha256}")
        print(f"RX_V48_PILOT_PMTILES_OBJECT=gs://{BUCKET}/{object_name}")
        print(f"RX_V48_PILOT_SAMPLE_CAR={extraction['sample']['car_code']}")
        print("RX_V48_PILOT_MAP_ANALYSIS_SNAPSHOT_EQUAL=PASS")
        print("RX_V48_PILOT_NATIONAL_GENERATION=BLOCKED")
        print("RX_V48_VECTOR_PILOT_WORKER=PASS")
    finally:
        if sampler.thread.is_alive():
            sampler.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"RX_V48_VECTOR_PILOT_WORKER=FAIL_CLOSED:{type(exc).__name__}:{exc}", file=sys.stderr)
        raise
