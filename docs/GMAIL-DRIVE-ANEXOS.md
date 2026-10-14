# Anexos Gmail e Google Drive

## Configurar e autorizar

Nas **Configurações → Anexos de faturas**, guardar a conta Google, a pasta Drive e a pasta/etiqueta Gmail. Usar `inbox` para a caixa de entrada, `all` para todas as mensagens, ou o nome/ID de uma etiqueta existente. A conta autorizada deve coincidir com o email configurado. As configurações, autorizações e anexos ficam isolados por empresa.

A autorização anterior com `drive.file` pode não permitir aceder a uma pasta já existente. Desligar e voltar a ligar a conta para autorizar a nova permissão Drive. A aplicação solicita `gmail.readonly` (leitura de mensagens/anexos), `drive` (acesso de leitura/gestão ao Drive para usar a pasta existente) e identificação do email. O GPGest não apaga ou move ficheiros nem altera mensagens; a importação escreve apenas na pasta configurada. A permissão Drive concedida pela Google é mais ampla do que essa restrição da aplicação.

Em desenvolvimento, abrir `http://localhost:5174`, entrar na empresa e clicar em **Ligar Gmail e Drive**. A conta deve ser utilizador de teste do projeto Google; Gmail API e Drive API devem estar ativas. Credenciais Web em `.local/google.json`, excluído do Git. Tokens cifrados no servidor; nunca devolvidos ao navegador.

## Consultar e copiar

1. Abrir **Conferência documental → Anexos Gmail / Drive** ou a mesma área nas Configurações.
2. Indicar um período de até 90 dias e clicar em **Consultar emails**.
3. Cada página consulta até 30 mensagens. Se existir outra página, clicar em **Consultar mais emails**. A consulta não envia ficheiros para o Drive, não marca mensagens como lidas e não altera a caixa de email.
4. Selecionar até 30 anexos e clicar em **Guardar selecionados no Drive**.
5. São aceites PDFs, JPGs e PNGs até 5 MB, com validação do conteúdo. Imagens declaradas como inline e outros formatos são ignorados.

A mesma conta/mensagem/parte Gmail é registada uma única vez. Conteúdo idêntico, incluindo reenvios, reutiliza a cópia existente na mesma pasta. Os IDs Drive são preparados e persistidos antes do envio, permitindo repetir uma operação com resposta incerta sem criar outra cópia. Uma falha por ficheiro aparece separadamente e permite repetir os que falharam. Não se efetua uma sincronização automática em segundo plano.

## Associar à fatura

1. Num anexo importado, abrir **Ver anexo** e **Associar à fatura**.
2. Rever sugestões, motivos e confiança. O nome do ficheiro, assunto e texto extraível do PDF são comparados com número, NIF/nome do fornecedor, montante e data da fatura.
3. Se necessário, pesquisar a fatura manualmente. Só aparecem faturas de lojas a que o utilizador tem acesso. A pesquisa está limitada a 1000 resultados por consulta; refinar o texto para encontrar documentos antigos.
4. Escolher a fatura, marcar a confirmação e guardar.

A confiança é uma pontuação indicativa, não uma probabilidade calibrada. Não existe OCR para imagens ou PDFs digitalizados nesta versão; estes podem ser associados manualmente. A associação guarda apenas uma referência ao Drive: novos PDFs/imagens e texto extraído não ficam na base de dados. A consulta e entrega à contabilidade leem temporariamente o ficheiro do Drive. Mantém os estados de disponibilidade, ERP, pagamento, reconciliação e entrega, sem criar compras automaticamente. Cada anexo só pode estar associado a uma fatura. Máximo de cinco anexos por fatura; conteúdo já existente não é duplicado. O [assistente documental](ASSISTENTE-DOCUMENTAL.md) também verifica fornecedor e ERP, sugerindo as ações seguintes.

Para desfazer, remover o anexo na conferência documental. A associação Gmail fica livre novamente; o ficheiro Drive continua guardado. Consultas, cópias e associações ficam registadas no histórico.

## Limites e operação

As listas de anexos mostram até 500 registos recentes, acrescentando os resultados da consulta em curso. Tokens expirados/revogados ou permissões insuficientes exigem nova autorização. Alterar a conta configurada exige autorizar a nova conta; alterar a pasta não move ficheiros anteriores. Não se testaram cópias em contas reais sem seleção explícita dos ficheiros pelo utilizador; os testes usam respostas Google simuladas e esquemas de dados isolados.
