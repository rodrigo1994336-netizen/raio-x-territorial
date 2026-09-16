# REGRA PERMANENTE — BASE ATUALIZADA ANTES DO MERGE

Aplicável ao projeto Raio-X Territorial. Escrita em 16/09/2026, depois de a `main` ficar vermelha duas
vezes pelo mesmo motivo.

## O que aconteceu

Os PRs #78 e #79 saíram da **mesma** base (`a8a296c`). Cada um passou verde no seu ramo. Foram mesclados um
atrás do outro e, juntos, deixaram a `main` vermelha (`8db056c`):

- o #78 criou uma regra de portão que enumera os módulos de remendo pela propriedade
  "levanta quando a âncora não casa";
- o #79, no mesmo dia, trocou o canal de sinal de um desses módulos — ele passou a **imprimir**
  `RX_..._ANCHOR_MISSING=` em vez de derrubar o arranque.

Nenhum dos dois CIs podia ver o defeito: ele só existe na combinação, e **nenhum dos dois rodou sobre a
combinação**.

## A regra

**CI verde é evidência sobre a árvore em que ele rodou, nunca sobre a árvore que vai existir depois do
merge.** Assim que a base anda, o verde do ramo deixa de falar do que vai ser mesclado.

Antes de mesclar qualquer PR:

1. conferir se a `main` andou desde o último verde daquele ramo;
2. se andou, atualizar o ramo (`git merge origin/main` ou rebase) e **esperar o CI novo**;
3. só então mesclar.

Sinal de alerta específico, que exige a revalidação mesmo quando o diff parece não se tocar: **um PR mexe em
portão/verificação e o outro mexe no código que aquele portão mede.**

## Gate automático — `scripts/base_atualizada_gate.py`

Roda no job `base-atualizada` do `Raio-X Quality Gate`, no PR e no push da `main`.

| regra | quando | o que reprova |
|---|---|---|
| `pr_base_atualizada` | no PR | o head do PR não contém a ponta atual da `main` |
| `merge_nao_stale` | no push da `main` | o merge que acabou de entrar juntou um ramo que **não continha** a ponta anterior da `main` |
| `ci_roda_a_trava` | sempre | o fluxo de trabalho deixou de chamar a trava, ou deixou de disparar em `push`/`pull_request` |

Cada regra tem controle positivo por construção: repositórios git sintéticos montados na hora (um com o
merge atrasado, que tem de reprovar; um com o ramo em dia, que tem de passar), o caso do clone raso (que não
pode passar calado por falta de histórico) e a mutação do YAML que tira a chamada do CI.

O passo exige `actions/checkout@v4` com `fetch-depth: 0`. Sem o histórico, o portão **reprova** — nunca
passa dizendo "está em dia" sem ter olhado.

## O que o gate NÃO promete

Ele não **impede** o merge. O check do PR é uma fotografia do instante em que rodou, e ninguém re-roda o CI
de um PR parado quando outro PR entra. Quem impede é a proteção de branch do GitHub com
**"require branches to be up to date before merging"**, que muda quem consegue mesclar e por isso é decisão
do dono. Enquanto ela não estiver ligada, o `merge_nao_stale` garante pelo menos que a violação aparece
vermelha **no ato**, com os dois commits nomeados — inclusive quando todos os outros portões ficam verdes
por sorte, que é o caso silencioso e o pior deles.
