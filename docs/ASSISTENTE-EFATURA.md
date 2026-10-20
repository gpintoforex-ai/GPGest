# Assistente e-Fatura: fatura, anexo e ERP

## Análise da implementação anterior

O GPGest já importava e-Fatura para `Purchase`/`DocumentImport`, mantinha estados documentais independentes em `DocumentState`, consultava compras Wintouch para `ERPDocument` e guardava associações em `DocumentERPLink`. Os anexos podiam ser locais (`PurchaseAttachment`) ou referências estáveis Drive (`DriveDocumentAttachment`). Já existiam importação Gmail, pesquisa de subpastas, QR fiscal, digitalização, propostas de despesa e exportação para contabilidade.

O assistente dividia a conferência em etapas, usava pontuações de nomes/texto e exigia confirmação de cada associação. A ausência no catálogo local podia ser confundida com ausência no ERP. Não havia uma lista que reunisse as duas verificações, cache de extração, validação específica do conteúdo ou agendamento destas conferências.

O contrato Wintouch existente é de leitura de compras em EUR. O conector permite outras escritas, como rubricas e liquidações, mas **não implementa criar fornecedores nem faturas de compra**. As novas propostas continuam identificadas como propostas, sem pedidos de escrita fictícios nem simulações apresentadas como operações reais.

## Conferência em seis etapas (e-Fatura ↔ anexos ↔ compras)

A conferência cruza três fontes independentes: **e-Fatura** (documentos comunicados pelos fornecedores), **anexos** (PDF, fotografia ou digitalização) e **compras** (registadas em Compras no modo autónomo, ou no ERP no modo integrado). Um registo do e-Fatura não substitui o documento original; ter um anexo não significa que a compra esteja lançada ou paga.

Em **Contabilidade → Conferência documental → Clara · Verificar documentos**:

1. **Pesquisar ficheiros** — escolher o período e a loja. O assistente procura ficheiros CSV e XLSX nas pastas e-Fatura configuradas ou, sem elas, nas pastas de Anexos e respetivas subpastas. Cada ficheiro é identificado **pelo conteúdo**, com as colunas que o importador reconhece. Só as exportações e-Fatura são listadas, com nome, data, formato, número de documentos, período e estado: *Novo*, *Já importado* (incluindo o mesmo conteúdo carregado antes do computador) ou *Alterado desde a importação*. Extratos bancários e outras listas aparecem em *ficheiros ignorados*, com o motivo; as exportações de outra empresa (quando o ficheiro indica o adquirente) também. SAF-T de vendas (XML) não entra na pesquisa. A identificação de cada versão de ficheiro fica em memória, para não voltar a descarregá-lo. Também é possível carregar o ficheiro do computador.
2. **Importar e-Fatura** — pré-visualização com as colunas reconhecidas, o período do ficheiro, documentos novos, já existentes, conflitos (com o campo divergente) e documentos **não importados**: os que a coluna *Situação* indica como *Documento Anulado pelo Emitente* e os de total 0,00 €. Estes ficam visíveis com o motivo, sem bloquear o resto do ficheiro. Se um documento anulado já existir como ativo na aplicação, a pré-visualização avisa; a importação não o altera (a correção faz-se por estorno). Avisa quando há documentos fora do período a conferir, quando o ficheiro não indica o NIF do adquirente e quando o mesmo conteúdo já foi importado. Um NIF de adquirente diferente do da empresa impede a importação; conflitos impedem a confirmação. Guarda o ID e a versão do ficheiro no Drive e a impressão digital SHA-256 do conteúdo. O ficheiro no Drive nunca é movido, renomeado ou apagado.
3. **Procurar anexos** — pesquisa as pastas de anexos (QR fiscal, texto do PDF ou OCR local; o nome do ficheiro é só um indício). Só associa automaticamente um candidato único, cujo conteúdo confirme NIF, tipo, número, data e total, com a pesquisa completa.
4. **Conferir compras** — no modo autónomo, procura a compra em **Compras** (ligada ao documento ou com a mesma identidade NIF + tipo + número) e compara data, total, base, IVA e estado. No modo integrado, consulta o ERP. Diferenças de arredondamento dentro da tolerância configurada ficam visíveis como nota; acima dela são divergências. Uma falha do ERP aparece como *Consulta ao ERP indisponível*, nunca como compra em falta.
5. **Rever propostas**:
   - **Anexos propostos**, com pré-visualização, campos coincidentes, divergentes e não identificados, o motivo e a força: *forte* (o conteúdo confirma tudo e é o único candidato), *possível* (o conteúdo identifica o documento, mas há divergências) ou *ambígua* (só pelo nome, ou vários candidatos). Ações: **Associar**, **Escolher outro** (abre a ficha para anexar outro ficheiro) e **Rejeitar** (deixa de ser sugerido; é possível voltar a considerar). As propostas fortes podem ser confirmadas em lote depois de pré-visualizadas; o servidor volta a confirmar que continuam fortes e únicas.
   - **Lançamentos em falta** — quando há anexo validado e não há compra: **Preparar lançamento** (ver abaixo).
   - **Documentos em falta** — **Pedir ao fornecedor** por email (SMTP configurado em Configurações → Email), com pré-visualização e confirmação. A lista dos documentos segue em anexo (CSV) e, depois do envio aceite, ficam como *Solicitado ao fornecedor*.
6. **Concluir** — resumo dos documentos conferidos e das pendências, agrupadas pela próxima ação.

O assistente guarda no servidor a etapa, o período e a loja de cada utilizador e retoma-os quando volta a ser aberto. Importações e associações são gravadas no momento em que são confirmadas.

### Estados e próxima ação

Cada documento tem estados separados para **e-Fatura** (*Comunicado* ou *Sem correspondência*), **anexo** (*validado*, *por confirmar*, *em falta*, *por procurar*) e **compra** (*consistente*, *com divergências*, *rascunho por confirmar*, *não encontrada*, *ERP indisponível*, *por conferir*). O resultado (*Conferido*, *Pendente*, *Divergência*, *Para rever*) e a próxima ação são calculados a partir destas dimensões e explicados em linguagem simples. Por exemplo:

| Situação | Próxima ação |
|---|---|
| e-Fatura + compra, sem anexo | Pedir documento ao fornecedor |
| e-Fatura + anexo, sem compra | Preparar lançamento |
| Anexo + compra, sem e-Fatura | Confirmar no e-Fatura (assinalado para revisão, não é erro) |
| Três fontes, valores diferentes | Rever divergências da compra |
| Três fontes coincidentes | Nenhuma — documento conferido |

As compras confirmadas em Compras também criam um registo de documento; só conta como e-Fatura o que veio de uma importação e-Fatura. Compras e documentos ERP do período sem correspondência no e-Fatura aparecem na lista para revisão. Os filtros permitem procurar por fornecedor ou documento, estado e ação pendente.

### Preparar lançamento

No **modo autónomo**, cria um **rascunho** em Compras ligado ao documento, com o anexo de origem. Os campos vêm do e-Fatura; as linhas vêm do QR fiscal do anexo (base e IVA por taxa), quando existe. A taxa só é deduzida no continente e quando o IVA declarado lhe corresponde; caso contrário, fica por indicar. Sem QR, é possível propor uma linha a partir da base e do IVA do e-Fatura. Cada linha tem rubrica própria, que por omissão é a do fornecedor (uma fatura com mercadorias e serviços pode ter rubricas diferentes). O vencimento, a descrição das linhas e as rubricas são indicados ou confirmados pelo utilizador: não se inventam linhas, taxas, vencimentos nem condições de pagamento. O total das linhas tem de coincidir com o do documento.

Se não existir entidade com o NIF, propõe criar o fornecedor (sujeito a confirmação). Uma entidade inativa ou não classificada como fornecedor tem de ser revista antes. Antes de criar, verifica de novo os duplicados: outro rascunho ou compra para o mesmo documento, a mesma identidade de fornecedor em Compras ou uma associação ERP. O rascunho não fica pago e não movimenta caixa, banco nem stock. A confirmação faz-se em Compras, com as validações próprias.

### Propostas automáticas de lançamento (modo autónomo)

Em **Rever propostas → Lançamentos em falta**, cada fatura e-Fatura com anexo validado e sem lançamento tem uma proposta preparada automaticamente:

- **Linhas:** base e IVA por taxa do QR fiscal do anexo; sem QR, uma linha a partir da base e do IVA do e-Fatura, apenas quando a taxa resulta exatamente dos valores (0, 6, 13 ou 23%).
- **Rubrica:** a do fornecedor; pode ser escolhida na proposta.
- **Vencimento:** lido no anexo (texto do PDF ou OCR: «Data de vencimento», «Vencimento», «Data limite de pagamento», «Pagar até»); senão, o **prazo de pagamento habitual** do fornecedor (Tabelas → Fornecedores, em dias); nas faturas-recibo e simplificadas, a data do documento; senão, fica por indicar.
- **Pronta** quando há fornecedor ativo, rubrica, vencimento e linhas cujo total coincide exatamente com o documento. As restantes mostram o que falta e abrem **Rever e preparar**.

As propostas prontas lançam-se em lote, com confirmação: é criada a compra confirmada em Compras (com o anexo de origem) e o movimento previsto. Não se registam pagamentos nem movimentos de stock. A opção «Criar os fornecedores em falta» cria-os com o nome e o NIF do e-Fatura, com registo no histórico. Os duplicados são verificados de novo antes de cada lançamento; uma compra confirmada corrige-se com nota de crédito. Não existe lançamento sem confirmação.

### OCR local

O OCR usa o Tesseract instalado no servidor, sem enviar documentos para serviços externos. Configuração no `.env`: `GPGEST_OCR_COMMAND` (executável), `GPGEST_OCR_LANG=por+eng` e, opcionalmente, `GPGEST_OCR_TESSDATA` com uma pasta de idiomas própria (por exemplo, `.local/tessdata` com `por.traineddata` de [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast)). Ative-o em Configurações → Verificação automática e-Fatura. Ficheiros já analisados só são relidos com OCR quando a versão muda ou a configuração do OCR é alterada.

No **modo integrado**, o conector Wintouch não permite criar compras nem fornecedores. Mantém-se a proposta revista e exportável descrita abaixo, sem simular um lançamento.

### Configuração

Em **Configurações → Verificação automática e-Fatura**:
- **Pastas dos ficheiros exportados do e-Fatura** — distintas das pastas de anexos.
- **Pastas dos anexos de compras e despesas** — sem pastas indicadas, usa a pasta definida em Anexos.
- **Pesquisar também nas subpastas** — aplica-se às duas pesquisas.
- **Tolerância de arredondamento** — 0 a 5 cêntimos.

A migração **0050_efatura_drive** acrescenta a origem Drive das importações, as pastas e-Fatura, a opção de subpastas, as rejeições de anexos e o estado do assistente.

### Formatos suportados

O importador reconhece as colunas efetivamente presentes no ficheiro: NIF e nome do emitente (ou a coluna *Emitente* com «NIF - Nome»), número (incluindo *Nº Fatura / ATCUD*), tipo, data, total, IVA, base tributável e situação. Foi verificado com exportações reais do portal (colunas *Setor*, *Emitente*, *Nº Fatura / ATCUD*, *Tipo*, *Data Emissão*, *Total*, *IVA*, *Base Tributável*, *Situação*, *Comunicação Emitente* e *Comunicação Adquirente*; tipos *Fatura*, *Fatura-recibo*, *Fatura simplificada* e *Nota de crédito*; situações *Registado* e *Documento Anulado pelo Emitente*). O NIF do adquirente só é validado quando o ficheiro tem essa coluna; a exportação habitual do portal não a inclui, e nesse caso a pré-visualização avisa. Ficheiros sem as colunas obrigatórias são recusados, sem assumir outras colunas.

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
