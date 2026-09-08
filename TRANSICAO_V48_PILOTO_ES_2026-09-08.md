# TRANSIÇÃO — V48 QUADRÍCULA DE VETOR — PILOTO ES

Data: 2026-09-08
Branch ativa: `ops/v48-budget-gate-probe-20260908`
Produção: `https://raio-x-territorial-app.onrender.com`

## REGRA DE CONTINUIDADE

- NÃO reabrir SINAFLOR/V47.
- NÃO gerar nacional.
- NÃO atualizar ponteiro global `active.json`.
- NÃO fazer merge/deploy da quadrícula antes da conferência do usuário.
- Fail-closed sempre.
- Mapa e análise devem usar exatamente o mesmo snapshot SICAR: `2026-08-04` / `sicar-2026-08-04`.
- Um único job ES por tentativa autorizada; `maxRetryCount=0`.
- Nenhum `pilot-003` automático.
- Workflows que criam compute pago: `workflow_dispatch` somente, regra permanente.

## ESTADO DA PRODUÇÃO

V47/V48 SINAFLOR está fechado e em produção.
Main anterior da aplicação: `ee11f50b5db669b80977bdecb2258bebcf26065c`.
Workflow manual pago foi levado isoladamente à main pelo PR #36.
Merge do PR #36: `2ccf6e8402485ae830bba6b10c580aa6b2de1c2a`.
PR #36 alterou somente `.github/workflows/v48-es-batch-pilot.yml`.
Render não iniciou novo deploy por esse merge; produção permaneceu LIVE no deploy anterior.

## PILOTO E CONTRATO

UF piloto: ES.
Snapshot: `2026-08-04`.
Features esperadas: 129.496.
Geometrias: 129.495 Polygon, 1 MultiPolygon, 0 outras, 2 CARs duplicados no snapshot.
Job atual autorizado: `rx-v48-es-sicar-20260804-pilot-002`.
Região: `southamerica-east1`.
Machine: `e2-standard-8`.
Provisioning: SPOT.
Disco: `pd-balanced`, 100 GB.
Retry: 0.
Max run: 3600 s.
Runtime SA: `rx-v48-vector-worker@metodo-afp-plataforma.iam.gserviceaccount.com`.
Bucket: `raio-x-territorial-car-metodo-afp-plataforma`.
Nacional: BLOQUEADO.

## PILOT-001 — FALHA CONFIRMADA

Batch job: `rx-v48-es-sicar-20260804-pilot-001`.
GitHub run: `34249774836`.
Causa encontrada no Cloud Logging da VM:
- download do Tippecanoe e SHA-256 passaram;
- `make` iniciou;
- `g++: fatal error: cannot execute 'cc1plus': No such file or directory`;
- `cc: fatal error: cannot execute 'cc1': No such file or directory`;
- também apareceu `make: uname: No such file or directory`.
Correção aprovada: PATH explícito, gcc/g++/coreutils explícitos, gate real de cc1/cc1plus antes do build.

## PILOT-002 — PRIMEIRO DISPATCH NÃO CRIOU VM

GitHub run: `34263657282`.
Job GitHub: `102187546131`.
Branch efetivamente checada: `ops/v48-budget-gate-probe-20260908`.
SHA efetivamente checado: `1033694f0e965b84240375a30b3c8a9047bb14a2`.
Preflight: PASS.
Falha ocorreu antes do submit, no inline Python gate:
`SyntaxError: unexpected character after line continuation character`.
Passo `Submit or observe...`: SKIPPED.
Portanto nenhuma VM pilot-002 foi criada nessa rodada.

## CORREÇÃO DO GATE

Novo arquivo compilável: `scripts/v48_es_paid_gate.py`.
Commit criação: `009ee34f9c48f1c3fe4f6ad963830ee14cd03495`.

Gate estático atualizado para:
- `python -m py_compile scripts/v48_es_batch_submit.py scripts/v48_es_paid_gate.py scripts/v48_vector_pilot_worker.py`;
- executar `python scripts/v48_es_paid_gate.py`;
- validar runnable exato com `bash -n`;
- validar pacotes por tokens sem casar barras/whitespace.
Commit: `5afb2cb04d09d745b0983be60c1133487aa23674`.

Workflow pago atualizado:
- continua SOMENTE `workflow_dispatch`;
- compila submitter/gate/worker antes de preflight/mutação;
- usa `python scripts/v48_es_paid_gate.py` em vez de Python inline.
Commit: `184742ca78ff17609298c324dabb37defed1b418`.

Gate estático final:
- run `34264478861`;
- job `102190292058`;
- conclusão SUCCESS;
- py_compile PASS;
- contrato do pilot-002 PASS;
- runnable `bash -n` PASS.

O workflow legado `V48 Budget Gate Probe` continua podendo aparecer vermelho por falta intencional de permissão Budget Reader da SA. Isso é ruído conhecido e NÃO deve motivar expansão IAM.

## BOOTSTRAP CORRIGIDO QUE DEVE SER OBSERVADO NA PRÓXIMA VM

Esperar no Cloud Logging:
- `RX_V48_BATCH_BOOTSTRAP=TOOLCHAIN_OK`
- `RX_V48_BATCH_BOOTSTRAP_CC1=<path executável>`
- `RX_V48_BATCH_BOOTSTRAP_CC1PLUS=<path executável>`

Antes disso o runnable:
- exporta PATH `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`;
- usa `$SUDO apt-get update -y`;
- instala explicitamente `build-essential gcc g++ coreutils ...`;
- verifica `command -v gcc g++ uname make curl tar sha256sum python3 stat`;
- verifica executáveis internos retornados por `gcc -print-prog-name=cc1` e `g++ -print-prog-name=cc1plus`.

## MÉTRICAS ESPERADAS SE PASSAR

Linha final do runnable:
`RX_V48_BATCH_FINAL_METRICS=...`

Campos:
- `pmtiles_bytes`
- `apt_update_s`
- `apt_install_s`
- `toolchain_gate_s`
- `tippecanoe_download_s`
- `tippecanoe_verify_extract_s`
- `tippecanoe_build_s`
- `tippecanoe_install_s`
- `python_env_deps_s`
- `worker_prepare_s`
- `worker_s`
- `bq_s`
- `tiles_s`
- `pmtiles_upload_s`
- `manifest_upload_s`
- `worker_reported_s`
- `total_s`

Também esperar `RX_V48_BATCH_RUNNABLE=PASS`.

## PRÓXIMA AÇÃO EXATA

1. Fazer novo `workflow_dispatch` de `V48 ES Single Spot Pilot` apontando para `ops/v48-budget-gate-probe-20260908` no commit mais recente.
2. Confirmar checkout da branch de trabalho e JOB_ID `rx-v48-es-sicar-20260804-pilot-002`.
3. Se criar o Batch, acompanhar somente esse job determinístico.
4. Se falhar, NÃO redisparar: trazer estado, exit code e Cloud Logging, especialmente TOOLCHAIN_OK/CC1/CC1PLUS.
5. Se passar, extrair `RX_V48_BATCH_FINAL_METRICS`, PMTiles SHA/tamanho, manifest e preparar validação mapa-análise.
6. Nacional continua BLOQUEADO até métricas e autorização expressa do usuário.
