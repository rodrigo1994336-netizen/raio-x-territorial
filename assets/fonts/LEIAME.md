# Fontes do relatório-livro v10

Oito arquivos estáticos das releases oficiais da Adobe, copiados **sem modificação**:

- Source Serif 4, versão 4.005R (`adobe-fonts/source-serif`, `source-serif-4.005_Desktop.zip`):
  SmText Regular, Semibold e It; Display Regular, Semibold e It.
- Source Sans 3, versão 3.052R (`adobe-fonts/source-sans`, `TTF-source-sans-3.052R.zip`):
  Regular e Semibold.

Licença: SIL Open Font License 1.1, © Adobe, com Reserved Font Name "Source"
(`OFL-SourceSerif4-Adobe-LICENSE.md` e `OFL-SourceSans3.txt`).

Regras:

- Não subsetar, converter nem instanciar estes TTF: arquivo modificado não pode usar o nome
  "Source". O reportlab subseta só dentro do PDF.
- Trocar um arquivo exige atualizar `SHA256SUMS` no mesmo commit
  (`sha256sum *.ttf > SHA256SUMS`); `report_book_v10_style.conferir_fontes()` e o teste
  `tests/book/test_style.py` conferem.
- Sem estes arquivos o motor v10 não desenha (`BookAssetsMissing`); não há queda para
  Helvetica ou Times.
