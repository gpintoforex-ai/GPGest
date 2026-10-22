# Digitalização rápida por PIN

No ecrã de entrada (`/entrar`) existe o botão **Digitalizar documento**. Permite que um dispositivo autorizado (telemóvel ou tablet da loja) fotografe um documento, reveja o recorte, gere o PDF e o guarde na pasta cloud da empresa, apenas com um PIN.

Fluxo: **PIN → Fotografar → Rever e recortar → Gerar PDF → Guardar na cloud**.

O acesso rápido só digitaliza e envia. Não mostra o dashboard, documentos anteriores, dados da empresa nem configurações. Não lista nem descarrega ficheiros da pasta cloud, e não cria fornecedores, compras, pagamentos ou movimentos de stock.

## Configurar (administrador)

1. Em **Configurações › Anexos**, ligar a conta Google e indicar a pasta Drive. Opcionalmente, configurar mais pastas de anexos na **Conferência documental**. Só estas pastas podem ser destino.
2. Em **Configurações › Parâmetros › Digitalização › Acessos de digitalização rápida**, carregar em **Novo acesso rápido** e indicar:
   - o nome ou dispositivo;
   - a loja;
   - a pasta cloud de destino (mostrada pelo nome);
   - um PIN de 6 a 12 algarismos, sem sequências nem algarismos repetidos;
   - o estado ativo.
3. Ao criar, aparece um **QR code** e um link de emparelhamento. Leia o QR com a câmara do dispositivo ou abra o link nele. O código é de uso único e expira em 30 minutos.
4. A partir daí, o dispositivo abre a digitalização com o botão **Digitalizar documento** no ecrã de entrada (ou em `/digitalizar`) e pede apenas o PIN.

Ações disponíveis em cada acesso:

- **Editar**: nome, loja, pasta, estado ou novo PIN. Qualquer alteração termina as sessões abertas.
- **Novo emparelhamento**: troca de dispositivo; o anterior deixa de funcionar.
- **Desbloquear**: depois de tentativas falhadas.
- **Revogar**: definitivo; o dispositivo deixa de funcionar de imediato. Os documentos já guardados mantêm-se.

A câmara em direto exige HTTPS (ver `ALLOWED_ORIGINS` e publicação em [Digitalização de documentos](DIGITALIZACAO-DOCUMENTOS.md)).

## Segurança

- **Associação à empresa.** O dispositivo guarda um token de dispositivo (identificador da empresa, do acesso e um segredo aleatório de 320 bits), obtido no emparelhamento. A empresa é sempre resolvida por esse identificador e nunca pelo PIN. Um PIN sem token não identifica nenhuma empresa.
- **PIN.**
  - É validado só no servidor e guardado apenas como hash Argon2.
  - Nunca é mostrado, registado na auditoria ou escrito em logs.
  - Ao fim de 5 tentativas falhadas, o acesso bloqueia de forma progressiva: 1, 5, 15, 60 e 240 minutos.
  - Há ainda um limite de 30 tentativas por endereço em 15 minutos.
- **Sessão.**
  - Token próprio, guardado só em memória no dispositivo, sem cookies.
  - Termina após 10 minutos sem atividade e dura no máximo 1 hora.
  - É validada em cada pedido, incluindo envios: estado ativo, revogação e prazos.
  - A sessão da aplicação (cookie) não abre o acesso rápido, e o token de digitalização não abre nenhuma outra API.
- **Credenciais cloud.** Ficam no servidor. Os erros explicam que a pasta está indisponível ou que a autorização expirou, sem expor dados da conta.
- **Abuso.** No máximo 60 documentos por hora e por acesso. Mantêm-se os limites de páginas e de tamanho da digitalização (20 páginas e 5 MB por omissão) e de 15 MB por fotografia.
- **Auditoria.** São registados o emparelhamento, os inícios de sessão, os bloqueios, os PDFs gerados, os envios e os erros. O utilizador registado é o administrador responsável pelo acesso, com a origem «digitalizacao_rapida».

## Captura e edição

- **Câmara.** Pede autorização e prefere a câmara traseira. Mostra a imagem inteira, sem cortes (o que se vê é o que é fotografado), e permite várias páginas. A galeria é sempre uma alternativa.
- **Câmara recusada ou indisponível.** Aparecem instruções para iPhone/Safari e Android/Chrome, o botão **Fotografar** (aplicação da câmara do sistema) e **Tentar novamente**.
- **Deteção das margens em direto.** Enquanto se aponta a câmara, o contorno do documento é desenhado sobre a imagem, como nas aplicações de digitalização:
  - azul: documento detetado;
  - tracejado amarelo: demasiado longe ou a cortar a margem;
  - verde, com barra de progresso: imóvel e pronto a fotografar.

  Uma indicação em texto acompanha sempre a cor (não depende só da cor): «A procurar o documento…», «Aproxime o telemóvel», «Afaste um pouco», «Pouco contraste», «Mantenha o telemóvel imóvel…», «A fotografar…».
- **Imagem nítida.** Ao fotografar (automático ou botão), a aplicação guarda uma rajada curta de ~6 imagens em 0,4 s e fica com a mais nítida na zona do documento, para não usar a imagem tremida pelo toque ou pela mão. Pede foco automático contínuo quando o telemóvel o permite.
- **Captura automática.** Ligada por omissão. Fotografa quando o documento está inteiro na imagem e imóvel cerca de 0,8 segundos (cantos a menos de 3 % da posição inicial e sem movimento brusco). Uma ou duas análises falhadas seguidas (reflexo, foco ou exposição a ajustar) não recomeçam a contagem; a fotografia é sempre tirada numa análise com o documento detetado. O botão «Captura automática ligada/desligada» guarda a preferência neste dispositivo. O botão de fotografar funciona sempre. A fotografia usa os cantos que estavam no ecrã.
- **Como funciona a deteção.** É local, no navegador, sem bibliotecas nem serviços externos. As imagens analisadas não são guardadas nem enviadas.
  - Analisa cerca de 8 imagens por segundo, numa versão reduzida do vídeo (256 px), e analisa menos vezes em telemóveis lentos.
  - Combina dois métodos e fica com o melhor quadrilátero:
    - **Região clara**: o papel pela «brancura» (claro e pouco saturado, o que distingue mesas de madeira e fundos coloridos), limiar de Otsu e contorno da maior região clara. Rápido e exato quando há contraste.
    - **Arestas retas**, para **papel branco sobre mesa branca**: os lados da folha deixam arestas ténues mas compridas (sombra fina de contacto, pequena diferença de brilho ou de tom). O mapa de arestas usa um limiar relativo ao ruído da imagem e desconta a luz de fundo; as retas (transformada de Hough) são afinadas por mínimos quadrados e combinadas em quadriláteros.
  - Cada lado proposto tem de coincidir com arestas reais ao longo do comprimento **e junto dos dois cantos**, e não pode continuar para lá deles. Assim, linhas de texto, blocos de texto, tabelas e bordas de outros objetos (teclado, telemóvel, fim da mesa) não passam por lados da folha.
  - Na dúvida não deteta nada: a captura automática nunca usa um contorno que não seja fiável.
  - Funciona com o papel rodado ou em perspetiva e rejeita formas que não parecem um documento.

  A mesma deteção é usada nas fotografias da galeria. Se a deteção falhar, propõe-se a página inteira para ajuste manual dos quatro cantos, por arrasto ou com as setas do teclado.
- **Página pronta.** Com as margens detetadas, a fotografia aparece logo recortada, endireitada e melhorada («Página N pronta»), como numa aplicação de digitalização. A partir daí pode **Ajustar cantos**, mudar o modo de cor, rodar, repetir a fotografia, eliminar, **Fotografar outra** ou **Concluir**.
- **Limites.** Uma folha branca sobre uma mesa branca é detetada quando algum lado se distingue (sombra, brilho ou tom); se um lado for de todo invisível (mesmo branco, sem sombra e com luz difusa), a folha não é detetada e ajustam-se os cantos à mão. Ajuda incliná-la um pouco em relação à luz ou usar uma superfície mais escura. Não usa modelos de aprendizagem automática.
- **Correção de perspetiva.** É feita no servidor da própria aplicação (Pillow), tal como os modos:
  - **Original**;
  - **Cor** (por omissão): retira sombras, luz desigual e dominantes de cor (cada canal é dividido pelo fundo do papel, estimado sem a tinta), deixa o papel branco, escurece o texto e aviva as cores; logótipos e blocos escuros grandes são preservados;
  - **Cinzentos**: o mesmo tratamento, sem cor;
  - **Preto e branco** (limiar local, tolera sombras).

  Não há reconstrução generativa nem OCR externo.
- **Páginas.** Miniaturas para reordenar, ajustar os cantos, rodar, repetir a fotografia e eliminar.
- **PDF.** Respeita a orientação e não força A4: talões compridos ficam com 80 mm de largura. Antes de enviar, mostra o tamanho final, a pré-visualização e o descarregamento.

## Classificação e envio

- **Tipos.** Fatura de fornecedor, Despesa, Comprovativo de pagamento, Documento de caixa e Outro. Descrição opcional. Fornecedor, número e valor não são pedidos.
- **Nome.** É sugerido como `AAAA-MM-DD_tipo_identificador.pdf` (data de Lisboa) e pode ser editado. Se já existir um ficheiro com esse nome na pasta, acrescenta-se o identificador; nunca se substitui um ficheiro.
- **Envio.**
  - O servidor valida as imagens e o PDF.
  - O identificador Google é pré-gerado e fica gravado, pelo que repetir após uma falha de rede não cria duplicados.
  - O botão fica bloqueado durante o envio.
  - O sucesso só aparece depois de o Drive confirmar o ficheiro (tamanho, MD5 e hash).
- **Falha no envio.** O PDF fica na sessão (cifrado no servidor durante 2 horas) e pode **Tentar novamente** ou **Descarregar**.
- **Sessão expirada a meio.** As páginas continuam no ecrã; basta voltar a introduzir o PIN.
- **Registo.** Cada envio fica como digitalização de origem «Digitalização rápida», com:
  - empresa e loja;
  - data;
  - acesso/dispositivo;
  - tipo;
  - identificador e pasta cloud.

  O administrador vê as últimas digitalizações no painel dos acessos. O ficheiro fica numa pasta de anexos configurada, onde a **conferência documental** o encontra. A área normal de digitalização não reenvia estes registos nem cria compras a partir deles.
- **Fim.** **Digitalizar outro documento** limpa o documento e mantém a sessão. **Terminar** apaga as páginas do dispositivo, termina a sessão e volta ao ecrã de entrada.

## Privacidade

As fotografias só existem na memória da página, sem gravação permanente no dispositivo. Os metadados EXIF são removidos ao reencodificar. As fotografias são libertadas depois do envio ou ao terminar.

O PDF temporário fica cifrado no servidor e a limpeza periódica apaga-o ao fim de 2 horas. As sessões expiradas são apagadas na mesma tarefa.

Não há analítica nem registo de imagens ou PINs. O único dado guardado no dispositivo é o token de emparelhamento (armazenamento local do navegador).

## Implementação e verificação

- Backend:
  - `app/quick_scan.py`: emparelhamento, PIN, sessões, geração, envio e administração;
  - modelos `QuickScanAccess` e `QuickScanSession`;
  - colunas `origin` e `quick_access_id` em `document_scans`;
  - migração `0055_quick_scan`;
  - reutiliza `process_image`, `make_pdf` e `GoogleDriveStorage`.
- Frontend:
  - `src/quickscan/QuickScan.tsx`, `LiveEdges.tsx` (deteção em direto), `edges.ts` (algoritmo), `quickApi.ts` e CSS;
  - `src/QuickScanAccesses.tsx` (administração);
  - botão em `App.tsx`.
- Bibliotecas: nenhuma nova. O QR de emparelhamento usa o `zxing-cpp` já instalado (Apache-2.0). Foi removida a dependência `@techstark/opencv-js`, que não era usada.
- `backend/tests/test_quick_scan.py` cobre:
  - PIN fraco e PIN errado;
  - bloqueio progressivo e desbloqueio;
  - expiração por inatividade, desativação e revogação;
  - novo emparelhamento e uso único do código;
  - ausência de acesso a outras áreas e só administradores;
  - fotografias com cantos e modos, várias páginas e talão;
  - nomes sem colisão;
  - falha de rede com repetição sem duplicado;
  - pasta indisponível e autorização expirada;
  - isolamento entre empresas por identificador;
  - QR legível.
- `scripts/test-edge-detection.mjs` (Node, sem navegador): deteção em cenas sintéticas, incluindo fatura rodada sobre madeira, papel a 45°, perspetiva, texto, ruído, iluminação desigual, papel longe, cortado ou a ocupar a imagem, branco sobre branco (com sombra, quase igual, luz desigual, teclado ao lado, talão estreito), mesa vazia, forma irregular e tempo por análise. Inclui um banco de cenas realistas geradas por `scripts/document-scenes.mjs` (texto com margens, perspetiva, sombras finas, luz desigual, desfocagem, ruído, objetos, folha cortada e mesa vazia): exige pelo menos 80 % de folhas detetadas e **nenhum** contorno errado.
- `scripts/test-quick-scan-live-ui.py` (Chromium com vídeo sintético como câmara) cobre contorno em direto, indicação coerente, captura automática com os cantos certos, captura manual com a automática desligada e preferência guardada, e ausência de captura sem documento.
- `scripts/test-quick-scan-ui.py` (Chromium com câmara simulada e ecrã móvel) cobre:
  - criação do acesso e QR;
  - emparelhamento por link e PIN errado/certo;
  - captura e arrasto de cantos;
  - deteção automática numa fotografia da galeria;
  - reordenação, tipo, PDF e erro de envio sem conta Google;
  - Terminar;
  - câmara recusada em iPhone simulado;
  - revogação.

Os testes automáticos não substituem a verificação num iPhone/Safari e num Android/Chrome reais, com HTTPS, câmara física e uma conta Google ligada.
