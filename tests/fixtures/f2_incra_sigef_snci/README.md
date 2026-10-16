# F2 · SIGEF e SNCI pela base oficial do INCRA — fixtures

Respostas reais gravadas em 13/09/2026 (rede residencial no Brasil) com o próprio transporte de
`incra_acervo_f2.py` (mesma URL, mesmo `bbox`, `maxFeatures=500`, `outputFormat=GML2`). Usadas por
`scripts/f2_incra_sigef_snci_gate.py`, que roda sem rede. A data exata e as URLs estão em `manifest.json`.

## O que foi alterado nas respostas (LGPD)

- `rt`, `art`, `cod_profissional_credenciado`, `num_processo`, `registro_matricula` e `registro_data`
  tiveram o valor trocado por `SENTINELA-LGPD-REMOVIDO`. O gate prova que esse texto nunca aparece
  na saída do módulo, do relatório nem do cartão (o parser só lê uma lista branca de campos).
- `nome_area` e `nome_imovel` foram pseudonimizados (`IMOVEL DE REFERENCIA 01`, …): nome de imóvel de
  outro cadastro pode conter nome de pessoa.
- Geometrias, códigos de parcela, número de certificação, códigos SNCR, datas e status ficaram como vieram.
- `car_*.geojson`: só a geometria e `cod_imovel`, `uf`, `municipio`, `area` do SICAR.
- `*__pamgia_sigef_publico_10.json`: resultado de `deploy_app.query_sigef` (o espelho que o relatório
  lia), reduzido a `{type, features, ok}`.

## Casos

| rótulo | CAR | o que prova |
|---|---|---|
| `curvelo_teste` | MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F | as 4 camadas responderam vazias: SIGEF e SNCI `not_found` |
| `vizinho_snci` | MG-3120904-528DEBA144FF4CEE994BA14D1ABD0E71 | SNCI nº 061308000091-60 (06/08/2013, 178,7390 ha) cobre ~100% do CAR; bordas SIGEF < 1% ignoradas |
| `vizinho_sigef2025` | MG-3120904-82A2876828AB43EE91CA8D1E33EE785F | SIGEF particular aprovado em 18/06/2025 cobre 99,84%; espelho público PAMGIA = 0 |
| `vizinho_sigef2021` | MG-3120904-DB822B19289143419DA829F1633B75EF | SIGEF particular REGISTRADA, aprovada em 28/05/2021, cobre 99,91%; espelho público PAMGIA = 0 |

## Controles negativos reais

| arquivo | pedido | resposta do INCRA | leitura correta |
|---|---|---|---|
| `control__rj_certificada_sigef_publico.xml` | `certificada_sigef_publico_rj` | HTTP 200, 0 bytes | não respondeu (pendente) |
| `control__unknown_theme.xml` | tema inexistente `imoveiscertificados_privado_xx` | HTTP 200, 0 bytes | não respondeu (pendente) |
| `control__json_output.xml` | `outputFormat=application/json` | HTTP 200 + `ServiceExceptionReport` | não respondeu (pendente) |

Regravar (precisa de rede; o CI nunca roda): `PYTHONPATH=. python scripts/f2_record_incra_fixtures.py`. Teste de fonte externa valida a regra, nunca o valor: ao
regravar, os percentuais podem mudar na última casa e o gate aceita a faixa.
