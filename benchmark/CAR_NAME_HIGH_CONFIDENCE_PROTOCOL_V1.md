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
