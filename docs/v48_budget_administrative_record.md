# V48 — Registro administrativo de orçamento

Registro declarado pelo responsável do projeto e verificado manualmente no Google Cloud Console em 08/09/2026.

## Orçamento

- Nome: `Raio-X Territorial`
- Periodicidade: mensal
- Valor: US$ 20
- Alertas: 50%, 90% e 100%
- Ação: somente alertas; não há desligamento automático autorizado por este registro
- Escopo: projeto `metodo-afp-plataforma`

## Limite da automação

A service account deliberadamente não possui permissões de Budget API para listar, criar ou alterar orçamentos. Por isso, workflows V48 não devem tratar a existência desse orçamento como invariante verificável pela service account.

O `V48 Budget Gate Probe` verifica apenas invariantes que a credencial atual consegue provar sem ampliar IAM: faturamento ativo, conta de faturamento vinculada e projeto resolvido.

Este arquivo é evidência administrativa, não substitui uma leitura futura do Console nem autoriza aumento de IAM.
