# Arquivo digital

O arquivo digital junta num só índice todos os documentos da empresa:
- digitalizações, incluindo a [digitalização rápida por PIN](DIGITALIZACAO-RAPIDA.md);
- carregamentos diretos;
- anexos de compras, de movimentos de caixa e de depósitos;
- ficheiros das pastas de anexos do Google Drive;
- anexos lidos do Gmail.

Sobre cada documento extrai os dados, propõe relações com compras, ERP, bancos e pagamentos, e regista o histórico. O original nunca é substituído nem apagado.

Menu **Arquivo digital**:

| Página | Para que serve |
|---|---|
| Visão geral | Indicadores, atualização do arquivo, recebidos recentemente e o assistente Clara. |
| Caixa de entrada | Carregar ficheiros e rever os documentos novos ou com erro. |
| Todos os documentos | Pesquisa global, filtros e ações em lote com confirmação. |
| Digitalizar documento | A digitalização existente, que passou para aqui a partir de Contabilidade. |
| Associações | Propostas do motor de relações, com justificação; confirmar, rejeitar ou anular. |
| Pendências | Documentos por rever, compras sem documento, pagamentos sem comprovativo, faturas sem compra e prazos de conservação terminados. |
| Dossiês contabilísticos | Documentos por mês. O fecho, a exportação e o envio continuam em *Contabilidade › Entrega à contabilidade*. |
| Auditoria | Alterações, decisões e acessos (administradores e gestores). |
| Configurações | Conservação, OCR, processamento automático, Drive e verificação de integridade. |

O menu **Contabilidade** mantém Conferência documental, Despesas por rubrica, Mapas de gestão e Entrega à contabilidade.

## Diagnóstico de partida

O que já existia e foi reutilizado:
- **Extração local** (`invoice_evidence.py`, `invoice_qr.py`): QR fiscal da AT, texto do PDF e OCR Tesseract opcional.
- **Provas Drive** (`drive_documents.py`, `InvoiceEvidenceCache`), com leitura limitada a 5 MB e verificação SHA-256.
- **Anexos de compras:** em base de dados e no Drive.
- **Digitalizações**, com envio idempotente para o Drive.
- **Anexos do Gmail**.
- **Pagamentos com alocações parciais** (`DocumentPayment`, `PaymentDocumentLine`).
- **Extratos bancários e conciliação**.
- **Períodos e dossiê** (`accounting_delivery.py`, `dossier_package.py`).
- **Auditoria:** a tabela `audit`.
- **Agendador em segundo plano** (`integration_scheduler.py`).
- **Autorização** por perfil e por loja e **isolamento** por esquema PostgreSQL por empresa.

O que faltava:
- um índice por ficheiro (até aqui, tudo partia da compra);
- uma caixa de entrada única e uma fila com tentativas;
- relações com o banco e os pagamentos;
- correções com histórico, registo de acessos, conservação e verificação de integridade;
- o assistente.

## Arquitetura

- **`archive.py`:** o motor.
  - Indexação idempotente das fontes existentes.
  - Leitura do original e verificação SHA-256.
  - Extração.
  - Motor de relações.
  - Fila assíncrona.
- **`archive_api.py`:** as rotas `/api/archive/*`.
- **`archive_assistant.py`:** o assistente.
- **Interface:** `frontend/src/Archive.tsx` e `Archive.css`.
- **Migração** `0056_digital_archive` (aditiva, compatível com os dados existentes):

| Tabela | Conteúdo |
|---|---|
| `archive_documents` | Um registo por ficheiro, identificado por `source_key` (por exemplo, `drive:<id>`), o que impede duplicar o índice quando o mesmo ficheiro aparece por várias fontes. Guarda empresa (esquema), loja, tipo, categoria, origem, nome original, armazenamento, SHA-256, datas de emissão/entrada/arquivo, NIF e entidade, número, base, IVA, total e moeda (cêntimos inteiros), referência de pagamento, estados de processamento e revisão, extração com confiança, correções, prazo de conservação, retenção obrigatória e integridade. |
| `archive_blobs` | Originais carregados diretamente, cifrados (Fernet, chave da plataforma). |
| `archive_links` | Relações documento ↔ compra/e-Fatura, compra do ERP, movimento bancário, pagamento, movimento de caixa e depósito. Têm valor imputado (parciais e agrupados), pontuação, força, estado, método e justificação. |
| `archive_events` | Histórico de alterações e acessos. |
| `archive_jobs` | Fila de extração e relações: tentativas, próxima tentativa e erro. |
| `archive_settings` | Configuração por empresa. |

### Fontes e caixa de entrada

**Atualizar arquivo** indexa, sem copiar nem alterar nada:
- as digitalizações guardadas;
- os anexos Drive das compras;
- os ficheiros já analisados pela conferência (reutiliza a extração em cache, sem novo download);
- os anexos de compras, movimentos e depósitos;
- os anexos do Gmail;
- opcionalmente, os PDF/JPG/PNG das pastas de anexos do Drive e respetivas subpastas (só metadados; até 500 por atualização).

Os carregamentos diretos aceitam PDF, JPG, PNG e WebP até 10 MB, 20 ficheiros de cada vez. Verificações:
- o conteúdo é verificado pela assinatura do ficheiro, não pela extensão;
- PDFs cifrados, com JavaScript, ações automáticas ou ficheiros embutidos são rejeitados;
- as imagens são validadas com limite de píxeis;
- um ficheiro com o mesmo SHA-256 de outro já arquivado não é guardado de novo.

### Processamento assíncrono

Cada documento novo entra na fila (`extract`, depois `match`).

- **Quem processa:** o agendador trata até 5 trabalhos por empresa a cada 30 segundos, se o processamento automático estiver ativo. Os botões *Processar fila*, *Atualizar arquivo* e *Repetir processamento* processam de imediato.
- **Sem repetições:** a fila usa bloqueio por linha (`FOR UPDATE SKIP LOCKED`), pelo que dois processos não fazem o mesmo trabalho.
- **Falhas temporárias** (rede, Google, autorização): nova tentativa após 1, 5, 15, 60 e 240 minutos, até 5 tentativas.
- **Falhas permanentes** (ficheiro removido, formato, integridade): o documento fica em *Erro*, com a mensagem.
- **Interrupções:** trabalhos interrompidos há mais de 10 minutos voltam à fila.

### Extração e confiança

- **QR fiscal:** NIF, tipo, número, data, total e base/IVA por taxa, com confiança 99%.
- **Texto do PDF:** 75%.
- **OCR:** 60%.
- **Tipo pelo texto** (fatura, fatura-recibo, nota de crédito, recibo, comprovativo, extrato, contrato): 60%.
- **Referência Multibanco:** 70%.
- **Nome da entidade:** procurado na ficha pelo NIF, ou nas compras.

Um campo só é preenchido quando há um único valor no documento. Havendo vários, ficam como candidatos e nada é inventado. Um QR que indique documento anulado gera um aviso.

As correções manuais ficam no histórico (antes, depois, utilizador e data) e nunca são substituídas por um novo processamento.

### Motor de relações

As regras são determinísticas. A pontuação vai até 100 e a justificação mostra-se sempre.

- **Compras/e-Fatura e compras do ERP:**
  - NIF e número: 60 pontos;
  - só NIF: 30; só número: 20;
  - total igual: +25; mesma data: +15; datas próximas: +5.
  - É preciso NIF ou número iguais.
- **Movimentos bancários (débitos):**
  - valor igual: 50 pontos;
  - número na descrição: +30; referência de pagamento: +30; NIF: +20; nome da entidade: +15;
  - data coerente (comprovativos ±3 dias; faturas até 120 dias).
- **Pagamento parcial:** valor inferior ao total, com menção do documento ou da entidade. Fica com o valor imputado.
- **Pagamento agrupado:** até 4 documentos do mesmo NIF cuja soma é igual ao débito. Cada documento fica com o seu valor.
- **Pagamentos registados (`DocumentPayment`):** as mesmas regras, com bónus quando o pagamento já está alocado à compra associada ao documento.
- **Dossiê contabilístico:** o período (ou mês) é determinado pela data de emissão e indicado no detalhe do documento.

**Força das propostas:**
- *Forte*: 90 pontos ou mais, sem alternativas.
- *Possível*: abaixo de 90.
- *Ambígua*: há outros candidatos do mesmo género a menos de 10 pontos. Fica sempre para validação humana.

Ninguém confirma por omissão. A opção «Confirmar automaticamente identidades fortes por QR» está desligada e, quando ativada, só se aplica a compras com NIF, número, data e total iguais ao QR e sem alternativas.

Ao confirmar uma relação com uma compra:
- um ficheiro do Drive fica como anexo Drive da compra, sem cópia;
- um ficheiro em base de dados é copiado para os anexos da compra, até 5 anexos e 5 MB;
- a compra passa a «digital», o que a torna visível na conferência e no dossiê.

As relações nunca criam fornecedores, compras, pagamentos, lançamentos contabilísticos nem stock. A anulação exige motivo e não apaga anexos já criados.

### Assistente (Clara)

Interpreta perguntas em português localmente, sem serviços externos.

- **Pesquisa:** por exemplo, «Encontra as faturas da EDP de setembro». Entende tipo, entidade e período (mês, trimestre, ano, este mês, mês passado).
- **Documentos em falta para a contabilidade.**
- **Pagamentos sem comprovativo.**
- **Preparar o dossiê:** leitura com resumo e ligação para a Entrega à contabilidade, onde se fecha e envia com confirmação.
- **Relacionar anexos com compras/ERP:** é uma operação de escrita.
  - Devolve um plano cifrado, válido 15 minutos e pessoal.
  - Só administradores e gestores o podem executar, depois de confirmar.
  - O resultado são apenas propostas.

Cada resposta indica o modo (leitura ou escrita), a interpretação, as fontes consultadas, os documentos e as relações usadas.

## Segurança e conformidade

- **Isolamento:** um esquema PostgreSQL por empresa. As identificações de documentos de outra empresa devolvem 404.
- **Lojas:** a leitura é limitada às lojas autorizadas. Documentos sem loja (por exemplo, do Drive) só são vistos por administradores e gestores.
- **Perfis:**

| Perfil | Pode |
|---|---|
| Consulta | Ler. |
| Operador | Carregar e processar nas suas lojas. |
| Gestor | Tudo o anterior, mais corrigir, decidir associações, atualizar, ver a auditoria e usar o assistente para escrita. |
| Administrador | Tudo o anterior, mais configuração, retenção obrigatória e verificação de integridade. |

- **Proteção:** em trânsito, HTTPS (Cloudflare). Em repouso, os carregamentos diretos ficam cifrados.
  - A chave `PLATFORM_ENCRYPTION_KEY` tem de estar incluída nos backups.
  - Os originais são servidos com `Content-Security-Policy: sandbox` e `nosniff`.
- **Integridade:** o SHA-256 é verificado em cada leitura. *Configurações › Verificar* recalcula por lotes. Qualquer diferença é registada e o documento é assinalado.
- **Auditoria:** eventos por documento (indexação, carregamento, processamento, correções, revisão, decisões, visualizações, descarregamentos, integridade), mais registos na auditoria geral para as escritas.
- **Conservação:** por omissão até 31/12 do ano de emissão mais 10 anos (configurável entre 10 e 30). Existe ainda a marca de retenção obrigatória.
  - **O arquivo nunca elimina documentos.** O fim do prazo aparece nas Pendências como decisão manual.
  - A digitalização não autoriza, por si só, a destruição de originais em papel. Os requisitos (por exemplo, o Decreto-Lei n.º 28/2019) devem ser confirmados com o contabilista.
  - Ver também o [enquadramento legal](ENQUADRAMENTO-LEGAL.md) e os [procedimentos de privacidade](PROCEDIMENTOS-PRIVACIDADE.md).
- **RGPD:** guarda-se apenas o necessário para a gestão documental. Não há envio a serviços de IA ou OCR externos, e os acessos ficam registados.
- **Credenciais cloud:** ficam no servidor (OAuth existente). Os erros não expõem tokens.
- **Backups:** os documentos indexados e os originais cifrados estão na base de dados da empresa e seguem a política de backups do PostgreSQL/Neon. Os ficheiros no Drive seguem a conta Google da empresa.

## Utilização

1. **Aplicar a migração:** `cd backend; ..\.venv\Scripts\alembic.exe upgrade head`.
2. **Indexar o que já existe:** em *Arquivo digital › Visão geral*, carregar em **Atualizar arquivo** (administrador ou gestor).
3. **Novos documentos:** entram pela *Caixa de entrada* (carregar), pela *Digitalizar documento*, pela digitalização rápida ou pelas pastas Drive configuradas.
4. **Rever:** abrir cada documento, confirmar ou corrigir os dados e carregar em **Guardar e marcar revisto**.
5. **Validar:** em *Associações*, validar as propostas. Pode selecionar as fortes e confirmar em lote; a confirmação mostra um resumo e pede confirmação.
6. **Antes de entregar à contabilidade:** ver *Pendências* e *Dossiês contabilísticos*. Depois, usar *Contabilidade › Entrega à contabilidade*.

## Testes

- `backend/tests/test_archive.py` (7 testes) cobre:
  - carregamento com rejeição de PDF com JavaScript e de ficheiro executável;
  - duplicados por SHA-256 e cifra em repouso;
  - extração por QR com confiança;
  - correções com histórico e preservação após reprocessar;
  - pré-visualização e confirmação em lote, com anexação à compra;
  - anulação com motivo;
  - integridade alterada;
  - indexação idempotente de todas as fontes (incluindo a caixa de entrada do Drive), sem envio de ficheiros;
  - pagamentos total, parcial e agrupado, e pagamento registado parcial;
  - permissões e âmbito por loja;
  - configuração e conservação;
  - falha cloud com nova tentativa diferida, recuperação de trabalho interrompido e erro permanente;
  - assistente: leitura com fontes, e escrita só com plano confirmado e por quem pode;
  - isolamento entre empresas.
- A regressão dos módulos existentes corre na suite completa (`pytest -q`).

## Limitações

- **Assistente:** é baseado em regras, não num modelo de linguagem. Perguntas fora dos padrões descritos caem na pesquisa.
- **OCR:** depende do Tesseract instalado no servidor (`GPGEST_OCR_*`). Imagens sem QR nem texto ficam para preenchimento manual.
- **Drive:**
  - a leitura de ficheiros do Drive está limitada a 5 MB (o mesmo limite da conferência);
  - a caixa de entrada do Drive só lê as pastas de anexos configuradas;
  - outras clouds não estão implementadas: o contrato de armazenamento é extensível.
- **Motor de relações:** trata débitos bancários (despesas). Documentos de venda/recebimento não são relacionados automaticamente.
- **Dossiê:** a exportação continua a incluir os anexos das compras (e as digitalizações sem compra). Documentos do arquivo sem compra associada aparecem nas Pendências, mas ainda não entram no ZIP.
