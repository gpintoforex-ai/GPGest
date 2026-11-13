# Descritivos dos artigos para o site (IA com revisão)

A IA (Claude, o mesmo serviço do assistente) propõe o conteúdo de cada artigo para a loja online: **descrição**,
**resumo**, **pontos-chave**, **título SEO** e **meta-descrição**. Nada é publicado sem revisão.

## Onde

- **Ficha do artigo → Descrição e anexos → Conteúdo para o site:** «Gerar descritivo com IA» mostra a proposta, as
  frases a confirmar e a informação em falta. «Usar no formulário» passa a proposta para os campos da ficha; o texto
  só fica gravado quando se grava o artigo (a proposta passa a «Aplicada na ficha»). «Descartar» não muda nada.
- **Artigos e validades → Descritivos do site (lote):** lista os artigos ativos sem descrição (os serviços ficam de
  fora) e as propostas por rever. Selecionam-se até 25 artigos e «Gerar propostas» trata-os um de cada vez, com
  progresso e «Parar». Cada proposta abre-se em «Rever», edita-se e aprova-se com «Aprovar e gravar na ficha» (grava
  pela gravação normal da ficha, com controlo de revisão), ou gera-se outra ou descarta-se.

Os dois sítios exigem a permissão de editar artigos.

## O que a IA usa e o que não faz

- **Fontes:** nome, código, marca, família, categoria, unidade e códigos de barras da ficha, e até quatro imagens do
  artigo por HTTPS (fotografia e imagens adicionais: rótulo, embalagem). Se uma imagem não puder ser lida, repete
  só com a ficha e indica-o. Custos, preços, fornecedores e observações internas nunca são enviados.
- **Regras:** português de Portugal; só factos da ficha ou das imagens; nunca inventa composição, posologia, modo de
  utilização, certificações nem benefícios; **alegações de saúde** só como estão no rótulo e sempre assinaladas em
  «Confirme antes de publicar»; advertências do rótulo incluídas e assinaladas; sem preços, stock nem promoções.
- **Responsabilidade:** produtos de saúde e suplementos têm regras próprias para alegações; quem aprova confirma o
  texto antes de o publicar.

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
