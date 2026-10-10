Quero construir uma web app de gestão financeira e operacional, em português de Portugal, para uma empresa que utiliza o software Wintouch.

Age como analista funcional e programador sénior. Desenvolve uma aplicação funcional, com base de dados persistente, interface profissional e instruções passo a passo para uma pessoa com pouca experiência em programação.

## 1. Objetivo

A aplicação deverá permitir:

- Gestão e fecho de caixa diário.
- Registo de entradas e saídas de caixa.
- Criação de talões de depósito bancário.
- Integração com o Wintouch.
- Ligação a contas bancárias e reconciliação bancária.
- Controlo de artigos por lote e validade.
- Gestão de custos, receitas e despesas.
- Relatórios e indicadores para apoio à gestão.

Deve suportar várias lojas, caixas e contas bancárias, mesmo que inicialmente seja utilizada apenas numa loja.

## 2. Regras essenciais

- Não inventes endpoints, tabelas ou funcionalidades da API do Wintouch.
- Antes de implementar a integração real, identifica a versão do Wintouch e solicita a documentação ou estrutura de dados necessária.
- Cria uma camada de integração independente, permitindo utilizar API, acesso autorizado de leitura à base de dados ou importação de ficheiros, conforme as possibilidades confirmadas.
- Não escrevas diretamente na base de dados do Wintouch.
- Enquanto não existir acesso à integração, disponibiliza um modo de demonstração claramente identificado e importação de CSV/Excel.
- Não apresentes dados simulados como dados reais.
- Usa valores decimais exatos para dinheiro, evitando erros de arredondamento.
- Distingue movimentos de dinheiro de receitas e despesas: uma transferência ou um depósito não constitui, por si só, uma receita ou despesa.
- A aplicação será uma ferramenta de gestão. Não deverá emitir documentos fiscais nem substituir o software de faturação.

## 3. Gestão diária de caixa

Implementa abertura, movimentos e fecho por loja, caixa, operador e data.

### Abertura
- Registar fundo inicial.
- Identificar operador, data e hora.
- Impedir duas sessões abertas simultaneamente na mesma caixa.
- Permitir passagem de caixa entre operadores com registo de responsabilidade.

### Movimentos
Permitir registar:
- Recebimentos de vendas.
- Recebimentos de clientes.
- Entradas extraordinárias.
- Pagamentos a fornecedores.
- Pequenas despesas.
- Reembolsos.
- Retiradas para depósito.
- Reforços de fundo.
- Transferências entre caixas.

Cada movimento deverá incluir:
- Tipo, categoria, montante e meio de pagamento.
- Data do movimento e data do registo.
- Loja, caixa e utilizador.
- Cliente ou fornecedor, quando aplicável.
- Documento associado e referência externa.
- Observações e anexos.
- Origem: manual, Wintouch ou importação.

Separar numerário, Multibanco/cartão, transferência e outros meios. Apenas os movimentos em numerário devem alterar o saldo físico da caixa.

### Fecho
- Calcular saldo esperado em numerário.
- Permitir contagem por moedas e notas.
- Comparar saldo contado com saldo esperado.
- Apresentar diferenças e exigir justificação quando existirem.
- Separar totais por meio de pagamento.
- Gerar relatório de fecho em PDF.
- Bloquear alterações após o fecho.
- Permitir reabertura apenas a utilizadores autorizados, com motivo e histórico.

Fórmula:
Saldo esperado = fundo inicial + entradas em numerário − saídas em numerário.

Movimentos financeiros confirmados não devem ser eliminados sem rasto: implementar anulação ou estorno com motivo.

## 4. Talões de depósito bancário

Permitir preparar e imprimir um talão interno de depósito em PDF, contendo:
- Empresa e loja.
- Número sequencial único.
- Data.
- Banco, conta de destino e IBAN parcialmente oculto.
- Montante.
- Discriminação de moedas e notas, quando aplicável.
- Origem do dinheiro e fechos de caixa relacionados.
- Responsável pela preparação e pelo depósito.
- Campos para assinatura.
- Observações e comprovativo anexado.

Estados:
- Preparado.
- Retirado da caixa/em trânsito.
- Depositado.
- Reconciliado.
- Cancelado.

Regras:
- Preparar um talão não deve retirar automaticamente dinheiro da caixa.
- A retirada efetiva deve gerar um movimento ligado ao depósito.
- O dinheiro retirado e ainda não creditado deve ficar identificado como valor em trânsito.
- O crédito bancário deverá ser associado ao mesmo depósito.
- Impedir associação duplicada de movimentos ou valores superiores ao montante disponível.
- O talão interno não deve ser apresentado como comprovativo emitido pelo banco.

## 5. Integração com Wintouch

Preparar sincronização, conforme os dados efetivamente disponíveis, de:
- Vendas e devoluções.
- Recebimentos e meios de pagamento.
- Clientes e fornecedores.
- Artigos e stock.
- Compras e custos.
- Lotes e validades, se existirem na origem.

Implementar:
- Configuração segura da ligação.
- Sincronização manual e automática.
- Importação inicial e incremental.
- Identificadores externos para impedir duplicações.
- Registo de última sincronização, erros e tentativas.
- Reprocessamento seguro.
- Tratamento de documentos anulados ou corrigidos.
- Mapeamento de lojas, meios de pagamento e categorias.
- Página para consultar dados importados e problemas de correspondência.

Evitar contabilizar duas vezes a mesma venda ou recebimento quando já tenha sido importado do Wintouch.

Se lotes e validades não estiverem disponíveis, permitir geri-los localmente, deixando clara a origem desses dados.

## 6. Bancos e reconciliação bancária

Criar uma camada para ligação bancária através de um prestador autorizado de Open Banking, caso exista suporte para os bancos utilizados.

Antes da integração real, confirmar:
- Bancos e contas a ligar.
- Prestador, cobertura, custos e documentação.
- Processo de consentimento e renovação do acesso.

Nunca pedir nem armazenar credenciais de homebanking na aplicação.

Disponibilizar também importação de extratos CSV/Excel, com pré-visualização, mapeamento de colunas e deteção de duplicados.

### Reconciliação
- Mostrar movimentos bancários e movimentos internos lado a lado.
- Sugerir correspondências por montante, data, referência, entidade e descrição.
- Mostrar o motivo e grau de confiança da sugestão.
- Exigir confirmação do utilizador por defeito.
- Permitir correspondências de um para um, um para vários e vários para um.
- Suportar pagamentos parciais, comissões e diferenças justificadas.
- Permitir desfazer uma reconciliação com registo de auditoria.
- Identificar movimentos pendentes, reconciliados e ignorados com justificação.
- Controlar valores já associados, impedindo reconciliação duplicada.

Prever recebimentos de cartão agrupados pelo banco, com comissões descontadas: distinguir valor bruto das vendas, comissão e valor líquido creditado.

Não criar receitas automaticamente para todos os créditos bancários nem despesas para todos os débitos. Depósitos próprios e transferências entre contas devem ser tratados como transferências.

## 7. Artigos, lotes e validades

Criar uma área com:
- Código, código de barras e descrição do artigo.
- Categoria, fornecedor e localização.
- Lote.
- Data de receção e validade.
- Quantidade disponível por lote.
- Custo unitário e preço de venda.
- Tipo de prazo: validade ou consumo preferencial, quando aplicável.
- Estado: disponível, próximo do prazo, prazo ultrapassado ou bloqueado.

Funcionalidades:
- Registo manual e importação.
- Pesquisa por código de barras.
- Alertas configuráveis, por exemplo 90, 60, 30 e 7 dias.
- Listas ordenadas pela validade mais próxima.
- Sugestão de saída pelo método FEFO: primeiro a expirar, primeiro a sair.
- Registo de quebras, devoluções, ofertas e abates, com motivo.
- Histórico das alterações.
- Valor do stock próximo do prazo e perdas por validade.
- Sugestões de ações comerciais para artigos próximos do prazo.

Distinguir o stock total importado do Wintouch das quantidades atribuídas localmente a lotes. Mostrar divergências sem alterar silenciosamente o stock na origem.

## 8. Custos, receitas e despesas

Permitir registar:
- Receitas de vendas e outras receitas.
- Compras e custo das mercadorias vendidas.
- Rendas, salários, energia, água, telecomunicações e seguros.
- Comissões bancárias.
- Transportes, marketing e outras despesas.
- Despesas recorrentes e compromissos futuros.

Cada registo deve incluir:
- Categoria e centro de custo.
- Entidade.
- Data do documento, vencimento e pagamento.
- Valor sem IVA, IVA e total, quando aplicável.
- Estado: pendente, parcialmente pago, pago ou vencido.
- Documento e anexos.
- Loja.
- Ligação aos movimentos de caixa ou banco.

Distinguir:
- Resultado de gestão: receitas menos custos e despesas reconhecidos.
- Fluxo de caixa: dinheiro efetivamente recebido e pago.
- Compras de stock e custo das mercadorias vendidas, evitando tratar automaticamente todas as compras como custo das vendas do período.

Apresentar:
- Receitas e despesas por período.
- Margem bruta e resultado de gestão estimado.
- Comparação mensal e anual.
- Orçamento versus realizado.
- Valores a receber e a pagar.
- Previsão de tesouraria baseada em saldos e vencimentos.
- Distribuição por categoria, loja e centro de custo.

Explicar os critérios de cálculo utilizados e identificar indicadores incompletos quando faltarem dados.

## 9. Interface

Criar um design moderno, simples e responsivo, adaptado a computador, tablet e telemóvel.

Menu:
1. Dashboard.
2. Caixa diária.
3. Entradas e saídas.
4. Depósitos.
5. Bancos.
6. Reconciliação.
7. Artigos e validades.
8. Receitas e despesas.
9. Relatórios.
10. Integrações.
11. Configurações.

Dashboard com:
- Saldo de numerário.
- Saldo bancário, indicando data da última atualização.
- Dinheiro em trânsito.
- Receitas e despesas do mês.
- Resultado estimado.
- Caixas por fechar e diferenças.
- Movimentos por reconciliar.
- Pagamentos vencidos.
- Artigos próximos da validade.

Usar euros, datas DD/MM/AAAA e fuso horário Europe/Lisbon. Mostrar mensagens claras de sucesso, erro e ausência de dados.

## 10. Utilizadores e segurança

Perfis:
- Administrador.
- Gestor.
- Operador de caixa.
- Consulta/contabilista.

Implementar:
- Permissões por ação e loja.
- Autenticação segura.
- Registo de auditoria de alterações financeiras.
- Proteção de credenciais e segredos no servidor.
- Validação de ficheiros anexados.
- Cópias de segurança e procedimento de recuperação.
- Proteção contra movimentos duplicados e operações concorrentes.
- Ausência de credenciais ou dados bancários sensíveis nos logs.

## 11. Arquitetura

Na ausência de um projeto existente, utiliza:
- Frontend: React com TypeScript.
- Backend: FastAPI em Python.
- Base de dados: PostgreSQL.
- Migrações de base de dados.
- API documentada.
- Configuração por variáveis de ambiente.

Mantém módulos independentes para Wintouch e bancos.

Inclui dados de demonstração separados dos dados reais. A aplicação deve funcionar localmente antes de ser preparada para alojamento.

## 12. Desenvolvimento por fases

Fase 1:
- Base do projeto, autenticação e permissões.
- Lojas, caixas e contas bancárias.
- Abertura, movimentos e fecho de caixa.
- Categorias financeiras e auditoria.

Fase 2:
- Talões de depósito e PDFs.
- Receitas, despesas e pagamentos.
- Dashboard e relatórios.

Fase 3:
- Artigos, lotes, validades e alertas.

Fase 4:
- Integração Wintouch, após confirmação da documentação.
- Importação de extratos e reconciliação.

Fase 5:
- Open Banking, após escolha e validação do prestador.
- Previsões de tesouraria e automatizações.

## 13. Validação e entrega

Testa principalmente:
- Cálculo do saldo de caixa.
- Separação entre numerário e cartão.
- Fechos e estornos.
- Depósitos em trânsito.
- Importações repetidas sem duplicação.
- Reconciliações parciais e agrupadas.
- Transferências que não alteram o resultado.
- Quantidades e alertas de validade.
- Permissões e operações simultâneas.

Entrega:
- Código completo e organizado.
- Estrutura de pastas.
- Migrações.
- Ficheiro .env.example sem segredos.
- Instruções de instalação no Windows, passo a passo.
- Comandos para iniciar frontend e backend.
- Guia de utilização.
- Explicação do que funciona e do que depende de integrações externas.

Começa por apresentar um plano curto, o modelo de dados e as perguntas essenciais sobre o Wintouch. Depois implementa a Fase 1 funcional. Avança com os módulos independentes mesmo que a documentação das integrações ainda não esteja disponível.

Não te limites a criar uma maquete visual: os formulários, cálculos, permissões e gravação na base de dados devem funcionar.