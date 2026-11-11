# Centro inteligente de encomendas e abastecimento

## Levantamento e plano

A análise de 30/10/2026 encontrou o circuito operacional em `customer_orders`, os documentos reais em `procurement`, o ledger físico em `inventory`, recomendações e conferência em `order_cycle` e condições por artigo/fornecedor em `article_suppliers`. Reservas, receções parciais, cancelamentos, devoluções, contactos, revisões, idempotência, auditoria e permissões já existiam. A evolução reutiliza-os, sem criar outro ledger ou enviar encomendas automaticamente.

Lacunas identificadas: a comparação preliminar por fornecedor usava montantes antes de descontos; a listagem de pedidos não distribuía stock livre por clientes; recomendações não consultavam as condições negociadas por artigo e mostravam uma única janela de vendas; o simulador exigia cálculo manual.

Plano apresentado antes de programar: leitura consolidada e necessidades líquidas → interface e ligação ao simulador → regras de reposição → testes e documentação. As alterações anteriores existentes na árvore de trabalho foram preservadas. Não há migração de base de dados: o stock máximo adicional usa o JSON das políticas de reposição existentes.

## Utilização

Em **Encomendas → Encomendas de clientes**, escolha a loja no Centro de Encomendas. O painel segue a referência visual final e mantém o nome e o logótipo IAZELA; a utilização detalhada descrita abaixo continua em **Detalhes de clientes, stock e cobertura**. Os indicadores distinguem pedidos abertos, falta de cobertura física, artigos efetivamente por comprar, encomendas enviadas, receções parciais, pedidos prontos e urgência/atraso. A pesquisa encontra artigos, clientes e números de pedido; pode combinar fornecedor, cliente, estado e filtros rápidos.

Expanda um artigo para consultar todos os clientes, datas, quantidades pedidas/entregues, reservas, cobertura proposta pelo stock livre, compras pendentes, rascunhos e trânsito ainda não atribuído. **Cobertura proposta não é reserva**: abra o pedido para reservar fisicamente. Só reservas utilizáveis contam como prontas para entrega.

Os grupos por fornecedor mostram o subtotal sem IVA, descontos, valor elegível, mínimo, portes, IVA e total previsto. O cálculo utiliza a mesma função Decimal do simulador. A regra normal é o líquido das mercadorias após descontos; bases contratuais específicas já configuradas são conservadas e identificadas. Portes grátis têm configuração independente. Custos desconhecidos, artigos provisórios, fornecedor não associado ao pedido e stock ERP indisponível impedem avançar a partir da proposta consolidada.

**Simular encomenda** abre o simulador existente com as quantidades líquidas e as atribuições aos clientes. Os totais atualizam após uma pausa curta na edição; respostas antigas não substituem o cálculo de dados mais recentes. Pode comparar até três fotografias de totais na sessão, remover artigos, ajustar quantidades, selecionar reposição e guardar um rascunho. A comparação é temporária; o rascunho conserva a composição escolhida. Preparar a encomenda real e enviar ao fornecedor continuam a exigir as ações e confirmações existentes.

## Cálculo e controlo

- O cálculo é separado por loja; não supõe transferências entre lojas.
- Os pedidos urgentes precedem os restantes, seguindo-se prazo e antiguidade, com desempate estável por identificador.
- As reservas de todos os clientes são descontadas do stock livre. Lotes expirados não oferecem cobertura.
- As atribuições a rascunhos e compras já preparadas não são propostas de novo. Do trânsito livre retira-se primeiro o que já está atribuído a clientes; o saldo é distribuído uma única vez.
- São usados custos acordados ainda válidos por artigo/fornecedor, preços gerais configurados e custos conhecidos. Mínimos e múltiplos de compra arredondam a quantidade agregada; as quantidades adicionais são reposição, não novas atribuições aos clientes.
- As leituras do centro não criam documentos, movimentos, reservas, mensagens, artigos ou escritas ERP. Custos exigem a permissão de gestão já existente.

As recomendações apresentam vendas físicas a 30/60/90 dias, média diária, cobertura e prazo. Consultam preço, mínimo e múltiplo negociados, segurança, objetivo de cobertura, trânsito, procura, validade, margem e teto de capital. Um stock máximo opcional limita a reposição após satisfazer a procura. Um múltiplo não pode ultrapassar os limites de risco: quando não há quantidade admissível, a proposta fica a zero e exige revisão. Sem histórico não se inventam vendas; mínimos explicitamente configurados continuam a poder justificar uma proposta, acompanhada de aviso. Margem negativa impede reposição especulativa.

## Integração e limites

O endpoint de consulta é `GET /api/order-cycle/supply-center?store_id=…`. As escritas continuam em `/api/customer-orders`, `/api/procurement` e no adaptador Wintouch existente. São preservados revisão, chaves idempotentes, isolamento por empresa/loja e confirmação de operações externas. Não foram efetuadas operações no ERP real nem envios a fornecedores durante esta implementação.

As vendas usadas nas sugestões são saídas físicas locais de tipo `sale`, tal como no motor existente. Não se somam documentos fiscais e saídas físicas para evitar duplicar vendas. Quando a autoridade de stock é ERP e o conector não fornece quantidades, o planeamento exige validação na origem. Não se inferem validade, sazonalidade, preços desconhecidos ou chegada garantida; a data de entrega é uma estimativa pelo prazo configurado. Instalações muito grandes continuam a beneficiar de paginação e agregações adicionais no servidor.

## Verificação

`backend/tests/test_supply_center.py` cobre stock partilhado por vários clientes, reservas de terceiros, trânsito atribuído e livre, receção parcial, mínimos líquidos independentes de IVA/portes, preços e múltiplos negociados, rascunhos sem nova necessidade, proteção de custos, stock ERP desconhecido, janelas de vendas e prevenção de excesso de stock. A regressão usa também os testes existentes de pedidos, ciclo e compras. Os testes usam uma base isolada de demonstração e transportes substituídos.

Validação executada:

- 12 testes novos e 16 testes do ciclo: **28 aprovados** na versão final.
- Regressão de pedidos, ciclo, compras e escrita Wintouch: 66 aprovados e uma incompatibilidade num teste antigo, que esperava criar uma compra sem rubrica apesar da nova validação já existente da ficha do fornecedor. O teste foi adaptado à rejeição antecipada e verifica que nenhum documento foi criado; a sua nova execução passou. Não se alterou a regra da aplicação para obter esse resultado.
- Compilação TypeScript/Vite de produção: aprovada.
- `check:ui`: aprovado, incluindo os botões da grelha de artigos convertidos para o componente partilhado.
- Ruff dos módulos novos, motor de reposição e testes novos: aprovado. O ficheiro histórico `test_procurement.py` conserva os seus avisos de estilo anteriores; não foi feita uma reformatação global desse ficheiro já alterado localmente.
- `git diff --check`: aprovado.
- `scripts/test-supply-center-ui.py`: aprovado a 1440 px em tema claro e 390 px em tema escuro; verifica consolidação, filtro por cliente, ligação ao simulador, totais automáticos, cenários e ausência de escritas reais. As capturas estão em `.local/supply-center-*.png` e `.local/supply-simulator-*.png`.
- `scripts/test-customer-orders-ui.py`: regressão das nove áreas anteriores aprovada a 1440 e 390 px, incluindo edição de pedidos, simulação, originais e aprovação da conferência; sem movimentos de stock, ERP ou email reais.

A reposição adicional existente em rascunhos é apresentada separadamente e descontada das novas sugestões, sem ser transformada em disponibilidade física. Reservas que deixaram de ter stock utilizável, por exemplo devido à validade, ficam assinaladas para revisão e não contam como prontas para entrega.

Esta implementação está no código local. Não inclui publicação, migração ou reinício da API em produção.
