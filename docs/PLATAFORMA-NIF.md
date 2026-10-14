# Empresas e acesso pelo NIF

Em `/plataforma`, o administrador da plataforma pode criar v?rias empresas. Cada empresa tem NIF ?nico de nove d?gitos, administrador inicial, utilizadores, lojas, caixas e dados isolados num esquema pr?prio. O mesmo email pode pertencer a empresas diferentes.

1. Escolher **Criar empresa**, indicar nome, NIF e credenciais do primeiro administrador.
2. Configurar a liga??o Wintouch individualmente para cada empresa, se necess?ria.
3. Abrir o acesso da empresa: o NIF fica preenchido no formul?rio.
4. Entrar com NIF, email e palavra-passe. O NIF identifica a empresa; as credenciais autenticam o utilizador.

Empresas existentes mant?m todos os dados. Em **Configurar empresa**, preencher o NIF real: a migra??o n?o inventa n?meros fiscais. At? esta configura??o, os links antigos continuam a funcionar apenas para empresas sem NIF. Depois de atribu?do o NIF, o identificador antigo deixa de permitir a sele??o.

A plataforma mant?m o seu acesso administrativo separado, por email e palavra-passe. Desativar uma empresa impede a entrada e o uso das suas sess?es. NIF duplicados s?o impedidos pela API e pela base de dados. A implementa??o valida o formato de nove d?gitos; n?o consulta o registo fiscal.

## Reposi??o da palavra-passe

Em **Configurar empresa ? Utilizadores e acesso**, consulte o email e estado de cada utilizador. Escolha **Repor palavra-passe**, indique uma nova palavra-passe com pelo menos 12 caracteres e confirme-a. A opera??o termina as sess?es desse utilizador e regista a reposi??o no hist?rico da plataforma, sem guardar a palavra-passe no hist?rico. Apenas o administrador da plataforma pode usar esta fun??o. A nova palavra-passe deve ser entregue ao utilizador; n?o ? enviado email automaticamente.

## Reinicializar dados

Em **Configurar empresa ? Reinicializar dados**, escreva o NIF e confirme a reinicializa??o. A empresa passa para um esquema novo sem dados operacionais, preservando utilizadores, palavras-passe, lojas, caixas, permiss?es e categorias. A configura??o central Wintouch mant?m-se. Sess?es antigas deixam de funcionar. O esquema anterior fica guardado como c?pia interna, identificado no hist?rico da plataforma; n?o existe recupera??o autom?tica desta c?pia na interface. A op??o n?o ? executada ao abrir a configura??o.
