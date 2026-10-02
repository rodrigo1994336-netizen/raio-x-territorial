# Captador Rádio — Escala Fácil

Aplicativo Android dedicado para ficar próximo à rede rádio durante o turno.

## O que faz

- mantém a captura de voz ativa em **Foreground Service**;
- funciona com a tela bloqueada;
- usa reconhecimento de voz do Android em pt-BR;
- salva toda comunicação finalizada em SQLite local;
- extrai natureza, local, referência e prioridade por regras locais;
- envia cada comunicação para a entidade **FilaRadio** do app Base44 **Escala Fácil**;
- se a internet ou autenticação falhar, guarda localmente e reenvia depois;
- não mantém a tela acesa.

## Integração Base44

App ID já configurado:

`6ac00e0b1b497c55d2b6762e`

O usuário faz login no próprio Captador Rádio. O app chama:

`POST https://base44.app/api/apps/<APP_ID>/auth/login`

e depois envia os registros para:

`POST https://base44.app/api/apps/<APP_ID>/entities/FilaRadio`

## Uso

1. Instale o APK no Android dedicado.
2. Abra e permita **Microfone** e **Notificações**.
3. Informe a companhia padrão (ex.: `220ª Cia`).
4. Faça login com o usuário do Escala Fácil/Base44.
5. Toque em **INICIAR ESCUTA**.
6. Pode bloquear a tela; a notificação persistente indica que o captador segue ativo.
7. No Escala Fácil, as transmissões entram na Fila Rádio para atribuição a recurso principal e apoios.

## Observação operacional

O reconhecimento depende do serviço de voz disponível no aparelho. Para uso diário, deixe o aparelho dedicado ligado à alimentação e posicionado próximo ao alto-falante do rádio.
