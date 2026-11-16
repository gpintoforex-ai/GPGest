# Fotografias dos artigos (fotografia principal e rótulo)

Na ficha do artigo, por baixo da fotografia, há dois botões:

- **Fotografia do produto** (ícone da câmara): **Tirar fotografia** abre a câmara no telemóvel ou tablet;
  **Escolher ficheiro** aceita JPG, PNG ou WebP até 20 MB. A fotografia fica guardada no IAZELA. Em
  alternativa, continua a ser possível indicar um endereço HTTPS («Ou usar um endereço HTTPS»). Quando existem as
  duas, a fotografia carregada é a que conta.
- **Rótulo** (ícone ao lado de «Rótulo»): fotografia do rótulo (câmara, ficheiro ou **URL da fotografia (HTTPS)**
  com «Guardar esta imagem na IAZELA»). A IA lê-a para propor o descritivo do site (ingredientes, quantidades por
  dose, dose diária, modo de utilização, advertências, conservação). Fotografe o rótulo de frente, nítido e sem
  reflexos; o que estiver ilegível fica em «Informação em falta» e não é adivinhado.
- **Rótulo no site** (no mesmo painel, «No site»): por omissão o rótulo é só para a IA e não tem endereço público.
  **Publicar o rótulo no site (legível e em português)** envia-o para a loja, que o guarda no media e preenche o campo
  «rótulo em português» da informação alimentar (a ligação «Consultar rótulo em português» da página do produto).
  **Também como última imagem da galeria** acrescenta-o, em último lugar, às imagens do produto. Um rótulo posto à
  mão no WordPress mantém-se (e a Publicação online indica-o); desligar a publicação retira da loja o rótulo que foi
  posto pela IAZELA. Substituir a fotografia do rótulo desliga a publicação até nova confirmação. Na tira, «Para a
  IA» ou «No site». Auditoria: `artigo_rotulo_publicacao`. Migração **0099** (`article_images.public`, `.gallery`).

**Guardar a imagem de um endereço.** Em «Ou usar um endereço HTTPS», depois de colar o endereço da imagem,
**Guardar esta imagem na IAZELA** faz o servidor descarregá-la e guardá-la como fotografia carregada (com o mesmo
tratamento abaixo). A loja online e a IA deixam de depender do site de origem, que pode recusar pedidos de outros
servidores ou mudar o endereço. Só HTTPS na porta normal, até 20 MB, 20 s e três redirecionamentos; endereços da rede
interna do servidor são recusados (também depois de um redirecionamento). Se o site de origem não permitir, guarde a
imagem no computador e use «Escolher ficheiro». O endereço de origem fica na auditoria. Confirme que pode usar a
imagem (por exemplo, do fornecedor ou da marca).

O carregamento é imediato (não espera por «Guardar») e exige que o artigo já esteja gravado e a permissão de editar
artigos. Carregar outra imagem substitui a anterior; **Retirar** apaga-a do IAZELA. As duas operações ficam na
auditoria (`artigo_imagem_carregada`, `artigo_imagem_retirada`, com o hash do ficheiro).

## Tratamento do ficheiro

Ao carregar, a imagem é convertida para JPEG com o lado maior até 2000 px. A rotação da câmara é aplicada, a
transparência passa a fundo branco e os **metadados são removidos** (localização GPS, modelo do telemóvel, data).
Ficheiros que não sejam imagens são recusados. As imagens ficam guardadas na base da empresa (tabela
`article_images`, migração **0098**), sem alojamento externo.

## Loja online

A fotografia carregada é a fotografia principal enviada para a loja (ver [publicação online](PUBLICACAO-ONLINE.md)),
seguida das imagens adicionais por HTTPS. A loja descarrega-a por um endereço público
`/api/public/article-images/{empresa}/{imagem}/{hash}.jpg`, que só funciona com o hash certo e muda quando a
fotografia muda: a loja substitui a imagem do produto na sincronização seguinte. O rótulo só é servido por este
endereço depois de publicado na ficha.

A origem do endereço é a da API vista pela loja (cabeçalhos `X-Forwarded-Host`/`X-Forwarded-Proto` do Cloudflare
Worker). Para fixá-la, defina `PUBLIC_API_ORIGIN` (por exemplo, `https://iazela.exemplo.pt`) no `.env` do servidor.
O plugin descarrega as fotografias do IAZELA pela mesma origem da API já configurada (como os restantes pedidos
ao GPGest), porque o `download_url()` do WordPress recusa endereços locais e portas fora de 80/443/8080; as imagens
de outros sites continuam pelo `download_url()`. Num WordPress local que bloqueie pedidos externos, permita também
`/api/public/article-images/` da origem da API. Retirar a fotografia carregada
volta a enviar o endereço HTTPS da ficha. Sem ele, a loja mantém a fotografia que já tinha.

## IA

Ao gerar o descritivo, seguem para a IA, por esta ordem: a fotografia do rótulo, a fotografia carregada e, por
fim, as imagens por HTTPS da ficha (sem o endereço da fotografia quando há fotografia carregada). As imagens
carregadas são enviadas diretamente ao serviço de IA, sem endereço público. Ver
[descritivos do site](DESCRITIVOS-SITE-IA.md).

## API e testes

`GET /api/stock/articles/{id}/images`, `PUT|DELETE /api/stock/articles/{id}/images/photo|label` (multipart,
campo `file`), `POST /api/stock/articles/{id}/images/photo|label/from-url` (`{url}`),
`PUT /api/stock/articles/{id}/images/label/publication` (`{public, gallery}`), `GET /api/stock/articles/{id}/images/{papel}/{hash}.jpg` (sessão iniciada) e o endereço público
acima. Testes: `backend/tests/test_article_images.py` (normalização, substituição, permissões, auditoria, envio à
loja, endereço público e leitura do rótulo pela IA simulada).
