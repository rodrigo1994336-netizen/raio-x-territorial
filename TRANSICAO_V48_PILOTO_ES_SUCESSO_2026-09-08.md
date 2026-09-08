# TRANSIÇÃO DELTA — V48 PILOTO ES 002 — SUCESSO

Data: 2026-09-08
Branch: `ops/v48-budget-gate-probe-20260908`
Complementa: `TRANSICAO_V48_PILOTO_ES_2026-09-08.md`

## RESULTADO

- GitHub workflow: `V48 ES Single Spot Pilot`
- Run: `34265129898` (#3)
- GitHub job: `102192465194`
- Dispatch: manual (`workflow_dispatch`)
- Head: `547fe770ca33036e4b97fea49f09a9b521f919fa`
- Checkout: `ops/v48-budget-gate-probe-20260908`
- Batch job: `rx-v48-es-sicar-20260804-pilot-002`
- Batch state: `SUCCEEDED`
- Job criado por esse run: `true`
- `maxRetryCount=0`
- UF: ES
- snapshot mapa/análise: `2026-08-04`
- nacional: `false` / BLOQUEADO

## BATCH

- createTime: `2026-09-08T18:47:52.384850390Z`
- RUNNING: `2026-09-08T18:48:58.619896299Z`
- SUCCEEDED: `2026-09-08T18:57:15.836409768Z`
- runDuration: `497.216513469s`
- machine: `e2-standard-8`
- provisioning: `SPOT`
- disk temporário: `pd-balanced` 100 GB
- task count: 1
- task result: SUCCEEDED=1

## ARTEFATOS / MANIFEST

- PMTiles object: `car/sicar-2026-08-04/uf/ES.pmtiles`
- PMTiles bytes: `167000688`
- PMTiles SHA-256: `988ec0ce57c9842b7ceda669916427fe115619f668e73c1ed1d65af8aafa8e3a`
- source fingerprint: `08c31deb1e7bb96c96225b4a50865f2c8986ecf92afa63206691575d3123ff31`
- pilot manifest: `car/sicar-2026-08-04/manifests/pilot/ES/08c31deb1e7bb96c96225b4a50865f2c8986ecf92afa63206691575d3123ff31.json`
- GHA artifact ID: `10071870243`
- artifact ZIP SHA-256: `ccb615f5804920bfc36e800ed0cf631f242b4c780ecff3b8db04a33326ee2a83`

## DADOS / WORKER

- features: `129496`
- polygons: `129499`
- duplicate CARs: `2`
- total geometry points: `3665471`
- GeoJSONL bytes: `166631717`
- BQ dry-run bytes: `101184372`
- BQ processed bytes: `101184372`
- BQ billed bytes: `101711872`
- worker total: `412.633s`
- BigQuery/extraction: `360.174s`
- Tippecanoe/tiles: `49.307s`
- PMTiles upload: `1.61s`
- peak RSS: `302014464` bytes
- peak system CPU: `99.8%`
- peak temp used: `563224576` bytes

## AMOSTRA

- CAR: `ES-3200102-00009F9E92794C0F9F1B28812A6E82C7`
- município: `3200102`
- lat: `-20.1969113448968`
- lon: `-41.039691114002636`
- `map_analysis_snapshot_equal=true`

## PENDÊNCIA DE STDOUT

O runnable foi instrumentado para emitir `RX_V48_BATCH_FINAL_METRICS=...` com tempos de apt/toolchain/download/build/install/venv/worker e `total_s`. A SA de orquestração não possui `logging.viewer` por decisão de least privilege, e o stdout da VM não é copiado pelo Batch para o artifact/GitHub log. Portanto os campos internos do bootstrap só devem ser preenchidos a partir da linha real do Cloud Logging em sessão de dono; NÃO inferir por subtração.

Também foram emitidos na VM, mas exigem Cloud Logging para leitura literal:
- `RX_V48_BATCH_BOOTSTRAP=TOOLCHAIN_OK`
- `RX_V48_BATCH_BOOTSTRAP_CC1=...`
- `RX_V48_BATCH_BOOTSTRAP_CC1PLUS=...`
- `RX_V48_BATCH_FINAL_METRICS=...`
- `RX_V48_BATCH_RUNNABLE=PASS`

## BLOQUEIOS / PRÓXIMA FASE

- NÃO criar `pilot-003` automaticamente.
- NÃO gerar nacional.
- NÃO atualizar `active.json` global.
- NÃO mergear/deployar quadrícula ainda.
- Próximo: obter a linha literal `RX_V48_BATCH_FINAL_METRICS` pelo Cloud Logging do dono; depois medir PMTiles (TTFB/ranges/p50/p95/bytes por viewport) e validar mapa-análise com o CAR amostral.
- Nacional só após números completos e autorização expressa do usuário.
