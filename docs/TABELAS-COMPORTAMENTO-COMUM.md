# Tabelas — comportamento comum

Todas as tabelas de **Tabelas** usam a mesma página e as mesmas regras, definidas uma vez no servidor
(`backend/app/table_framework.py`) e na interface (`frontend/src/tables/`). Cada tabela declara apenas
os seus campos, validações, permissões, dependências e capacidades de integração
(`backend/app/table_specs.py`).

## Tabelas revistas

| Tabela | Chave | Integração Wintouch |
|---|---|---|
| Clientes | `customers` | Consultar, criar, editar, ativar, desativar e eliminar (sobre `entities`) |
| Fornecedores | `suppliers` | Consultar, criar, editar, ativar, desativar e eliminar (sobre `entities`) |
| Prazos de pagamento | `payment_terms` | Consultar, criar, editar e eliminar (`payment_terms`) |
| Armazéns | `warehouses` | Consultar, criar, editar, ativar, desativar e eliminar (`warehouses`) |
| Rubricas | `categories` | Consultar, criar e editar (`items`, fluxo de envio já validado) |
| Bancos | `banks` | Só na IAZELA (ver limitações) |
| Caixas | `registers` | Só na IAZELA; a associação a contas de caixa faz-se em Integrações |

**Meios de pagamento**, **Operadores / funcionários** e **Artigos e validades** mantêm páginas próprias
(ver limitações).

## Operações e interface

Em todas as tabelas, na mesma posição e com os mesmos ícones e textos:

- **Novo …** (canto superior direito), **Editar** (lápis), **Desativar/Ativar** e **Remover** (caixote), por linha.
  **Consultar** (olho) só aparece a quem não pode editar; quem edita abre a ficha pelo lápis.
- Pesquisa, filtro **Estado** (Ativos, Inativos, Todos), ordenação por coluna e paginação (10 a 100 por página).
- Com integração: filtros **Origem** (IAZELA/ERP) e **Sincronização** e a barra **Integração com o ERP**
  com um único botão, **Sincronizar**, e a regra para conflitos.
- Colunas comuns: código e descrição, origem, código no ERP, estado da sincronização e data da última
  sincronização concluída. Não há coluna de estado: um registo inativo mostra a etiqueta «Inativo» junto
  ao nome (e aparece esbatido); o filtro **Estado** escolhe Ativos, Inativos ou Todos. Os estados aparecem com ícone e texto, nunca só por cor.
- **Ver erro** abre os detalhes do último erro de sincronização.

A integração só aparece quando a empresa está em modo integrado. Cada operação indisponível mostra o
motivo (perfil, escrita não autorizada em Integrações, recurso desativado ou operação não suportada pelo conector).

## Estado ativo/inativo

- O estado é independente do estado de sincronização.
- Um registo inativo mantém-se no histórico e nos documentos existentes.
- Não pode ser escolhido em novos registos. O servidor recusa-o (`table_rules.ensure_usable`) e os
  seletores escondem-no. A exceção é manter o valor que um documento já tinha, ao editá-lo.
- Aplicado a rubricas (movimentos, previstos, compras, liquidações, repartições, fichas), caixas
  (abertura de folhas de caixa), armazéns (movimentos de stock) e prazos de pagamento (fichas de entidades).
- Novas colunas `active` em `categories`, `registers` e `stock_warehouses` (migração **0062**).

## Remoção

- Antes de remover, o servidor conta os registos dependentes. Com dependências, a remoção da aplicação
  é bloqueada e é proposta a desativação.
- A janela **Remover** distingue **Remover da aplicação**, **Remover do ERP** e **Remover de ambos**.
  Nenhuma opção vem escolhida e cada uma indica porque está indisponível.
- A confirmação mostra o registo e o destino.
- Se o ERP não permitir eliminar mas permitir desativar, a opção passa a **Desativar no ERP**.
- Em **Remover de ambos**, o ERP é tratado primeiro; se falhar, nada é removido na IAZELA.
- A eliminação nunca é propagada para o ERP de forma implícita. A ausência de um registo numa
  importação não o elimina na IAZELA.

## Sincronização com o ERP

### Gravação automática ao criar

Em modo integrado, um registo criado na IAZELA é gravado logo no ERP (mesmas regras e verificações da
sincronização). A mensagem final indica o resultado, por exemplo o código atribuído pela Wintouch. O
registo fica só na IAZELA, com o motivo, quando:
- o ERP não permite criar nessa tabela ou a escrita não está autorizada em Integrações;
- o utilizador não é administrador (fica para a próxima sincronização feita por um administrador);
- a empresa é de demonstração;
- já existe no ERP um registo com a mesma chave (ex.: o mesmo NIF), que deve ser ligado pela sincronização;
- o ERP recusa ou não responde: fica com o erro visível e é repetido pela sincronização.

As alterações e a ativação/desativação feitas na IAZELA ficam «Alterações pendentes» até à
sincronização seguinte.

### Sincronizar

**Sincronizar** recebe as alterações do ERP e envia as da IAZELA, sempre em dois passos.

1. **Pré-visualização**, sem alterar nada. O resumo agrupa os registos a criar, atualizar, desativar,
   remover, ignorar ou em conflito, com as diferenças campo a campo e o motivo de cada um.
2. **Execução** dos registos escolhidos. Um código de controlo garante que se executa o que foi mostrado;
   se os dados mudarem entretanto, a execução é recusada e pede nova pré-visualização.

O que é proposto:

- registos do ERP em falta na IAZELA são criados; os ligados alterados no ERP são atualizados;
- alterações feitas na IAZELA são enviadas para o ERP;
- registos antigos que só existem na IAZELA são propostos para criar no ERP, mas **desmarcados**: marque
  os que quer enviar (evita criar no ERP, por engano, clientes que só fazem sentido localmente). O
  interruptor **Marcar todos**, numa barra destacada por cima da lista, marca ou desmarca a lista
  inteira, com a contagem «N de M marcados»;
- alterações dos dois lados ficam em conflito, resolvidas pela regra ou por decisão em cada registo.

Um gestor pode sincronizar para receber as alterações do ERP; as gravações no ERP exigem um
administrador e, para um gestor, aparecem como erro com esse motivo.

Resultado e recuperação:

- A execução corre **em segundo plano** no servidor (`POST …/sync/execute` devolve logo o
  identificador; `GET …/sync/runs/{id}` dá o progresso). A janela mostra uma barra de progresso com
  «N de M», o registo em tratamento, os erros até ao momento e o tempo estimado. Pode ser fechada:
  ao reabrir **Sincronizar**, o acompanhamento é retomado (`GET …/sync/running`).
- Uma execução sem sinal de vida há mais de 10 minutos (ex.: servidor reiniciado) é dada como
  interrompida e deixa de bloquear novas sincronizações; os registos já tratados ficam gravados.
- O resultado é apresentado por registo. Em falhas parciais, os concluídos ficam guardados e
  **Repetir os que falharam** volta a pré-visualizar só esses.
- Cada execução fica no histórico de sincronizações (recurso `tabela:<chave>`) e na auditoria.

### Identificação, duplicados e conflitos

- Ligação estável IAZELA ↔ ERP em `erp_links`, por empresa (esquema), ERP e empresa na API. As
  entidades usam o espelho `wt_entities` e as rubricas `category_integration_links`, ambos já existentes.
- Registos ainda não ligados ligam-se apenas por uma chave da tabela: código nos prazos e armazéns, NIF
  nas entidades. Se só a descrição coincidir, o registo aparece como conflito e só é ligado por decisão explícita.
- Repetir uma importação não volta a criar registos já ligados.
- **Código do ERP único nas entidades:** se a Wintouch devolver, para um código que já existe na IAZELA,
  outro identificador (entidade recriada ou identificador mudado), não nasce uma segunda entidade.
  Com o mesmo NIF, ou sem NIF num dos lados, a entidade existente é atualizada com os dados do ERP,
  incluindo o identificador novo, e mantém a ficha local (rubrica, prazo, histórico). Com outro NIF, o
  código foi reutilizado: a antiga fica marcada como eliminada no ERP e a nova é importada. Se as duas
  existirem na Wintouch, a importação é recusada («código repetido na Wintouch») até se corrigir lá.
  A sincronização completa das entidades aplica a mesma regra às repetições já existentes: fica a
  entidade que veio do ERP, a ficha local passa para ela e a outra fica marcada como eliminada; os
  códigos repetidos na própria Wintouch aparecem no aviso da sincronização.
- `base_values` guarda os valores acordados na última sincronização. Alterado só de um lado, a alteração
  segue para o outro; alterado nos dois lados, é **conflito**.
- A regra **Em conflito** (por tabela, só administrador) pode ser **Perguntar em cada registo**
  (por omissão), **Prevalece o ERP** ou **Prevalece a IAZELA**. Um conflito sem decisão fica marcado e nada é alterado.
- Campos geridos pelo ERP (nome e NIF das entidades, com a origem das entidades no ERP) são só de leitura
  na IAZELA e prevalecem sempre do lado do ERP.

### Escrita no ERP

- A escrita só é possível com **escrita no ERP** autorizada em Configurações → Integrações. Na empresa
  de demonstração exige ainda confirmação expressa.
- Na criação, o identificador é gerado e gravado antes do POST. Uma falha de comunicação deixa o
  registo «por confirmar»: a sincronização seguinte verifica-o por GET em vez de repetir a criação.
- Um registo só fica **Sincronizado** depois de um GET ao ERP devolver os valores enviados.
- Uma recusa do ERP (HTTP 4xx) fica como **Erro**, com a mensagem do ERP, e pode ser repetida.
- Na atualização, se o registo mudou no ERP depois da pré-visualização (`Version`), o envio é recusado.

## Operações por integração (Wintouch)

| Tabela | Consultar | Criar | Editar | Ativar/Desativar | Eliminar |
|---|---|---|---|---|---|
| Prazos de pagamento | Sim | Sim | Sim | Não — a Wintouch não tem estado nos prazos | Sim |
| Armazéns | Sim | Sim | Sim | Sim (`IsDisabled`) | Sim |
| Clientes / Fornecedores | Sim | Sim (ver «Criar entidades na Wintouch») | Sim (Cliente, Fornecedor, Ativo, prazo, morada e contactos; nome/NIF só com origem local) | Sim (`Enabled`) | Sim, só sem documentos na Wintouch (`has_movements`); com documentos, desativar |
| Rubricas | Sim | Sim (fluxo existente) | Sim (nome, fluxo existente) | Não — sem estado na Wintouch | Não |
| Bancos | — | — | — | — | — |
| Caixas | — | — | — | — | — |

Os outros conectores (Eticadata, ZoneSoft) não declaram capacidades e não mostram integração.

### Criar entidades na Wintouch

Um cliente ou fornecedor criado na IAZELA aparece como «Apenas local». Para o criar na Wintouch,
selecione-o e use **Enviar para o ERP**: a pré-visualização mostra «Criar no ERP».

- **Código:** é pedido primeiro à numeração automática da Wintouch (`generate_code` para clientes,
  `generate_code_supplier` para fornecedores). Se a API não o devolver (nesta empresa devolvia vazio e
  erro 500), usa-se o próximo número da série que a Wintouch já usa para o mesmo tipo (ex.: `F0104` →
  `F0105`; clientes `C…`). Um código já existente ou reservado por outra criação nunca é usado.
- **País e moeda:** pelo prefixo do NIF (ex.: `ES…`); sem prefixo, Portugal. A moeda é a do país.
- **Dados enviados:** nome, NIF, Cliente/Fornecedor, estado, prazo de pagamento do papel respetivo e
  morada principal (morada, código postal, localidade, telefone e email). Os campos de controlo de
  crédito seguem os valores das entidades criadas na própria Wintouch.
- **Prazo de pagamento:** se a entidade tiver um prazo, ele tem de existir antes na Wintouch. Caso
  contrário, a criação é recusada com indicação para o enviar primeiro em Prazos de pagamento.
- **Duplicados:** a pré-visualização ignora entidades cujo NIF já existe na Wintouch (devem ser
  importadas para ficarem ligadas). Antes do pedido, o NIF volta a ser confirmado
  (`entities/by_vat`).
- **Segurança da escrita:** o identificador e o código são gravados antes do pedido. Se a ligação
  falhar, a sincronização seguinte procura a entidade por esse identificador e nunca cria uma segunda.
  A entidade só fica ligada depois de a Wintouch devolver os dados enviados. Se a morada não vier com
  a entidade, é criada à parte (`addresses`) e confirmada.
- No fim, a ficha da IAZELA fica ligada à entidade da Wintouch (origem ERP, código no ERP preenchido),
  sem duplicar a ficha.
- Exige a capacidade **Criar e atualizar clientes e fornecedores** e a escrita no ERP autorizadas
  em Integrações.

### Morada e contactos das entidades

A Wintouch guarda morada, código postal, localidade, telefone e email na **morada principal** da
entidade (`EntityAddresses`), e não na entidade. A sincronização de entidades lê as moradas à
parte (`GET /addresses`), junta-as a cada entidade por `EntityID` e guarda a principal na cópia local
(`wt_entities`, migração **0064**). A lista de entidades **não** usa `embed=EntityAddresses`: com
muitas entidades (verificado com 465), a Wintouch recusa-a com HTTP 500 («O nº de registos é
demasiado elevado»), mesmo com `limit=1`. A leitura de uma só entidade com `embed` continua a funcionar.
Alterar estes dados na IAZELA numa entidade da Wintouch deixa-a em «Alterações pendentes»
(`contact_local`) até ser enviada. Cada gravação na Wintouch reenvia as moradas, porque a Wintouch
recusa entidades sem exatamente uma morada principal.

### Ficha de clientes e fornecedores

- **Meio de pagamento** (tabela «Meios de pagamento», migração **0065**), ao lado da rubrica e do prazo.
  É sincronizado com `PaymentMethodID`/`SupplierPaymentMethodID` através da associação entre os meios da
  Wintouch e os da IAZELA (Configuração inicial). Um meio sem associação única impede o envio, com
  indicação para a rever. Sem meio escolhido, uma entidade da Wintouch segue o meio do ERP.
- Ao **criar**, rubrica, prazo e meio de pagamento são obrigatórios (`create_required`); as fichas já
  existentes podem ser editadas sem eles.
- A janela de criar/editar tem dois separadores: **Dados gerais** (identificação e contactos, localização)
  e **Condições comerciais** (rubrica, prazo e meio de pagamento), com os botões de guardar sempre
  visíveis. Os separadores ocupam o mesmo espaço, pelo que a janela não muda de tamanho ao trocar. Se
  faltar um campo obrigatório noutro separador, a janela mostra-o ao guardar. A ficha cabe sem
  deslocação num portátil comum (1366×768).
- O tipo (cliente/fornecedor) não aparece na ficha: resulta da tabela onde se cria. Editar em
  «Fornecedores» mantém a marca de cliente, e vice-versa.
- Cada campo tem um ícone ilustrativo junto ao nome (NIF, nome, email, morada, prazo, meio, …).
- Em **Editar**, os botões **Anterior**/**Seguinte** mudam de registo pela ordem da listagem; com
  alterações por guardar, pede confirmação antes.
- **NIF:** o dígito de controlo é verificado ao escrever. Ao sair do campo, um NIF português válido e
  novo é consultado no VIES (com a indicação «A procurar no VIES…») e só preenche campos vazios.
- Tarefas demoradas (carregar, consultar o VIES, comparar ou sincronizar com o ERP, remover) mostram um
  texto com três pontos animados.

### Eliminar entidades

- **Remover de ambos:** elimina na Wintouch e retira da IAZELA. Exige que não haja movimentos na
  IAZELA nem documentos na Wintouch.
- **Remover do ERP:** elimina só na Wintouch; a ficha fica na IAZELA como «Apenas local», com a morada.
- **Desativar no ERP:** para entidades com documentos na Wintouch, que não podem ser eliminadas.
- Uma entidade do ERP não pode ser removida só da IAZELA. A cópia da Wintouch é mantida para os
  documentos já importados, marcada como eliminada no ERP (`erp_deleted_at`, migração **0063**), e
  deixa de aparecer nas listas e nas importações.
- Se a entidade já tiver sido apagada diretamente na Wintouch, **Remover de ambos** apenas conclui a
  remoção na IAZELA.

## Permissões e rastreabilidade

As permissões são validadas no servidor, por operação:

| Operação | Perfis |
|---|---|
| Consultar | Todos |
| Criar, editar, ativar/desativar, remover | Administrador e gestor |
| Sincronizar (receber do ERP) | Administrador e gestor |
| Gravar no ERP (ao criar, ao sincronizar) e remover no ERP | Administrador |

Os armazéns exigem ainda a permissão de stock «ajustar stock».

A auditoria regista quem, quando, em que empresa (`empresa`), em que tabela e registo, a ação, o
destino e o resultado (`concluido` ou `erro` com a mensagem). As tabelas que delegam nas regras já
existentes (entidades) mantêm as suas entradas de auditoria.

## Acrescentar uma tabela

1. Criar uma subclasse de `TableSpec` em `table_specs.py`. Definir `key`, `title`, `singular`,
   `feminine`, `model`, `fields` (`TableField`: tipo, obrigatório, obrigatório só ao criar, limites, ajuda,
   e a disposição na janela: separador `tab`, secção `group` e largura `span` numa grelha de 6 colunas), as `permissions`
   (opcional), `dependencies()` e, se necessário, `clean()`, `unique_checks()` e `blocked_reason()`.
2. Para integrar, criar uma `ErpBinding` com o recurso, `synced_fields`, `match_field`, `capabilities`
   e `reasons` (motivo de cada operação não suportada) e o mapeamento `canonical()`, `create_payload()`,
   `update_payload()` e `active_payload()`.
3. Registar a tabela com `register(...)` e mostrá-la com `<TablePage tableKey="…" />`.

Os testes de referência estão em `backend/tests/test_tables.py` e `scripts/test-tables-ui.py`.

## Limitações conhecidas

- **Escrita na Wintouch não validada com dados reais.** Prazos, armazéns e entidades (incluindo a
  criação) usam os recursos documentados da API v1 e o formato confirmado por leitura das entidades
  existentes. Só foram testados contra uma API simulada. A empresa atual tem a escrita
  desligada. Antes de a ligar, deve validar-se num registo de teste.
- **Bancos:** a Wintouch desta empresa não tem bancos e a estrutura do recurso não pôde ser confirmada.
- **Caixas:** as contas de caixa da Wintouch têm turnos e estado operacional. Só se associam, em Integrações.
- **Meios de pagamento:** mantêm a página própria, com a conta bancária por estabelecimento. Os meios
  da Wintouch têm campos fiscais (SAF-T) e associam-se em Integrações.
- **Operadores / funcionários:** incluem utilizadores e credenciais. Nunca são sincronizados (os
  funcionários da Wintouch incluem a palavra-passe).
- **Artigos e validades:** mantêm o módulo de stock, com lotes, preços e o fluxo validado de criação de artigos no ERP.
- O estado «Alterações pendentes» deteta alterações locais sem consultar o ERP. As alterações feitas
  só no ERP aparecem na pré-visualização seguinte.
- Filtros, ordenação e paginação são aplicados no servidor sobre a tabela em memória, adequado aos
  catálogos atuais (centenas de registos).
