# Operadores / funcionários

Em **Tabelas → Operadores / funcionários**, consulte os utilizadores com acesso ao GPGest e os funcionários sem acesso. Pode pesquisar por nome ou contacto, filtrar por tipo de registo e utilizar o filtro geral de estabelecimento.

O administrador dispõe de duas opções:

- **Novo funcionário** guarda nome, estabelecimentos e, opcionalmente, email e telefone. Não cria utilizador nem palavra-passe. Pode editar o cadastro e ativar ou inativar o funcionário, mantendo o registo e a auditoria.
- **Novo utilizador** abre o formulário de acesso já existente: nome, email, palavra-passe inicial, perfil e loja autorizada. Mantêm-se os perfis administrador, gestor, operador e consulta.

Os restantes perfis consultam apenas funcionários associados aos estabelecimentos a que têm acesso. A criação e alteração do cadastro são reservadas ao administrador. Os utilizadores já existentes continuam a aparecer nas configurações e nas operações de caixa; um cadastro de funcionário sem acesso não substitui o utilizador responsável pela operação.

A persistência utiliza a migração **0037**, que cria as tabelas `employees` e `employee_stores` em cada empresa. A migração preserva utilizadores, credenciais e movimentos existentes. As alterações registam utilizador, data e hora na auditoria. O cadastro é preservado na reinicialização operacional de uma empresa, juntamente com as restantes configurações da equipa.
