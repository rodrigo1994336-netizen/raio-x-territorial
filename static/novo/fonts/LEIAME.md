# Fontes da tela nova (/novo)

Três arquivos copiados **sem modificação** de `assets/fonts` da branch `r2/livro-v10` (releases
oficiais da Adobe): Source Serif 4 Display Semibold (títulos), Source Sans 3 Regular e Semibold
(texto e botões).

Licença: SIL Open Font License 1.1, © Adobe, com Reserved Font Name "Source"
(`OFL-SourceSerif4-Adobe-LICENSE.md` e `OFL-SourceSans3.txt`).

Regras:

- Não subsetar, converter nem instanciar estes TTF: arquivo modificado não pode usar o nome
  "Source". O servidor só comprime em gzip na entrega, o arquivo é o mesmo.
- `SHA256SUMS` confere os três arquivos; `scripts/o2_tela_nova_gate.py` falha se algum mudar.
