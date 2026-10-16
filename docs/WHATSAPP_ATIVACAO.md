# WhatsApp e monitoramento — como ligar quando houver usuários

Estado em 13/09/2026: **desligado** (`RX_WHATSAPP_ENABLED` ausente = off). Com ele
desligado o servidor não envia nenhuma mensagem e não processa webhook.

O que já está pronto e testado (`scripts/w1_whatsapp_ready_gate.py`, roda no quality-gate):

- webhook só aceita mensagem assinada pela Meta (`X-Hub-Signature-256` com o app secret);
  ligado sem o secret, recusa tudo (503);
- a mesma mensagem reenviada pela Meta não é respondida duas vezes;
- limite de 20 mensagens a cada 10 minutos por número;
- resposta à Meta imediata; a consulta roda depois;
- link colado na conversa só é seguido se for Google Maps (nunca outro endereço);
- monitoramento não alerta quando uma fonte não respondeu, nem quando só muda o nome do
  arquivo de focos do INPE;
- alerta fora da janela de 24 h vai por modelo aprovado (template);
- telefone nunca sai pela API; número de WhatsApp só é cadastrado pela própria conversa.

## Antes de ligar (decisões e bloqueios)

1. **Um assinante por imóvel.** Hoje a tabela de monitoramento guarda um destino por CAR:
   se duas pessoas monitorarem o mesmo imóvel, a segunda substitui a primeira. Resolver
   junto com login/contas (cada monitor pertence a uma conta).
2. **Banco durável.** O monitoramento precisa de `DATABASE_URL` (Postgres). O Key Value
   gratuito do Render perde os dados ao reiniciar.
3. **Central de alertas na web** continua desligada até existir login: sem conta, a lista
   de alertas seria de todos.
4. **Custo Meta:** conversa iniciada pela empresa (alerta) é cobrada por mensagem de modelo.

## Passo a passo (quem tiver acesso à conta Meta Business)

1. business.facebook.com → criar/usar a conta da empresa → WhatsApp → adicionar número.
2. developers.facebook.com → app do tipo Business → produto WhatsApp.
3. Anotar: **Phone number ID**, **App secret** (Configurações do app → Básico) e gerar um
   **token permanente** de usuário do sistema com `whatsapp_business_messaging`.
4. Criar o modelo de alerta (categoria Utilidade, idioma pt_BR), com três variáveis:
   `Raio-X Territorial: alerta no imóvel CAR {{1}}. {{2}}. Abra o Raio-X: {{3}}`
   e aguardar aprovação.
5. No Render, serviço `raio-x-territorial-app`, variáveis (com autorização do dono):
   - `RX_WHATSAPP_ENABLED=on`
   - `WHATSAPP_APP_SECRET`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`
   - `WHATSAPP_VERIFY_TOKEN` (texto aleatório longo, inventado por nós)
   - `WHATSAPP_ALERT_TEMPLATE=<nome do modelo>` e `WHATSAPP_ALERT_TEMPLATE_LANG=pt_BR`
   - `RX_PUBLIC_BASE_URL=https://raioxterritorial.com.br`
   - `DATABASE_URL` do Postgres
6. Na Meta, Webhook: URL `https://raioxterritorial.com.br/webhooks/whatsapp`, token = o
   `WHATSAPP_VERIFY_TOKEN`; assinar o campo `messages`.
7. Conferir `GET /v1/whatsapp/status` → `enabled: true`, `configured: true`.
8. Monitoramento automático: acrescentar `schedule` (cron) em
   `.github/workflows/monitoring-15min.yml`, que hoje só roda manualmente.
9. Teste real: mandar "menu" e um código CAR do próprio celular; mandar "monitorar".
