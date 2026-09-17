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
| `ci_roda_a_trava` | sempre | o fluxo não **executa** a trava num passo `run:`/`uses:`; o job que a executa tem `if:`, checkout sem `fetch-depth: 0`, ou nenhum checkout; o gatilho `push`/`pull_request` sumiu ou ganhou filtro (`branches`, `paths`) que não alcança a base |

### Três saídas por regra — `PASSA`, `FALHA` e `NÃO CONFERIDO`

Conserto de 16/09, revisão do PR #81. `PASSA` = conferiu e está certo. `FALHA` = conferiu e está errado.
**`NÃO CONFERIDO` = não tinha o que conferir nesta execução**, dito em voz alta em vez de virar um verde.

A trava nasceu de um `PASSA` que não tinha olhado nada (clone raso). Consertar só a porta por onde aquele
caso entrou deixaria a sala aberta: a **mesma** conclusão "nada a conferir" sai também de

- um merge por **squash ou rebase** — a ponta fica com um pai só, e o histórico não guarda rastro de que o
  verde do ramo foi medido em outra árvore;
- qualquer execução **fora de `push` e de `pull_request`** — no projeto, o `workflow_dispatch` que roda o CI
  de PR empilhado.

Nenhuma das duas tem evidência: viraram `NÃO CONFERIDO`. E quando nenhuma das duas regras de base
**concluiu** nada — nem passou, nem reprovou —, o relatório fecha com a linha **`SEM CONFERENCIA`** dizendo
isso. Continua verde, porque não há defeito a acusar; mas não finge que conferiu.

### Histórico raso é o caso silencioso desta trava

Num `git clone --depth 1` o commit da ponta vira **enxerto e reporta zero pais** para quem caminha no grafo
(`git rev-list --parents`, `git log`). Sem guarda, a leitura concluiria "não é merge, nada a conferir" e o
portão imprimiria **PASSA exatamente no caso que ele existe para pegar** — o pior jeito de falhar, porque tem
cara de sucesso. Três coisas fecham isso, não uma:

1. antes de qualquer conclusão, `merge_nao_stale` e `pr_base_atualizada` **recusam o clone raso**
   (`git rev-parse --is-shallow-repository`, com o arquivo `.git/shallow` como segunda leitura) e mandam
   pôr `fetch-depth: 0`;
2. os pais são lidos no **próprio objeto do commit** (`git cat-file commit`), que não caminha no grafo e por
   isso não aceita o enxerto como verdade: histórico cortado **sem** o marcador `shallow` reprova pela guarda
   do pai ausente, com a mensagem que diz o que fazer, em vez de virar erro cru;
3. a regra `ci_roda_a_trava` **exige `fetch-depth: 0` em todo passo de checkout** do job que executa a trava
   — ter a linha em outro passo não vale, porque quem traz o histórico é o checkout.

E o desvio "estou em PR" só vale se o `RX_PR_HEAD_SHA` for um commit **deste** clone: variável solta num
push não pode virar chave de desligar a regra do merge.

### `ci_roda_a_trava` lê configuração — e diz que é só isso

A regra confere o **passo que executa** (a chave `run:`/`uses:`, não qualquer linha do corpo do job), a
ausência de `if:` no job, e os filtros do gatilho. Quatro edições tiravam a trava do ar com a regra antiga
imprimindo PASSA: `if:` no job, `push: branches: [outro]`, `run:` virado comentário, e `fetch-depth: 0` num
segundo passo cobrindo um checkout raso. **As quatro reprovam agora**, cada uma pela sua mensagem.

Ainda assim, a regra afirma só o que um arquivo de configuração pode provar: que a configuração continua
plausível. Ela **não** prova que o job rodou — job que nunca dispara não deixa vermelho nenhum —, e o
relatório diz isso na própria saída.

### Controles positivos — cada um cobra a mensagem da sua guarda, e a cobertura é medida

Controles montados na hora. Duas regras de escrita, as duas nascidas de defeito real:

1. **todo controle chama a REGRA registrada** — a função que o `main()` executa —, nunca um ajudante abaixo
   dela. Um controle é uma afirmação sobre a função que ele **chama**, não sobre a que ele **nomeia**: até a
   revisão do #81, três controles se chamavam `merge_nao_stale <- …` e chamavam o ajudante `_merge_stale()`;
2. **nenhum controle aceita "levantou alguma coisa"** — cada um exige a marca da guarda que diz exercitar.

E a cobertura não se lê pela contagem de controles verdes. O próprio portão **enumera pelo AST do seu
arquivo** todo ponto que reprova (`fail`) ou que declara "não conferido" (`nao_conferido`), e exige que algum
controle verde tenha levantado **naquele ponto**. Guarda sem controle só fica de fora com o motivo escrito na
lista `ISENTAS`, impressa no relatório. Hoje: **17 guardas, 16 mortas por controle, 1 isenta declarada**.

Por que isso precisou existir: com 12 controles verdes, apagar o **único** `fail()` que justifica a trava
(`if atrasados:` → `if False:`, a reprovação do merge sobre base velha) mantinha os 12 verdes e o portão
imprimia PASS. N controles verdes convive com zero guardas cobertas.

Conferido por mutação, uma guarda por vez, sobre uma **cópia** do arquivo — nunca sobre a árvore de trabalho:
**18 das 19 mutações morrem** (a 19ª é a guarda isenta, declarada justamente por isso). As duas que mais
importam: apagar a reprovação do merge sobre base velha mata três controles, e **reescrever os controles para
chamar o ajudante em vez da regra — o defeito exato do #81 — deixa todos os controles verdes e mata a
COBERTURA**.

### A camada do veredito — o portão rodando a si mesmo, e cobrando o código de saída

Segunda revisão do #81. Medir cobertura pelos pontos que **levantam** mede o detector e declara o detector
inteiro; a camada que **traduz** o que foi levantado em veredito não levanta nada — ela soma e devolve um
número. Ficou inteira fora da população, e cinco edições de **uma linha** passavam com todos os controles
verdes. A pior: trocar `return 1` por `return 0` fazia o portão imprimir a **mesma** linha
`FALHA merge_nao_stale … merge sobre base desatualizada` e sair com código **0** — check verde no CI. É o
defeito do #81 (verde que não encosta no ponto que decide) repetido uma camada acima.

Controle em processo não alcança isso, porque **em processo se lê o valor de uma função e o CI lê outra
coisa**. Quem cobre é o bloco **ponta a ponta**: o portão executa **a si mesmo** em subprocesso
(`python scripts/base_atualizada_gate.py`) contra repositórios sintéticos e cobra o **código de saída** junto
com a linha correspondente:

| caso | o que é exigido | que mutação mata |
|---|---|---|
| repositório com merge atrasado | código **1** e a linha `FALHA merge_nao_stale … base desatualizada` | `return 1` → `return 0` |
| repositório em dia | código **0** e a linha `PASSA merge_nao_stale` | `return 0` → `return 1` |
| ponta com um pai só | código **0**, a linha `SEM CONFERENCIA`, as três regras executadas e `conferidas=['ci_roda_a_trava']` | contar `NÃO CONFERIDO` como conferida; pôr uma regra que sempre passa em `REGRAS_DE_BASE`; tirar o decorador que registra a regra |
| o portão inteiro, com a variável de suspensão | código 0 e a linha da COBERTURA na saída do filho | `if "--sem-controles" not in args:` → `if False:` |
| o portão inteiro, **sem** a variável | código 0 e as linhas `PONTA A PONTA` na saída do filho | a suspensão virar chave de desligar |

A população da cobertura passou a ter **duas partes**: os pontos que levantam (pelo AST dos `fail()`) e os
pontos em que o resultado **se inverte** (pelo AST dos `return` de `main()`), cada `return` exigindo ter sido
observado pelo código de saída de um controle verde.

Três coisas fecham a volta em torno do próprio bloco, e nenhuma sozinha:

1. ele roda **fora** do `--sem-controles` — senão a mesma linha que desliga os controles em processo
   desligaria também o único observador que lê o portão de fora;
2. a chamada dele fica **antes** de `controles()`, para que a cobertura do veredito — conferida lá dentro —
   fique vermelha se essa chamada sumir;
3. a recursão é barrada por **duas** travas com motivo no ambiente (a variável que o pai põe em todo filho e
   o contador de profundidade que todo filho propaga), e há controle provando que **sem** as variáveis a
   suspensão não existe: suspensão sem motivo seria chave de desligar.

E o vermelho chega ao CI por **dois caminhos independentes** (o `return` de `main()` e um segundo canal lido
na saída do programa), porque a linha que traduz falha em código de saída é ela mesma uma linha editável.

**O que isto ainda não cobre, dito em voz alta:** tirar a redundância do segundo canal não mata controle
nenhum enquanto o primeiro funciona — redundância não se prova sozinha, ela aparece quando a outra linha é
mutada. E o custo subiu: o portão passou de ~9 s para ~48 s no Windows, porque ele agora roda a si mesmo
cinco vezes (o job tem `timeout-minutes: 5`, e o teto de cada filho é 180 s, abaixo dele).

## O que o gate NÃO promete

Ele não **impede** o merge. O check do PR é uma fotografia do instante em que rodou, e ninguém re-roda o CI
de um PR parado quando outro PR entra. Quem impede é a proteção de branch do GitHub com
**"require branches to be up to date before merging"**, que muda quem consegue mesclar e por isso é decisão
do dono. Enquanto ela não estiver ligada, o `merge_nao_stale` garante pelo menos que a violação aparece
vermelha **no ato**, com os dois commits nomeados — inclusive quando todos os outros portões ficam verdes
por sorte, que é o caso silencioso e o pior deles.
