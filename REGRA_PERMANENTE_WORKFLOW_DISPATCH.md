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

## Motivo

O GitHub só expõe de forma confiável o botão `Run workflow` quando a definição do workflow existe na branch padrão. Manter essa regra evita repetir o bootstrap operacional a cada novo workflow manual.

## Precedentes

- V48 ES Single Spot Pilot.
- V48 Canonical SICAR Snapshot Audit — PR #37, merge commit `67491d64910413e1a8b4883d852b5d22f93779da`.
