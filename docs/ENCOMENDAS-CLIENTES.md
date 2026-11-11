# Encomendas de clientes — análise e implementação

## Análise do projeto e arquitetura proposta

Levantamento realizado antes de desenvolver (21/10/2026):

| Necessidade | Estrutura confirmada | Decisão |
|---|---|---|
| Clientes e fornecedores | `BusinessEntity` e `WintouchEntity`, com nome, contactos, papéis e estado | Reutilizar entidades existentes; entidades ERP recebem uma referência local única quando utilizadas, sem criação ou alteração no ERP. Clientes manuais ficam apenas no pedido. |
| Artigos | `StockArticle`, metadados, códigos de barras e ligação a `WintouchProduct` | Selecionar artigos existentes; produtos provisórios ficam nas linhas do pedido, sem criar artigos no ERP. |
| Compras e receções | `ProcurementDocument`, `ProcurementLink`, confirmação, PDF e email | Converter simulações confirmadas em encomendas deste circuito. Receber pelo mesmo serviço; permitir associar uma receção existente sem repetir stock. |
| Stock | `StockEntry`, armazéns, lotes, FEFO e bloqueio transacional por empresa | Reservas por artigo/armazém/lote; saídas respeitam reservas. Entrega local liberta a reserva e regista uma saída uma única vez. |
| ERP | Política e permissões existentes; artigos ERP de leitura | Consultar o catálogo sincronizado. O conector atual não expõe receções detalhadas nem permite escrever stock: bloquear essas operações em vez de inventar uma sincronização. |
| Email | `CompanyEmailSettings`, `EmailDispatch`, SMTP, pré-visualização e envio confirmado | Reutilizar os envios do fornecedor; criar comunicações transacionais com o mesmo transporte e histórico. Não enviar na simulação nem ao abrir WhatsApp. |
| Autorização | `db_session` por empresa, `scope`, `authorize`, permissões de custo/stock | Leitura por loja; registo/entrega por administrador, gestor ou operador; compras e custos reservados à gestão autorizada. |
| Interface | ModuleTabs, DetailDrawer, AppDialog, ManagedForm, DataTable e filtros compactos | Um destino dentro de Compras e Stock, com seis separadores e formulários por tarefa. |

Dados novos: pedidos, linhas, reservas, condições comerciais por fornecedor, simulações, relações entre linhas de compra e pedidos e eventos/contactos. Quantidades em Decimal; montantes monetários arredondados por linha. Migração aditiva `0057` em todos os esquemas das empresas.

Sequência: modelo e simulador → pedidos/reservas → ligação ao circuito de compras e receções → comunicações/entregas → interface → testes de quantidades, concorrência, repetição, permissões e isolamento.

## Regras de integridade

- Preparar uma encomenda de compra é diferente de enviar email e de receber confirmação do fornecedor.
- A simulação guarda propostas, sem criar compras, movimentos de stock ou mensagens enviadas.
- Quantidades em simulações ativas não podem voltar a ser atribuídas a outra simulação. Reposição adicional é separada dos pedidos de clientes.
- Receções confirmadas são a fonte das quantidades recebidas. Associar uma receção existente não cria outra entrada.
- Reservas não criam stock. Confirmação comercial não disponibiliza produtos.
- Cancelar quantidades pendentes liberta as respetivas reservas/atribuições; não cancela silenciosamente uma compra já preparada.
- Operações mutáveis usam chave de pedido, revisão e bloqueio por empresa. Repetir a mesma operação não duplica quantidades nem mensagens.
- Contactos servem apenas o acompanhamento do pedido, respeitando a autorização e o canal escolhido. WhatsApp aberto fica preparado, exigindo confirmação manual do resultado.

## Utilização, validação e limitações

### Percurso de utilização

1. Abra **Compras e Stock → Encomendas de clientes**. São seis separadores, pela ordem do trabalho: **Centro de Encomendas** (indicadores e necessidades consolidadas, ver `CENTRO-ABASTECIMENTO.md`), **Pedidos de clientes**, **Encomendas a fornecedores**, **Receção e conferência**, **Condições dos fornecedores** e **Histórico**. O Centro substitui o antigo Resumo e as Necessidades de Compra. Os Levantamentos passaram a ser o filtro **Prontos a levantar** dos pedidos, e a Conferência ERP está no separador da receção.
2. Em **Pedidos de clientes**, os filtros **Em aberto**, **Prontos a levantar**, **Concluídos** e **Todos** mostram quantos pedidos há em cada um. **Precisa de atenção** junta os atrasos, os clientes por contactar, os produtos sem fornecedor e as encomendas abaixo do mínimo. Para registar um pedido, escolha **Novo pedido**. Pesquise o cliente por nome/contacto ou preencha manualmente. Adicione artigos por código, descrição ou EAN, ou uma descrição provisória. Indique quantidades, preço de venda sem IVA, fornecedor, prazo, prioridade e autorização/canal de contacto.
3. **Editar pedido** (na grelha ou no detalhe) abre a mesma janela do novo pedido, com cliente, contactos, prazo, prioridade, observações e produtos. Pode corrigir descrições, preços, fornecedores e quantidades, e acrescentar ou retirar produtos. Um produto já reservado, em compra ou entregue mantém o artigo e não desce abaixo da quantidade em curso. Retirar um produto livre cancela-o e exige motivo. Fica no histórico. A loja não muda. Produtos entregues ou cancelados não aparecem na edição. Cada janela usa cores para o que pede atenção: estado de cada produto, urgência, atraso, falta de fornecedor, contacto não autorizado, quantidade por encomendar (âmbar) e quantidade pronta a entregar (verde).
   No detalhe pode também corrigir contactos, ajustar quantidades, associar um artigo real, reservar stock disponível, libertar reservas ou cancelar quantidades pendentes. Produtos provisórios não movimentam stock; associe-os antes de reservar ou preparar compras. Se já estiverem numa simulação, remova-os dessa simulação ou cancele o rascunho primeiro.
4. A encomenda mínima aceite pelo fornecedor, os portes, os portes grátis e o prazo de entrega definem-se uma vez na **ficha do fornecedor** (Tabelas → Fornecedores → Condições comerciais → Encomendas ao fornecedor, ou o lápis junto ao fornecedor). O simulador usa-os automaticamente, e **Editar na ficha** abre a ficha. Depois de gravar, a simulação aberta atualiza-se. **Ajustar condições só nesta simulação** muda os valores apenas nessa simulação.
   **Encomenda mínima e portes grátis são regras diferentes:**
   - a **encomenda mínima aceite pelo fornecedor** é o valor abaixo do qual o fornecedor recusa a encomenda; a encomenda real só se prepara abaixo dele com exceção autorizada;
   - **portes grátis a partir de** é o valor abaixo do qual a encomenda paga portes.

   Exemplo: mínimo de 50 € e portes grátis a partir de 150 €. Uma encomenda de 30 € é recusada, uma de 100 € paga portes e uma de 200 € não paga. Quando o fornecedor só tem a regra dos portes («150 € para não pagar portes»), preencha apenas «Portes grátis a partir de» e deixe a encomenda mínima vazia. Em **Condições dos fornecedores**, além destes, configure a base de cálculo do mínimo, descontos e escalões, portes, limiar de portes gratuitos, IVA, moeda, custos por artigo e prazos dos alertas. A coluna **Por encomendar** mostra quantos produtos esperam por cada fornecedor.
   **Pesquisas:** o artigo procura-se por código, nome ou EAN (sem acentos, várias palavras em qualquer ordem; um código ou EAN exato, como o do leitor, escolhe logo o artigo). O fornecedor procura-se por nome ou NIF. Junto ao fornecedor escolhido, **Editar fornecedor** abre a ficha das Tabelas (a mesma das Tabelas → Fornecedores, com as mesmas permissões). A ficha também abre na grelha de condições e nas necessidades de compra.
5. No **Centro de Encomendas**, veja o que falta comprar por artigo e por fornecedor, com o mínimo de cada fornecedor. **Simular encomenda** abre o simulador já preenchido (ver `CENTRO-ABASTECIMENTO.md`).
6. Em **Encomendas a fornecedores**, **Encomendas necessárias para pedidos de clientes** mostra o que ainda falta encomendar para satisfazer os pedidos, por fornecedor e loja. Cada linha indica a quantidade em destaque, os produtos, o número do pedido, o cliente e a **data do pedido**. Mostra também o valor estimado, com uma barra até ao **mínimo de encomenda sem portes**. Na coluna **Entrega ao cliente** vê-se a entrega pretendida de cada pedido e os **dias em atraso** (vermelho), «Entrega hoje» (âmbar) ou os dias que faltam; sem data de entrega, os dias desde o pedido. A cor da linha indica o passo seguinte: vermelho = pedido em atraso ou produto sem fornecedor, âmbar = abaixo do mínimo ou sem custo conhecido, verde = pronta a encomendar. Sem custo conhecido o valor não conta como «pronto»: indique o preço na simulação. Os dias em atraso aparecem também na grelha dos pedidos de clientes. Os produtos sem fornecedor aparecem primeiro, a vermelho. **Simular encomenda** abre o simulador já com essas linhas; **Abrir rascunho** abre a simulação em curso desse fornecedor.
   Por baixo, a grelha **Simulações e encomendas a fornecedores** acompanha cada proposta, do rascunho à confirmação, com o estado a cores.
   **Nova simulação**: selecione fornecedor/loja e agregue os pedidos pendentes (um a um ou **Adicionar todos**).
   - O simulador mostra as **condições do fornecedor**: mínimo de encomenda sem portes e respetivo critério, portes e portes grátis, prazo de entrega. Se o critério do mínimo incluir portes, aparece um aviso.
   - **Reposição: artigos com vendas no período** lista os artigos do fornecedor com vendas no **período de vendas** escolhido (30, 60, 90, 180 ou 365 dias): os que têm **mínimo** na ficha entram quando o stock livre está abaixo dele; os que **não têm mínimo** entram só pelas vendas do período. A predefinição é o período do fornecedor, configurável em **Condições dos fornecedores → Prazos** (90 dias por omissão). A quantidade proposta desconta o que está em trânsito e os pedidos dos clientes, e respeita mínimos e múltiplos acordados. Artigos com validade sem dias de validade configurados ficam limitados ao mínimo/nível de reposição da ficha (sem eles, às vendas do período).
   - **Bonificação simulada:** com uma bonificação acordada com o fornecedor (**na compra de X, oferta de Y**), a oferta conta para a necessidade e propõe-se a menor compra que, somada à oferta, a cobre (ex.: necessidade 12 com 10 + 2 → comprar 10). A coluna **Comprar (bonificação)** mostra a oferta, o total recebido e o custo unitário efetivo; sem oferta na quantidade proposta, indica quanto falta comprar para a receber. Ao preparar a encomenda, a oferta é acrescentada como linha a preço 0.
   - **Em cada produto de compra:** o campo **Quantidade de oferta** fica vazio para usar a bonificação acordada (o valor automático aparece no próprio campo) ou recebe a oferta combinada nesta encomenda, que prevalece; tem de respeitar a precisão do artigo. Com bonificação, o botão **Comprar X e receber Y de oferta** (ou **Completar para X**) acerta a quantidade de compra ao bloco seguinte da bonificação e volta a pôr a oferta automática. Os artigos ficam marcados; **Adicionar à encomenda** junta-os como reposição. As restantes sugestões ficam em **Outras sugestões de reposição**.
   - **Calcular simulação** apresenta totais e valor em falta para o mínimo sem criar documentos. **Guardar simulação** guarda o rascunho e as atribuições aos clientes.
   **Editar** (na grelha ou no detalhe) abre a mesma janela da emissão:
   - **Rascunhos:** editam-se livremente.
   - **Encomendas já emitidas, ainda sem receções nem faturas:** ao guardar, a encomenda real é atualizada com o **mesmo número** e a versão anterior fica registada (`versions`). As atribuições aos clientes acompanham as linhas. Se a encomenda já tinha sido enviada, fica a âmbar «Alterada: reenviar ao fornecedor», e a pré-visualização do email volta a permitir o envio. O aviso desaparece quando o reenvio é aceite. Uma confirmação do fornecedor anterior deixa de valer e fica no histórico.
   - **Com receções ou faturas:** não se editam. O botão fica inativo com o motivo: receba o que chegou e faça outra encomenda para o resto.
7. No detalhe da simulação, prepare explicitamente a encomenda real no circuito existente de compras. Se o mínimo não for atingido, é necessária uma exceção expressa. Consulte o PDF e pré-visualize a mensagem antes de confirmar o envio ao fornecedor. Registe a confirmação comercial separadamente.
8. Em **Receção e conferência**, registe as quantidades efetivamente recebidas, lotes e validades, ou associe uma receção já confirmada da mesma encomenda. A associação não repete a entrada de stock. Receções parciais mantêm o saldo em aberto; quantidades atribuídas são reservadas aos clientes e o excedente fica em stock geral. No mesmo separador estão os originais (OCR e cloud) e a **Conferência com o ERP**. Uma receção confirmada em **Compras e Stock → Receções de mercadoria** também reserva automaticamente aos clientes o que vem das encomendas deles (ver `COMPRAS-E-STOCK.md`). Não é preciso associá-la depois.
9. Nos **Pedidos de clientes → Prontos a levantar**, contacte o cliente autorizado com produtos disponíveis. O email exige pré-visualização e confirmação explícita; WhatsApp abre a mensagem preparada e requer registo manual do resultado. Registe apenas a quantidade efetivamente entregue. **Histórico** conserva utilizador, momento, ação e resultado.

### Numeração dos pedidos

Cada pedido de cliente recebe um **número sequencial** da empresa: PC-0001, PC-0002, … O número é atribuído ao gravar, sob o bloqueio de escrita da empresa, por isso dois pedidos simultâneos nunca ficam com o mesmo. Repetir a mesma gravação não gasta número.

As **encomendas a fornecedores** novas têm o mesmo tipo de numerador: EC-0001, EC-0002, … Aplica-se às encomendas preparadas a partir das simulações e às criadas em Compras e Stock sem número do documento; um número escrito pelo utilizador mantém-se. As encomendas emitidas antes desta mudança mantêm o número longo, porque podem ter sido enviadas ao fornecedor com ele.

A migração **0072** renumerou os pedidos existentes por ordem de criação. O número antigo («PC-» seguido do identificador interno) ficou em `legacy_number` e continua a encontrar o pedido na pesquisa: é o que aparece nos movimentos de stock das entregas feitas antes da mudança. A migração é repetível e o `downgrade` repõe os números antigos.

### Dados e API

As tabelas novas são `customer_orders`, `customer_order_lines`, `customer_order_reservations`, `supplier_order_terms`, `customer_purchase_simulations`, `customer_supply_links` e `customer_order_events`. Estão no esquema de cada empresa. Documentos, movimentos de stock e envios continuam nas estruturas existentes.

Os endpoints ficam em `/api/customer-orders`: listagem, catálogo, histórico, criação/correção de pedidos, ações por linha, condições por fornecedor, cálculo/rascunhos de simulação, preparação, confirmação do fornecedor, receção e contactos. O envio do PDF ao fornecedor reutiliza `/api/procurement`. A documentação interativa da API apresenta os contratos de cada operação.

Instalação do backend: realizar cópia de segurança, instalar a versão de código e executar `alembic upgrade head` na pasta `backend`, com o ambiente da instalação, antes de reiniciar a API. A migração `0057` é aditiva e foi aplicada na instalação local. A API pública, alojada noutra máquina, precisa desta atualização antes de disponibilizar o módulo publicamente.

### Verificação

- `backend/tests/test_customer_orders.py`: cálculo monetário e mínimos, agregação e rastreio, receções parciais, associação sem duplicação de stock, reservas, lotes expirados, entregas, cancelamentos, revisões, idempotência e concorrência.
- O mesmo conjunto cobre permissões por função/loja, isolamento entre empresas, referências ERP, migração repetível, contactos autorizados e envio SMTP com transporte substituído por teste.
- Regressões do circuito de compras, inventário, modos de operação e plataforma executadas em conjunto.
- `scripts/test-customer-orders-ui.py`: menu de seis separadores sem duplicados, filtros dos pedidos, criação de artigos reais/provisórios, pesquisa EAN, agregação, cálculo do mínimo e proteção de alterações por guardar, em desktop e telemóvel. Todas as chamadas API são interceptadas: não cria dados reais nem envia mensagens.
- Compilação de produção da interface, `check:ui`, Ruff e verificação do diff.

Resultado da validação em 21/10/2026: a execução conjunta terminou com 92 testes aprovados e uma falha de importação no novo teste ERP (`Company` importado do módulo incorreto). A importação do teste foi corrigida para `app.platform_models`; esse teste foi executado separadamente e passou. Não se repetiu toda a bateria após esta correção exclusiva do teste. O circuito principal tinha também passado anteriormente os 57 testes de encomendas/compras/inventário. Os testes de interface passaram a 1440 e 390 píxeis; compilação de produção, `check:ui` e Ruff passaram. A API local reiniciou com a migração 0057 e respondeu HTTP 200 em saúde e OpenAPI.

### Limitações explícitas

- O módulo não emite documentos fiscais. A entrega regista uma saída de stock local e deve ser acompanhada pelo procedimento fiscal aplicável da instalação.
- O conector ERP atual não fornece receções detalhadas nem escrita de stock. Essas operações ficam bloqueadas quando a autoridade de stock é o ERP ou o artigo é de leitura; não são apresentados movimentos fictícios como sincronizados.
- Condições noutra moeda podem ser simuladas, mas o circuito de compras existente só suporta EUR; não se inventam taxas de câmbio.
- Abrir WhatsApp não comprova envio ou leitura. O resultado é declarado pelo operador. A aceitação SMTP também não comprova leitura; estados de falha/incerteza seguem o transporte existente, sem repetição automática.
- Cancelar o pedido de um cliente não anula uma encomenda já preparada ao fornecedor. A quantidade recebida sem atribuição ativa passa a stock geral.
- A listagem atual carrega o conjunto autorizado de pedidos. Instalações com grande volume deverão acrescentar paginação no servidor e política de retenção dos históricos.
