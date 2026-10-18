# Email e documentação contabilística

## Configurar por empresa

Em **Configurações → Email**, um administrador define o servidor SMTP, porta,
STARTTLS ou SSL/TLS, autenticação, remetente e limite máximo da mensagem. Indique
também o contabilista, destinatários adicionais, CC, assinatura e modelos de email.
Deixar a palavra-passe vazia conserva a credencial guardada; a remoção é explícita.
As palavras-passe são cifradas com a chave da plataforma e nunca são devolvidas à interface.
Conserve essa chave nos backups da instalação.

**Testar ligação guardada** verifica TLS, autenticação e o limite SIZE anunciado,
sem transmitir emails. A configuração inicia desativada. A periodicidade mensal,
trimestral ou personalizada organiza períodos e lembretes; o envio é sempre manual.

## Preparar e enviar

Em **Contabilidade → Entrega à contabilidade → Enviar à contabilidade**:

1. Confirme a empresa selecionada, estabelecimento(s), datas e categorias.
2. Prepare o dossiê. São reutilizados os mesmos coletores e relatórios da entrega
   contabilística: compras e notas de crédito, documentos digitalizados, fechos,
   depósitos, extratos e comprovativos disponíveis, pendências e divergências.
3. Reveja destinatários, CC, assunto, mensagem, ficheiros e versões anteriores.
   A consulta das divergências usa a última conferência guardada; não executa
   automaticamente uma nova conferência ERP/OCR nem altera a situação documental.
4. Escolha ZIP ou anexos individuais. O índice identifica fornecedor, NIF,
   documento, data, valor em cêntimos, nome original, caminho enviado e SHA-256,
   sempre que esses dados estejam disponíveis. Os nomes internos usam IDs para
   evitar colisões; os bytes dos originais são preservados.
5. Atualize a pré-visualização, assinale a confirmação explícita e envie.
   Alterar o rascunho exige nova pré-visualização e confirmação.

É possível enviar documentação disponível com pendências. As ausências e anexos
cloud indisponíveis são identificados; inclua a categoria dos relatórios para os
anexar ao email. Não são criadas ligações públicas.

Os extratos são CSV gerados dos movimentos importados. O importador bancário
existente não conserva o XLS/CSV original; esse original não é reconstruído nem
apresentado como anexo original. Comprovativos guardados como anexos de movimentos
são incluídos na categoria de pagamentos.

O dossiê preparado tem um limite de 100 MB e 1000 ficheiros. Os ficheiros preparados
ficam numa cópia cifrada e privada no histórico da empresa; uma alteração posterior
do original não modifica um envio já preparado. Essa cópia ocupa espaço na base
de dados e deve ser incluída na política de backups e conservação da empresa.

## Limites e falhas

O tamanho inclui MIME, cabeçalhos, índice e codificação dos anexos. É respeitado
o menor limite entre o configurado e o anunciado pelo servidor. A verificação é
repetida antes de transmitir conteúdo. Se o servidor impuser uma divisão ainda não
autorizada, nenhum conteúdo é enviado: é necessário rever e autorizar mensagens
numeradas. Um único ficheiro demasiado grande exige reduzir a seleção ou usar
a partilha privada já configurada no projeto; nunca é gerado um URL público.

O servidor valida todos os destinatários antes de receber o conteúdo de cada
mensagem. Destinatários recusados impedem essa mensagem. Não existem tentativas
automáticas. Em falhas parciais, **Preparar nova tentativa** exclui os ficheiros das
mensagens já aceites; as restantes podem ser revistas e enviadas explicitamente.
Se a ligação cair depois de iniciar DATA, a entrega pode ser incerta: confirme
no servidor e com o destinatário antes de reconhecer o risco e repetir.

Se a resposta à interface se perder, consulte o estado guardado e o histórico
antes de preparar outro envio. Uma tentativa interrompida há pelo menos 20 minutos
pode ser recuperada manualmente como falhada, com entrega incerta.

## Histórico e permissões

- **Preparado**: ficheiros congelados; ainda não enviado.
- **Envio em curso**: tentativa em execução; não repetir.
- **Aceite pelo servidor SMTP**: o servidor aceitou todas as mensagens.
  Não prova entrega final, leitura ou receção pelo contabilista.
- **Falhado**: consultar o resultado de cada mensagem e eventual entrega incerta.
- **Receção confirmada**: confirmação manual, com nota, utilizador e data.

O histórico conserva período, estabelecimentos, destinatários, mensagem,
utilizador, ficheiros, hashes, tamanhos, Message-ID, resultado de cada mensagem e
auditoria. Nos envios seguintes propõe apenas versões novas ou alteradas ainda
não aceites para todos os destinatários atuais. Pode selecionar novamente as anteriores.

Configuração, preparação, consulta de anexos e envio exigem administrador da
empresa selecionada, tal como o dossiê integral existente. A separação por esquema
da base de dados impede acesso cruzado entre empresas. O envio não contabiliza,
reconcilia, conclui documentos nem encerra períodos.

## Instalação e validação

Aplicar `alembic upgrade head` na pasta `backend` com o perfil correto e reiniciar
a API. A migração `0042` acrescenta configuração e histórico aos esquemas existentes;
novas empresas recebem as mesmas tabelas automaticamente.

Testes: `backend/tests/test_accounting_email.py`,
`backend/tests/test_accounting_email_tenants.py` e
`scripts/test-accounting-email-ui.py`. Os testes usam dados fictícios, base
descartável e SMTP simulado; não enviam emails reais.
