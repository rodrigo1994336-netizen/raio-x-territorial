# Ponte no Brasil — como ligar (fechada e com teto de gasto)

**Para que serve.** O site do INCRA (acervo fundiário: SIGEF e SNCI) não responde ao nosso servidor, que fica
nos Estados Unidos (Render). Daqui do Brasil ele responde. A "ponte" é um programa pequeno no Google Cloud,
em São Paulo, que faz essa consulta por nós. Ela só busca em dois endereços oficiais:
`acervofundiario.incra.gov.br` e `geoserver.car.gov.br` (SICAR).

**Como o Raio-X usa.** INCRA vai sempre pela ponte. SICAR tenta direto, como hoje; só quando o SICAR não
responde ao Render a mesma consulta vai pela ponte, dentro do mesmo tempo de espera. Sem as variáveis no
Render, o sistema funciona exatamente como hoje.

**O que esta versão protege (15/09/2026).**

1. **A ponte fica fechada pelo próprio Google.** Só entra quem tem a chave do Raio-X. Quem descobrir o
   endereço recebe "proibido" do Google, antes de chegar à ponte, e o Google não cobra esses pedidos.
2. **Teto de gasto de R$ 20 por mês.** Se o gasto da ponte passar disso, o Google pausa a ponte sozinho.
   O site continua no ar; o INCRA volta a "consulta pendente" até você liberar. Nada é apagado.

| o que acontece | versão anterior (aberta) | esta versão |
|---|---|---|
| uso normal | R$ 0 a R$ 1 por mês | igual |
| alguém descobre só o endereço e inunda de pedidos | sem teto: perto de US$ 90 por mês de máquina, mais US$ 0,40 por milhão de pedidos | **US$ 0** (o Google recusa e não cobra) |
| alguém rouba a chave (teria de invadir o Render ou a sua conta Google) | — | pausa em **R$ 20**; pode passar um pouco pelo atraso do Google (veja "Quanto custa") |

**Já ligou a versão anterior?** Faça de novo os Passos 3, 4 e 5. Entre o Passo 4 e o fim do Passo 5 o INCRA
fica em "consulta pendente" (a ponte já fechou e o Render ainda não tem a chave). Isso é esperado.

**Antes de começar.** O código desta versão precisa estar na `main` do GitHub (o PR mesclado, com a sua
autorização). O bloco do Passo 4 baixa o código de lá e para com `PAROU` se ainda não estiver.

Tempo total: uns 25 minutos. Você vai precisar de um cartão para ativar o faturamento do Google (é exigência
do Google para usar o Cloud Run, mesmo na faixa grátis). **Eu não mexo em cartão nem em senha: essa parte é
sua.**

---

## Passo 1 — Criar (ou reaproveitar) o projeto Raio-X no Google Cloud

**Se você já criou o projeto `raio-x-ponte` pela versão anterior, use ele** (o nome não importa) e pule para o
Passo 2 só para conferir o faturamento.

1. Abra <https://console.cloud.google.com/projectcreate> e entre com a conta **rodrigo1994336@gmail.com**.
   Se o Google pedir para aceitar os termos, a decisão é sua.
2. Em **Project name** (nome do projeto), escreva: `Raio-X`. Este mesmo projeto vai servir depois para a
   cópia mensal do CAR (Base dos Dados/BigQuery); não precisa criar outro.
3. Em **Parent resource** / **Location**, deixe como está (conta pessoal não tem organização).
4. Clique em **Create**.
5. No topo da página, confira se o projeto selecionado é o `Raio-X` (o seletor de projetos fica no alto, à
   esquerda). Se não for, clique nele e escolha `Raio-X`.

## Passo 2 — Ativar o faturamento

1. Abra <https://console.cloud.google.com/billing>.
2. Se você ainda não tem uma conta de faturamento, o Google pede para criar uma: siga as telas dele e
   informe o cartão.
3. Na aba **My projects** (Meus projetos), ache o projeto, abra o menu **Actions** (três pontinhos), escolha
   **Change billing** e selecione a sua conta de faturamento. Clique em **Set account**.

## Passo 3 — Teto de gasto de R$ 20 (pausa a ponte sozinho)

Os nomes abaixo são os da documentação do Google para o "orçamento com teto" (spend cap). É um recurso novo
do Google, ainda em pré-lançamento ("Preview"); a tela pode aparecer em português.

1. Abra <https://console.cloud.google.com/billing/budgets> (página **Budgets & alerts**) e clique em
   **Create budget** (ou **Create new budget**).
2. Escolha **Spend cap enforcement** (e não *Alerts only*). Em **Name**, escreva `teto ponte R$ 20`. Clique
   em **Next**.
3. Em **Project**, escolha o projeto Raio-X. Em **Service**, escolha **Cloud Run**. Clique em **Next**.
4. Em **Target amount**, escreva `20` (a moeda é a da sua conta: reais). Clique em **Next**.
5. Clique em **Finish**. O Google já manda e-mail em 50%, 80% e 100% do valor.

**Se a opção Spend cap enforcement não aparecer:** crie um aviso comum (*Alerts only*), mesmo projeto, valor
`10`, e me avise. A ponte continua fechada (quem tem só o endereço não gera gasto); só o caso "chave roubada"
fica sem pausa automática.

**Se já existe o aviso `ponte R$ 10` da versão anterior:** pode deixar. Se o Google recusar criar o teto por
já existir um orçamento igual, apague o aviso antigo e crie o teto.

## Passo 4 — Abrir o Cloud Shell e colar UM bloco

1. Com o projeto Raio-X selecionado no topo, clique em **Activate Cloud Shell** (Ativar o Cloud Shell), no
   alto da página. Um terminal abre na parte de baixo. Se o Google pedir autorização para o Cloud Shell usar
   suas credenciais, autorize.
2. Copie o bloco inteiro abaixo, cole no terminal e aperte **Enter**. Ele leva de 8 a 15 minutos (a maior
   parte é esperar o Google aplicar as permissões).

```bash
bash <<'PONTE'
set -Eeuo pipefail
trap 'echo; echo "PAROU: o passo acima falhou (linha $LINENO). Me mande so a mensagem de erro acima (ela nao mostra segredo)."; exit 1' ERR
parou() { echo; echo "PAROU: $*"; exit 1; }
REGIAO=southamerica-east1
SERVICO=ponte-brasil
CONTA=ponte-render
PASTA="$HOME/.raio-x-ponte"
PROJETO="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
[ -n "$PROJETO" ] || parou "nenhum projeto selecionado. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo."
echo "Projeto: $PROJETO"
gcloud config set project "$PROJETO" --quiet >/dev/null 2>&1 || parou "o Google nao aceitou o projeto $PROJETO."

echo "1/9 Ligando Cloud Run, Cloud Build, Artifact Registry e IAM (1 a 2 minutos)..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com iam.googleapis.com --quiet \
  || parou "o Google nao ligou os servicos. Confira o faturamento (Passo 2) e me mande so a mensagem de erro acima."
NUMERO="$(gcloud projects describe "$PROJETO" --format='value(projectNumber)')"
BUILD="${NUMERO}-compute@developer.gserviceaccount.com"

echo "2/9 Dando ao build a permissao de publicar (ate 3 minutos)..."
OK=0
for TENTATIVA in $(seq 1 12); do
  # a conta do build aparece alguns segundos depois de ligar o Cloud Run: tenta de novo em vez de dar erro
  if gcloud projects add-iam-policy-binding "$PROJETO" --member="serviceAccount:$BUILD" \
       --role=roles/run.builder --condition=None --quiet >/dev/null 2>&1; then OK=1; break; fi
  sleep 10
done
[ "$OK" = 1 ] || parou "o Google nao aceitou a permissao do build. Cole o bloco de novo daqui a 5 minutos."
sleep 60

echo "3/9 Baixando o codigo publico do Raio-X..."
rm -rf "$HOME/raio-x-ponte"
git clone --quiet --depth 1 https://github.com/rodrigo1994336-netizen/raio-x-territorial.git "$HOME/raio-x-ponte" \
  || parou "nao consegui baixar o codigo do GitHub."
test -f "$HOME/raio-x-ponte/scripts/ponte_brasil_conferir.py" \
  || parou "a versao da ponte fechada ainda nao esta na main do GitHub. Nao mexa no Render."

echo "4/9 Token da ponte: reaproveita o que ja existe; senao gera um novo..."
TOKEN="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --format=json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((e.get("value","") for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env",[]) if e.get("name")=="PONTE_TOKEN"),""))' 2>/dev/null || true)"
[ -n "$TOKEN" ] || TOKEN="$(openssl rand -hex 32)"

echo "5/9 Publicando a ponte FECHADA em Sao Paulo (3 a 6 minutos)..."
gcloud run deploy "$SERVICO" --source "$HOME/raio-x-ponte/ponte_brasil" --region "$REGIAO" \
  --max-instances 1 --min-instances 0 --memory 256Mi --no-allow-unauthenticated \
  --set-env-vars "PONTE_TOKEN=$TOKEN" --quiet \
  || parou "a publicacao da ponte falhou. Me mande so a mensagem de erro acima."
for QUEM in allUsers allAuthenticatedUsers; do
  # a versao anterior deste guia abria a ponte ao publico: tira, se ainda estiver la
  gcloud run services remove-iam-policy-binding "$SERVICO" --region "$REGIAO" --member="$QUEM" \
    --role=roles/run.invoker --quiet >/dev/null 2>&1 || true
done
POLITICA="$(gcloud run services get-iam-policy "$SERVICO" --region "$REGIAO" --format=json)"
case "$POLITICA" in
  *'"allUsers"'*|*'"allAuthenticatedUsers"'*) parou "a ponte continua aberta ao publico. NAO coloque nada no Render." ;;
esac
URL="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --format='value(status.url)')"
[ -n "$URL" ] || parou "a ponte nao informou o endereco."

echo "6/9 Conta de servico do Render, com permissao so de chamar a ponte..."
EMAIL="$CONTA@$PROJETO.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$CONTA" --display-name="Raio-X no Render chama a ponte" --quiet >/dev/null \
    || parou "o Google nao criou a conta de servico. Me mande so a mensagem de erro acima."
fi
OK=0
for TENTATIVA in $(seq 1 12); do
  if gcloud run services add-iam-policy-binding "$SERVICO" --region "$REGIAO" --member="serviceAccount:$EMAIL" \
       --role=roles/run.invoker --quiet >/dev/null 2>&1; then OK=1; break; fi
  sleep 10
done
[ "$OK" = 1 ] || parou "o Google nao deu a permissao de chamar a ponte. Cole o bloco de novo daqui a 5 minutos."

echo "7/9 Chave da conta de servico (fica so no seu Cloud Shell e no Render)..."
umask 077
mkdir -p "$PASTA"
CHAVE_JSON="$PASTA/chave.json"
ATUAL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("private_key_id",""))' "$CHAVE_JSON" 2>/dev/null || true)"
CHAVES="$(gcloud iam service-accounts keys list --iam-account "$EMAIL" --managed-by=user --format='value(name.basename())')"
NOVA=0
if [ -n "$ATUAL" ] && grep -qx -- "$ATUAL" <<<"$CHAVES"; then
  echo "  chave: a mesma de antes (no Render ela nao muda)"
else
  rm -f "$CHAVE_JSON"
  gcloud iam service-accounts keys create "$CHAVE_JSON" --iam-account "$EMAIL" --quiet >/dev/null \
    || parou "o Google nao criou a chave. Me mande so a mensagem de erro acima."
  NOVA=1
  echo "  chave: nova"
fi

echo "8/9 Conferindo: ponte fechada, chave aceita, INCRA e SICAR pela ponte (ate 8 minutos)..."
export RX_PONTE_BRASIL_URL="$URL" RX_PONTE_BRASIL_TOKEN="$TOKEN"
RX_PONTE_BRASIL_CHAVE="$(base64 -w0 "$CHAVE_JSON")"
export RX_PONTE_BRASIL_CHAVE
python3 "$HOME/raio-x-ponte/scripts/ponte_brasil_conferir.py" --esperar 480 || exit 1
if [ "$NOVA" = 1 ]; then
  NOVO_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["private_key_id"])' "$CHAVE_JSON")"
  for K in $CHAVES; do
    # chaves antigas desta conta (de uma tentativa anterior) deixam de valer
    [ "$K" = "$NOVO_ID" ] || gcloud iam service-accounts keys delete "$K" --iam-account "$EMAIL" --quiet >/dev/null 2>&1 || true
  done
fi

echo "9/9 Preparando a chave para o Render..."
base64 -w0 "$CHAVE_JSON" > "$HOME/raio-x-chave-render.txt"
echo >> "$HOME/raio-x-chave-render.txt"
echo
echo "================ COPIE PARA O RENDER (NAO MANDE NO CHAT) ================"
echo "RX_PONTE_BRASIL_URL   = $URL"
echo "RX_PONTE_BRASIL_TOKEN = $TOKEN"
echo "RX_PONTE_BRASIL_CHAVE = no arquivo raio-x-chave-render.txt (o navegador vai baixar agora)"
echo "=========================================================================="
if [ "$NOVA" = 1 ]; then echo "A CHAVE E NOVA: coloque (ou troque) RX_PONTE_BRASIL_CHAVE nos DOIS servicos do Render."; fi
cloudshell download "$HOME/raio-x-chave-render.txt" 2>/dev/null \
  || echo "Se o download nao comecar: no Cloud Shell, clique nos tres pontinhos (More) > Download e escreva raio-x-chave-render.txt"
PONTE
```

3. No fim aparece a caixa **COPIE PARA O RENDER** com a URL e o token, e o navegador baixa o arquivo
   `raio-x-chave-render.txt` (a chave). Deixe o Cloud Shell aberto para copiar no próximo passo.

> **URL, token e chave são segredos.** Cole os três direto no Render. Não mande no chat, no WhatsApp nem por
> e-mail. Se o bloco for colado de novo, ele reaproveita o mesmo token e a mesma chave (não precisa mexer no
> Render outra vez), a não ser que apareça `A CHAVE E NOVA`.

**Se aparecer `PAROU: o INCRA nao respondeu pela ponte`**: pare aqui. Não vá para o Passo 5. A ponte subiu
fechada, mas o INCRA pode estar barrando os endereços do Google; ligar a ponte assim não ajudaria e esconderia
o motivo. Me mande só as linhas que começam com dois espaços (`fechada`, `credencial`, `saude`, `INCRA`,
`SICAR`): elas não mostram segredo.

**Se aparecer `PAROU: o Google nao criou a chave`** com a palavra *constraint* ou *policy* no erro: a conta
tem uma regra que proíbe chave de conta de serviço. Me mande só a mensagem; não tente contornar.

Se aparecer outro `PAROU:` ou uma mensagem de erro em vermelho, copie **só a mensagem de erro** e me mande.

## Passo 5 — Colocar as três variáveis no Render (nos DOIS serviços)

O INCRA e o SICAR são consultados pelo site **e** pelo gerador de relatório. Os dois precisam das variáveis:

- `raio-x-territorial-app` (o site)
- `raio-x-territorial-report` (o relatório em PDF)

Antes: abra o arquivo baixado `raio-x-chave-render.txt` (pasta **Downloads**) com dois cliques; ele abre no
Bloco de Notas. É uma linha só, bem comprida.

Para cada um dos dois serviços:

1. Abra <https://dashboard.render.com> e clique no serviço.
2. No menu da esquerda, clique em **Environment**.
3. Se `RX_PONTE_BRASIL_URL` e `RX_PONTE_BRASIL_TOKEN` já existem (versão anterior) e o token da caixa é o
   mesmo, deixe os dois como estão e vá para o item 6.
4. Clique em **+ Add Environment Variable**. Em **Key**, escreva `RX_PONTE_BRASIL_URL`; em **Value**, cole a
   URL que o Cloud Shell mostrou (começa com `https://`).
5. Clique de novo em **+ Add Environment Variable**. **Key**: `RX_PONTE_BRASIL_TOKEN`; **Value**: cole o token.
6. Clique de novo em **+ Add Environment Variable**. **Key**: `RX_PONTE_BRASIL_CHAVE`; **Value**: no Bloco de
   Notas, clique dentro do texto, aperte **Ctrl+A** e depois **Ctrl+C**; volte ao Render, clique em **Value**
   e aperte **Ctrl+V**. (Se a variável já existia, troque o valor dela pelo novo.)
7. Clique para salvar e escolha **Save, rebuild, and deploy** (publica a versão mais nova da `main` já com as
   variáveis). Se escolher **Save only**, as variáveis só valem no próximo deploy.

Depois dos dois serviços: apague o arquivo `raio-x-chave-render.txt` da pasta Downloads. A chave continua
guardada no seu Cloud Shell (pasta pessoal, protegida pela sua conta Google) e no Render.

## Passo 6 — Conferir

1. No Render, em cada serviço, abra **Logs** e procure por `RX_PONTE_BRASIL`. Tem que aparecer
   `RX_PONTE_BRASIL=on host=... incra=ponte sicar=direto_primeiro acesso=google assinatura=...`.
   - Sem `acesso=google` no fim: falta a chave nesse serviço (item 6 do Passo 5).
   - `RX_PONTE_BRASIL=off motivo=chave_invalida`: a chave foi colada pela metade. Repita o item 6 (Ctrl+A no
     Bloco de Notas pega a linha inteira).
   - `RX_PONTE_BRASIL=off motivo=configuracao_invalida`: a URL não começa com `https://` ou o token foi
     colado pela metade. Corrija no **Environment**.
   - `RX_PONTE_BRASIL=erro_credencial motivo=http_400`: o Google recusou a chave (foi trocada ou apagada).
     Cole o bloco de conferência abaixo; se ele parar na credencial, use "Se o token ou a chave vazar".
   - `RX_PONTE_BRASIL=erro_ponte http=403 origem=ponte_ou_fonte` ou `http=401`: pode ser a ponte recusando
     (chave ou token diferentes dos da ponte) **ou** a fonte oficial recusando. O Render não tem como saber
     qual dos dois. **Antes de trocar qualquer coisa**, cole o bloco de conferência abaixo:
     - se o INCRA aparecer `respondeu pela ponte`, a ponte e a fonte estão bem: o que está no Render difere do
       que está na ponte. Cole o bloco do Passo 4 de novo (ele mostra os mesmos valores) e confira o Render;
     - se aparecer `nao respondeu (codigo 401)` ou `(codigo 403)` com `fechada` e `saude` ok, quem recusou foi
       a fonte oficial: não mexa em nada, me mande as linhas.
2. No site, abra o imóvel de prova (Curvelo `MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F`). A referência
   INCRA (SIGEF/SNCI) deve aparecer sem "consulta pendente". Se a fonte oficial estiver fora do ar, continua
   "consulta pendente" — isso é correto, não é defeito da ponte.
3. A primeira leitura depois de a ponte ficar parada pode sair como "consulta pendente": a máquina demora
   alguns segundos para ligar. Abra de novo o mesmo imóvel; a segunda já deve responder. Depois de ligar, eu
   meço esse tempo antes de mexer em qualquer prazo.

Bloco de conferência (não mostra segredo):

```bash
bash <<'CONFERE'
set -Eeuo pipefail
trap 'echo; echo "PAROU: o passo acima falhou (linha $LINENO). Me mande so a mensagem de erro acima."; exit 1' ERR
parou() { echo; echo "PAROU: $*"; exit 1; }
REGIAO=southamerica-east1
SERVICO=ponte-brasil
CHAVE_JSON="$HOME/.raio-x-ponte/chave.json"
URL="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --format='value(status.url)' 2>/dev/null || true)"
[ -n "$URL" ] || parou "a ponte nao existe neste projeto."
TOKEN="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --format=json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((e.get("value","") for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env",[]) if e.get("name")=="PONTE_TOKEN"),""))' 2>/dev/null || true)"
[ -n "$TOKEN" ] || parou "a ponte esta sem token. Cole o bloco do Passo 4."
[ -s "$CHAVE_JSON" ] || parou "a chave nao esta neste Cloud Shell. Use o bloco de 'Se o token ou a chave vazar' (ele cria outra)."
rm -rf "$HOME/raio-x-ponte"
git clone --quiet --depth 1 https://github.com/rodrigo1994336-netizen/raio-x-territorial.git "$HOME/raio-x-ponte" \
  || parou "nao consegui baixar o codigo do GitHub."
export RX_PONTE_BRASIL_URL="$URL" RX_PONTE_BRASIL_TOKEN="$TOKEN"
RX_PONTE_BRASIL_CHAVE="$(base64 -w0 "$CHAVE_JSON")"
export RX_PONTE_BRASIL_CHAVE
python3 "$HOME/raio-x-ponte/scripts/ponte_brasil_conferir.py" --esperar 0 || exit 1
CONFERE
```

---

## Quanto custa (honesto)

Preços da página oficial do Cloud Run para São Paulo (cobrança por pedido), conferidos em 15/09/2026:
**US$ 0,0000336 por vCPU-segundo**, **US$ 0,0000035 por GiB-segundo** de memória e **US$ 0,40 por milhão de
pedidos**. A ponte usa 1 vCPU e 256 MiB, no máximo uma máquina, que desliga sozinha quando ninguém chama.

**No uso normal: R$ 0 a R$ 1 por mês.**

- **Cloud Run**: a faixa grátis mensal é de 2 milhões de pedidos, 180.000 vCPU-segundos e 360.000
  GiB-segundos por conta de faturamento, aplicada como desconto pelo preço das regiões mais baratas; São
  Paulo é um pouco mais cara por segundo, então a faixa grátis cobre um pouco menos tempo lá. Exemplo:
  5.000 consultas de 2 segundos por mês são 10.000 segundos de máquina, uns US$ 0,35 antes da faixa grátis.
- **Saída de rede** (os dados que a ponte devolve ao Render; conferido em 14/09/2026): tabela "Premium Tier"
  do Google, de US$ 0,12 a US$ 0,19 por GB. Uma leitura do INCRA tem de alguns KB a centenas de KB: 1.000
  relatórios por mês ≈ 0,4 GB ≈ **menos de R$ 1**. Num mês em que o SICAR ficar muito tempo sem responder ao
  Render, o mapa passa pela ponte e pode chegar a **alguns reais**.
- **Cloud Build** (monta a ponte a cada publicação): 2.500 minutos grátis por mês; cada publicação usa uns
  3 minutos. **Artifact Registry** (guarda a imagem): 0,5 GB grátis; cada publicação guarda ~50 MB.
- **Chave, conta de serviço e permissões**: não têm custo.

**No pior caso, por situação:**

- **Alguém descobre só o endereço e inunda de pedidos: US$ 0.** A ponte está fechada; o Google recusa o
  pedido antes de chegar à ponte e diz, na página de preços, que pedido recusado pela permissão não é cobrado.
  Na versão anterior (aberta) esse mesmo ataque mantinha a máquina ligada o mês todo: 2.592.000 segundos ×
  US$ 0,0000336 ≈ US$ 87 de CPU + US$ 2 de memória, mais US$ 0,40 por milhão de pedidos, sem teto
  (1.000 pedidos por segundo = US$ 1,44 por hora).
- **Alguém rouba a chave** (teria de invadir o Render ou a sua conta Google): o teto pausa a ponte quando a
  estimativa do Google passar de **R$ 20**. O teto não é instantâneo, e o que passar durante o atraso é
  cobrado. Com a máquina ocupada sem parar, o atraso custa até uns **US$ 0,12 por hora (≈ US$ 3 por dia)**
  de máquina, mais US$ 0,40 por milhão de pedidos. O teto conta o valor **bruto**, sem descontar a faixa
  grátis; o uso normal fica muito abaixo de R$ 20.
- **Uso legítimo muito alto** (muitos relatórios num mês): se chegar a R$ 20, a ponte pausa e o INCRA fica em
  "consulta pendente" até você liberar. O e-mail de 50% e 80% avisa antes.

### Se chegar o e-mail do teto (ou um gasto que você não esperava)

1. Cole no Cloud Shell o bloco de "Se o token ou a chave vazar" (a chave e o token antigos param de valer na
   hora) e troque `RX_PONTE_BRASIL_TOKEN` e `RX_PONTE_BRASIL_CHAVE` nos dois serviços do Render.
2. Me avise: eu olho os registros para saber se foi ataque ou uso de verdade.
3. Para religar a ponte (nomes da documentação do Google): abra <https://console.cloud.google.com/billing/budgets>,
   na faixa de aviso clique em **View spend cap details**, clique no orçamento `teto ponte R$ 20`, depois em
   **Lift spend cap** e em **Confirm**. A ponte pode levar até 1 hora para voltar. **Atenção:** pela
   documentação, depois de liberado no mesmo mês o teto só volta a valer naquele mês se você **aumentar o
   valor** — então edite o orçamento, suba o valor (por exemplo, de 20 para 25) e salve.

**Parar a ponte na hora** (se o gasto não parar): no Cloud Shell,

```bash
gcloud run services delete ponte-brasil --region southamerica-east1 --quiet
```

O site continua no ar: o INCRA volta a "consulta pendente" e o SICAR segue direto. Para religar, cole o bloco
do Passo 4 de novo.

### Por que esta proteção e não outra (decidido em 15/09/2026)

| opção | quem só tem o endereço | quem rouba a chave | efeito colateral | trabalho para você |
|---|---|---|---|---|
| 1. Só fechar pelo Google | US$ 0 | sem teto | nenhum | uma variável a mais no Render |
| 2. Desligar o faturamento pelo orçamento (função automática) | paga até o aviso chegar (horas; o Google avisa "várias vezes por dia") | idem | o Google avisa que desliga **tudo** do projeto e que recursos podem ser apagados sem volta (inclusive a futura cópia do CAR) | vários serviços a mais do Google, fila de mensagens e função |
| **3. Fechar pelo Google + teto do Cloud Run (escolhida)** | **US$ 0** | **R$ 20 + atraso** | só a ponte pausa; nada é apagado; o resto do projeto segue | 5 cliques no console (Passo 3) |

## Cópia mensal do CAR (Base dos Dados/BigQuery)

O mesmo projeto Raio-X será reaproveitado para a cópia mensal do CAR (decisão 3 do plano). **Hoje não há nada
a ligar**: preparar agora não economiza passo, e o tamanho da cópia ainda não foi medido contra a faixa grátis
do BigQuery. O teto do Passo 3 vale só para o Cloud Run: pela documentação do Google, os outros serviços do
projeto não são afetados quando ele pausa. Quando a cópia vier, ela ganha o próprio limite.

## Como desligar

- **Parar de usar, sem apagar nada**: no Render, em cada serviço, **Environment**, apague
  `RX_PONTE_BRASIL_URL`, `RX_PONTE_BRASIL_TOKEN` e `RX_PONTE_BRASIL_CHAVE` e salve com deploy. O Raio-X volta
  a consultar direto, exatamente como antes. A ponte fechada e parada não gera gasto de máquina.
- **Apagar a ponte**: no Cloud Shell, `gcloud run services delete ponte-brasil --region southamerica-east1`.
  Para voltar, basta colar o bloco do Passo 4 de novo.
- **Zerar tudo no Google**: em <https://console.cloud.google.com/cloud-resource-manager>, selecione o projeto
  e use **Shut down** (desligar o projeto). O Google apaga o projeto depois de 30 dias. Isso também apagaria a
  futura cópia do CAR, se ela já existir.

## Se o token ou a chave vazar

Cole no Cloud Shell (troca o token da ponte, cria uma chave nova, apaga as antigas e baixa a nova), depois
troque `RX_PONTE_BRASIL_TOKEN` e `RX_PONTE_BRASIL_CHAVE` nos dois serviços do Render (itens 5 a 7 do Passo 5).
Até trocar no Render, o INCRA fica em "consulta pendente".

```bash
bash <<'TROCA'
set -Eeuo pipefail
trap 'echo; echo "PAROU: o passo acima falhou (linha $LINENO). Me mande so a mensagem de erro acima."; exit 1' ERR
parou() { echo; echo "PAROU: $*"; exit 1; }
REGIAO=southamerica-east1
SERVICO=ponte-brasil
CONTA=ponte-render
PASTA="$HOME/.raio-x-ponte"
PROJETO="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
[ -n "$PROJETO" ] || parou "nenhum projeto selecionado. Escolha o projeto Raio-X no topo do console."
EMAIL="$CONTA@$PROJETO.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$EMAIL" >/dev/null 2>&1 || parou "a conta ponte-render nao existe: cole o bloco do Passo 4."
URL="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --format='value(status.url)' 2>/dev/null || true)"
[ -n "$URL" ] || parou "a ponte nao existe neste projeto: cole o bloco do Passo 4."
umask 077
mkdir -p "$PASTA"
echo "1/3 Trocando o token da ponte..."
TOKEN="$(openssl rand -hex 32)"
gcloud run services update "$SERVICO" --region "$REGIAO" --update-env-vars "PONTE_TOKEN=$TOKEN" --quiet >/dev/null \
  || parou "o Google nao trocou o token. Me mande so a mensagem de erro acima."
echo "2/3 Chave nova; as antigas param de valer agora..."
ANTIGAS="$(gcloud iam service-accounts keys list --iam-account "$EMAIL" --managed-by=user --format='value(name.basename())')"
rm -f "$PASTA/chave-nova.json"
gcloud iam service-accounts keys create "$PASTA/chave-nova.json" --iam-account "$EMAIL" --quiet >/dev/null \
  || parou "o Google nao criou a chave nova. Me mande so a mensagem de erro acima."
for K in $ANTIGAS; do
  gcloud iam service-accounts keys delete "$K" --iam-account "$EMAIL" --quiet >/dev/null \
    || parou "nao consegui apagar a chave antiga $K. Me mande so a mensagem de erro acima."
done
mv -f "$PASTA/chave-nova.json" "$PASTA/chave.json"
echo "3/3 Preparando para o Render..."
base64 -w0 "$PASTA/chave.json" > "$HOME/raio-x-chave-render.txt"
echo >> "$HOME/raio-x-chave-render.txt"
echo
echo "================ COPIE PARA O RENDER (NAO MANDE NO CHAT) ================"
echo "RX_PONTE_BRASIL_URL   = $URL (nao mudou)"
echo "RX_PONTE_BRASIL_TOKEN = $TOKEN"
echo "RX_PONTE_BRASIL_CHAVE = no arquivo raio-x-chave-render.txt (o navegador vai baixar agora)"
echo "=========================================================================="
cloudshell download "$HOME/raio-x-chave-render.txt" 2>/dev/null \
  || echo "Se o download nao comecar: no Cloud Shell, clique nos tres pontinhos (More) > Download e escreva raio-x-chave-render.txt"
TROCA
```

Se vazar só o **endereço** da ponte: não precisa fazer nada. Ela está fechada.

---

### Referência técnica (para quem mantém)

- Código da ponte: `ponte_brasil/app.py` (só biblioteca padrão) e `ponte_brasil/Dockerfile`. Publicada com
  `--no-allow-unauthenticated`; a conta `ponte-render@PROJETO.iam.gserviceaccount.com` tem só
  `roles/run.invoker` na ponte (não no projeto). O `X-Ponte-Token` continua obrigatório dentro da ponte.
- Cliente no Raio-X: `br_bridge.py`. Com `RX_PONTE_BRASIL_CHAVE` (JSON da chave, ou o JSON em base64), assina
  um JWT RS256 com `target_audience` = endereço da ponte, troca em `https://oauth2.googleapis.com/token` pelo
  token de identidade (cache de 1 hora, renovado 5 minutos antes, em segundo plano) e manda
  `X-Serverless-Authorization: Bearer ...` pela entrada padrão do curl. Assinatura pela biblioteca
  `cryptography` quando existe; senão em Python puro (o gate confere que as duas dão os mesmos bytes). Sem a
  chave, igual à versão anterior; sem as variáveis, igual a antes da ponte.
- Transportes: `deploy_app._curl`, `incra_acervo_f2.curl_fetch`, `incra_snci_public_v42._curl`,
  `sicar_detail_sources._curl` e `sicar_detail_sources_v2._curl`; `deploy_app.probe_sources` mede o INCRA
  pela ponte quando ela está ligada.
- Conferência no Cloud Shell: `scripts/ponte_brasil_conferir.py` (usa o mesmo `br_bridge`; nunca imprime
  segredo nem o endereço). Gate: `scripts/ponte_brasil_gate.py` (no `quality-gate.yml`), que também confere a
  sintaxe e as travas dos blocos deste guia.
- Chamada: `GET|POST {URL}/v1/fetch` com `X-Ponte-Token`, `X-Ponte-Url` (endereço oficial),
  `X-Ponte-Timeout` opcional (segundos, até 25) e `X-Serverless-Authorization`. Recusa da ponte vem com
  `X-Ponte-Error` e `X-Ponte-Origin: ponte`; resposta da fonte vem com `X-Ponte-Origin: upstream`; recusa do
  Google (403) vem sem esses cabeçalhos.
- Saúde: `GET {URL}/v1/health` (com token de identidade; nunca um caminho terminado em "z": o Cloud Run
  reserva esses caminhos e responde antes da ponte).
- Fontes (Google Cloud, conferidas em 15/09/2026; frases originais):
  - Cloud Run pricing, <https://cloud.google.com/run/pricing>: "Requests are only billed when they reach the
    container after successfully being authenticated, requests denied by IAM policy are not billed." Preços de
    São Paulo na tabela "Services (Requests-based billing)".
  - Manage spend cap budgets, <https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps>:
    "When a spend cap budget is enforced, usage of your specified services is automatically paused until you
    manually lift the spend cap."; serviços elegíveis incluem "Cloud Run"; "No, resources or data aren't
    deleted."; "Even though faster than reports, the enforcement of spend caps isn't instant and any cost
    overages are billed as normal."; "The cost calculations for spend caps are based on gross costs and don't
    include savings and credits."; "If the cap was enforced and lifted within the same billing month, it won't
    trigger again for the rest of the month, unless you increase the target amount of the cap."
  - Disable billing usage with notifications,
    <https://docs.cloud.google.com/billing/docs/how-to/disable-billing-with-notifications>: "Warning: This
    tutorial removes Cloud Billing from your project, shutting down all resources. Resources might be
    irretrievably deleted."; "There's a delay between incurring costs and receiving budget notifications, so
    you might incur additional costs for usage that hasn't arrived at the time that all services are stopped."
  - Create, edit, or delete budgets, <https://docs.cloud.google.com/billing/docs/how-to/budgets>: "Setting an
    alerts-only budget doesn't automatically cap Google Cloud or Google Maps Platform usage or spending."
  - Set up programmatic notifications,
    <https://docs.cloud.google.com/billing/docs/how-to/budgets-programmatic-notifications>: "Budget
    notifications are sent to the Pub/Sub topic multiple times per day with the current status of your budget."
  - Authenticate service-to-service, <https://docs.cloud.google.com/run/docs/authenticating/service-to-service>:
    "Self-sign a service account JWT with the target_audience claim set to the URL of the receiving service";
    "Exchange the self-signed JWT for a Google-signed ID token"; cabeçalho "X-Serverless-Authorization: Bearer
    ID_TOKEN"; "If you must authenticate with a service account key, you are responsible for the security of
    the private key".
  - gcloud run deploy, <https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy>: "Use
    --allow-unauthenticated to enable and --no-allow-unauthenticated to disable."
  - Manage files with Cloud Shell, <https://docs.cloud.google.com/shell/docs/uploading-and-downloading-files>:
    "Use the cloudshell download command to download files".
  - Security baseline constraints, <https://docs.cloud.google.com/resource-manager/docs/manage-baseline-constraints>:
    organizações criadas desde 03/05/2024 bloqueiam chave de conta de serviço por padrão (conta pessoal sem
    organização não tem essa regra; se tiver, o Passo 4 para com `PAROU`).
  - Render, <https://render.com/docs/configure-environment-variables>: "Save only: Render saves the new
    environment variables without triggering a deploy."
  - Da versão anterior (14/09/2026): Cloud Run known issues (caminhos reservados), Network pricing, Cloud Build
    pricing, Artifact Registry pricing, "Deploy services from source code".
