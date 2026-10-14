# Assistente e-Fatura: fatura, anexo e ERP

## Análise da implementação anterior

O GPGest já importava e-Fatura para `Purchase`/`DocumentImport`, mantinha estados documentais independentes em `DocumentState`, consultava compras Wintouch para `ERPDocument` e guardava associações em `DocumentERPLink`. Os anexos podiam ser locais (`PurchaseAttachment`) ou referências estáveis Drive (`DriveDocumentAttachment`). Já existiam importação Gmail, pesquisa de subpastas, QR fiscal, digitalização, propostas de despesa e exportação para contabilidade.

O assistente dividia a conferência em etapas, usava pontuações de nomes/texto e exigia confirmação de cada associação. A ausência no catálogo local podia ser confundida com ausência no ERP. Não havia uma lista que reunisse as duas verificações, cache de extração, validação específica do conteúdo ou agendamento destas conferências.

O contrato Wintouch existente é de leitura de compras em EUR. O conector permite outras escritas, como rubricas e liquidações, mas **não implementa criar fornecedores nem faturas de compra**. As novas propostas continuam identificadas como propostas, sem pedidos de escrita fictícios nem simulações apresentadas como operações reais.

## Como utilizar

1. Em **Contabilidade > Conferência documental > Clara · Verificar documentos**, escolher o período e importar o CSV/XLSX e-Fatura ou continuar com os documentos existentes.
2. Clicar em **Verificar faturas**. Um administrador consulta o ERP atual e o Drive. Em empresas de demonstração, confirmar explicitamente consultas de dados reais. Gestores podem verificar anexos, mas a consulta atual ao ERP exige um administrador.
3. Usar os filtros **Todas**, **Concluídas**, **Anexos em falta**, **Por lançar no ERP** e **Para rever**. A lista apresenta data, fornecedor/NIF, referência, valor, anexo, ERP e próxima ação; os totais referem-se ao período inteiro, independentemente da paginação.
4. Abrir o anexo ou rever os candidatos. Uma associação automática exige conteúdo suficiente, correspondência segura e ausência de outros candidatos. Confirmar manualmente significa abrir o ficheiro e verificar os dados, com motivo registado.
5. Quando o ERP foi consultado e não encontrou lançamento, e existe um anexo validado, abrir **Rever proposta para o ERP**. Fornecedor e fatura são revistos na mesma janela. O fornecedor existente é identificado por NIF; entidades inativas, não classificadas como fornecedor ou duplicadas exigem revisão, sem propor automaticamente outra entidade com o mesmo NIF.
6. Guardar a proposta revista ou exportá-la para revisão externa. Antes de guardar, o ERP e o fornecedor são consultados novamente. Um candidato recém-encontrado impede a proposta. **Guardar ou exportar não cria um fornecedor nem uma compra no ERP.** Não há um botão de execução operacional enquanto faltar um contrato de escrita implementado e validado.

Sem anexo validado, o assistente mostra a pendência e permite procurar novamente, digitalizar ou abrir a ficha para anexar manualmente. As ferramentas anteriores de conferência continuam acessíveis em **Ferramentas anteriores de conferência** e na ficha individual.

## Regras de correspondência e validação

- Empresa: isolamento pelo esquema da empresa e pelas lojas permitidas ao utilizador.
- Identidade: NIF do emitente + tipo + número. Normaliza maiúsculas, espaços e variantes gráficas dos separadores. Conserva a distinção entre `/` e `-`, a série e os zeros à esquerda; remove apenas um prefixo redundante igual ao tipo do documento.
- Confere data e valor. A tolerância inicial é 2 cêntimos, configurável entre 0 e 5. O contrato de compras atual suporta EUR; moedas explícitas diferentes ou ambíguas no anexo impedem associação automática.
- O nome do ficheiro só encontra candidatos. A validação usa QR fiscal, texto extraível ou OCR local. Dados incompletos, extração parcial, divergências e candidatos múltiplos exigem revisão.
- ERP e anexo são independentes: lançamento associado sem anexo continua pendente. Só há conclusão com anexo validado, associação ERP e sem falhas que comprometam a verificação.
- Referências manuais nunca são substituídas. Renomear um ficheiro preserva o ID e a associação. Alterar o conteúdo invalida a validação e exige revisão; o ficheiro não é duplicado nem eliminado.
- Documentos de teste e-Fatura não são associados automaticamente a documentos ERP reais.

## Configuração por empresa

Para a consulta atual ao ERP, a empresa deve estar em **Modo integrado**, com a ligação Wintouch ativa e as credenciais da empresa configuradas. Nas opções de sincronização, ativar **Documentos de compras** e, para verificar o fornecedor, **Clientes e fornecedores**. Se um recurso estiver desativado ou a ligação falhar, a interface indica que a verificação está pendente, em vez de presumir que não existe lançamento ou fornecedor.

Em **Configurações > Anexos**, manter a conta Google autorizada e a pasta de destino existentes. Em **Verificação automática e-Fatura**, um administrador pode indicar várias pastas de pesquisa, um link ou ID por linha. As subpastas são incluídas. Esta opção não altera a pasta de destino dos uploads.

A pesquisa usa as APIs reais de [consulta de ficheiros Google Drive](https://developers.google.com/workspace/drive/api/guides/search-files). São guardados IDs, nome, pasta, versão/hash e dados fiscais extraídos; não se guarda o PDF, imagem ou texto integral na cache de extração. A conta configurada é autenticada no servidor; o navegador não recebe tokens.

**OCR opcional:** instalar Tesseract no servidor e definir `GPGEST_OCR_COMMAND` com o caminho completo do executável; `GPGEST_OCR_LANG` define os idiomas instalados, por exemplo `por+eng` (valor inicial: `eng`). Ativar OCR nas configurações. Sem executável, a interface indica a indisponibilidade e mantém QR/texto disponíveis. O conteúdo é enviado ao processo local por memória, sem serviço OCR externo. Segue a [documentação Tesseract](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).

**Agendamento:** intervalo mínimo 15 minutos; 0 desativa. A janela inicial abrange os últimos 90 dias. O agendamento usa o administrador que o configurou; não corre se esse utilizador estiver inativo ou perder o perfil. Empresas de demonstração exigem confirmação explícita. A tarefa só consulta e associa correspondências seguras; nunca cria fornecedores, compras ou pagamentos.

## Fiabilidade e limites

Cada empresa tem uma verificação em curso de cada vez, com reserva recuperável após 20 minutos. Associações usam as restrições únicas existentes e bloqueios de linhas. Repetir uma execução reutiliza os IDs e as associações. Falhas de ligação ficam identificadas como falha ou indisponibilidade, nunca como “não encontrado”. Cada fatura conserva histórico de verificações, validações e propostas.

A cache evita voltar a descarregar e extrair ficheiros cuja versão não mudou. A lista de metadados Drive é atualizada para detetar novos ficheiros, alterações e renomeações; não existe um cursor incremental ERP validado, pelo que a consulta ERP continua a usar o snapshot real do conector. Cada execução verifica até 200 faturas, dando prioridade às menos recentemente verificadas, e extrai até 100 ficheiros novos/alterados. Repita para continuar quando a pesquisa estiver parcial. Não se associa automaticamente com uma pesquisa incompleta.

Limites: 20 pastas configuradas, 1000 subpastas por raiz, 5000 ficheiros de pesquisa, ficheiros até 5 MB, QR em PDFs até 40 páginas. Texto/OCR limitados a 10 páginas; extrações parciais exigem revisão. OCR tem limite de tempo e não é garantia de leitura. O conector atual não suporta lançamento de compras noutra moeda nem escrita de fornecedores/compras.

## Ficheiros e testes

- `invoice_workflow.py`: configuração, verificação, associações, validação, propostas, histórico e agendamento.
- `invoice_evidence.py`: identidade conservadora e extração local de QR/texto/OCR.
- Modelos `InvoiceWorkflowSettings`, `InvoiceWorkflowCheck`, `InvoiceEvidenceCache`, `InvoiceEvidenceValidation`, `InvoiceWorkflowRun`; migração **0034** em todos os esquemas da empresa.
- `InvoiceWorkflow.tsx`/CSS: lista simples, resumo, filtros, paginação, revisão conjunta e configurações; integração no assistente existente.
- Testes de backend: cenários de aceitação com base de dados isolada e Drive/ERP simulados, PDFs reais com QR, valores/candidatos divergentes, repetição, alteração de ficheiro, permissões e agendamento.
- Teste de interface `scripts/test-invoice-workflow-ui.py`: dados fictícios, filtros, estados independentes, revisão conjunta, confirmação e ecrã móvel emulado.

As consultas e escritas numa conta Google/ERP reais não são demonstradas pelos testes simulados. Nesta instalação, OCR ainda depende do executável e dos idiomas indicados. A criação no ERP continua bloqueada até existir um contrato operacional de criação de fornecedores e compras, campos obrigatórios, idempotência e consulta após timeout.
