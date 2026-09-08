# TRANSIÇÃO — V48 PÓS-PILOTO ES — OTIMIZAÇÃO NACIONAL

Data: 2026-09-08
Branch ativa: `ops/v48-budget-gate-probe-20260908`
Repositório: `rodrigo1994336-netizen/raio-x-territorial`
Produção: `https://raio-x-territorial-app.onrender.com`

## REGRA DE CONTINUIDADE

- NÃO reabrir SINAFLOR/V47.
- NÃO gerar nacional ainda.
- NÃO criar `pilot-003` automaticamente.
- NÃO atualizar `car/manifests/active.json`.
- NÃO fazer merge/deploy da quadrícula antes da conferência expressa do usuário.
- Fail-closed sempre.
- Mapa e análise devem usar o MESMO snapshot SICAR: `2026-08-04` / `sicar-2026-08-04`.
- Workflows que criam compute pago: `workflow_dispatch` somente, regra permanente.
- Não expandir IAM apenas para observabilidade/conveniência.

## ESTADO DO PILOTO ES — APROVADO

GitHub workflow: `V48 ES Single Spot Pilot`
Run: `34265129898` (#3)
GitHub job: `102192465194`
Head: `547fe770ca33036e4b97fea49f09a9b521f919fa`
Checkout efetivo: `ops/v48-budget-gate-probe-20260908`
Batch job: `rx-v48-es-sicar-20260804-pilot-002`
Batch state: `SUCCEEDED`
`maxRetryCount=0`
UF: ES
Snapshot mapa/análise: `2026-08-04`
Nacional: BLOQUEADO

### Artefatos e métricas comprovadas

- PMTiles: `car/sicar-2026-08-04/uf/ES.pmtiles`
- `pmtiles_bytes = 167000688`
- PMTiles SHA-256: `988ec0ce57c9842b7ceda669916427fe115619f668e73c1ed1d65af8aafa8e3a`
- source fingerprint: `08c31deb1e7bb96c96225b4a50865f2c8986ecf92afa63206691575d3123ff31`
- manifest: `car/sicar-2026-08-04/manifests/pilot/ES/08c31deb1e7bb96c96225b4a50865f2c8986ecf92afa63206691575d3123ff31.json`
- GHA artifact ID: `10071870243`
- artifact SHA-256: `ccb615f5804920bfc36e800ed0cf631f242b4c780ecff3b8db04a33326ee2a83`
- Batch runDuration: `497.216513469s`
- worker total: `412.633s`
- BigQuery/extraction: `360.174s`
- Tippecanoe/tiles: `49.307s`
- PMTiles upload: `1.61s`
- features: `129496`
- polygons resultantes: `129499`
- duplicate CARs: `2`
- geometry points: `3665471`
- GeoJSONL bytes: `166631717`
- BigQuery processed bytes: `101184372`
- BigQuery billed bytes: `101711872`
- peak RSS: `302014464` bytes
- peak system CPU: `99.8%`
- peak temp disk used: `563224576` bytes
- `map_analysis_snapshot_equal = true`

Amostra:
- CAR: `ES-3200102-00009F9E92794C0F9F1B28812A6E82C7`
- município: `3200102`
- lat: `-20.1969113448968`
- lon: `-41.039691114002636`

## LEITURA DO GARGALO APROVADA PELO USUÁRIO

O piloto revelou que a geração dos tiles NÃO é o gargalo principal:
- BigQuery/extraction: `360.174s` (~72% do Batch runDuration)
- tiles: `49.307s` (~10%)
- upload: `1.61s`

O worker atualmente pagina resultados do BigQuery com `page_size=1000` e processa a transferência entre a infraestrutura do BigQuery e a VM em `southamerica-east1`.

O usuário projetou, a partir do ES:
- ES = 129.496 CARs ~= 1,55% do Brasil
- arquivo ES = 167.000.688 bytes
- Brasil projetado ~= 10,7 GB de PMTiles
- armazenamento projetado ~= US$ 0,21/mês
- custo do piloto ~= US$ 0,012

Essas projeções são hipóteses de planejamento do usuário e devem ser validadas antes de decisão nacional.

# PRIMEIRA MISSÃO OBRIGATÓRIA DO NOVO CHAT

ANTES de qualquer nacional, atacar o gargalo de extração BigQuery e trazer números comparáveis para quatro alternativas.

## 1. PAGE_SIZE MAIOR NA API BIGQUERY

Objetivo:
- testar `page_size` significativamente maior que 1000, pelo menos `10000` e `50000`, sem alterar snapshot nem semântica da query;
- medir o ganho real em tempo de extração/iteração;
- verificar memória, tamanho médio por página, número de requests, estabilidade e eventual limitação prática da API/client library;
- preferir teste controlado no ES ou equivalente que NÃO publique novo objeto imutável conflitante;
- não criar novo Batch pago sem autorização expressa se o teste exigir compute pago.

Entregar:
- baseline `page_size=1000` = `360.174s` no ES;
- tempos medidos para 10k e 50k, se executáveis;
- requests/páginas estimadas/medidas;
- ganho percentual;
- efeito em RSS e robustez;
- projeção nacional.

## 2. BIGQUERY `EXPORT DATA` DIRETO PARA GCS

Avaliar tecnicamente e economicamente:
- `EXPORT DATA` da query/snapshot para GCS, idealmente em formato apropriado para ingestão subsequente;
- se pode exportar diretamente geometrias/atributos necessários sem mudar verdade analítica;
- custo de query/extração/exportação conforme pricing vigente e região real do dataset;
- se existe cobrança específica de data extraction/export ou apenas query + storage/network, confirmar em fonte oficial atual;
- restrições de região entre dataset BigQuery e bucket GCS;
- tempo esperado/medido para ES e projeção nacional;
- se `EXPORT DATA` elimina paginação linha a linha e permite worker consumir arquivo sequencial/paralelo.

Entregar:
- arquitetura proposta;
- custos explícitos e separados: query, storage temporário, transferência/egress, compute;
- tempo ES e Brasil projetado;
- prós/contras operacionais e de reproducibilidade;
- compatibilidade com publicação imutável e snapshot `2026-08-04`.

## 3. EXTRAÇÃO/GERAÇÃO EM REGIÃO DOS EUA PRÓXIMA AO BIGQUERY

Avaliar:
- mover a VM/Batch de geração para região dos EUA próxima ao BigQuery, caso a localização real do dataset favoreça isso;
- fazer BigQuery -> VM local nos EUA;
- gerar PMTiles nos EUA;
- transferir apenas o `.pmtiles` final para o bucket em São Paulo;
- comparar com manter VM em São Paulo.

Usar como hipótese inicial citada pelo usuário, MAS verificar pricing vigente antes de decidir:
- transferência EUA -> América Latina: ~US$ 0,14/GiB.

Entregar:
- região BigQuery real e regiões Batch candidatas;
- custo Spot equivalente de `e2-standard-8` ou melhor opção adequada;
- custo de transferência do PMTiles final EUA -> São Paulo;
- custo de transferir dados brutos vs apenas PMTiles;
- tempo ES e Brasil projetado;
- diferença de latência/throughput esperada e, se possível, medida.

## 4. PLANO NACIONAL OTIMIZADO — POR UF E EM PARALELO

Somente depois dos itens 1–3:
- escolher a melhor estratégia por evidência, não preferência;
- projetar o Brasil inteiro por UF;
- considerar paralelismo controlado por UFs;
- respeitar orçamento mensal de US$ 20 e workflow manual-only para compute pago;
- manter 27 arquivos PMTiles por UF + overview nacional ultraleve;
- manter fingerprints seletivos por UF;
- nacional continua BLOQUEADO até aprovação expressa do usuário.

Entregar tabela por UF com, no mínimo:
- CARs do snapshot canônico quando disponíveis;
- PMTiles bytes projetados;
- tempo de extração projetado;
- tempo de tiles projetado;
- tempo total projetado;
- custo compute;
- custo BigQuery;
- custo storage;
- custo transferência/egress;
- grupo/lote de paralelismo sugerido.

Entregar também cenários:
- sequencial;
- paralelismo moderado;
- paralelismo máximo recomendado dentro do orçamento/quotas.

## CRITÉRIO DE DECISÃO

A melhor opção deve minimizar principalmente:
1. tempo total nacional;
2. custo total;
3. complexidade operacional;
4. risco de inconsistência;
5. dependência de transferência inter-região.

Não aceitar uma otimização que quebre:
- `analysis_snapshot == map_snapshot == 2026-08-04`;
- semântica de consolidação dos 2 CARs duplicados no snapshot;
- ordem/fingerprint determinística quando necessária para reprodutibilidade;
- publicação imutável com `if_generation_match=0`;
- nacional bloqueado até aprovação.

## ORDEM DE TRABALHO DO NOVO CHAT

1. Ler este arquivo inteiro.
2. Não refazer o piloto ES.
3. Confirmar o estado atual em poucas linhas.
4. Começar imediatamente pelos quatro estudos de otimização acima, com fontes oficiais atuais para pricing/regiões/limites BigQuery/Batch/GCS.
5. Não executar nacional nem criar compute pago sem autorização expressa.
6. Ao final, recomendar uma arquitetura nacional e apresentar projeção de tempo/custo por UF e em paralelo.

## ARQUIVOS DE CONTEXTO COMPLEMENTARES

- `TRANSICAO_V48_PILOTO_ES_2026-09-08.md`
- `TRANSICAO_V48_PILOTO_ES_SUCESSO_2026-09-08.md`
- `config/v48_vector_pilot.json`
- `scripts/v48_vector_pilot_worker.py`
- `scripts/v48_es_batch_submit.py`
- `scripts/v48_es_paid_gate.py`
- `.github/workflows/v48-es-batch-pilot.yml`
- `.github/workflows/v48-es-batch-submit-static.yml`

## NÃO FAZER

- nenhum `pilot-003` automático;
- nenhuma geração nacional antes da decisão de otimização e aprovação;
- nenhum `active.json` global;
- nenhum merge/deploy da quadrícula;
- nenhuma mudança de snapshot;
- nenhuma expansão IAM por conveniência;
- nenhum gatilho automático em workflow que possa criar compute pago;
- nenhuma reabertura de SINAFLOR/V47.
