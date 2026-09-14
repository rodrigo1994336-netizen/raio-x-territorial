# Ponte no Brasil — como ligar

**Para que serve.** O site do INCRA (acervo fundiário: SIGEF e SNCI) não responde ao nosso servidor, que fica
nos Estados Unidos (Render). Daqui do Brasil ele responde. A "ponte" é um programa pequeno no Google Cloud,
em São Paulo, que faz essa consulta por nós. Ela só aceita pedidos com uma senha (o **token**) e só busca em
dois endereços oficiais: `acervofundiario.incra.gov.br` e `geoserver.car.gov.br` (SICAR).

**Como o Raio-X usa.** INCRA vai sempre pela ponte. SICAR tenta direto, como hoje; só quando o SICAR não
responde ao Render a mesma consulta vai pela ponte, dentro do mesmo tempo de espera. Sem as duas variáveis
no Render, o sistema funciona exatamente como hoje.

**Antes de começar.** O código da ponte precisa estar na `main` do GitHub (o PR da ponte mesclado, com a sua
autorização). O bloco de comandos abaixo baixa o código de lá.

Tempo total: uns 20 minutos. Você vai precisar de um cartão para ativar o faturamento do Google (é exigência
do Google para usar o Cloud Run, mesmo na faixa grátis). **Eu não mexo em cartão nem em senha: essa parte é
sua.**

---

## Passo 1 — Criar o projeto no Google Cloud

1. Abra <https://console.cloud.google.com/projectcreate> e entre com a conta **rodrigo1994336@gmail.com**.
   Se o Google pedir para aceitar os termos, a decisão é sua.
2. Em **Project name** (nome do projeto), escreva: `raio-x-ponte`.
3. Em **Parent resource** / **Location**, deixe como está (conta pessoal não tem organização).
4. Clique em **Create**.
5. No topo da página, confira se o projeto selecionado é o `raio-x-ponte` (o seletor de projetos fica no
   alto, à esquerda). Se não for, clique nele e escolha `raio-x-ponte`.

## Passo 2 — Ativar o faturamento

1. Abra <https://console.cloud.google.com/billing>.
2. Se você ainda não tem uma conta de faturamento, o Google pede para criar uma: siga as telas dele e
   informe o cartão.
3. Na aba **My projects** (Meus projetos), ache `raio-x-ponte`, abra o menu **Actions** (três pontinhos),
   escolha **Change billing** e selecione a sua conta de faturamento. Clique em **Set account**.

## Passo 3 — Alerta de gasto de R$ 10

1. Abra <https://console.cloud.google.com/billing/budgets> e clique em **Create budget**.
2. **Name**: `ponte R$ 10`. Em **Scope**, deixe o período mensal e marque o projeto `raio-x-ponte`.
3. **Amount**: escolha **Specified amount** e escreva `10` (a moeda é a da sua conta: reais).
4. **Actions**: o Google já sugere avisos em 50%, 90% e 100%. Deixe assim; o aviso vai para o seu e-mail.
5. Clique em **Finish** (ou **Save**).

> O alerta **só avisa**; ele não bloqueia gasto. Quem limita de verdade é a própria ponte: no máximo uma
> máquina ligada, que desliga sozinha quando ninguém usa.

## Passo 4 — Abrir o Cloud Shell e colar UM bloco

1. Com o projeto `raio-x-ponte` selecionado no topo, clique em **Activate Cloud Shell** (Ativar o Cloud
   Shell), no alto da página. Um terminal abre na parte de baixo. Se o Google pedir autorização para o
   Cloud Shell usar suas credenciais, autorize.
2. Copie o bloco inteiro abaixo, cole no terminal e aperte **Enter**. Ele leva de 5 a 10 minutos.

```bash
bash <<'PONTE'
set -euo pipefail
REGIAO=southamerica-east1
SERVICO=ponte-brasil
PROJETO="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
if [ -z "$PROJETO" ]; then
  echo "PAROU: nenhum projeto selecionado. Escolha raio-x-ponte no topo do console e abra o Cloud Shell de novo."
  exit 1
fi
echo "Projeto: $PROJETO"
gcloud config set project "$PROJETO" --quiet >/dev/null
echo "1/6 Ligando Cloud Run, Cloud Build e Artifact Registry (1 a 2 minutos)..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --quiet
NUMERO="$(gcloud projects describe "$PROJETO" --format='value(projectNumber)')"
echo "2/6 Dando ao build a permissao de publicar (espera 1 minuto para valer)..."
gcloud projects add-iam-policy-binding "$PROJETO" \
  --member="serviceAccount:${NUMERO}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --condition=None --quiet >/dev/null
sleep 60
echo "3/6 Baixando o codigo publico do Raio-X..."
rm -rf "$HOME/raio-x-ponte"
git clone --quiet --depth 1 https://github.com/rodrigo1994336-netizen/raio-x-territorial.git "$HOME/raio-x-ponte"
test -f "$HOME/raio-x-ponte/ponte_brasil/app.py" || { echo "PAROU: a pasta ponte_brasil ainda nao esta na main."; exit 1; }
echo "4/6 Token: reaproveita o da ponte que ja existe; senao gera um novo..."
TOKEN="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --format=json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((e.get("value","") for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env",[]) if e.get("name")=="PONTE_TOKEN"),""))' 2>/dev/null || true)"
[ -n "$TOKEN" ] || TOKEN="$(openssl rand -hex 32)"
echo "5/6 Publicando a ponte em Sao Paulo (3 a 6 minutos)..."
gcloud run deploy "$SERVICO" --source "$HOME/raio-x-ponte/ponte_brasil" --region "$REGIAO" \
  --max-instances 1 --min-instances 0 --memory 256Mi --allow-unauthenticated \
  --set-env-vars "PONTE_TOKEN=$TOKEN" --quiet
URL="$(gcloud run services describe "$SERVICO" --region "$REGIAO" --format='value(status.url)')"
echo "6/6 Conferindo a ponte..."
echo "  saude: $(curl -s --max-time 40 "$URL/healthz" || echo sem_resposta)"
for ALVO in \
  "INCRA|https://acervofundiario.incra.gov.br/i3geo/ogc.php?tema=certificada_sigef_particular_mg&service=WFS&request=GetCapabilities" \
  "SICAR|https://geoserver.car.gov.br/geoserver/sicar/ows?service=WFS&version=1.0.0&request=GetCapabilities"; do
  NOME="${ALVO%%|*}"; ENDERECO="${ALVO#*|}"
  CODIGO="$(printf 'X-Ponte-Token: %s\nX-Ponte-Url: %s\n' "$TOKEN" "$ENDERECO" \
    | curl -s -o /dev/null -w '%{http_code}' --max-time 40 -H @- "$URL/v1/fetch" || true)"
  if [ "$CODIGO" = "200" ]; then echo "  $NOME: respondeu pela ponte"; else echo "  $NOME: nao respondeu agora (codigo $CODIGO). A ponte esta no ar; tente de novo mais tarde."; fi
done
echo
echo "================ COPIE PARA O RENDER (NAO MANDE NO CHAT) ================"
echo "RX_PONTE_BRASIL_URL   = $URL"
echo "RX_PONTE_BRASIL_TOKEN = $TOKEN"
echo "=========================================================================="
PONTE
```

3. No fim aparecem duas linhas: `RX_PONTE_BRASIL_URL` e `RX_PONTE_BRASIL_TOKEN`. Deixe o Cloud Shell aberto
   para copiar no próximo passo.

> **O token é uma senha.** Cole direto no Render. Não mande no chat, no WhatsApp nem por e-mail. Se o
> bloco for colado de novo, ele reaproveita o mesmo token (não precisa mexer no Render outra vez).

Se aparecer `PAROU:` ou uma mensagem de erro em vermelho, copie **só a mensagem de erro** (sem o token) e me
mande.

## Passo 5 — Colocar as duas variáveis no Render (nos DOIS serviços)

O INCRA e o SICAR são consultados pelo site **e** pelo gerador de relatório. Os dois precisam das variáveis:

- `raio-x-territorial-app` (o site)
- `raio-x-territorial-report` (o relatório em PDF)

Para cada um dos dois:

1. Abra <https://dashboard.render.com> e clique no serviço.
2. No menu da esquerda, clique em **Environment**.
3. Clique em **+ Add Environment Variable**. Em **Key**, escreva `RX_PONTE_BRASIL_URL`; em **Value**, cole a
   URL que o Cloud Shell mostrou (começa com `https://`).
4. Clique de novo em **+ Add Environment Variable**. **Key**: `RX_PONTE_BRASIL_TOKEN`; **Value**: cole o
   token.
5. Clique para salvar e escolha **Save, rebuild, and deploy** (publica a versão mais nova da `main` já com as
   variáveis). Se escolher **Save only**, as variáveis só valem no próximo deploy.

## Passo 6 — Conferir

1. No Render, em cada serviço, abra **Logs** e procure por `RX_PONTE_BRASIL`. Tem que aparecer
   `RX_PONTE_BRASIL=on host=... incra=ponte sicar=direto_primeiro`.
   - `RX_PONTE_BRASIL=off motivo=configuracao_invalida`: a URL não começa com `https://` ou o token foi
     colado pela metade. Corrija no **Environment**.
   - `RX_PONTE_BRASIL=erro_ponte http=401`: o token do Render não é o da ponte. Cole de novo.
2. No site, abra o imóvel de prova (Curvelo `MG-3120904-DFB380BECD7A4323AD8AA68FA14D011F`). A referência
   INCRA (SIGEF/SNCI) deve aparecer sem "consulta pendente". Se a fonte oficial estiver fora do ar, continua
   "consulta pendente" — isso é correto, não é defeito da ponte.

---

## Quanto custa (honesto)

Preços da página oficial do Google Cloud, conferidos em 14/09/2026:

- **Cloud Run**: a faixa grátis mensal é de 2 milhões de pedidos, 180.000 vCPU-segundos e 360.000
  GiB-segundos por conta de faturamento. O Google aplica essa faixa como desconto calculado pelo preço das
  regiões mais baratas ("Tier 1"); São Paulo é "Tier 2" (um pouco mais cara por segundo), então a faixa grátis
  cobre um pouco menos tempo lá. Para o nosso uso (algumas centenas a poucos milhares de consultas por mês,
  cada uma de 1 a 3 segundos, máquina de 256 MiB que desliga sozinha), fica dentro da faixa grátis.
- **Saída de rede** (os dados que a ponte devolve ao Render): cobrada pela tabela "Premium Tier" do Google,
  na faixa de US$ 0,12 a US$ 0,19 por GB. Uma leitura do INCRA tem de alguns KB a centenas de KB. Estimativa:
  1.000 relatórios por mês ≈ 0,4 GB ≈ **menos de R$ 1**. Num mês em que o SICAR ficar muito tempo sem
  responder ao Render, o mapa passa pela ponte e pode chegar a **alguns reais**.
- **Cloud Build** (monta a ponte a cada publicação): 2.500 minutos grátis por mês; cada publicação usa uns
  3 minutos.
- **Artifact Registry** (guarda a imagem da ponte): 0,5 GB grátis; cada publicação guarda ~50 MB. Acima
  disso, centavos por mês.

Resumo: normalmente **R$ 0 a R$ 1 por mês**. O alerta de R$ 10 avisa bem antes de qualquer surpresa.

## Como desligar

- **Parar de usar, sem apagar nada**: no Render, em cada serviço, **Environment**, apague
  `RX_PONTE_BRASIL_URL` e `RX_PONTE_BRASIL_TOKEN` e salve com deploy. O Raio-X volta a consultar direto,
  exatamente como antes. A ponte parada não gasta (desliga sozinha sem pedidos).
- **Apagar a ponte**: no Cloud Shell, `gcloud run services delete ponte-brasil --region southamerica-east1`.
  Apagar é definitivo; para voltar, basta colar o bloco do passo 4 de novo (gera um token novo, que precisa ir
  para o Render).
- **Zerar tudo no Google**: em <https://console.cloud.google.com/cloud-resource-manager>, selecione
  `raio-x-ponte` e use **Shut down** (desligar o projeto). O Google apaga o projeto depois de 30 dias.

## Se o token vazar

Cole no Cloud Shell (troca o token da ponte e mostra o novo), depois atualize `RX_PONTE_BRASIL_TOKEN` nos dois
serviços do Render:

```bash
NOVO="$(openssl rand -hex 32)"
gcloud run services update ponte-brasil --region southamerica-east1 --update-env-vars "PONTE_TOKEN=$NOVO" --quiet
echo "RX_PONTE_BRASIL_TOKEN = $NOVO"
```

---

### Referência técnica (para quem mantém)

- Código da ponte: `ponte_brasil/app.py` (só biblioteca padrão) e `ponte_brasil/Dockerfile`.
- Cliente no Raio-X: `br_bridge.py`, usado pelos transportes `deploy_app._curl`, `incra_acervo_f2.curl_fetch`,
  `incra_snci_public_v42._curl`, `sicar_detail_sources._curl` e `sicar_detail_sources_v2._curl`.
- Gate: `scripts/ponte_brasil_gate.py` (no `quality-gate.yml`).
- Chamada: `GET|POST {URL}/v1/fetch` com `X-Ponte-Token`, `X-Ponte-Url` (endereço oficial) e
  `X-Ponte-Timeout` opcional (segundos, até 25). Recusa da ponte vem com `X-Ponte-Error`.
- Fontes consultadas: Cloud Run pricing, Network pricing, Cloud Build pricing, Artifact Registry pricing,
  "Deploy services from source code", "Create, edit, or delete budgets and budget alerts" (Google Cloud) e
  "Environment variables" (Render), todas em 14/09/2026.
