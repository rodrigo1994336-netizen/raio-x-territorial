# CAR Name Coverage Protocol V1 — frozen before measurement

Date frozen: 2026-09-06
Scope of first measurement: Minas Gerais (MG)
Purpose: measure the percentage of current CAR records whose rural-property denomination can be resolved at zero marginal cost per consultation.

## 1. Denominator — eligible CAR

A CAR record is ELIGIBLE when all conditions below are true:

1. `cod_imovel` is present and syntactically valid for the state layer being measured.
2. The CAR is a current/non-terminal record. Included statuses: `AT` (Ativo), `PE` (Pendente), and `SU` (Suspenso), when that status exists in the source. Excluded terminal/superseded statuses: `CA` (Cancelado) and `RE` (Retificado).
3. Municipality is present and can be normalized; UF is known from the state layer/code.
4. Declared area is numeric, finite, and strictly greater than zero.

`tipo_imovel` does NOT restrict eligibility. IRU, PCT and AST remain in the denominator if they satisfy the conditions above. Results may additionally be stratified by `tipo_imovel`, but the headline denominator is not changed after measurement.

Spatial overlap, pending analysis, suspension, environmental overlap, or lack of SIGEF certification do NOT exclude a CAR.

Missing geometry does NOT exclude a CAR because the municipality+area fallback can still be evaluated. A geometry-dependent high-confidence route is simply marked unavailable for that record.

If duplicate rows with the same `cod_imovel` are returned, the denominator counts the CAR code once. Identical duplicates are collapsed. Conflicting duplicates are reported separately as `excluded_data_conflict` and are not silently assigned to any outcome.

Records excluded for invalid/missing code, municipality, or non-positive/non-numeric area are counted and published alongside the denominator as exclusions. They are not hidden.

## 2. CAFIR candidate universe

Only CAFIR records in active situation `02` are used for automatic naming. The CAFIR snapshot/version and file hashes must be stored with the result.

No CPF/CNPJ, owner, holder, registrant, or other personal-data field may be used in matching.

## 3. Resolution cascade and mutually exclusive outcomes

Each eligible CAR is assigned to exactly one headline outcome, in this order:

1. `NOME_GRATUITO_ALTA`: denomination resolved by a legitimate zero-cost public source using a deterministic identifier/link (e.g. public SICAR `nomeImovel` route, if a legitimate free route is proven; or exact INCRA-code bridge). Confidence: ALTA or MUITO ALTA. Provenance is mandatory.
2. `NOME_MUNICIPIO_AREA`: no higher-confidence free result; exactly one active CAFIR denomination candidate is found by normalized municipality + area window. Confidence: MEDIA. Provenance is mandatory.
3. `SEM_NOME_AMBIGUIDADE`: no higher-confidence free result; two or more CAFIR candidates fall inside the allowed municipality+area window. No automatic tie-break by owner/titular is allowed.
4. `SEM_NOME_AUSENCIA_CAFIR`: no higher-confidence free result and no usable CAFIR denomination candidate exists inside the allowed window. A CAFIR record with blank/unusable denomination is treated as absence of usable denomination and logged with subreason `blank_name`, not as a successful name.

The four headline counts must sum exactly to the eligible denominator.

## 4. Municipality + area fallback tolerance

Frozen formula before measurement:

`tolerance_ha = max(car_area_ha * 0.005, 0.01)`

That is 0.5% of declared CAR area or 0.01 ha, whichever is larger.

This tolerance must not be changed after viewing coverage results without creating a new protocol/version and rerunning the full benchmark.

## 5. Provenance contract

Every successful name stores at least:
- displayed denomination;
- resolution method;
- source;
- confidence level;
- source snapshot/date;
- evidence used for the match;
- candidate count for the method;
- effective tolerance when municipality+area was used.

Fallback display when no denomination is resolved:

`Imóvel rural — <município>/<UF>`

Never display `NOME NÃO CONFIRMADO` and never leave the title blank.

## 6. Publication requirements

The benchmark result must publish together:
- total rows read;
- status counts before filtering;
- eligible denominator;
- all exclusion counts/reasons;
- the four headline counts and percentages;
- stratification by CAR status and `tipo_imovel` where available;
- CAFIR snapshot/hash;
- exact tolerance formula;
- timestamp;
- warnings/limitations.

Unavailable source must never be converted into `zero occurrences`, `Livre`, or absence. It is a source-state condition, not a factual negative result.
