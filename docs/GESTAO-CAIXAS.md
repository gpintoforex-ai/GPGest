# Editar e remover caixas

Em **Tabelas → Caixas**, administradores e gestores encontram as ações **Editar**
e **Remover** em cada caixa dos estabelecimentos a que têm acesso.

**Editar** permite corrigir o nome e, numa caixa sem utilização nem associações
protegidas, mudar o estabelecimento. As restantes configurações, incluindo moeda,
fundo de maneio e responsável, são preservadas. Quando existe histórico ou uma
associação protegida, pode alterar o nome mantendo o estabelecimento original.

**Remover** pede confirmação com o nome da caixa e do estabelecimento. A remoção é
definitiva e só é permitida quando não existem sessões/folhas de caixa, depósitos,
terminais de pagamento, associações Wintouch ou de integração, nem saldo inicial
diferente de zero. A aplicação explica o motivo de um bloqueio e conserva os dados.
Uma referência inicial a zero é removida juntamente com a caixa sem utilização.

As alterações e remoções ficam na auditoria. Os perfis de consulta e operador
mantêm a consulta da tabela sem estas ações. Uma falha de gravação mantém os
campos preenchidos; uma falha de remoção mantém a caixa na lista.

Não são apagados movimentos nem desfeitas associações automaticamente para
permitir uma remoção. Não há alterações ao esquema da base de dados.
