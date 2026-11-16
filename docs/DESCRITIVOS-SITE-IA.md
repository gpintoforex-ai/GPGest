# Descritivos dos artigos para o site (IA com revisão)

A IA (Claude, o mesmo serviço do assistente) propõe o conteúdo de cada artigo para a loja online: **descrição**,
**resumo**, **pontos-chave**, **título SEO** e **meta-descrição**. Nada é publicado sem revisão.

## Onde

- **Ficha do artigo → Descrição e anexos → Conteúdo para o site:** «Gerar descritivo com IA» mostra a proposta, as
  frases a confirmar e a informação em falta. «Usar no formulário» passa a proposta para os campos da ficha; o texto
  só fica gravado quando se grava o artigo (a proposta passa a «Aplicada na ficha»). «Descartar» não muda nada.
- **Artigos e validades → Descritivos do site (lote):** lista os artigos ativos sem descrição (os serviços ficam de
  fora) e as propostas por rever. Selecionam-se até 25 artigos e «Gerar propostas» trata-os um de cada vez, com
  progresso e «Parar». A coluna das imagens indica os artigos que já têm fotografia do rótulo. Cada proposta abre-se em «Rever», edita-se e aprova-se com «Aprovar e gravar na ficha» (grava
  pela gravação normal da ficha, com controlo de revisão), ou gera-se outra ou descarta-se.

Os dois sítios exigem a permissão de editar artigos.

## O que a IA usa e o que não faz

- **Fontes:** nome, código, marca, família, categoria, unidade e códigos de barras da ficha; a **fotografia do
  rótulo** e a fotografia carregadas na ficha (ver [fotografias dos artigos](FOTOGRAFIAS-ARTIGOS.md)); e até quatro
  imagens do artigo por HTTPS (fotografia e imagens adicionais). O rótulo é a fonte mais fiável: a IA lê
  ingredientes, quantidades por dose, dose diária, modo de utilização, advertências e conservação, transcreve
  números e unidades tal como estão e indica o que estiver ilegível em «Informação em falta». Um rótulo de outro
  produto não é usado e fica assinalado. Se uma imagem por HTTPS não puder ser lida, repete sem ela (mantendo as
  carregadas) e indica-o. Custos, preços, fornecedores e observações internas nunca são enviados.
- **Regras:** português de Portugal; só factos da ficha ou das imagens; nunca inventa composição, posologia, modo de
  utilização, certificações nem benefícios; **alegações de saúde** só como estão no rótulo e sempre assinaladas em
  «Confirme antes de publicar»; advertências do rótulo incluídas e assinaladas; sem preços, stock nem promoções.
- **Responsabilidade:** produtos de saúde e suplementos têm regras próprias para alegações; quem aprova confirma o
  texto antes de o publicar.

## SEO (Rank Math)

- **Palavra-chave principal** (`seo_keyword`, campo da ficha em «Conteúdo para o site»): a IA propõe-a (2 a 4 palavras,
  como os clientes pesquisam: tipo de produto e ingrediente ou característica principal) a partir de factos da ficha
  ou do rótulo.
- A IA põe a palavra-chave no início do título SEO, na meta-descrição e na primeira frase da descrição, e repete-a
  com naturalidade. A descrição pode ter secções com subtítulos (linha «## Composição», «## Modo de utilização»…),
  só com factos confirmados; as advertências obrigatórias entram na secção «## Advertências». Até 2000 caracteres.
- Na loja: subtítulos em H2, ligação interna para a categoria da família, campos do Rank Math e texto alternativo
  da fotografia (ver [publicação online](PUBLICACAO-ONLINE.md)).
- **Não seguimos** três sugestões genéricas do Rank Math: 600 palavras (numa página de produto seria texto de
  enchimento, contrário à regra de não inventar), ligações externas DoFollow (levam o cliente para fora da loja) e
  índice de conteúdos (não se aplica a páginas de produto).

## Advertências obrigatórias (texto fixo)

Em **Descritivos do site → Advertências obrigatórias** define-se um título e um texto fixo da empresa e as famílias a que
se aplicam. Por omissão: «Advertências» e «Suplemento Alimentar. Não deve exceder a toma diária indicada. Os
suplementos alimentares não devem ser utilizados como substitutos de uma alimentação variada. É importante ter um
regime alimentar equilibrado e um modo de vida saudável. Manter fora do alcance das crianças. Conservar em local
fresco e seco.», nas famílias com «suplement» no nome.

- Em cada proposta de um artigo dessas famílias, a IAZELA acrescenta o texto palavra por palavra (não é a IA que o
  escreve; se a descrição já o tiver, não se repete): no fim da secção com o mesmo subtítulo («## Advertências», onde a
  IA põe as advertências do rótulo) ou, sem ela, numa secção nova no fim. A proposta indica que o inclui.
- A IA é informada (`avisos_obrigatorios_automaticos`) e não escreve estes avisos gerais; continua a escrever as
  advertências próprias do rótulo (grávidas, lactantes, crianças, medicação, alergénios), assinaladas para confirmar.
- Aplica-se às próximas propostas; descrições já gravadas não mudam (gere uma proposta nova para as atualizar).
- Alterações com controlo de versão e auditoria (`descritivo_avisos_alterados`). API:
  `GET/PUT /api/stock/web-content/settings` (`{title, text, families, revision}`).

## Site e auditoria

- A loja online recebe pelo catálogo (`GET /api/webshop/catalog`) os campos `description`, `summary`, `highlights`,
  `seo_title` e `seo_description` de cada artigo.
- Auditoria: `artigo_descritivo_ia_gerado` (modelo e tokens), `artigo_descritivo_aprovado` (antes/depois e se foi
  editado), `artigo_descritivo_descartado`, e a edição normal da ficha.

## Configuração e custo

- Exige `ANTHROPIC_API_KEY` no `.env` do servidor (local e produção). Sem chave, os botões explicam o que falta e
  nada é enviado.
- Cada proposta é um pedido ao modelo `claude-opus-5-5` (esforço médio, saída estruturada, reencaminhamento
  automático para outro modelo se o pedido for recusado) e gasta créditos da conta Anthropic.

API: `GET/POST /api/stock/articles/{id}/web-content`, `POST …/web-content/approve` (`{campos, revision}`),
`POST …/web-content/discard`, `GET /api/stock/web-content?state=sem_descritivo|por_rever`. Migração **0093**
(`article_content_proposals`). Testes: `backend/tests/test_article_content.py` (IA simulada) e
`scripts/test-article-web-content-ui.py`.
