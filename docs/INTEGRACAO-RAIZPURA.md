# Raizpura ↔ GPGest — configuração da integração

Implementação local, 1 de novembro de 2026. Não foi escolhida nem ativada uma empresa/loja/armazém por omissão. A decisão é feita nos parâmetros, como solicitado.

## Configurar

1. No GPGest/IAZELA, selecionar a empresa pretendida. Abrir **Integrações → Conectores → Raizpura · Loja online**.
2. Introduzir o nome da ligação, selecionar a **loja** e o **armazém ativo**. Criar a ligação. A empresa é a selecionada na aplicação; as escolhas não são fixas no código.
3. No WordPress, abrir **WooCommerce → GPGest**. Introduzir a origem da API (por exemplo, `http://127.0.0.1:8001` neste computador, sem `/api`), o identificador da empresa, o identificador da ligação e a chave apresentados pelo GPGest. O endereço é configurável. Fora de localhost é exigido HTTPS.
4. Guardar, **Testar ligação**, selecionar sincronização de catálogo/stock e envio de encomendas; ativar preços apenas depois de rever as classes fiscais e a opção de preços com/sem IVA do WooCommerce. Executar **Sincronizar agora**.
5. Rever os artigos importados em rascunho: informação alimentar, rótulo, dose, alegações, fiscalidade e fotografias. A importação não aprova produtos nem ativa as vendas.

Para renovar uma chave, usar **Renovar chave** no GPGest e substituir apenas a chave no WordPress, mantendo empresa e identificador. Não criar outra ligação para uma simples renovação. **Desativar ligação** revoga o acesso. Para mudar empresa/loja/armazém depois de existir histórico, criar uma nova ligação e reconciliar previamente produtos e encomendas; não reutilizar correspondências de outra empresa.

## Direção e regras

| Informação | Direção / comportamento |
|---|---|
| Famílias | GPGest → categorias WooCommerce, a partir das famílias dos artigos. O GPGest atual guarda a família na ficha do artigo; não existe uma tabela autónoma de famílias vazias para exportar. |
| Artigos | Identificação estável por empresa/ligação e ID do artigo; código/SKU, nome, descrição, marca, código de barras e unidade. Novos artigos em rascunho; produtos ocultos/manuais não são associados só porque têm o mesmo SKU. Um conflito de SKU exige revisão. |
| Preços | Facultativos. Conserva os preços líquidos/brutos e taxa recebidos; usa o preço adequado à opção com/sem IVA do WooCommerce. Não inventa a classe fiscal. Promoções cujo preço muda exigem rever a evidência dos 30 dias. |
| Stock | GPGest → WooCommerce: apenas armazém selecionado, descontando reservas e excluindo lotes expirados. Sem encomendas em atraso automáticas. Artigos fracionados/serviços não são vendáveis nesta versão. |
| Encomendas | WooCommerce → Centro de Encomendas GPGest. Referência única por site/encomenda, versão e registo de auditoria. Repetir o envio não duplica a encomenda nem a reserva. |
| Checkout | Confirma a reserva no GPGest antes de iniciar o pagamento, tanto no checkout clássico como na Store API. Falhas/stock insuficiente impedem o início do pagamento e ficam para repetição/revisão. |
| Entrega | A entrega no GPGest continua a registar a saída física, pelos mecanismos existentes de stock/lotes. A ligação não lança uma segunda saída no pagamento. A entrega integral é refletida como concluída no WooCommerce quando este estava em processamento. |
| Cancelamentos | Libertam reservas quando ainda não existem entregas/compras atribuídas. Cancelamentos fora de ordem não reabrem pedidos. Devoluções após entrega, alterações de linhas/montantes/cliente e conflitos exigem revisão; não há reembolso ou reposição física automática. |

Não há escrita na Wintouch nem criação de movimentos financeiros/faturas por este conector. Os totais da encomenda são conservados em cêntimos; os totais líquidos por linha são exatos. O preço unitário de referência do Centro de Encomendas é arredondado ao cêntimo e não substitui uma fatura fiscal.

O catálogo percorre páginas de até 100 artigos, cinco páginas por execução, retomando o cursor. Só no fim de um percurso completo os produtos que desapareceram da origem são colocados em rascunho/sem stock; não são apagados. A informação alimentar editorial no WordPress é preservada. Mudanças de nome/descrição/marca/classificação devolvem o artigo a rascunho e exigem nova revisão. As famílias novas importadas só entram na página inicial depois de terem produtos publicados. Fotografias e rótulos continuam a ser geridos no WordPress nesta versão.

## Frequência, falhas e operação

O evento WordPress é agendado a cada cinco minutos. Para execução fiável, configurar um cron do alojamento. **Neste ambiente local o WP-Cron está desativado:** usar o botão de sincronização ou executar o PHP local com `wp-content/plugins/raizpura-commerce/bin/sync.php`. O script só funciona em CLI; pode ser chamado por um agendador do sistema. É possível definir `RAIZPURA_WP_ROOT` para outra instalação.

As encomendas e versões pendentes são guardadas no próprio pedido WooCommerce e repetidas sem mudar a referência. O painel apresenta os erros; o pedido conserva o erro técnico e a referência GPGest. Stock importado com mais de 15 minutos fica indisponível para compra até nova sincronização. Enquanto existirem envios pendentes, a disponibilidade pode ser conservadora para evitar vender unidades por confirmar.

Configurar também o cancelamento de encomendas não pagas e respetivo cron WooCommerce. A reserva de uma encomenda não paga só é libertada quando o seu cancelamento chega ao GPGest. Não usar reservas como confirmação de recebimento. Uma alteração editorial de linhas ou montantes de uma encomenda já integrada gera conflito, não uma nova encomenda.

Se o stock for declarado como autoridade do ERP e o conector não o disponibilizar, a quantidade é desconhecida e a venda fica bloqueada. Não são inventadas quantidades. A API de integração só admite encomendas em EUR e unidades inteiras.

## Credenciais, dados e implantação

O GPGest guarda apenas SHA-256 de uma chave aleatória, válida por 90 dias, revogável/renovável e ligada à empresa, loja, armazém e permissões atuais do utilizador que a criou. A chave é apresentada uma única vez; não se reutiliza a sessão administrativa. O WordPress cifra-a com AES-256-GCM derivado dos seus segredos. Alterar os salts pode exigir reintroduzir a chave. Não copiar chaves para ambientes de teste/clones.

O token só segue no cabeçalho Authorization, sem redirecionamentos. Nome, email, telefone, morada de entrega, artigos e totais seguem para o GPGest para tratar a encomenda; não são transmitidos dados de cartão, palavras-passe ou tokens de pagamento. Os payloads de repetição são removidos após confirmação. Incluir esta circulação de dados na informação de privacidade e coordenar conservação/pedidos de direitos nos dois sistemas antes da abertura.

Migração GPGest: `backend/migrations/versions/0074_webshop.py`. Foi testada em esquema temporário e aplicada à base local (versão anterior 0073). Noutro ambiente, efetuar backup e executar as migrações antes de disponibilizar as novas rotas. Usar o plugin Raizpura Commerce 1.2 ou posterior e o backend correspondente. A interface de configuração exige administrador/gestor no GPGest e administrador no WordPress.

## Validação

- Sete testes de API: credenciais/permissões/renovação/revogação, reservas/idempotência, concorrência/falta de stock/rollback, lotes expirados/paginação, entrega/isolamento de ligação e cancelamento anterior à criação e validade pela data portuguesa.
- Um teste de migração: criação no esquema correto, tipo BIGINT das versões e repetição segura.
- Quinze cenários WordPress com transporte simulado: importação, correspondência estável, conservação de informação alimentar, stock desconhecido, credenciais cifradas e repetição após falha.
- Ensaio real por HTTP em esquema temporário: importar stock 10, reservar 2 antes do pagamento, sincronizar stock 8, cancelar e repor disponibilidade 10. Uma encomenda, sem duplicados, uma entrada de stock original e zero movimentos financeiros. Sem pagamentos ou emails reais. Dados de teste removidos.
- TypeScript e verificações dos ficheiros Python/PHP alterados.

Resultados detalhados e fontes dos testes estão incluídos em `docs`/`tests` do projeto Raizpura e nos testes do backend GPGest. Não foi publicada nenhuma alteração nem criada ligação comercial por omissão.
