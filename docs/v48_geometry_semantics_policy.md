# V48 — Política canônica de semântica geométrica

Status: **APROVADA PELO OWNER em 2026-09-10; audit geométrico #6 aprovado e correção conjunta V47 + V48 autorizada.**

## Evidência aprovada no audit #6

Run `34519865245`, commit `0872cfc9452e16922329a5f4d0055867938f2e74`:

- `ST_Polygon`: 8.430.505 CARs;
- `ST_MultiPolygon`: 36.403 CARs;
- `ST_GeometryCollection`: 977 CARs (aprox. 0,0115%);
- 1 CAR sem geometria publicada, no Maranhão;
- total de CARs distintos: 8.467.886;
- Curvelo `MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F`: `ST_Polygon` tanto na linha que a V47 escolheria quanto após `ST_UNION_AGG`.

Fingerprint canônico aceito do conteúdo do audit #6:

`2c718fe0c6f5798430f65eb32a9bb9a9e0b51710ee6de34ce2dd72aac83830cd`

## Invariantes permanentes

Assim como `analysis_snapshot == map_snapshot`, ficam congeladas as regras:

`analysis_geometry_normalization == map_geometry_normalization`

`analysis_snapshot == map_snapshot == canonical_snapshot_for_uf`

A geometria usada na análise, no mapa e nas exportações deve sair da **mesma fonte canônica por UF e da mesma normalização dimensional, declarada e versionada**. Não é permitido que mapa, análise, KML ou PNG façam reparos, descartes ou coerções geométricas independentes e ainda sejam apresentados como o mesmo dado.

A raiz arquitetural do defeito não é a mera existência de `GeometryCollection`; é a existência de **lógicas geométricas paralelas por consumidor**.

## Política aprovada para GeometryCollection

Versão lógica inicial: `v48-polygonal-extraction-1`.

1. A geometria de origem do CAR é preservada como evidência e recebe fingerprint próprio.
2. `Polygon` e `MultiPolygon` permanecem poligonais sem descarte dimensional intencional.
3. `GeometryCollection` é decomposta por dimensão com `ST_DUMP(..., 2)`, mantendo somente componentes poligonais para a geometria renderizável/analisável.
4. Componentes não poligonais nunca são convertidos artificialmente em área.
5. Deve ser registrado, por UF e no consolidado nacional:
   - número de CARs `GeometryCollection`/normalizados;
   - número de CARs com partes não poligonais descartadas;
   - quantidade de componentes lineares descartados e comprimento total descartado;
   - quantidade de componentes pontuais descartados;
   - área poligonal antes da normalização;
   - área poligonal depois da normalização;
   - diferença de área.
6. Se uma coleção não contiver nenhum componente poligonal, o CAR não entra na geometria renderizada/analisada e deve ser listado nominalmente por `id_imovel`; nunca vira polígono vazio.
7. Devem existir dois fingerprints independentes:
   - `source_geometry_fingerprint`: geometria original recebida;
   - `render_geometry_fingerprint`: geometria poligonal efetivamente usada depois da normalização.
8. A igualdade de área poligonal antes/depois da extração é invariante de publicação. Diferença acima da tolerância numérica congelada deve causar **FAIL_CLOSED da UF**, sem publicação.
9. A política de normalização deve ter identificador/versionamento explícito em manifestos e relatórios de geração.
10. O painel do cliente deve exibir, **somente** quando a normalização tiver sido efetivamente aplicada ao imóvel e houver componentes não poligonais descartados, exatamente a declaração:

> A geometria publicada para este imóvel contém elementos que não são área (linhas ou pontos). O Raio-X considera apenas a parte poligonal.

Essa declaração não aparece para imóveis que não exigiram normalização.

## Imóvel sem geometria publicada

Imóvel presente no snapshot canônico, mas sem geometria publicada, é um estado próprio. Não pode cair em `else`, não pode ser convertido em área zero e não pode receber erro genérico.

Regras:

- não entra no mapa;
- não gera KML;
- não gera PNG;
- não é enviado ao Tippecanoe;
- permanece contado e auditável no universo cadastral;
- é listado nominalmente por `id_imovel`;
- na análise, a apresentação ao cliente deve ser exatamente:

> este imóvel não possui geometria publicada na base do CAR em DD/MM/AAAA

A data é a do snapshot canônico da UF.

## Mapa, KML e PNG

A geometria autoritativa dessas quatro superfícies é única:

1. análise;
2. mapa do imóvel;
3. KML;
4. PNG.

Todas consomem a geometria canônica normalizada derivada do BigQuery/SICAR no snapshot canônico da UF. O WFS pode permanecer como fonte auxiliar de atributos e descoberta durante a transição, mas sua geometria não pode substituir, complementar ou servir de fallback para a geometria autoritativa do imóvel.

Para imóvel sem geometria canônica, mapa/KML/PNG permanecem indisponíveis em vez de reutilizar geometria WFS.

## Regressão permanente

Dois sentinelas reais e complementares ficam congelados:

- **Polygon de controle:** `MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F` (Curvelo), snapshot MG `2026-08-04`. Deve continuar `ST_Polygon` antes e depois da normalização e não pode ser marcado como normalizado.
- **GeometryCollection real:** `MG-3100708-4B47889790D4418F8941396E87C461F0`, snapshot MG `2026-08-04`. Deve continuar sendo reconhecido como `ST_GeometryCollection` na origem, produzir geometria renderizável exclusivamente poligonal após a normalização, declarar descarte não poligonal e preservar fingerprints source/render independentes.

### Proveniência obrigatória do sentinela GeometryCollection

O audit geométrico #6 (`run 34519865245`) provou que MG contém **202** CARs `ST_GeometryCollection` após `ST_UNION_AGG` no snapshot canônico `2026-08-04`. O artifact do audit guardou as contagens, mas não os `id_imovel` individuais.

O identificador permanente foi então obtido por consulta autenticada mínima e determinística no `V48 Geometry Sentinel Discovery`, run `34527676691`, usando exatamente a semântica `ST_GEOMETRYTYPE` após `ST_UNION_AGG`, `ORDER BY id_imovel LIMIT 1`. O resultado foi:

`MG-3100708-4B47889790D4418F8941396E87C461F0`

Proveniência congelada:

- source audit run: `34519865245`;
- source audit content fingerprint: `2c718fe0c6f5798430f65eb32a9bb9a9e0b51710ee6de34ce2dd72aac83830cd`;
- discovery run: `34527676691`;
- discovery content fingerprint: `3ab26311d90b56e0d57a40680d3d1c363431810c251c2f7bd99554edb62c2ed1`;
- regra de seleção: `lexicographically first canonical MG GeometryCollection after ST_UNION_AGG`.

O gate real também deve provar o estado do CAR sem geometria do Maranhão e registrar nominalmente seu `id_imovel`.

## Regra permanente — identificadores de regressão

**Identificador copiado de log não vale como dado canônico.** Logs podem truncar, mascarar, concatenar ou reformatar valores. Um identificador usado como sentinela permanente só é admissível quando tiver sido obtido e validado por consulta estruturada ou artifact verificável, com snapshot, semântica da consulta e proveniência registradas.

O antigo código de SE `SE-2800209-07D88A428D8B4ED5B5647683275F103`, copiado de mensagem de erro de VM, foi rejeitado: a consulta canônica retornou zero linhas e o sufixo não atende ao formato completo de 32 caracteres hexadecimais. Ele não pode voltar a ser usado como sentinela ou evidência cadastral.

## Evidência e recuperação

A publicação de cada UF deve preservar a prova da normalização: versão, fingerprints source/render, registros dos CARs normalizados, descartes de linhas/pontos, áreas antes/depois/diferença e exclusões nominais.

Se uma execução cair antes de gravar evidência geométrica completa, `RECONCILE_MANIFEST_ONLY` não pode reconstruir um manifesto com menos prova. Nesse caso a UF falha fechada e deve ser refeita.

O consolidado nacional deve agregar as mesmas métricas por UF e nacionalmente.

## Restrições de mudança

- Uma frente geométrica única V47 + V48; não abrir correções paralelas por exportador.
- Nenhum deploy sem conferência do owner.
- Nenhum `active.json` nesta correção.
- Nenhum redisparo da geração nacional antes de a correção estar fechada e conferida.
- Nenhum workflow `v48-*` por `push`.
- Compute pago apenas por `workflow_dispatch` explícito.
