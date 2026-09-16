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
| `ci_roda_a_trava` | sempre | o fluxo deixou de chamar a trava, o job que a chama perdeu o `fetch-depth: 0`, ou o fluxo deixou de disparar em `push`/`pull_request` |

### Histórico raso é o caso silencioso desta trava

Num `git clone --depth 1` o commit da ponta vira **enxerto e reporta zero pais**. Sem guarda, a leitura dos
pais concluiria "não é merge, nada a conferir" e o portão imprimiria **PASSA exatamente no caso que ele
existe para pegar** — o pior jeito de falhar, porque tem cara de sucesso. Por isso (conserto de 16/09):

1. antes de qualquer conclusão, `merge_nao_stale` e `pr_base_atualizada` **recusam o clone raso**
   (`git rev-parse --is-shallow-repository`, com o arquivo `.git/shallow` como segunda leitura) e mandam
   pôr `fetch-depth: 0`;
2. a regra `ci_roda_a_trava` **exige a linha `fetch-depth: 0` no job que chama a trava** — guarda que
   depende de configuração externa some numa edição e deixa o portão verde para sempre;
3. o desvio "estou em PR" só vale se o `RX_PR_HEAD_SHA` for um commit **deste** clone: variável solta num
   push não pode virar chave de desligar a regra do merge.

### Controles positivos — cada um cobra a mensagem da sua guarda

12 controles montados na hora, e **nenhum deles aceita "levantou alguma coisa"**: cada um exige a marca da
guarda que diz exercitar (controle que aceita qualquer erro fica verde com o erro que prova o contrário).
Entre eles: repositórios sintéticos com o merge atrasado (reprova) e com o ramo em dia (passa); um
`git clone --depth 1` **de verdade** do repositório atrasado (reprova pelo histórico raso), precedido da
prova de que o clone raso realmente apaga os pais; o **negativo** do mesmo repositório clonado inteiro (não
cai na guarda de raso e ainda acusa o atraso); a regra do PR exercitada pela própria regra, com head
atrasado e head em dia; o `RX_PR_HEAD_SHA` inexistente; e três mutações do YAML — chamada removida,
`fetch-depth: 0` removido, gatilho `pull_request` removido — cada uma com a sua marca.

Conferido por mutação da própria trava: desligar qualquer uma das três guardas novas deixa **vermelho o
controle correspondente** (o do clone raso volta a imprimir `não é merge`, que era o defeito).

## O que o gate NÃO promete

Ele não **impede** o merge. O check do PR é uma fotografia do instante em que rodou, e ninguém re-roda o CI
de um PR parado quando outro PR entra. Quem impede é a proteção de branch do GitHub com
**"require branches to be up to date before merging"**, que muda quem consegue mesclar e por isso é decisão
do dono. Enquanto ela não estiver ligada, o `merge_nao_stale` garante pelo menos que a violação aparece
vermelha **no ato**, com os dois commits nomeados — inclusive quando todos os outros portões ficam verdes
por sorte, que é o caso silencioso e o pior deles.
