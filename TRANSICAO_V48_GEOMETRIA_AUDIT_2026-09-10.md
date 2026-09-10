# TRANSIÇÃO V48 — AUDITORIA E SEMÂNTICA GEOMÉTRICA

Data: 2026-09-10
Branch de trabalho: `ops/v48-budget-gate-probe-20260908`
HEAD preparado para o próximo audit: `4a151cbf340f023c44516ff52443fd00692969dd`
Produção V47 auditada: commit `ee11f50b5db669b80977bdecb2258bebcf26065c`

## 1. Estado do run nacional

Workflow: `V48 National CAR Quadrícula #1`
Run: `34510603924`
Disparo: `workflow_dispatch`
Branch: `ops/v48-budget-gate-probe-20260908`
Commit executado: `0309adf07fa98b5c08a851205dec658273e477a2`
Token usado: `GENERATE_27_UFS`
Concorrência: 3
Resultado: **workflow completo failure; 27/27 UFs falharam; nenhuma UF publicada**.
Preflight passou. O finalizador falhou porque `all_27_published=false`.
Não houve `active.json`. Não houve deploy.

Causa de dado confirmada em log interno de VM:
- `RX_V48_NATIONAL_WORKER=FAIL_CLOSED`
- `RuntimeError: unexpected_geometry_type:GeometryCollection`
- exemplo observado: `SE-2800209-07D88A428D8B4ED5B5647683275F103`
- caminho: `row_to_feature -> extract_geojsonl`, em `v48_national_worker.py`.

O fail-closed funcionou como projetado: a geometria inesperada não foi publicada silenciosamente.

## 2. Política geométrica aprovada e congelada

Documento canônico: `docs/v48_geometry_semantics_policy.md`.

Invariante no mesmo nível de `analysis_snapshot == map_snapshot`:

`analysis_geometry_normalization == map_geometry_normalization`

A geometria usada na análise e a geometria usada no mapa devem sair da **mesma normalização dimensional, declarada e versionada**.

Política aprovada para `GeometryCollection`:
1. Preservar a geometria original e seu fingerprint.
2. `Polygon` e `MultiPolygon` permanecem poligonais sem descarte dimensional intencional.
3. Para `GeometryCollection`, usar `ST_DUMP(..., 2)` para extrair exclusivamente componentes poligonais.
4. Partes lineares/pontuais nunca viram área artificial.
5. Registrar quantos CARs tiveram partes não poligonais descartadas, comprimento total de linhas descartadas, quantidade de pontos descartados, área poligonal antes/depois e diferença.
6. Coleção sem nenhum polígono: CAR não entra, é listado nominalmente e nunca vira polígono vazio.
7. Dois fingerprints: `source_geometry_fingerprint` e `render_geometry_fingerprint`.
8. Divergência de área poligonal acima da tolerância congelada => **FAIL_CLOSED da UF**.
9. Política deve ter versão explícita; versão lógica inicial aprovada: `v48-polygonal-extraction-1`.

Nada disso foi aplicado ainda ao worker ou à produção. Ordem do owner: **audit primeiro; correção V47/V48 depois, numa única frente**.

## 3. V47 em produção — GeometryCollection

A V47 não rejeita `GeometryCollection` de forma geral.
- `_safe_geom()` aceita geometrias Shapely válidas/reparáveis.
- `area_ha_grs80()` possui fallback explícito para coleções e soma área dos membros sem fabricar área para linha/ponto.
- declarado × medido e residual usam Shapely/GEOS + GRS80.
- sobreposição CAR × CAR está com o hardening ativo: union por CAR, `ST_INTERSECTION`, e só conta quando `ST_AREA(intersection)>0`.
- erro irrecuperável vira `state=unavailable`/“Fonte indisponível”; não derruba o portal inteiro.

Conclusão atual: não há evidência de erro de área em massa; a lacuna principal é **auditabilidade/normalização dimensional consistente**.

## 4. Pergunta obrigatória: tipo de Curvelo

CAR de referência:
`MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F`
Snapshot canônico MG: `2026-08-04`.

O benchmark V47 validou área e residual, mas nunca registrou `ST_GEOMETRYTYPE`.
O audit v2 agora medirá duas coisas:
- `v47_selected_row_geometry_type`: tipo da linha que a V47 escolheria com `ORDER BY data_atualizacao DESC NULLS LAST LIMIT 1`;
- `post_union_geometry_type`: tipo depois de `ST_UNION_AGG` por `id_imovel`, relevante à V48 e ao hardening de overlap.

Essa resposta é obrigatória antes da correção.

## 5. Pergunta obrigatória: KML e PNG atuais

O painel V47 rápido não exporta a geometria BigQuery da análise. Ele usa o GeoJSON retornado pelo SICAR/WFS através de `fetch_car_live_resilient()` e o repassa sem normalização.

Blobs verificados contra a produção V47:
- `portal_map_panel_v45.py`: `67fcc022c8fe763f5fc282972b7f066aa7d3b3fb`
- `car_resilient.py`: `300b5c742f0c63dad58b2ed8ce3534a7d9135a46`

Comportamento atual para um `GeometryCollection` GeoJSON padrão recebido do WFS:
- **KML:** `coordsKml()` só trata `Polygon` e `MultiPolygon`; para outro tipo retorna vazio; `downloadKml()` encerra. Resultado: **nenhum arquivo é gerado**.
- **PNG:** `flatten()` percorre `g.coordinates`; `GeometryCollection` padrão usa `g.geometries`; ficam zero pontos e `downloadPng()` encerra. Resultado: **nenhum arquivo é gerado**.
- portanto, não há evidência de linha/ponto solto sendo exportado como perímetro nesse caso; o defeito atual é **falha silenciosa de exportação**.

Lacuna adicional: análise (BigQuery) e exportação (WFS) não têm hoje equivalência de fonte/snapshot/normalização provada. A decisão/correção sobre KML/PNG deve ser fechada junto da correção geométrica após o audit, sem abrir frente separada.

## 6. Audit geométrico — run #5 e correção do próprio audit

Workflow: `V48 Canonical SICAR Snapshot Audit #5`
Run: `34516087808`
HEAD executado: `45a0c5b0a117c01a3158ced53a7bfda00b337602`
Resultado: **failure antes de autenticação/BigQuery**.

Causa do vermelho:
`AssertionError: canceled count-stage marker missing`

Era um falso gate de estágio: exigia a string literal `CANCELED_BY_OWNER_2026-09-09` no workflow, embora a invariante real seja que `v48_canonical_car_counts.py` não seja executável por esse workflow.

Consequências do run #5:
- nenhuma consulta real BigQuery executada;
- nenhum dry-run geométrico executado;
- nenhum artifact de contagens produzido.

Correção aplicada: o gate agora afirma a **invariante** (“script cancelado não aparece no workflow”) e não exige marcador histórico de estágio.

## 7. Audit v2 pronto para novo disparo

HEAD: `4a151cbf340f023c44516ff52443fd00692969dd`
Script: `scripts/v48_geometry_type_audit.py`
Schema: `v48-canonical-geometry-type-audit-2`

O audit v2:
- usa o manifesto canônico imutável por UF;
- SP permanece em `2026-06-02`;
- MG permanece em `2026-08-04`;
- conta CARs por `ST_GEOMETRYTYPE` **depois de `ST_UNION_AGG` por `id_imovel`**;
- não serializa GeoJSON/WKT/WKB;
- faz dry-run das 27 UFs antes de qualquer query real;
- guard de 2 GiB por UF;
- UF acima do guard não executa query real;
- reconcilia soma dos tipos com CARs que possuem geometria;
- registra linhas NULL e CARs sem geometria tipável;
- mede os dois tipos de Curvelo descritos acima;
- registra no artifact a semântica atual de KML/PNG e os blobs de produção verificados.

Pré-checagem **local** no HEAD `4a151cb...`:
- `RX_V48_GEOMTYPE_IMPORT_CHECK=PASS`
- `RX_V48_PUSH_TRIGGER_OFFENDERS=NONE`
- `RX_V48_CANONICAL_SNAPSHOT_STATIC_GATE=PASS`
- `RX_V48_CANONICAL_SNAPSHOT_MANIFEST_GATE=PASS`

Esses resultados são locais e **não são CI PASS**.

## 8. Próxima ação exata

Disparar manualmente no GitHub:
`Actions -> V48 Canonical SICAR Snapshot Audit -> Run workflow`
Branch: `ops/v48-budget-gate-probe-20260908`

O run válido deve estar no HEAD `4a151cbf340f023c44516ff52443fd00692969dd` ou em HEAD posterior que apenas atualize esta transição, sem modificar a lógica auditada. Preferencialmente usar exatamente `4a151cb...` para prova limpa.

Depois do audit:
1. ler tabela por UF/tipo e total nacional;
2. responder tipo exato de Curvelo;
3. decidir numa única frente a correção geométrica de V47 + V48 e o alinhamento de KML/PNG;
4. só depois reexecutar quadrícula nacional;
5. UF que falhar é refeita isoladamente.

Proibido até nova autorização:
- redisparar geração nacional;
- publicar `active.json`;
- fazer deploy;
- corrigir geometria silenciosamente.

Fila depois da quadrícula: confiabilidade do serviço -> mineração ANM -> Raio-X do Poço.
