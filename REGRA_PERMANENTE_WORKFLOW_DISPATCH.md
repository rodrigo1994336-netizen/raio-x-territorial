# REGRA PERMANENTE — WORKFLOWS MANUAIS

Aplicável ao projeto Raio-X Territorial.

## Regra

Todo workflow que precise de disparo manual pelo GitHub Actions deve nascer com o arquivo YAML correspondente presente na branch padrão `main`, mesmo quando o código executado permanecer em uma branch de trabalho.

## Restrições

- O workflow manual na `main` deve usar `workflow_dispatch`.
- Não adicionar `push`, `pull_request` ou `schedule` ao workflow manual salvo decisão expressa e documentada.
- Se o workflow puder criar compute pago, `workflow_dispatch` é obrigatório e exclusivo.
- O código de trabalho não deve ser levado à `main` apenas para disponibilizar o botão `Run workflow`.
- Para executar código de uma branch de trabalho, selecionar explicitamente essa branch no disparo manual; `actions/checkout@v4` sem `ref:` fixo deve usar o ref selecionado no workflow_dispatch.
- O PR que leva apenas o workflow manual à `main` deve ser mínimo e conter somente o YAML necessário.
- Antes do merge desse PR, conferir o diff e provar que nenhum outro arquivo entrou.

## Regra permanente — gatilhos V48 nesta branch

Na branch `ops/v48-budget-gate-probe-20260908`, nenhum workflow cujo arquivo comece por `v48-` pode usar gatilho `push`.

Motivo: vermelho automático recorrente cria ruído operacional e treina a equipe a ignorar falhas. Uma falha real precisa continuar visualmente rara e acionável.

O gate V48 deve varrer `.github/workflows/v48-*.yml` e falhar se encontrar `push:`. Mudança futura dessa regra exige decisão expressa e documentada.

## Regra permanente — scripts chamados por workflow

- Script chamado por workflow não pode depender do diretório corrente nem do diretório de invocação para resolver módulos do repositório.
- Se importar módulo localizado fora da própria pasta, deve resolver explicitamente a raiz a partir de `Path(__file__).resolve()` e inseri-la no caminho de importação, ou o módulo deve morar junto do script.
- Scripts com potencial de consulta/compute pago devem oferecer um modo de `--import-check` que termine antes de autenticação, criação de cliente de nuvem ou consulta.
- O gate estático deve executar esse `--import-check` usando o mesmo formato de chamada do workflow (`python scripts/<script>.py --import-check`). Erro de import deve deixar o gate vermelho antes da etapa paga.
- Sempre que um novo script pago for acrescentado a workflow manual, ele deve ser acrescentado ao gate de import real na mesma alteração.

## Regra permanente — gates afirmam invariantes, nunca estágio

Gate deve provar propriedades que precisam permanecer verdadeiras durante toda a vida daquele contrato, e não uma etapa temporária do processo.

Exemplos de invariantes corretas:
- manifesto pinado é content-addressed;
- fingerprint declarado, nome do arquivo e conteúdo recomputado coincidem;
- mapa e análise usam o mesmo snapshot canônico da UF;
- workflow V48 desta branch não possui gatilho `push`.

Exemplo proibido de teste de estágio:
- exigir `PINNED_MANIFEST_PATH == ""` apenas porque o manifesto ainda não havia sido promovido naquele momento.

Quando uma etapa muda legitimamente o estado do projeto, o gate não deve virar falso vermelho. A regra é: **gate afirma invariante, nunca estágio**.

## Orçamento V48

A existência e configuração do orçamento são fato administrativo registrado em `docs/v48_budget_administrative_record.md`, verificado manualmente pelo responsável no Console em 08/09/2026.

A service account não recebe permissões adicionais de Budget API apenas para tornar um gate verde. O Budget Gate deve afirmar somente invariantes que a credencial atual consegue verificar.

## Motivo

O GitHub só expõe de forma confiável o botão `Run workflow` quando a definição do workflow existe na branch padrão. Manter essa regra evita repetir o bootstrap operacional a cada novo workflow manual.

A regra de import real evita que `py_compile`/AST passe enquanto a execução real falha por resolução de módulos, como ocorreu na execução #4 do V48 Canonical SICAR Snapshot Audit.

A regra de invariantes evita falso vermelho após uma transição legítima de estado, como ocorreu quando o Snapshot Contract Gate ainda exigia manifesto vazio depois da promoção do manifesto canônico.

## Precedentes

- V48 ES Single Spot Pilot.
- V48 Canonical SICAR Snapshot Audit — PR #37, merge commit `67491d64910413e1a8b4883d852b5d22f93779da`.
- V48 Canonical CAR Counts — execução #4: manifesto PASS, falha de import antes do dry-run; originou a regra permanente de independência do diretório de invocação.
- V48 Snapshot Contract Static Gate #16 — o gate estático passou, mas uma asserção de estágio obsoleta gerou falso vermelho; originou a regra `gate afirma invariante, nunca estágio`.
