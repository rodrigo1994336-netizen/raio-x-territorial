# V48 — Política canônica de semântica geométrica

Status: **APROVADA PELO OWNER em 2026-09-10; implementação corretiva aguardará o audit geométrico.**

## Invariante de mesmo nível do snapshot

Assim como `analysis_snapshot == map_snapshot`, fica congelada a seguinte regra permanente:

`analysis_geometry_normalization == map_geometry_normalization`

A geometria usada na análise e a geometria usada no mapa devem sair da **mesma normalização dimensional, declarada e versionada**, aplicada sobre a mesma fonte canônica por UF. Não é permitido que mapa e análise façam reparos, descartes ou coerções geométricas diferentes e ainda sejam apresentados como o mesmo dado.

## Política aprovada para GeometryCollection

Versão lógica inicial: `v48-polygonal-extraction-1`.

1. A geometria de origem do CAR é preservada como evidência e recebe fingerprint próprio.
2. `Polygon` e `MultiPolygon` permanecem poligonais sem descarte dimensional intencional.
3. `GeometryCollection` é decomposta por dimensão com `ST_DUMP(..., 2)`, mantendo somente componentes poligonais para a geometria renderizável/analisável.
4. Componentes não poligonais nunca são convertidos artificialmente em área.
5. Deve ser registrado, por UF e no consolidado nacional:
   - número de CARs `GeometryCollection`;
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
8. A igualdade de área poligonal antes/depois da extração é invariante de publicação. Diferença acima da tolerância numérica explicitamente congelada deve causar **FAIL_CLOSED da UF**, sem publicação.
9. A política de normalização deve ter identificador/versionamento explícito em manifestos e relatórios de geração.

## Exportações KML/PNG — estado atual auditado, correção ainda não aberta

Na V47 em produção, KML/PNG são alimentados pelo GeoJSON do SICAR/WFS obtido por `fetch_car_live_resilient`, enquanto a análise de integridade usa SICAR via BigQuery. Portanto, hoje não há prova de equivalência de fonte/snapshot/normalização entre análise e exportação.

O exportador atual aceita `Polygon` e `MultiPolygon` para KML. Para um `GeometryCollection` GeoJSON padrão, `coordsKml()` retorna vazio e `downloadKml()` encerra sem criar arquivo. No PNG, `flatten()` percorre `g.coordinates`; um `GeometryCollection` padrão usa `g.geometries`, resultando em zero pontos e encerramento sem arquivo. Assim, o risco atual identificado é **falha silenciosa de exportação**, não exportação de linha/ponto solto como perímetro.

A decisão de vincular KML/PNG à mesma geometria canônica normalizada será fechada **junto com a correção V47/V48 após o audit**, em uma única frente, conforme ordem do owner.

## Restrições de mudança

- Esta política não autoriza deploy.
- Esta política não autoriza `active.json`.
- Esta política não autoriza redisparo nacional.
- Nenhuma correção geométrica deve ser implementada antes da leitura do audit de tipos nas 27 UFs e do tipo de Curvelo.
