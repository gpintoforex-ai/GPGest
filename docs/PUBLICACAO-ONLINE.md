# Publicação online (catálogo IAZELA → Raizpura)

O IAZELA é o sistema central do catálogo publicado na loja online. Em **Integrações → Publicação online**
(administrador ou gestor da loja da ligação) escolhe os artigos que vão para a Raizpura, o SKU e o preço do
canal. A loja recebe só o que mudou e devolve o resultado de cada artigo. Requer a migração **0095** e o
plugin **Raizpura Commerce 1.3** ou posterior.

## O que se gere

| Coluna | Conteúdo |
|---|---|
| Artigo | Nome e códigos: IAZELA, Wintouch (quando o artigo está ligado ao ERP) e ID do produto WooCommerce |
| SKU / EAN | SKU na loja (por omissão o código Wintouch, ou o código IAZELA) e o EAN/GTIN válido da ficha |
| Preço | Preço com IVA, preço sem IVA e taxa; «preço do canal» quando o canal tem preço próprio |
| Margem | (preço do canal sem IVA − custo médio) ÷ preço sem IVA; só para quem pode ver custos |
| Estado de publicação | Publicado ou retirado, e o estado da sincronização: Sincronizado, A aguardar a loja, Erro ou Bloqueado. Mostra também o estado na loja (por exemplo, rascunho), a informação alimentar em falta e o conteúdo editado na loja que foi preservado |
| Última sincronização | Momento da última confirmação da loja |
| Erros | Motivo do bloqueio ou o erro devolvido pela loja, com a hora da nova tentativa |
| Ações | **Publicar**, **Atualizar** (envia de novo o conteúdo atual), **Retirar da loja**, **SKU e preço**, **Histórico** |

**Adicionar artigos à loja** procura por código, nome ou EAN os artigos ativos ainda não publicados.

## O que é enviado

Nome, descrição, resumo e pontos-chave, SEO, família (categoria principal), categoria (subcategoria da
família), marca, códigos de barras e GTIN, unidade, preço sem IVA, taxa de IVA, preço com IVA, imagens
(fotografia e fotografias adicionais da ficha) e o estado de publicação. Para artigos ligados à Wintouch, o
nome, o código e o preço base vêm do artigo Wintouch, como no catálogo existente.

O **preço do canal** substitui o preço base só nesta loja. Sem ele, usa-se o preço de venda base do artigo.

Um artigo publicado fica **bloqueado** (e a loja recebe a retirada) quando está inativo, é um serviço, tem casas
decimais na unidade, ou não tem preço (zero ou desconhecido) nem taxa de IVA. Preço zero nunca é tratado como
oferta.

## Suplementos alimentares

A informação alimentar obrigatória (ingredientes e alergénios, quantidade, dose diária, substâncias por dose,
condições de utilização, conservação, operador responsável, imagem do rótulo, revisão e verificação da
notificação) é preenchida e revista na loja, por produto. A sincronização **nunca a altera**. Um produto novo
fica em rascunho; só fica à venda quando essa informação está completa e revista. Quando o nome, a descrição, o
resumo, a marca, a família, a categoria ou a unidade mudam no IAZELA, a revisão volta a ser exigida e o produto
volta a rascunho. O que falta aparece na coluna **Estado de publicação**.

## Conteúdo próprio da loja

Descrições (longa e curta) e imagens editadas na loja são preservadas: a loja compara com o último texto que
recebeu do IAZELA e, se foi alterado na loja, não o substitui e comunica-o («conteúdo editado na loja
preservado»). Num produto existente associado, as descrições e imagens que já tinha também se mantêm. Com a
opção de preços desligada no plugin, o preço também fica como está na loja.

## Correspondências e duplicados

- Cada artigo, SKU e produto WooCommerce só pode estar numa publicação por ligação (restrições na base de
  dados). Publicar duas vezes o mesmo artigo, repetir um SKU ou associar um produto já usado é recusado.
- A loja comunica de hora a hora os seus produtos simples (ID, SKU, GTIN, nome, estado). Com isso:
  - um produto criado por esta ligação para um artigo (tem o identificador do artigo) é reconhecido por
    identidade exata; se estiver à venda, passa a ser gerido como publicação;
  - os restantes aparecem em **Produtos da loja sem associação**, com sugestões pelo mesmo SKU ou EAN. Só o
    operador associa. Vários artigos candidatos, o mesmo EAN em vários produtos da loja ou um artigo já
    publicado noutro produto ficam marcados e nunca se resolvem sozinhos.
- Publicar um artigo cujo SKU já existe na loja sem associação é recusado: associe o produto existente. Com o
  mesmo EAN, é pedida a confirmação explícita para criar um produto novo (associar é o recomendado).
- Na loja, um produto só é atualizado quando tem o identificador do artigo desta ligação, ou quando foi
  associado no IAZELA; um produto de outro artigo nunca é reaproveitado.

## Sincronização incremental e falhas

A loja pede as publicações cujo conteúdo ainda não confirmou (até 50 por pedido, cinco pedidos por ciclo),
aplica-as e devolve o resultado. Cada publicação tem uma impressão digital do conteúdo; só é enviada de novo
quando algo publicado muda (incluindo a ficha do artigo). Um resultado de um envio antigo nunca anula uma
confirmação mais recente.

Um erro fica registado e a nova tentativa é agendada (1, 5, 15, 60, 180 e depois 360 minutos). **Atualizar**
envia de imediato. Se a loja aplicou o artigo mas a resposta se perdeu, o envio seguinte atualiza o mesmo
produto (a identidade é o artigo IAZELA gravado no produto). O stock dos artigos publicados é atualizado em cada
ciclo, com o disponível do armazém da ligação (sem reservas nem lotes expirados).

**Retirar da loja** deixa o produto em rascunho e sem stock; não apaga o produto, as encomendas, a informação
alimentar nem o histórico.

## Histórico e auditoria

O histórico de cada publicação regista publicação, retirada, alterações de SKU/preço (antes e depois),
associações, pedidos de atualização e cada resultado da loja (sucesso ou erro). As ações do operador ficam
também na auditoria geral.

## Limitações

- Produtos com variações não são suportados.
- A classe de imposto do WooCommerce não é alterada: a taxa de IVA vai para o produto como informação
  (`_rp_gpgest_vat_rate`). Validar as classes fiscais e a opção de preços com/sem IVA antes de ativar os preços.
- O SEO é guardado no produto (`_rp_gpgest_seo_title`, `_rp_gpgest_seo_description`); a sua apresentação
  depende do tema ou do plugin de SEO, que não são alterados.
- As encomendas do site continuam pelo circuito existente (ver `INTEGRACAO-RAIZPURA.md`).
- O catálogo antigo (`GET /api/webshop/catalog`) mantém-se para compatibilidade, mas o plugin 1.3 deixa de o usar:
  os artigos não publicados no IAZELA deixam de ser criados como rascunhos.

## API

Gestão (sessão IAZELA): `GET/POST /api/webshop/publications`, `GET /api/webshop/publications/articles`,
`PUT /api/webshop/publications/{id}`, `POST /api/webshop/publications/{id}/publish|withdraw|resync`,
`GET /api/webshop/publications/{id}/history`, `POST /api/webshop/publications/associate`,
`GET /api/webshop/publications-suggestions`.

Loja (credencial da ligação): `GET /api/webshop/publications-feed`, `POST /api/webshop/publications-results`,
`GET /api/webshop/publications-stock`, `POST /api/webshop/remote-products`.

## Testes

- `backend/tests/test_webshop_catalog.py` (8 testes): criação e histórico, atualizações incrementais (ficha e
  preço do canal, concorrência), retirada e artigo inativo, prevenção de duplicados e associação explícita, EAN
  ambíguo, reconhecimento por identidade exata, falhas e nova tentativa, preço desconhecido e permissões.
- Raizpura `tests/test_gpgest_wordpress.php` (WordPress local, API simulada): criação em rascunho, informação
  alimentar em falta, resultado perdido sem duplicar, preservação da informação alimentar, da descrição e da
  imagem, publicação só com revisão, retirada, SKU existente, associação e comunicação dos produtos.
- Raizpura `tests/test_gpgest_e2e.py`: ensaio HTTP real com o backend em esquema temporário.
