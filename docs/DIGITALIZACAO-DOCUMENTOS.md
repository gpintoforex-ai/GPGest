# Digitalização de documentos

O botão **Folhas por página** está no dossiê contabilístico, na conferência documental e nas fichas de compras, movimentos de caixa abertos e depósitos. Administradores e gestores podem preparar e guardar. A associação a uma ficha existente reutiliza o documento; o envio não cria uma segunda compra.

## Configurar

1. Aplicar as migrações até `0033_document_scanning` e instalar os requisitos do backend, incluindo Pillow.
2. Em **Configurações > Anexos**, ligar a conta Google da empresa e indicar uma pasta Google Drive na qual essa conta possa criar ficheiros. A autorização OAuth e os tokens permanecem no servidor. A ligação Google existente usa o fluxo de autenticação no computador.
3. Em **Digitalização de documentos**, definir os limites e a organização. Valores iniciais: 20 páginas e PDF até 5 MB; máximos configuráveis: 40 páginas e 5 MB. A organização inicial é `Empresa / Contabilidade / Ano / Mês / Tipo`, pela data do documento. Também se pode escolher apenas período/tipo ou a pasta configurada.
4. Para usar no telemóvel, publicar a mesma instalação num endereço acessível pelo dispositivo, preferencialmente HTTPS, e configurar `ALLOWED_ORIGINS` com esse endereço. O endereço local `127.0.0.1` do computador não é acessível pelo telemóvel. Este módulo não altera automaticamente a rede, certificados ou firewall.

O servidor verifica a pasta e a capacidade de escrita antes de enviar. Mudanças de conta ou pasta após preparar um documento exigem preparar uma nova versão. Não são criadas ligações públicas. O único fornecedor implementado é Google Drive, através de uma interface de armazenamento extensível.

## Utilizar

Fotografar usa o seletor de ficheiros com indicação da câmara traseira (`capture=environment`), sujeito ao comportamento do navegador. Há alternativas de galeria e PDF existente. Fotografias aceites: JPEG, PNG e WebP, até 15 MB cada e 50 MB no conjunto. HEIC requer exportação para JPEG.

Pode adicionar, repetir, eliminar e reordenar páginas; rodar, recortar, ajustar quatro cantos para perspetiva e escolher cor, cinzentos ou contraste. A orientação EXIF é aplicada antes das alterações. Não há deteção automática do papel nem OCR. O aviso de possível desfocagem é uma estimativa visual e não substitui a revisão.

Os PDFs de fotografias conservam as proporções, incluindo talões compridos. PDFs existentes são verificados e conservam os bytes originais; PDFs cifrados ou com conteúdos ativos não suportados são rejeitados. A pré-visualização e o descarregamento permitem rever antes de confirmar o envio. Sem data, é necessário introduzi-la ou confirmar explicitamente a data de hoje.

Os nomes são sanitizados e têm um identificador próprio, evitando substituir ficheiros com nomes repetidos. A metadata inclui empresa/loja, dados do documento, associação, fornecedor cloud, identificadores de pasta/ficheiro, tamanho, SHA-256, utilizador e datas.

## Estados e recuperação

Ao carregar um PDF, o servidor procura QR codes fiscais nas páginas e preenche NIF do emitente, tipo, número, data e total com impostos. O nome da entidade é procurado nos registos existentes pelo NIF. Um documento já existente com a mesma identidade, data e total é associado à ficha; dados de uma ficha aberta são preservados quando o QR diverge. QR codes repetidos do mesmo documento contam uma vez; documentos diferentes exigem escolha. Sem QR legível, o preenchimento continua manual. A leitura é local, não segue URLs nem envia o PDF para serviços externos, e não valida automaticamente a fatura. Implementação: `invoice_qr.py`, pypdfium2 e zxing-cpp, conforme as [especificações da AT](https://info.portaldasfinancas.gov.pt/pt/apoio_ao_contribuinte/Negocios/Faturacao/Regras_mecanismos_comunicacao/Comunicacao_de_series_ATCUD/Codigo_barras_bidimensional/Documents/Especificacoes_Tecnicas_Codigo_QR.pdf).

**Em preparação → PDF gerado → A enviar → Guardado na cloud**. O último estado só aparece após confirmar o ficheiro remoto e gravar a associação local. Uma falha apresenta **Erro no envio** e permite repetir com o mesmo identificador Google. Se o ficheiro já chegou ao Drive, a repetição recupera a associação sem enviar outra cópia. A ligação a uma compra já existente pode ser recuperada explicitamente, mantendo a identidade do documento.

Guardar e validar são passos separados: o novo anexo fica **Anexo por validar**. A validação exige revisão e confirmação; não valida automaticamente os dados contabilísticos da fatura. Os anexos aparecem nas fichas e na exportação do dossiê. Consulta e descarregamento passam pelo servidor autenticado e respeitam empresa e loja.

As fotografias ficam apenas na janela aberta. Fechar ou recarregar pode perdê-las. O PDF temporário fica cifrado no servidor durante 24 horas; a tarefa de limpeza elimina o conteúdo expirado, preservando metadata e identificadores para recuperar ficheiros que já estejam na cloud. Depois de guardar, o conteúdo temporário é eliminado. Não existe promessa de funcionamento offline ou de recuperação de fotografias após fechar o navegador. Descarregar o PDF permite conservar uma cópia do trabalho.

## Implementação e verificação

- Backend: `document_scanning.py`, `scan_storage.py`, modelos e migração 0033; integrações com fichas, anexos, depósitos e exportação contabilística.
- Frontend: `DocumentScanner.tsx` e CSS, botões nas fichas e definições nos Anexos.
- API autenticada: `/api/scans/setup`, `/settings`, `/image-preview`, `/prepare`, `/{id}`, `/{id}/pdf`, `/{id}/upload`, `/{id}/associate` e `/{id}/validate`.
- `backend/tests/test_document_scanning.py`: imagens/PDF reais gerados em memória, base de dados de teste, Drive simulado, falha de envio, recuperação, expiração, permissões e isolamento entre empresas.
- `scripts/test-document-scanning-ui.py`: navegador Chromium com ecrã móvel emulado e respostas fictícias; galeria, páginas, rotação, associação, PDF, repetição e validação.

Os testes simulados não demonstram acesso a uma câmara física nem a uma conta Google real. Antes de utilização em produção, verificar captura e revisão num iPhone/Safari e Android/Chrome, leitura de um documento real legível e envio para a pasta configurada, incluindo perda de rede e permissões de escrita.

A estratégia de IDs pré-gerados segue a [documentação de uploads Google Drive](https://developers.google.com/workspace/drive/api/guides/manage-uploads). A orientação de fotografias usa [Pillow ImageOps.exif_transpose](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html).
