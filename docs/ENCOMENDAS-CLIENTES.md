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
| Interface | ModuleTabs, DetailDrawer, AppDialog, ManagedForm, DataTable e filtros compactos | Um destino dentro de Compras e Stock, com oito áreas internas e formulários por tarefa. |

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

1. Abra **Compras e Stock → Encomendas de clientes**. O Resumo mostra pedidos pendentes, disponibilidade, notificações e alertas. As oito áreas internas mantêm a navegação no mesmo módulo.
2. Em **Pedidos de clientes**, escolha **Novo pedido**. Pesquise o cliente por nome/contacto ou preencha manualmente. Adicione artigos por código, descrição ou EAN, ou uma descrição provisória. Indique quantidades, preço de venda sem IVA, fornecedor, prazo, prioridade e autorização/canal de contacto.
3. No detalhe pode corrigir contactos, ajustar quantidades, associar um artigo real, reservar stock disponível, libertar reservas ou cancelar quantidades pendentes. Produtos provisórios não movimentam stock; associe-os antes de reservar ou preparar compras. Se já estiverem numa simulação, remova-os dessa simulação ou cancele o rascunho primeiro.
4. Em **Fornecedores**, configure mínimo e respetiva base de cálculo, descontos e escalões, portes, limiar de portes gratuitos, IVA, moeda, custos por artigo e prazos dos alertas.
5. Em **Simulações**, selecione fornecedor/loja e agregue os pedidos pendentes. Pode acrescentar reposição para stock e ajustar preços. **Calcular simulação** apresenta totais e valor em falta para o mínimo sem criar documentos. **Guardar simulação** guarda o rascunho e as atribuições aos clientes.
6. No detalhe da simulação, prepare explicitamente a encomenda real no circuito existente de compras. Se o mínimo não for atingido, é necessária uma exceção expressa. Consulte o PDF e pré-visualize a mensagem antes de confirmar o envio ao fornecedor. Registe a confirmação comercial separadamente.
7. Em **Receções**, registe as quantidades efetivamente recebidas, lotes e validades, ou associe uma receção já confirmada da mesma encomenda. A associação não repete a entrada de stock. Receções parciais mantêm o saldo em aberto; quantidades atribuídas são reservadas aos clientes e o excedente fica em stock geral.
8. Em **Entregas**, contacte o cliente autorizado com produtos disponíveis. O email exige pré-visualização e confirmação explícita; WhatsApp abre a mensagem preparada e requer registo manual do resultado. Registe apenas a quantidade efetivamente entregue. **Histórico** conserva utilizador, momento, ação e resultado.

### Dados e API

As tabelas novas são `customer_orders`, `customer_order_lines`, `customer_order_reservations`, `supplier_order_terms`, `customer_purchase_simulations`, `customer_supply_links` e `customer_order_events`. Estão no esquema de cada empresa. Documentos, movimentos de stock e envios continuam nas estruturas existentes.

Os endpoints ficam em `/api/customer-orders`: listagem, catálogo, histórico, criação/correção de pedidos, ações por linha, condições por fornecedor, cálculo/rascunhos de simulação, preparação, confirmação do fornecedor, receção e contactos. O envio do PDF ao fornecedor reutiliza `/api/procurement`. A documentação interativa da API apresenta os contratos de cada operação.

Instalação do backend: realizar cópia de segurança, instalar a versão de código e executar `alembic upgrade head` na pasta `backend`, com o ambiente da instalação, antes de reiniciar a API. A migração `0057` é aditiva e foi aplicada na instalação local. A API pública, alojada noutra máquina, precisa desta atualização antes de disponibilizar o módulo publicamente.

### Verificação

- `backend/tests/test_customer_orders.py`: cálculo monetário e mínimos, agregação e rastreio, receções parciais, associação sem duplicação de stock, reservas, lotes expirados, entregas, cancelamentos, revisões, idempotência e concorrência.
- O mesmo conjunto cobre permissões por função/loja, isolamento entre empresas, referências ERP, migração repetível, contactos autorizados e envio SMTP com transporte substituído por teste.
- Regressões do circuito de compras, inventário, modos de operação e plataforma executadas em conjunto.
- `scripts/test-customer-orders-ui.py`: navegação nas oito áreas, criação de artigos reais/provisórios, pesquisa EAN, agregação, cálculo do mínimo e proteção de alterações por guardar, em desktop e telemóvel. Todas as chamadas API são interceptadas: não cria dados reais nem envia mensagens.
- Compilação de produção da interface, `check:ui`, Ruff e verificação do diff.

Resultado da validação em 21/10/2026: a execução conjunta terminou com 92 testes aprovados e uma falha de importação no novo teste ERP (`Company` importado do módulo incorreto). A importação do teste foi corrigida para `app.platform_models`; esse teste foi executado separadamente e passou. Não se repetiu toda a bateria após esta correção exclusiva do teste. O circuito principal tinha também passado anteriormente os 57 testes de encomendas/compras/inventário. Os testes de interface passaram a 1440 e 390 píxeis; compilação de produção, `check:ui` e Ruff passaram. A API local reiniciou com a migração 0057 e respondeu HTTP 200 em saúde e OpenAPI.

### Limitações explícitas

- O módulo não emite documentos fiscais. A entrega regista uma saída de stock local e deve ser acompanhada pelo procedimento fiscal aplicável da instalação.
- O conector ERP atual não fornece receções detalhadas nem escrita de stock. Essas operações ficam bloqueadas quando a autoridade de stock é o ERP ou o artigo é de leitura; não são apresentados movimentos fictícios como sincronizados.
- Condições noutra moeda podem ser simuladas, mas o circuito de compras existente só suporta EUR; não se inventam taxas de câmbio.
- Abrir WhatsApp não comprova envio ou leitura. O resultado é declarado pelo operador. A aceitação SMTP também não comprova leitura; estados de falha/incerteza seguem o transporte existente, sem repetição automática.
- Cancelar o pedido de um cliente não anula uma encomenda já preparada ao fornecedor. A quantidade recebida sem atribuição ativa passa a stock geral.
- A listagem atual carrega o conjunto autorizado de pedidos. Instalações com grande volume deverão acrescentar paginação no servidor e política de retenção dos históricos.
