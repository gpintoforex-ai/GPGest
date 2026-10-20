# Empresas e acesso pelo NIF

Em `/plataforma`, o administrador da plataforma pode criar várias empresas. Cada empresa tem NIF ?nico de nove dígitos, administrador inicial, utilizadores, lojas, caixas e dados isolados num esquema próprio. O mesmo email pode pertencer a empresas diferentes.

1. Escolher **Criar empresa**, indicar nome, NIF e credenciais do primeiro administrador.
2. Configurar a ligação Wintouch individualmente para cada empresa, se necessária.
3. Abrir o acesso da empresa: o NIF fica preenchido no formulário.
4. Entrar com NIF, email e palavra-passe. O NIF identifica a empresa; as credenciais autenticam o utilizador.

Empresas existentes mantém todos os dados. Em **Configurar empresa**, preencher o NIF real: a migração não inventa números fiscais. Até esta configuração, os links antigos continuam a funcionar apenas para empresas sem NIF. Depois de atribuído o NIF, o identificador antigo deixa de permitir a seleção.

A plataforma mantém o seu acesso administrativo separado, por email e palavra-passe. Desativar uma empresa impede a entrada e o uso das suas sessões. NIF duplicados são impedidos pela API e pela base de dados. A implementação valida o formato de nove dígitos; não consulta o registo fiscal.

## Reposição da palavra-passe

Em **Configurar empresa ? Utilizadores e acesso**, consulte o email e estado de cada utilizador. Escolha **Repor palavra-passe**, indique uma nova palavra-passe com pelo menos 12 caracteres e confirme-a. A operação termina as sessões desse utilizador e regista a reposição no histórico da plataforma, sem guardar a palavra-passe no histórico. Apenas o administrador da plataforma pode usar esta função. A nova palavra-passe deve ser entregue ao utilizador; não ? enviado email automaticamente.

## Reinicializar dados

Em **Configurar empresa ? Reinicializar dados**, escreva o NIF e confirme a reinicialização. A empresa passa para um esquema novo sem dados operacionais, preservando utilizadores, palavras-passe, lojas, caixas, permissões e categorias. A configuração central Wintouch mantém-se. Sessões antigas deixam de funcionar. O esquema anterior fica guardado como cópia interna, identificado no histórico da plataforma; não existe recuperação automática desta cópia na interface. A opção não ? executada ao abrir a configuração.
