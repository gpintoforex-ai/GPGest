# Avisos na barra do menu

Os avisos que pedem ação estão num só sítio: o **sino na barra do menu**, junto ao tema, à loja e ao utilizador. O número é o total de ocorrências que pedem atenção, não mensagens por ler nem um número de documentos únicos. O mesmo registo pode exigir atenção em mais de um tema. Fica **vermelho** quando algum aviso é urgente e **âmbar** nos restantes. Ao abrir, cada tema mostra quantos casos há, os primeiros cinco e um botão **Abrir…** que leva à página onde se resolve.

| Aviso | Cor | Página |
|---|---|---|
| Pedidos de clientes em atraso | vermelho | Encomendas de clientes → Pedidos de clientes |
| Encomendas a fornecedores atrasadas | vermelho | Receções de mercadoria |
| Reclamações de validade por fazer | vermelho | Receções de mercadoria |
| Clientes por contactar (produtos disponíveis) | âmbar | Encomendas de clientes → Pedidos de clientes |
| Produtos a devolver por validade | âmbar | Devoluções a fornecedores |
| Documentos do arquivo por rever, sem duplicados | âmbar | Todos os documentos |
| Lotes em stock a perder a validade (até ao maior prazo de alerta das definições de stock) | âmbar | Artigos e validades |

- Os avisos respeitam as permissões e a loja escolhida na barra. Os de compras só aparecem a administradores e gestores. Quem não tem acesso a um módulo não vê os avisos dele.
- A lista atualiza-se ao mudar de página ou de loja e a cada 2 minutos enquanto o separador está visível. O botão Atualizar permite repetir a consulta. Ler os avisos não altera nada.
- O painel identifica o âmbito e a hora da consulta. Uma falha inicial mostra indisponibilidade; uma falha de atualização conserva os dados da última consulta do mesmo âmbito, assinalados como anteriores. Nunca se apresenta zero por causa de uma falha. A mudança de empresa, utilizador ou loja limpa esse estado.
- Datas de atraso e validade usam o dia de Lisboa. Uma loja não autorizada é recusada. Documentos usam a mesma visibilidade do arquivo; com uma loja explícita não incluem documentos sem loja.
- O sino que existia no Centro de Encomendas passou para aqui.
- Para gestores, o contador **azul** do Google Drive (ficheiros SAF-T e extratos por importar) mantém-se separado do total de ocorrências da aplicação; não é somado a esse total.

Servidor: `GET /api/alerts?store_id=…` (`backend/app/alerts.py`) devolve `groups` (id, rótulo, contagem, cor, página, parâmetros da página, primeiros casos) e `total`, `generated_at`, `unit: attention_occurrences` e `read_only: true`. Estes avisos cobrem os temas da tabela; não representam ainda todas as pendências bancárias ou de tesouraria. Interface: `frontend/src/components/TopAlerts.tsx`. Testes: `test_menu_bar_alerts_gather_late_orders_and_expiry_claims` e `scripts/test-top-alerts-ui.py`.
