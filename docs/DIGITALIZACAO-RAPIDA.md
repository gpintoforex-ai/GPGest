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

- **Câmara.** Pede autorização e prefere a câmara traseira. Mostra uma moldura de enquadramento e permite várias páginas. A galeria é sempre uma alternativa.
- **Câmara recusada ou indisponível.** Aparecem instruções para iPhone/Safari e Android/Chrome, o botão **Fotografar** (aplicação da câmara do sistema) e **Tentar novamente**.
- **Deteção de margens.** É local, no navegador, sem bibliotecas nem serviços externos:
  - luminância e desfocagem;
  - limiar de Otsu;
  - maior região clara;
  - quatro cantos.

  O utilizador revê sempre o recorte. Se a deteção falhar, propõe-se a página inteira para ajuste manual dos quatro cantos, por arrasto ou com as setas do teclado.
- **Correção de perspetiva.** É feita no servidor da própria aplicação (Pillow), tal como os modos:
  - **Original**;
  - **Cor** (realce de contraste);
  - **Cinzentos**;
  - **Preto e branco** (limiar local, tolera sombras).

  Não há reconstrução generativa nem OCR externo.
- **Páginas.** Miniaturas para reordenar, editar o recorte, rodar, repetir a fotografia e eliminar.
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
  - `src/quickscan/QuickScan.tsx`, `edges.ts`, `quickApi.ts` e CSS;
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
