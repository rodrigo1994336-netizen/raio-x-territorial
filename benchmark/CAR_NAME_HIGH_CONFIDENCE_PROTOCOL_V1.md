# CAR Name High-Confidence Protocol V1 — frozen before measurement

Date frozen: 2026-09-06
Scope: Minas Gerais
Parent protocol: CAR_NAME_COVERAGE_PROTOCOL_V1
Stop rule frozen before measurement: if total automatic CAR->name coverage (high-confidence + municipality/area fallback) is >= 25.00000%, implement the high-confidence route and close Stage 2. If it is < 25.00000%, document the measured result, do not optimize thresholds, close Stage 2 anyway, and proceed to Stage 3.

## High-confidence route being measured

Only a deterministic non-personal-data bridge may enter NOME_GRATUITO_ALTA:

CAR polygon -> certified INCRA/SIGEF polygon -> exact INCRA property code -> active CAFIR record -> denomination.

Owner, holder, registrant, CPF, CNPJ and any other personal-data field are forbidden.

## Geometry acceptance — frozen before seeing result

A CAR/SIGEF bridge is accepted only when all conditions are simultaneously true:

1. Both geometries are valid/non-empty and intersect.
2. Intersection covers at least 98.0% of the CAR polygon area.
3. Intersection covers at least 98.0% of the SIGEF polygon area.
4. The SIGEF candidate exposes a non-empty INCRA property code (`codigo_imo`).
5. Among all SIGEF candidates satisfying items 1-4 for that CAR, exactly one distinct INCRA property code remains.
6. That exact INCRA code resolves to exactly one active CAFIR record with a usable denomination. Duplicate active CAFIR records for the same INCRA code with conflicting denominations are unresolved, never tie-broken.

Confidence label: MUITO_ALTA.
Method id: INCRA_CODE_EXACT_GEOMETRY_98.

No threshold may be relaxed after the statewide result is visible. Any different threshold requires a new protocol/version and a fresh benchmark.

## Source state

SIGEF public/private layers are public fundiária context from INCRA as mirrored in IBAMA/PAMGIA. If either required source is unavailable during the measurement, that condition must be reported as SOURCE_UNAVAILABLE and must not be converted to zero coverage.

## Benchmark accounting

The eligible denominator remains exactly the frozen denominator from CAR_NAME_COVERAGE_PROTOCOL_V1: 1,167,297 MG CAR records (AT+PE+SU under that run), unless source drift is explicitly reported. High-confidence successes are removed first from the fallback buckets; the remaining records retain the frozen municipality+area classifier.

Headline totals after the cascade must still sum to the eligible denominator:
- NOME_GRATUITO_ALTA
- NOME_MUNICIPIO_AREA
- SEM_NOME_AMBIGUIDADE
- SEM_NOME_AUSENCIA_CAFIR

## Reference CAR acceptance

The Curvelo benchmark CAR must be evaluated blind by this route. Its known denomination must not appear anywhere in the matching inputs or matching code. If the bridge does not independently resolve it, the result remains unresolved/ambiguous; no special case is permitted.

## Post-measurement audit note — denominator provenance

This note documents provenance only. It does not change the frozen protocol, eligibility criteria, thresholds, source selection, measurement result, or stop decision.

The previously cited figure of 1,175,979 MG CAR records was the SICAR `number_matched_all_statuses` count, not the eligible denominator. Its status composition in the measurement baseline was:

- AT: 1,010,686
- PE: 155,732
- SU: 879
- CA: 8,682
- RE: 0
- OTHER_OR_EMPTY: 0

The frozen eligible denominator is therefore:

`1,010,686 + 155,732 + 879 = 1,167,297`

The exact difference is:

`1,175,979 - 1,167,297 = 8,682`

Those 8,682 records are exactly the `CA` (Cancelado) records. `CA` and `RE` were already excluded by CAR_NAME_COVERAGE_PROTOCOL_V1 before the statewide high-confidence measurement. There was no post-result refinement of eligibility, no new structural-data exclusion, no source change, and no denominator optimization. The apparent denominator change came only from comparing the SICAR all-status count with the protocol-defined eligible AT+PE+SU count.

## Post-measurement audit note — Curvelo acceptance is partial, not a full parity win

The original practical acceptance target for the reference CAR in Curvelo was to click the property and display the denomination `SÍTIO LAGOA BONITA`, matching the competitor's practical output. That target was not achieved.

The accepted Stage 2 result is narrower and deliberately truth-preserving: the system did not promote a denomination that the frozen public-source protocol could not resolve uniquely. The reference CAR remained unresolved/ambiguous and therefore displayed the generic rural-property fallback instead of inventing or force-matching `SÍTIO LAGOA BONITA`.

Accordingly, Stage 2 is considered successful for source truth, provenance, non-invention, and frozen-protocol compliance, but not a full product-parity victory on the Curvelo denomination example. This distinction must remain visible in future audits and product comparisons.
