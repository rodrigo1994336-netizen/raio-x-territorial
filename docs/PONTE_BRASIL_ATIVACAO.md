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
2. **Teto de gasto de R$ 20 por mês.** Quando o Google percebe que o gasto da ponte passou disso, ele pausa
   a ponte sozinho. O site continua no ar; o INCRA volta a "consulta pendente" até você liberar. Nada é
   apagado. O Google avisa que essa percepção não é instantânea: o teto encurta o prejuízo, não garante o
   valor exato.
3. **Os blocos só mexem no projeto Raio-X.** Antes de qualquer ação eles conferem o projeto e param com
   `PAROU` se ele não for o Raio-X ou se usar Firebase (o do Método AFP usa). Nada é feito no projeto errado.
4. **A ponte roda com uma conta sem nenhuma permissão** no Google. Se alguém invadisse a ponte, não
   ganharia permissão para mexer no projeto.

| o que acontece | versão anterior (aberta) | esta versão |
|---|---|---|
| uso normal | R$ 0 a R$ 1 por mês | igual |
| alguém descobre só o endereço e inunda de pedidos | sem teto: perto de US$ 90 por mês de máquina, mais US$ 0,40 por milhão de pedidos | **US$ 0** (o Google recusa e não cobra) |
| alguém rouba a chave (teria de invadir o Render ou a sua conta Google) | — | o teto pausa, mas **com atraso** (o Google não diz quanto; os relatórios de custo levam até um dia, às vezes mais): dependendo de quantos pedidos por segundo o atacante mandar, o estouro pode chegar a **centenas de reais** (veja "Quanto custa") |

**Já ligou a versão anterior?** Faça de novo os Passos 3, 4 e 5. Entre o Passo 4 e o fim do Passo 5 o INCRA
fica em "consulta pendente" (a ponte já fechou e o Render ainda não tem a chave). Isso é esperado.

**Antes de começar.** O código desta versão precisa estar na `main` do GitHub (o PR mesclado, com a sua
autorização). Os blocos baixam o código de lá, conferem que é exatamente a versão revisada e param com
`PAROU` se não for.

**Telas que eu não vi.** Os nomes de botões e menus do Google Cloud (Passos 1 a 4, liberação do teto e "Zerar
tudo") e do Render (Passos 5 e 6) foram tirados da documentação oficial de cada um; eu não abri essas telas. Se algum nome
estiver diferente na sua tela, pare e me mande uma captura antes de clicar.

Tempo total: uns 25 minutos. Você vai precisar de um cartão para ativar o faturamento do Google (é exigência
do Google para usar o Cloud Run, mesmo na faixa grátis). **Eu não mexo em cartão nem em senha: essa parte é
sua.**

---

## Passo 1 — Criar (ou reaproveitar) o projeto Raio-X no Google Cloud

**Se você já criou o projeto `raio-x-ponte` pela versão anterior, use ele** e pule para o Passo 2 só para
conferir o faturamento.

1. Abra <https://console.cloud.google.com/projectcreate> e entre com a conta **rodrigo1994336@gmail.com**.
   Se o Google pedir para aceitar os termos, a decisão é sua.
2. Em **Project name** (nome do projeto), escreva: `Raio-X`. Logo abaixo o Google mostra o **ID do projeto**;
   ele tem de começar com `raio-x` (por exemplo `raio-x-471234`). Se não começar, use a opção de editar o ID (fica
   ao lado dele) e escreva um que comece com `raio-x`. Os blocos dos passos seguintes só aceitam projeto com esse
   nome ou esse ID. Este mesmo projeto vai servir depois para a cópia mensal do CAR (Base dos
   Dados/BigQuery); não precisa criar outro.
3. Em **Parent resource** / **Location**, deixe como está (conta pessoal não tem organização).
4. Clique em **Create**.
5. No topo da página, confira se o projeto selecionado é o `Raio-X` (o seletor de projetos fica no alto, à
   esquerda). Se não for, clique nele e escolha `Raio-X`. **Nunca use o projeto do Método AFP para a ponte.**

## Passo 2 — Ativar o faturamento

1. Abra <https://console.cloud.google.com/billing>.
2. Se você ainda não tem uma conta de faturamento, o Google pede para criar uma: siga as telas dele e
   informe o cartão.
3. Na aba **My projects** (Meus projetos), ache o projeto Raio-X, abra o menu **Actions** (três pontinhos),
   escolha **Change billing** e selecione a sua conta de faturamento. Clique em **Set account**.

## Passo 3 — Teto de gasto de R$ 20 (pausa a ponte sozinho)

Os nomes abaixo são os da documentação do Google para o "orçamento com teto" (spend cap). É um recurso novo
do Google, ainda em pré-lançamento ("Preview"): pode não aparecer para a sua conta, e a tela pode estar em
português.

1. Abra <https://console.cloud.google.com/billing/budgets> (página **Budgets & alerts**) e clique em
   **Create budget** (ou **Create new budget**).
2. Escolha **Spend cap enforcement** (e não *Alerts only*). Em **Name**, escreva `teto ponte R$ 20`. Clique
   em **Next**.
3. Em **Project**, escolha o projeto Raio-X (nunca o do Método AFP). Em **Service**, escolha **Cloud Run**.
   Clique em **Next**.
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
EXECUCAO=ponte-runtime
PASTA="$HOME/.raio-x-ponte"
CODIGO="$HOME/raio-x-ponte"
CODIGO_REVISADO="5200621efcc8408f311388d93b082d51c96c3ac4 ac5824dc9c684a5456a63528cf2aafd8989546d4 15da9fbe91d6d60f651c409e927868b53eeb858f"

echo "1/11 Conferindo o projeto (tem de ser o Raio-X, nunca o do Metodo AFP)..."
PROJETO="$(gcloud config get-value project 2>/dev/null || true)"
PROJETO="${PROJETO:-${GOOGLE_CLOUD_PROJECT:-}}"
[ -n "$PROJETO" ] || parou "nenhum projeto selecionado. Nada foi feito. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo."
NOME="$(gcloud projects describe "$PROJETO" --format='value(name)' 2>/dev/null || true)"
case "${PROJETO,,}|${NOME,,}" in
  raio-x*|*'|raio-x'*) ;;
  *) CERTO="$(gcloud projects list --filter='projectId:raio-x*' --format='value(projectId)' 2>/dev/null | head -n 1 || true)"
     [ -z "$CERTO" ] || parou "o Cloud Shell esta no projeto \"$NOME\" ($PROJETO), que NAO e o Raio-X. Nada foi feito. Cole: gcloud config set project $CERTO   e depois cole o bloco de novo."
     parou "o Cloud Shell esta no projeto \"$NOME\" ($PROJETO), que NAO e o Raio-X. Nada foi feito. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo." ;;
esac
FIREBASE="$(gcloud services list --enabled --project "$PROJETO" --filter='config.name=firebase.googleapis.com' --format='value(config.name)')" \
  || parou "nao consegui conferir o projeto $PROJETO. Nada foi feito. Me mande so a mensagem de erro acima."
[ -z "$FIREBASE" ] || parou "o projeto $PROJETO usa Firebase: parece o do Metodo AFP, nao o Raio-X. Nada foi feito."
echo "  projeto: $NOME ($PROJETO)"

echo "2/11 Baixando o codigo publico do Raio-X e conferindo que e a versao revisada..."
rm -rf "$CODIGO"
git clone --quiet --depth 1 https://github.com/rodrigo1994336-netizen/raio-x-territorial.git "$CODIGO" \
  || parou "nao consegui baixar o codigo do GitHub. Nada foi feito."
ACHADO="$(git -C "$CODIGO" rev-parse HEAD:ponte_brasil HEAD:br_bridge.py HEAD:scripts/ponte_brasil_conferir.py 2>/dev/null | tr '\n' ' ' || true)"
[ "$ACHADO" = "$CODIGO_REVISADO " ] \
  || parou "o codigo da ponte na main do GitHub nao e a versao revisada deste guia (o PR ainda nao foi mesclado, ou o codigo mudou depois). Nada foi feito. Nao mexa no Render e me avise."

echo "3/11 Ligando Cloud Run, Cloud Build, Artifact Registry e IAM (1 a 2 minutos)..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  --project "$PROJETO" --quiet \
  || parou "o Google nao ligou os servicos. Confira o faturamento (Passo 2) e me mande so a mensagem de erro acima."
NUMERO="$(gcloud projects describe "$PROJETO" --format='value(projectNumber)')"
BUILD="${NUMERO}-compute@developer.gserviceaccount.com"

echo "4/11 Dando ao build a permissao de montar a ponte (ate 3 minutos)..."
OK=0
for TENTATIVA in $(seq 1 12); do
  # a conta do build aparece alguns segundos depois de ligar o Cloud Run: tenta de novo em vez de dar erro
  if gcloud projects add-iam-policy-binding "$PROJETO" --member="serviceAccount:$BUILD" \
       --role=roles/run.builder --condition=None --quiet >/dev/null 2>&1; then OK=1; break; fi
  sleep 10
done
[ "$OK" = 1 ] || parou "o Google nao aceitou a permissao do build. Cole o bloco de novo daqui a 5 minutos."
sleep 60

echo "5/11 Conta com que a ponte roda (sem nenhuma permissao no projeto)..."
RUNTIME="$EXECUCAO@$PROJETO.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$RUNTIME" --project "$PROJETO" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$EXECUCAO" --display-name="Ponte no Brasil roda com esta conta (sem permissoes)" \
    --project "$PROJETO" --quiet >/dev/null \
    || parou "o Google nao criou a conta da ponte. Me mande so a mensagem de erro acima."
  sleep 30
fi

echo "6/11 Token da ponte: reaproveita o que ja existe; senao gera um novo..."
TOKEN="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --project "$PROJETO" --format=json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((e.get("value","") for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env",[]) if e.get("name")=="PONTE_TOKEN"),""))' 2>/dev/null || true)"
TOKEN_NOVO=0
if [ -z "$TOKEN" ]; then
  TOKEN="$(openssl rand -hex 32)"
  TOKEN_NOVO=1
fi

echo "7/11 Publicando a ponte FECHADA em Sao Paulo (3 a 6 minutos)..."
gcloud run deploy "$SERVICO" --source "$CODIGO/ponte_brasil" --region "$REGIAO" --project "$PROJETO" \
  --service-account "$RUNTIME" --max-instances 1 --min-instances 0 --memory 256Mi --no-allow-unauthenticated \
  --set-env-vars "PONTE_TOKEN=$TOKEN" --quiet \
  || parou "a publicacao da ponte falhou. Se for a primeira vez, espere 5 minutos e cole o bloco de novo; se repetir, me mande so a mensagem de erro acima."
for QUEM in allUsers allAuthenticatedUsers; do
  # a versao anterior deste guia abria a ponte ao publico: tira, se ainda estiver la
  gcloud run services remove-iam-policy-binding "$SERVICO" --region "$REGIAO" --project "$PROJETO" --member="$QUEM" \
    --role=roles/run.invoker --quiet >/dev/null 2>&1 || true
done
POLITICA="$(gcloud run services get-iam-policy "$SERVICO" --region "$REGIAO" --project "$PROJETO" --format=json)"
case "$POLITICA" in
  *'"allUsers"'*|*'"allAuthenticatedUsers"'*) parou "a ponte continua aberta ao publico. NAO coloque nada no Render." ;;
esac
URL="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --project "$PROJETO" --format='value(status.url)')"
[ -n "$URL" ] || parou "a ponte nao informou o endereco."

echo "8/11 Conta de servico do Render, com permissao so de chamar a ponte..."
EMAIL="$CONTA@$PROJETO.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$EMAIL" --project "$PROJETO" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$CONTA" --display-name="Raio-X no Render chama a ponte" --project "$PROJETO" --quiet >/dev/null \
    || parou "o Google nao criou a conta de servico. Me mande so a mensagem de erro acima."
fi
OK=0
for TENTATIVA in $(seq 1 12); do
  if gcloud run services add-iam-policy-binding "$SERVICO" --region "$REGIAO" --project "$PROJETO" \
       --member="serviceAccount:$EMAIL" --role=roles/run.invoker --quiet >/dev/null 2>&1; then OK=1; break; fi
  sleep 10
done
[ "$OK" = 1 ] || parou "o Google nao deu a permissao de chamar a ponte. Cole o bloco de novo daqui a 5 minutos."

echo "9/11 Chave da conta de servico (fica so no seu Cloud Shell e no Render)..."
umask 077
mkdir -p "$PASTA"
rm -f "$HOME/raio-x-chave-render.txt"
CHAVE_JSON="$PASTA/chave.json"
ATUAL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("private_key_id",""))' "$CHAVE_JSON" 2>/dev/null || true)"
CHAVES="$(gcloud iam service-accounts keys list --iam-account "$EMAIL" --project "$PROJETO" --managed-by=user --format='value(name.basename())')"
NOVA=0
if [ -n "$ATUAL" ] && grep -qx -- "$ATUAL" <<<"$CHAVES"; then
  echo "  chave: a mesma de antes"
else
  rm -f "$CHAVE_JSON"
  gcloud iam service-accounts keys create "$CHAVE_JSON" --iam-account "$EMAIL" --project "$PROJETO" --quiet >/dev/null \
    || parou "o Google nao criou a chave. Me mande so a mensagem de erro acima."
  NOVA=1
  echo "  chave: nova"
fi

echo "10/11 Conferindo: ponte fechada, chave aceita, INCRA e SICAR pela ponte (ate 8 minutos)..."
export RX_PONTE_BRASIL_URL="$URL" RX_PONTE_BRASIL_TOKEN="$TOKEN"
RX_PONTE_BRASIL_CHAVE="$(base64 -w0 "$CHAVE_JSON")"
export RX_PONTE_BRASIL_CHAVE
python3 "$CODIGO/scripts/ponte_brasil_conferir.py" --esperar 480 || exit 1
if [ "$NOVA" = 1 ]; then
  NOVO_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["private_key_id"])' "$CHAVE_JSON")"
  for K in $CHAVES; do
    # chaves antigas desta conta (de uma tentativa anterior) deixam de valer, so depois da conferencia
    [ "$K" = "$NOVO_ID" ] || gcloud iam service-accounts keys delete "$K" --iam-account "$EMAIL" --project "$PROJETO" --quiet >/dev/null 2>&1 || true
  done
fi

echo "11/11 Preparando a chave para o Render..."
base64 -w0 "$CHAVE_JSON" > "$HOME/raio-x-chave-render.txt"
echo >> "$HOME/raio-x-chave-render.txt"
echo
echo "================ COPIE PARA O RENDER (NAO MANDE NO CHAT) ================"
echo "RX_PONTE_BRASIL_URL   = $URL"
echo "RX_PONTE_BRASIL_TOKEN = $TOKEN"
echo "RX_PONTE_BRASIL_CHAVE = no arquivo raio-x-chave-render.txt (o navegador vai baixar agora)"
echo "=========================================================================="
if [ "$TOKEN_NOVO" = 1 ]; then echo "O TOKEN E NOVO: coloque (ou troque) RX_PONTE_BRASIL_URL e RX_PONTE_BRASIL_TOKEN nos DOIS servicos do Render."; fi
if [ "$NOVA" = 1 ]; then echo "A CHAVE E NOVA: coloque (ou troque) RX_PONTE_BRASIL_CHAVE nos DOIS servicos do Render."; fi
if [ "$TOKEN_NOVO" = 0 ] && [ "$NOVA" = 0 ]; then echo "TOKEN E CHAVE IGUAIS AOS DE ANTES: se o Render ja tem os dois, nao precisa mexer nele."; fi
cloudshell download "$HOME/raio-x-chave-render.txt" 2>/dev/null \
  || echo "Se o download nao comecar: no Cloud Shell, clique nos tres pontinhos (More) > Download e escreva raio-x-chave-render.txt"
PONTE
```

3. No fim aparece a caixa **COPIE PARA O RENDER** com a URL e o token, uma ou duas linhas dizendo **o que
   mudou**, e o navegador baixa o arquivo `raio-x-chave-render.txt` (a chave). Deixe o Cloud Shell aberto
   para copiar no próximo passo.

> **URL, token e chave são segredos.** Cole os três direto no Render. Não mande no chat, no WhatsApp nem por
> e-mail. Se o bloco for colado de novo, ele reaproveita o que puder e **diz o que mudou**:
> `O TOKEN E NOVO` (troque URL e token no Render), `A CHAVE E NOVA` (troque a chave no Render) ou
> `TOKEN E CHAVE IGUAIS AOS DE ANTES` (não precisa mexer no Render). Depois de apagar a ponte, o token é
> sempre novo.

**Se aparecer `PAROU: o Cloud Shell esta no projeto ... que NAO e o Raio-X`**: nada foi feito. Siga o que a
própria linha diz (ela mostra o comando com o ID certo, quando acha o projeto Raio-X) e cole o bloco de novo.

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

**O que colocar.** Na primeira vez, as três. Nas vezes seguintes, só o que o Cloud Shell mandou trocar
(`O TOKEN E NOVO`: URL e token; `A CHAVE E NOVA`: a chave). Não compare token a olho: siga a linha.

Antes: abra o arquivo baixado `raio-x-chave-render.txt` (pasta **Downloads**) com dois cliques; ele abre no
Bloco de Notas. É uma linha só, bem comprida.

Para cada um dos dois serviços:

1. Abra <https://dashboard.render.com> e clique no serviço.
2. No menu da esquerda, clique em **Environment**.
3. Para cada variável a colocar (`RX_PONTE_BRASIL_URL`, `RX_PONTE_BRASIL_TOKEN`, `RX_PONTE_BRASIL_CHAVE`):
   - **se ela já aparece na lista**, troque o valor dela pelo novo (não crie outra com o mesmo nome);
   - **se não aparece**, clique em **+ Add Environment Variable**, escreva o nome em **Key** e o valor em
     **Value**.
4. Valores: a URL e o token vêm da caixa do Cloud Shell (a URL começa com `https://`). A chave vem do Bloco de
   Notas: clique dentro do texto, aperte **Ctrl+A** e depois **Ctrl+C**; volte ao Render, clique no valor e
   aperte **Ctrl+V**.
5. Clique para salvar e escolha **Save, rebuild, and deploy** (publica a versão mais nova da `main` já com as
   variáveis). Se escolher **Save only**, as variáveis só valem no próximo deploy.

Depois dos dois serviços, apague as cópias da chave que sobraram:

- **No seu computador:** feche a aba do Bloco de Notas **sem salvar** (se ele perguntar, escolha não salvar).
  Na pasta Downloads, clique no arquivo `raio-x-chave-render.txt` e aperte **Shift+Delete** (apaga sem passar
  pela Lixeira); confirme.
- **No Cloud Shell:** cole `rm -f ~/raio-x-chave-render.txt` e aperte **Enter**.

A chave continua guardada no seu Cloud Shell (pasta pessoal, protegida pela sua conta Google) e no Render.

## Passo 6 — Conferir

1. No Render, em cada serviço, abra **Logs** e procure por `RX_PONTE_BRASIL`. Tem que aparecer
   `RX_PONTE_BRASIL=on host=... incra=ponte sicar=direto_primeiro acesso=google assinatura=...`.
   - Sem `acesso=google` no fim: falta a chave nesse serviço (Passo 5).
   - `RX_PONTE_BRASIL=off motivo=chave_invalida`: a chave foi colada pela metade. Cole de novo (Ctrl+A no
     Bloco de Notas pega a linha inteira).
   - `RX_PONTE_BRASIL=off motivo=configuracao_invalida`: a URL não começa com `https://` ou o token foi
     colado pela metade. Corrija no **Environment**.
   - `RX_PONTE_BRASIL=erro_credencial motivo=http_400`: o Google recusou a chave (foi trocada ou apagada).
     Cole o bloco de conferência abaixo; se ele parar na credencial, use "Se o token ou a chave vazar".
   - `RX_PONTE_BRASIL=erro_ponte http=403 origem=ponte_ou_fonte` ou `http=401`: pode ser a ponte recusando
     (chave ou token diferentes dos da ponte) **ou** a fonte oficial recusando. O Render não tem como saber
     qual dos dois. **Antes de trocar qualquer coisa**, cole o bloco de conferência abaixo:
     - se o INCRA aparecer `respondeu pela ponte`, a ponte e a fonte estão bem: o que está no Render difere do
       que está na ponte. Cole o bloco do Passo 4 de novo e troque no Render o que ele disser que é novo; se
       ele disser `TOKEN E CHAVE IGUAIS AOS DE ANTES`, troque mesmo assim a URL, o token e a chave nos dois
       serviços (o Render está com valores velhos);
     - se aparecer `nao respondeu (codigo 401)` ou `(codigo 403)` com `fechada` e `saude` ok, quem recusou foi
       a fonte oficial: não mexa em nada, me mande as linhas.
2. No site, abra o imóvel de prova (Curvelo `MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F`). A referência
   INCRA (SIGEF/SNCI) deve aparecer sem "consulta pendente". Se a fonte oficial estiver fora do ar, continua
   "consulta pendente" — isso é correto, não é defeito da ponte.
3. A primeira leitura depois de a ponte ficar parada pode sair como "consulta pendente": a máquina demora
   alguns segundos para ligar. Abra de novo o mesmo imóvel; a segunda já deve responder. Depois de ligar, eu
   meço esse tempo antes de mexer em qualquer prazo.

Bloco de conferência (não mostra segredo e não muda nada no Google):

```bash
bash <<'CONFERE'
set -Eeuo pipefail
trap 'echo; echo "PAROU: o passo acima falhou (linha $LINENO). Me mande so a mensagem de erro acima."; exit 1' ERR
parou() { echo; echo "PAROU: $*"; exit 1; }
REGIAO=southamerica-east1
SERVICO=ponte-brasil
CODIGO="$HOME/raio-x-ponte"
CHAVE_JSON="$HOME/.raio-x-ponte/chave.json"
CODIGO_REVISADO="5200621efcc8408f311388d93b082d51c96c3ac4 ac5824dc9c684a5456a63528cf2aafd8989546d4 15da9fbe91d6d60f651c409e927868b53eeb858f"
PROJETO="$(gcloud config get-value project 2>/dev/null || true)"
PROJETO="${PROJETO:-${GOOGLE_CLOUD_PROJECT:-}}"
[ -n "$PROJETO" ] || parou "nenhum projeto selecionado. Nada foi feito. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo."
NOME="$(gcloud projects describe "$PROJETO" --format='value(name)' 2>/dev/null || true)"
case "${PROJETO,,}|${NOME,,}" in
  raio-x*|*'|raio-x'*) ;;
  *) CERTO="$(gcloud projects list --filter='projectId:raio-x*' --format='value(projectId)' 2>/dev/null | head -n 1 || true)"
     [ -z "$CERTO" ] || parou "o Cloud Shell esta no projeto \"$NOME\" ($PROJETO), que NAO e o Raio-X. Nada foi feito. Cole: gcloud config set project $CERTO   e depois cole o bloco de novo."
     parou "o Cloud Shell esta no projeto \"$NOME\" ($PROJETO), que NAO e o Raio-X. Nada foi feito. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo." ;;
esac
FIREBASE="$(gcloud services list --enabled --project "$PROJETO" --filter='config.name=firebase.googleapis.com' --format='value(config.name)')" \
  || parou "nao consegui conferir o projeto $PROJETO. Nada foi feito. Me mande so a mensagem de erro acima."
[ -z "$FIREBASE" ] || parou "o projeto $PROJETO usa Firebase: parece o do Metodo AFP, nao o Raio-X. Nada foi feito."
echo "  projeto: $NOME ($PROJETO)"
rm -f "$HOME/raio-x-chave-render.txt"
URL="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --project "$PROJETO" --format='value(status.url)' 2>/dev/null || true)"
[ -n "$URL" ] || parou "nao achei a ponte no projeto $PROJETO (apagada, ou o Google nao respondeu). Cole o bloco do Passo 4."
TOKEN="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --project "$PROJETO" --format=json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((e.get("value","") for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env",[]) if e.get("name")=="PONTE_TOKEN"),""))' 2>/dev/null || true)"
[ -n "$TOKEN" ] || parou "a ponte esta sem token. Cole o bloco do Passo 4."
[ -s "$CHAVE_JSON" ] || parou "a chave nao esta neste Cloud Shell. Use o bloco de 'Se o token ou a chave vazar' (ele cria outra)."
rm -rf "$CODIGO"
git clone --quiet --depth 1 https://github.com/rodrigo1994336-netizen/raio-x-territorial.git "$CODIGO" \
  || parou "nao consegui baixar o codigo do GitHub."
ACHADO="$(git -C "$CODIGO" rev-parse HEAD:ponte_brasil HEAD:br_bridge.py HEAD:scripts/ponte_brasil_conferir.py 2>/dev/null | tr '\n' ' ' || true)"
[ "$ACHADO" = "$CODIGO_REVISADO " ] \
  || parou "o codigo da ponte na main do GitHub nao e a versao revisada deste guia. Nada foi rodado. Me avise."
export RX_PONTE_BRASIL_URL="$URL" RX_PONTE_BRASIL_TOKEN="$TOKEN"
RX_PONTE_BRASIL_CHAVE="$(base64 -w0 "$CHAVE_JSON")"
export RX_PONTE_BRASIL_CHAVE
python3 "$CODIGO/scripts/ponte_brasil_conferir.py" --esperar 0 || exit 1
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
- **Saída de rede** (os dados que a ponte devolve ao Render; tabela conferida em 14/09/2026): tabela
  "Premium Tier" do Google, de US$ 0,12 a US$ 0,19 por GB. Uma leitura do INCRA tem de alguns KB a centenas de
  KB: 1.000 relatórios por mês ≈ 0,4 GB ≈ **menos de R$ 1**. Num mês em que o SICAR ficar muito tempo sem
  responder ao Render, o mapa passa pela ponte e pode chegar a **alguns reais**.
- **Cloud Build** (monta a ponte a cada publicação): 2.500 minutos grátis por mês; cada publicação usa uns
  3 minutos. **Artifact Registry** (guarda a imagem): 0,5 GB grátis; cada publicação guarda ~50 MB.
- **Chave, contas de serviço e permissões**: não têm custo.

**No pior caso, por situação:**

- **Alguém descobre só o endereço e inunda de pedidos: US$ 0.** A ponte está fechada; o Google recusa o
  pedido antes de chegar à ponte e diz, na página de preços, que pedido recusado pela permissão não é cobrado.
  Na versão anterior (aberta) esse mesmo ataque mantinha a máquina ligada o mês todo: 2.592.000 segundos ×
  US$ 0,0000336 ≈ US$ 87 de CPU + US$ 2 de memória, mais US$ 0,40 por milhão de pedidos, sem teto
  (1.000 pedidos por segundo = US$ 1,44 por hora).
- **Alguém rouba a chave** (teria de invadir o Render ou a sua conta Google). Até o teto pausar, o Google
  cobra quatro coisas:

  | o que é cobrado | limite | por hora |
  |---|---|---|
  | máquina (1 vCPU + 256 MiB, só uma) | a própria ponte | até US$ 0,13 |
  | pedidos, **inclusive os que a ponte recusa** (limite de taxa, token errado) | nenhum: depende do atacante | US$ 1,44 a cada 1.000 pedidos por segundo |
  | saída de rede das respostas pequenas (recusas, cabeçalhos; estimativa de até 1 KB cada, não medida no Google) | nenhum: depende do atacante | até US$ 0,70 a cada 1.000 pedidos por segundo |
  | saída de rede dos dados das fontes | **a ponte devolve no máximo 1 GiB por hora** (trava nova, com teste) | até US$ 0,21 |

  A 1.000 pedidos por segundo são **uns US$ 2,50 por hora, ≈ US$ 60 por dia**; a 5.000 por segundo, perto de
  US$ 270 por dia. **O teto não segura esse valor na hora:** o Google diz que o teto "não é instantâneo", que
  o que passar nesse meio tempo é cobrado, e não diz quanto tempo ele leva; diz só que é mais rápido que os
  relatórios de custo, que costumam sair em até um dia e às vezes passam de 24 horas. Conforme o serviço, o
  teto usa o custo bruto estimado (mais rápido) ou o custo real com descontos (que demora mais), e a página
  não diz em qual grupo o Cloud Run está. Ela também não diz se a saída de rede entra no teto do Cloud Run:
  pode ficar de fora. Resumo: com a chave roubada, o teto **encurta o prejuízo, mas conte com até um dia de
  atraso ou mais**, e o estouro pode chegar a **centenas de reais**. Por isso a chave só fica no Render e no
  seu Cloud Shell, e o roteiro de emergência abaixo começa parando a ponte.
- **Uso legítimo muito alto** (muitos relatórios num mês): se chegar a R$ 20, a ponte pausa e o INCRA fica em
  "consulta pendente" até você liberar. O e-mail de 50% e 80% avisa antes (também com atraso). A trava de
  1 GiB por hora fica muito acima do uso normal (≈ 0,4 GB por **mês**); se um dia ela barrar, a consulta sai
  como "consulta pendente", nunca com dado errado.

### Se chegar o e-mail do teto (ou um gasto que você não esperava)

1. **Pare a ponte na hora** com o bloco abaixo. Trocar só a chave não basta: a credencial que o Google já
   entregou com a chave antiga ainda vale **até 1 hora**, e nesse tempo os pedidos continuam chegando à
   ponte e sendo cobrados. Apagar a ponte corta de verdade.
2. Cole o bloco de "Se o token ou a chave vazar" (cria uma chave nova e apaga as antigas).
3. Me avise: eu olho os registros para saber se foi ataque ou uso de verdade.
4. Para religar: cole o bloco do Passo 4 (ele vai dizer `O TOKEN E NOVO`) e troque URL, token e chave nos dois
   serviços do Render. Se o teto tiver pausado, libere também (nomes da documentação do Google): abra
   <https://console.cloud.google.com/billing/budgets>, na faixa de aviso clique em **View spend cap
   details**, clique no orçamento `teto ponte R$ 20`, depois em **Lift spend cap** e em **Confirm**. A ponte
   pode levar até 1 hora para voltar. **Atenção:** pela documentação, depois de liberado no mesmo mês o teto
   só volta a valer naquele mês se você **aumentar o valor** — então edite o orçamento, suba o valor (por
   exemplo, de 20 para 25) e salve.

**Parar a ponte na hora** (apaga só a ponte, e só no projeto Raio-X): no Cloud Shell,

```bash
bash <<'PARAR'
set -Eeuo pipefail
trap 'echo; echo "PAROU: o passo acima falhou (linha $LINENO). Me mande so a mensagem de erro acima."; exit 1' ERR
parou() { echo; echo "PAROU: $*"; exit 1; }
REGIAO=southamerica-east1
SERVICO=ponte-brasil
PROJETO="$(gcloud config get-value project 2>/dev/null || true)"
PROJETO="${PROJETO:-${GOOGLE_CLOUD_PROJECT:-}}"
[ -n "$PROJETO" ] || parou "nenhum projeto selecionado. Nada foi feito. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo."
NOME="$(gcloud projects describe "$PROJETO" --format='value(name)' 2>/dev/null || true)"
case "${PROJETO,,}|${NOME,,}" in
  raio-x*|*'|raio-x'*) ;;
  *) CERTO="$(gcloud projects list --filter='projectId:raio-x*' --format='value(projectId)' 2>/dev/null | head -n 1 || true)"
     [ -z "$CERTO" ] || parou "o Cloud Shell esta no projeto \"$NOME\" ($PROJETO), que NAO e o Raio-X. Nada foi feito. Cole: gcloud config set project $CERTO   e depois cole o bloco de novo."
     parou "o Cloud Shell esta no projeto \"$NOME\" ($PROJETO), que NAO e o Raio-X. Nada foi feito. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo." ;;
esac
FIREBASE="$(gcloud services list --enabled --project "$PROJETO" --filter='config.name=firebase.googleapis.com' --format='value(config.name)')" \
  || parou "nao consegui conferir o projeto $PROJETO. Nada foi feito. Me mande so a mensagem de erro acima."
[ -z "$FIREBASE" ] || parou "o projeto $PROJETO usa Firebase: parece o do Metodo AFP, nao o Raio-X. Nada foi feito."
EXISTE="$(gcloud run services list --region "$REGIAO" --project "$PROJETO" --filter="metadata.name=$SERVICO" --format='value(metadata.name)')" \
  || parou "nao consegui ver as pontes do projeto $PROJETO. Me mande so a mensagem de erro acima."
if [ -z "$EXISTE" ]; then
  echo "A ponte ja nao existe no projeto $NOME ($PROJETO). Nada a apagar."
  exit 0
fi
gcloud run services delete "$SERVICO" --region "$REGIAO" --project "$PROJETO" --quiet \
  || parou "o Google nao apagou a ponte. Me mande so a mensagem de erro acima."
EXISTE="$(gcloud run services list --region "$REGIAO" --project "$PROJETO" --filter="metadata.name=$SERVICO" --format='value(metadata.name)')" \
  || parou "apaguei, mas nao consegui conferir. Cole este bloco de novo."
[ -z "$EXISTE" ] || parou "a ponte ainda aparece no projeto $PROJETO. Cole este bloco de novo daqui a 1 minuto."
echo "PONTE APAGADA no projeto $NOME ($PROJETO). O site continua no ar; o INCRA fica em consulta pendente."
PARAR
```

O site continua no ar: o INCRA volta a "consulta pendente" e o SICAR segue direto. Para religar, cole o bloco
do Passo 4 de novo: como a ponte foi apagada, o token sai novo e ele avisa `O TOKEN E NOVO`; troque URL e
token nos dois serviços do Render.

### Por que esta proteção e não outra (decidido em 15/09/2026)

| opção | quem só tem o endereço | quem rouba a chave | efeito colateral | trabalho para você |
|---|---|---|---|---|
| 1. Só fechar pelo Google | US$ 0 | sem teto | nenhum | uma variável a mais no Render |
| 2. Desligar o faturamento pelo orçamento (função automática) | paga até o aviso chegar (horas; o Google avisa "várias vezes por dia") | idem | o Google avisa que desliga **tudo** do projeto e que recursos podem ser apagados sem volta (inclusive a futura cópia do CAR) | vários serviços a mais do Google, fila de mensagens e função |
| **3. Fechar pelo Google + teto do Cloud Run + 1 GiB por hora na ponte (escolhida)** | **US$ 0** | **pausa com atraso (até um dia ou mais); estouro pode chegar a centenas de reais** | só a ponte pausa; nada é apagado; o resto do projeto segue | 5 cliques no console (Passo 3) |

## Cópia mensal do CAR (Base dos Dados/BigQuery)

O mesmo projeto Raio-X será reaproveitado para a cópia mensal do CAR (decisão 3 do plano). **Hoje não há nada
a ligar**: preparar agora não economiza passo, e o tamanho da cópia ainda não foi medido contra a faixa grátis
do BigQuery. O teto do Passo 3 vale só para o Cloud Run: pela documentação do Google, os outros serviços do
projeto não são afetados quando ele pausa. Quando a cópia vier, ela ganha o próprio limite.

## Como desligar

- **Parar de usar, sem apagar nada**: no Render, em cada serviço, **Environment**, apague
  `RX_PONTE_BRASIL_URL`, `RX_PONTE_BRASIL_TOKEN` e `RX_PONTE_BRASIL_CHAVE` e salve com deploy. O Raio-X volta
  a consultar direto, exatamente como antes. A ponte fechada e parada não gera gasto de máquina.
- **Apagar a ponte**: cole o bloco de "Parar a ponte na hora" (ele confere o projeto antes). Para voltar,
  basta colar o bloco do Passo 4 de novo (token novo: troque no Render).
- **Zerar tudo no Google** (nomes da documentação do Google; eu não vi esta tela): em
  <https://console.cloud.google.com/cloud-resource-manager>, marque **só o projeto de nome `Raio-X` (ou
  `raio-x-ponte`), cujo ID começa com `raio-x`**, e use **Shut down** (desligar o projeto). **NUNCA marque o
  projeto do Método AFP**: desligar o projeto errado derruba o AFP. Se houver dúvida sobre qual é qual, pare e
  me mande uma captura da lista. O Google apaga o projeto depois de 30 dias. Isso também apagaria a futura
  cópia do CAR, se ela já existir.

## Se o token ou a chave vazar

Se o gasto já subiu, **primeiro** cole o bloco de "Parar a ponte na hora" (a credencial emitida com a chave
antiga vale até 1 hora). Depois cole no Cloud Shell o bloco abaixo: ele cria uma chave nova, apaga as antigas,
troca o token da ponte (se ela existir) e baixa a chave nova. Em seguida troque no Render, nos dois serviços, o
que ele disser que é novo (Passo 5). Até trocar no Render, o INCRA fica em "consulta pendente".

```bash
bash <<'TROCA'
set -Eeuo pipefail
trap 'echo; echo "PAROU: o passo acima falhou (linha $LINENO). Me mande so a mensagem de erro acima."; exit 1' ERR
parou() { echo; echo "PAROU: $*"; exit 1; }
REGIAO=southamerica-east1
SERVICO=ponte-brasil
CONTA=ponte-render
PASTA="$HOME/.raio-x-ponte"
PROJETO="$(gcloud config get-value project 2>/dev/null || true)"
PROJETO="${PROJETO:-${GOOGLE_CLOUD_PROJECT:-}}"
[ -n "$PROJETO" ] || parou "nenhum projeto selecionado. Nada foi feito. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo."
NOME="$(gcloud projects describe "$PROJETO" --format='value(name)' 2>/dev/null || true)"
case "${PROJETO,,}|${NOME,,}" in
  raio-x*|*'|raio-x'*) ;;
  *) CERTO="$(gcloud projects list --filter='projectId:raio-x*' --format='value(projectId)' 2>/dev/null | head -n 1 || true)"
     [ -z "$CERTO" ] || parou "o Cloud Shell esta no projeto \"$NOME\" ($PROJETO), que NAO e o Raio-X. Nada foi feito. Cole: gcloud config set project $CERTO   e depois cole o bloco de novo."
     parou "o Cloud Shell esta no projeto \"$NOME\" ($PROJETO), que NAO e o Raio-X. Nada foi feito. Escolha o projeto Raio-X no topo do console e abra o Cloud Shell de novo." ;;
esac
FIREBASE="$(gcloud services list --enabled --project "$PROJETO" --filter='config.name=firebase.googleapis.com' --format='value(config.name)')" \
  || parou "nao consegui conferir o projeto $PROJETO. Nada foi feito. Me mande so a mensagem de erro acima."
[ -z "$FIREBASE" ] || parou "o projeto $PROJETO usa Firebase: parece o do Metodo AFP, nao o Raio-X. Nada foi feito."
echo "  projeto: $NOME ($PROJETO)"
EMAIL="$CONTA@$PROJETO.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$EMAIL" --project "$PROJETO" >/dev/null 2>&1 || parou "a conta ponte-render nao existe: cole o bloco do Passo 4."
umask 077
mkdir -p "$PASTA"
rm -f "$HOME/raio-x-chave-render.txt"
echo "1/3 Token da ponte..."
EXISTE="$(gcloud run services list --region "$REGIAO" --project "$PROJETO" --filter="metadata.name=$SERVICO" --format='value(metadata.name)')" \
  || parou "nao consegui ver as pontes do projeto $PROJETO. Me mande so a mensagem de erro acima."
TOKEN=""
URL=""
if [ -n "$EXISTE" ]; then
  TOKEN="$(openssl rand -hex 32)"
  gcloud run services update "$SERVICO" --region "$REGIAO" --project "$PROJETO" --update-env-vars "PONTE_TOKEN=$TOKEN" --quiet >/dev/null \
    || parou "o Google nao trocou o token. Me mande so a mensagem de erro acima."
  URL="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --project "$PROJETO" --format='value(status.url)')"
else
  echo "  a ponte esta apagada: o token novo sai quando voce colar o bloco do Passo 4"
fi
echo "2/3 Chave nova; as antigas deixam de valer agora..."
ANTIGAS="$(gcloud iam service-accounts keys list --iam-account "$EMAIL" --project "$PROJETO" --managed-by=user --format='value(name.basename())')"
rm -f "$PASTA/chave-nova.json"
gcloud iam service-accounts keys create "$PASTA/chave-nova.json" --iam-account "$EMAIL" --project "$PROJETO" --quiet >/dev/null \
  || parou "o Google nao criou a chave nova. Me mande so a mensagem de erro acima."
for K in $ANTIGAS; do
  gcloud iam service-accounts keys delete "$K" --iam-account "$EMAIL" --project "$PROJETO" --quiet >/dev/null \
    || parou "nao consegui apagar a chave antiga $K. Me mande so a mensagem de erro acima."
done
mv -f "$PASTA/chave-nova.json" "$PASTA/chave.json"
echo "3/3 Preparando para o Render..."
base64 -w0 "$PASTA/chave.json" > "$HOME/raio-x-chave-render.txt"
echo >> "$HOME/raio-x-chave-render.txt"
echo
echo "================ COPIE PARA O RENDER (NAO MANDE NO CHAT) ================"
if [ -n "$TOKEN" ]; then
  echo "RX_PONTE_BRASIL_URL   = $URL"
  echo "RX_PONTE_BRASIL_TOKEN = $TOKEN"
fi
echo "RX_PONTE_BRASIL_CHAVE = no arquivo raio-x-chave-render.txt (o navegador vai baixar agora)"
echo "=========================================================================="
if [ -n "$TOKEN" ]; then echo "O TOKEN E NOVO: troque RX_PONTE_BRASIL_URL e RX_PONTE_BRASIL_TOKEN nos DOIS servicos do Render."; fi
echo "A CHAVE E NOVA: troque RX_PONTE_BRASIL_CHAVE nos DOIS servicos do Render."
cloudshell download "$HOME/raio-x-chave-render.txt" 2>/dev/null \
  || echo "Se o download nao comecar: no Cloud Shell, clique nos tres pontinhos (More) > Download e escreva raio-x-chave-render.txt"
TROCA
```

Depois de trocar no Render, apague as cópias da chave como no fim do Passo 5.

Se vazar só o **endereço** da ponte: não precisa fazer nada. Ela está fechada.

---

### Referência técnica (para quem mantém)

- Código da ponte: `ponte_brasil/app.py` (só biblioteca padrão) e `ponte_brasil/Dockerfile`. Publicada com
  `--no-allow-unauthenticated` e `--service-account ponte-runtime@PROJETO.iam.gserviceaccount.com` (conta sem
  nenhum papel: se o contêiner for comprometido, não ganha o Editor que a conta padrão do Compute Engine
  costuma ter em projeto sem organização). A conta padrão do Compute Engine fica só para o build, com
  `roles/run.builder`. A conta `ponte-render@PROJETO.iam.gserviceaccount.com` tem só `roles/run.invoker` na
  ponte (não no projeto). O `X-Ponte-Token` continua obrigatório dentro da ponte.
- Teto de saída na ponte: `EGRESS_BYTES_PER_HOUR` = 1 GiB de corpo devolvido por hora (balde; em T horas, no
  máximo T + 1 GiB); acima, `429 egress_budget`. Pedidos e respostas pequenas não têm limite possível do lado
  de dentro (o Cloud Run cobra o pedido que chega ao contêiner, mesmo recusado).
- Trava de projeto em todos os blocos: ID começando com `raio-x` ou nome começando com `Raio-X`, e sem
  `firebase.googleapis.com` ligado; toda chamada do `gcloud` leva `--project` explícito (ou o projeto como
  argumento). Nenhum bloco muda o projeto padrão do Cloud Shell.
- Código revisado: `CODIGO_REVISADO` nos blocos PONTE e CONFERE são os hashes do git de `ponte_brasil/`
  (árvore), `br_bridge.py` e `scripts/ponte_brasil_conferir.py`, conferidos com
  `git rev-parse HEAD:<caminho>` depois do clone; qualquer mudança nesses arquivos exige atualizar os dois
  blocos (o gate confere).
- Cliente no Raio-X: `br_bridge.py`. Com `RX_PONTE_BRASIL_CHAVE` (JSON da chave, ou o JSON em base64), assina
  um JWT RS256 com `target_audience` = endereço da ponte, troca em `https://oauth2.googleapis.com/token` pelo
  token de identidade (cache de 1 hora, renovado 5 minutos antes, em segundo plano) e manda
  `X-Serverless-Authorization: Bearer ...` pela entrada padrão do curl. Assinatura pela biblioteca
  `cryptography` quando existe; senão em Python puro (o gate confere que as duas dão os mesmos bytes). O
  caminho em Python puro fica porque a conferência roda no `python3` do Cloud Shell, onde não verifiquei se
  a `cryptography` existe; a linha `chave: lida (assinatura ...)` da conferência mostra qual foi usada, e com
  essa medição dá para decidir se ele sai. Sem a chave, igual à versão anterior; sem as variáveis, igual a
  antes da ponte.
- Credenciais já emitidas: apagar a chave não revoga o token de identidade que ela já gerou (vale até 1 hora);
  por isso o roteiro de emergência começa apagando a ponte.
- Transportes: `deploy_app._curl`, `incra_acervo_f2.curl_fetch`, `incra_snci_public_v42._curl`,
  `sicar_detail_sources._curl` e `sicar_detail_sources_v2._curl`; `deploy_app.probe_sources` mede o INCRA
  pela ponte quando ela está ligada.
- Conferência no Cloud Shell: `scripts/ponte_brasil_conferir.py` (usa o mesmo `br_bridge`; nunca imprime
  segredo nem o endereço). Gate: `scripts/ponte_brasil_gate.py` (no `quality-gate.yml`), que confere a
  sintaxe dos blocos deste guia e **roda cada bloco com `gcloud`, `git`, `python3`, `openssl` e
  `cloudshell` falsos**, conferindo projeto, papel, recurso e membro de cada permissão, opções da publicação,
  `umask` da chave, ordem "conferência antes de apagar chaves", aviso de token novo e segredo fora da tela.
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
    overages are billed as normal."; "your actual cost details are typically available within a day, but can
    sometimes take more than 24 hours to process"; "For certain services, we can estimate directional costs
    faster than we can calculate the actual costs. For those services, the spend amount we use to enforce a
    spend cap budget is based on estimated gross costs, excluding savings. For the remaining services, we
    calculate the spend amount using actual costs, inclusive of all savings." (a página não diz em qual grupo
    está o Cloud Run, nem se a saída de rede entra); "If the cap was enforced and lifted within the same
    billing month, it won't trigger again for the rest of the month, unless you increase the target amount of
    the cap."
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
  - Service identity, <https://docs.cloud.google.com/run/docs/securing/service-identity>: "Depending on your
    organization policy configuration, the default service account might automatically be granted the Editor
    role on your project."
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
