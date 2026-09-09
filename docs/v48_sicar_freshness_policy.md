# V48 — Política de atualidade e proveniência SICAR

## Regra canônica por UF

- Cada UF usa a data mais recente em que as oito tabelas obrigatórias possuem partição e linhas para aquela UF.
- Não existe fallback por CAR nem mistura de datas entre mapa e análise.
- O critério 8/8 permanece obrigatório até decisão expressa em contrário.

## Transparência para o cliente

A data e a idade do snapshot aparecem sempre:

`Base do CAR de São Paulo: 02/06/2026 · atualizada há 99 dias`

Quando a idade superar 60 dias, acrescentar exatamente a linha neutra:

`Esta base está mais antiga que a das demais unidades da federação.`

O limiar de 60 dias foi escolhido porque o ciclo observado nas demais UFs está em aproximadamente 36–39 dias; superar 60 dias indica perda de pelo menos um ciclo observado.

O painel deve oferecer `Ver auditoria · datas das bases por estado`, exibindo as datas e idades das 27 UFs, não apenas a UF do imóvel consultado.

## São Paulo — decisão registrada em 09/09/2026

- Snapshot canônico aprovado: 02/06/2026, com cobertura 8/8.
- SP possui 0/8 em 01/08/2026, 02/08/2026, 03/08/2026 e 04/08/2026.
- Portanto a defasagem não decorre de uma classe temática rara: São Paulo não foi incluído nas quatro extrações de agosto presentes no espelho da Base dos Dados.
- Manter o snapshot completo de 02/06/2026 é preferível a marcar SP como indisponível.

## Item futuro — não implementar agora

Se a idade do snapshot canônico de São Paulo ultrapassar 120 dias, reabrir explicitamente a decisão de fonte e avaliar ingestão de São Paulo diretamente do SICAR oficial, em vez de depender exclusivamente do espelho da Base dos Dados.

Esse limiar de 120 dias é apenas um gatilho de revisão arquitetural. Não autoriza troca automática de fonte, ingestão nova, deploy ou mudança do critério 8/8.
