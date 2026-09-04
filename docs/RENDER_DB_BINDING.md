# Raio-X Territorial — vínculo do Postgres gratuito no Render

Objetivo: ativar persistência de monitoramento sem mudar de plano.

## Serviço
- Web Service: `raio-x-territorial-app`
- Banco existente: `raio-x-territorial-db`
- Região: Virginia

## Passo único de infraestrutura
No Render Dashboard, abra `raio-x-territorial-app` → **Environment**.

Crie a variável:

- Key: `DATABASE_URL`
- Value: use a **Internal Database URL** do banco `raio-x-territorial-db`.

Não use a External Database URL quando o serviço e o banco estiverem no mesmo workspace/região.

## Driver PostgreSQL
O build atual do serviço é:

`pip install fastapi uvicorn httpx shapely pyproj reportlab pillow`

Altere para:

`pip install fastapi uvicorn httpx shapely pyproj reportlab pillow "psycopg[binary]"`

Depois salve. O Render fará redeploy.

## Validação
Nos logs devem aparecer:

- `RX_PERSISTENCE_BINDING=yes`
- `RX_POSTGRES_DRIVER=psycopg`
- `RX_PORTAL_V8_EXTENSION=loaded`

No portal, a seção Monitoramento deve mudar para:

`PERSISTENTE — PRONTO`

A rota `/v1/monitoring/status` deve reportar `persistence: durable`.

## O que o sistema cria automaticamente
Na primeira ativação de monitoramento:

- `rx_monitors`
- `rx_monitor_snapshots`
- `rx_monitor_alerts`
- `rx_monitor_runs`

Não é necessário executar SQL manualmente.

## Agenda
O workflow `.github/workflows/monitoring-15min.yml` chama o motor a cada 15 minutos.

Para produção, recomenda-se cadastrar o secret de GitHub `RX_MONITOR_TOKEN` e a mesma variável no Render. Sem token, há cooldown interno de 10 minutos contra abuso.

## Custo
Este desenho usa o Postgres Free já existente e não exige upgrade imediato. Reavaliar capacidade/retention antes do vencimento do banco gratuito ou quando houver clientes pagantes.
