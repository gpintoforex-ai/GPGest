# Encomendas online: pagamentos e reservas (Raizpura ↔ IAZELA)

O WooCommerce (Raizpura) faz o checkout; o IAZELA gere a operação: reserva, pagamento, preparação e faturação.
Requer a migração **0096** e o plugin Raizpura Commerce com `includes/payments.php`.

## Fluxo

1. **Disponibilidade:** ao concluir o checkout (clássico ou de blocos), a loja pergunta ao IAZELA o stock livre
   dos artigos (`POST /api/webshop/availability`). Sem stock, ou sem resposta, a encomenda não avança.
2. **Encomenda e reserva:** a encomenda é enviada com o meio de pagamento e o IAZELA valida e reserva
   atomicamente (bloqueio da empresa) por artigo e armazém da ligação. Se não for possível garantir a reserva,
   devolve erro e a loja não inicia o pagamento.
3. **Reserva temporária** até ao prazo do meio de pagamento (configurável por ligação):

   | Meio | Prazo inicial | Métodos WooCommerce reconhecidos |
   |---|---|---|
   | MB WAY | 15 minutos | `mbway_ifthen_for_woocommerce` |
   | Cartão | 15 minutos | `creditcard_ifthen…`, `vivacom_smart`, `ppcp-gateway`, WooPayments, gateway ifthenpay |
   | Multibanco | 48 horas | `multibanco_ifthen_for_woocommerce` |
   | Payshop | 48 horas | `payshop_ifthen_for_woocommerce` |
   | Transferência | 72 horas | `bacs` |
   | Outro meio | 60 minutos | os restantes |

   A mudança de meio de pagamento (página «pagar encomenda») recalcula o prazo a partir desse momento.
4. **Pagamento:** só conta a confirmação autenticada do prestador, nunca o regresso do navegador ou a página de
   agradecimento:
   - **ifthenpay** (Multibanco, MB WAY, cartão, Payshop, gateway): as ações `*_ifthen_callback_payment_complete`,
     que o plugin ifthenpay só dispara depois de validar a chave anti-phishing ou o HMAC do callback;
   - **meios autorizados** nas definições do plugin («Meios com confirmação do prestador»; por omissão só
     `vivacom_smart`, cujo plugin confirma o pagamento por webhook e consulta da transação na API Viva);
   - **transferência:** a passagem a «Em processamento» feita no painel por um administrador da loja (registada
     como «confirmada por administrador»).
5. **Reserva confirmada:** com o pagamento, a reserva deixa de expirar e a encomenda fica **pronta para
   preparação e faturação**. Uma encomenda online por pagar não pode ser entregue no IAZELA.

## Estados independentes

| Eixo | Estados |
|---|---|
| Encomenda (loja) | estado WooCommerce recebido (`pending`, `on-hold`, `processing`, `completed`, `cancelled`…) e o estado operacional no IAZELA (registada, entrega parcial, entregue, cancelada) |
| Pagamento | por pagar, pago, pagamento falhado, reembolsado, anterior ao registo |
| Reserva | temporária, confirmada, expirada, libertada, por rever, sem prazo (anterior) |

As encomendas existentes antes da migração 0096 ficam «anterior ao registo» e «sem prazo»: não expiram
automaticamente, porque não se sabe se foram pagas.

## Expiração e libertação

O agendador do IAZELA (de 30 em 30 segundos) e cada pedido da loja libertam as reservas temporárias vencidas e
sem pagamento: as unidades voltam ao stock livre e as quantidades da encomenda ficam canceladas. Uma encomenda com
entregas, faturas ou compras atribuídas não é libertada automaticamente; fica **por rever**. Na sincronização
seguinte, a loja cancela a encomenda WooCommerce por pagar («Reserva expirada no IAZELA»).

## Pagamentos repetidos, tardios ou divergentes

- Cada evento tem uma **chave estável**; o mesmo callback repetido (na loja ou no IAZELA) tem um só efeito. A
  mesma chave com conteúdo diferente é recusada.
- **Pagamento depois de expirar:** o IAZELA tenta reservar de novo (tudo ou nada). Com stock, fica confirmada e a
  loja pode reabrir a encomenda cancelada. Sem stock, fica **por rever** («Reponha o stock ou reembolse o
  cliente»); nunca se tira stock de outra encomenda nem se reembolsa automaticamente.
- **Montante diferente do total**, **segundo pagamento** da mesma encomenda e **encomenda paga cancelada** na
  loja ficam por rever, sem alterar a reserva.
- **Reembolso** confirmado pelo prestador passa o pagamento a reembolsado; com a reserva ainda ativa, fica por
  rever.

## Recuperação de falhas

- Sem resposta do IAZELA no checkout, a encomenda não avança para o pagamento e fica na fila da loja.
- Sem resposta quando chega o callback do prestador, o pagamento fica na fila da encomenda (`_rp_gpgest_payments`)
  e é reenviado com a mesma chave na sincronização seguinte.
- A expiração é idempotente e volta a correr depois de um reinício (agendador e pedidos da loja).

## Área «Encomendas online»

**Integrações → Encomendas online** (consulta para administradores, gestores e operadores; ações para
administradores e gestores): indicadores, filtros por ligação, pagamento, reserva e «só as que pedem decisão»,
pesquisa por número, cliente ou artigo, e exportação. O detalhe mostra artigos (reservado, entregue, faturado),
cliente, pagamentos (prestador, transação, verificação, efeito) e o histórico completo. Ações com motivo
obrigatório: **Tentar reservar de novo**, **Prolongar prazo**, **Libertar reserva**, **Marcar como resolvida**.
**Prazos de pagamento** configura os prazos por meio. As encomendas por rever aparecem nos avisos do sino.

## Compatibilidade

Encomendas WooCommerce pela API CRUD (HPOS ativo). O checkout de blocos (Store API) e o clássico usam as mesmas
validações e a mesma reserva antes do pagamento.

## Testes

- `backend/tests/test_webshop_payments.py`: prazos por meio e confirmação; expiração, pagamento tardio com e sem
  stock e aviso; eventos duplicados e montante divergente; concorrência na última unidade e callbacks
  simultâneos; cancelamentos e bloqueio da entrega por pagar; área de gestão, prazos e permissões; isolamento.
- Raizpura `tests/test_gpgest_wordpress.php`: disponibilidade (e IAZELA indisponível), meio de pagamento enviado,
  regresso do navegador ignorado, callback ifthenpay único e sem segredos, fila sem ligação, expiração que cancela
  a encomenda, transferência confirmada só pelo administrador, Viva.com aceite e meios não autorizados ignorados.
- Raizpura `tests/test_gpgest_e2e.py`: ensaio HTTP real com callback MB WAY repetido (um só evento), reserva
  confirmada e cancelamento de encomenda paga assinalado para reembolso.

## Limitações

- O IAZELA não recebe diretamente os callbacks dos prestadores: confia na validação feita pelo plugin do
  prestador na loja e na credencial da ligação. Antes de acrescentar um meio aos «Meios com confirmação do
  prestador», confirmar no código do seu plugin que só confirma depois de verificar junto do prestador.
- O meio dentro do Viva.com Smart Checkout (cartão, MB WAY…) não é distinguido: usa o prazo de cartão.
- Reembolsos parciais não são tratados como estado próprio (só o reembolso total).
