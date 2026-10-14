# Copiar os dados do PC para o Neon

O GPGest usa PostgreSQL e pode guardar a base de dados no Neon. Esta cópia mantém utilizadores, empresas, movimentos, anexos guardados na base, referências aos PDFs do Drive, auditoria e configurações cifradas. O ambiente continua em `demonstracao` para os testes.

A interface pode ficar no Cloudflare e a base no Neon. A API Python continua a executar no PC até ser alojada num servidor online. Mudar a base de dados, por si só, não permite desligar esse PC enquanto a API aí estiver.

## 1. Preparar o destino

1. No [painel do Neon](https://console.neon.tech/), crie um projeto de testes, por exemplo `gpgest-testes`, com PostgreSQL **16 ou mais recente**. Escolha uma região europeia próxima.
2. Use uma **base vazia**, sem as tabelas da aplicação. Pode criar uma base própria `gpgest` no projeto.
3. Abra **Connect**, selecione essa base e desligue **Connection pooling** para obter a ligação direta.
4. Copie apenas o URL `postgresql://…?sslmode=require…`, sem o comando `psql`.
5. Na raiz do GPGest, crie ou preencha o ficheiro privado `.local/neon.env`:

```dotenv
DATABASE_URL=COLE_AQUI_O_URL_DIRETO_DO_NEON
```

Mantenha os parâmetros TLS do URL fornecido pelo Neon. O ficheiro `.local/neon.env` está excluído do Git. Não coloque esta ligação no frontend, no Cloudflare Worker ou no chat; ela dá acesso à base de dados.

Referências: [ligar uma aplicação ao Neon](https://neon.com/docs/connect/connect-from-any-app) e [migração com pg_dump/pg_restore](https://neon.com/docs/import/migrate-from-postgres).

## 2. Criar e conferir a cópia

Na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe scripts\migrate-neon.py prepare
```

Este comando cria em `backups/`:

- Um `.dump` PostgreSQL, com `public` e todos os esquemas de empresas `gpgest_c_<id>`, incluindo históricos preservados. Os esquemas temporários dos testes ficam de fora.
- Um manifesto `.json` com contagens e assinaturas dos dados, calculadas a partir do mesmo snapshot usado pelo dump.
- Uma cópia privada `.env` e, se existir, `.google.json`, para preservar a chave de cifragem e a configuração Google. Estes ficheiros não são enviados para o Neon pelo script.

As assinaturas incluem o conteúdo dos anexos guardados na base. Os PDFs externos continuam no Drive; são copiadas as suas referências. Os backups e ficheiros de configuração contêm dados privados e estão excluídos do Git.

Para copiar esse backup, use o caminho do manifesto indicado pelo comando:

```powershell
.\.venv\Scripts\python.exe scripts\migrate-neon.py copy --manifest backups\NOME_DO_BACKUP.json
```

Pode omitir `--manifest` para criar um snapshot novo imediatamente antes da transferência:

```powershell
.\.venv\Scripts\python.exe scripts\migrate-neon.py copy
```

O script confirma que o destino está vazio, restaura numa única transação e compara empresas, tabelas, contagens, assinaturas dos dados e revisão das migrações. Não executa `DROP`, `--clean`, seeds ou reinicializações no destino. A base local é preservada. Um erro durante a restauração cancela essa transação; um erro de conferência impede a preparação do arranque. Detalhes de falhas do PostgreSQL ficam no ficheiro privado `.local/neon-migration-error.log`.

Depois de uma cópia conferida, cria `.local/neon-runtime.env` com a ligação Neon e as restantes definições locais, incluindo **a mesma `PLATFORM_ENCRYPTION_KEY`**. Não gere uma chave nova: as credenciais das integrações e os documentos cifrados deixariam de poder ser lidos. As contas e palavras-passe de acesso à aplicação são preservadas.

Para voltar a conferir o snapshot antes de usar a base:

```powershell
.\.venv\Scripts\python.exe scripts\migrate-neon.py verify --manifest backups\NOME_DO_BACKUP.json
```

Esta conferência exata é para antes de novos registos na base copiada; alterações posteriores legítimas fazem divergir as assinaturas. A cópia é pontual, não uma sincronização contínua. Se continuar a registar no PC depois do snapshot, esses registos não aparecem automaticamente no Neon. Para a mudança definitiva, pare a aplicação, inicie apenas o PostgreSQL local com `scripts/init-local.py` e crie uma cópia nova para outro destino vazio.

## 3. Testar o GPGest com os dados no Neon

Depois da transferência concluída e conferida:

```powershell
.\Parar.ps1
.\Iniciar-Neon.ps1
```

Abra `http://127.0.0.1:5174` e entre com as mesmas credenciais. Confira a seleção de empresas, movimentos, saldos e anexos. Este arranque valida a ligação, usa `.local/neon-runtime.env` e não inicializa PostgreSQL local. Para regressar à base local, execute `Parar.ps1` e `Iniciar.ps1`; o `.env` original continua intacto. Os dados introduzidos no Neon não são copiados de volta para o PC.

Para futuras migrações de código na base Neon, selecione explicitamente a configuração no terminal e use a ligação direta:

```powershell
$env:GPGEST_ENV_FILE = (Resolve-Path .local\neon-runtime.env).Path
try {
    Set-Location backend
    ..\.venv\Scripts\alembic.exe upgrade head
} finally {
    Set-Location ..
    Remove-Item Env:GPGEST_ENV_FILE -ErrorAction SilentlyContinue
}
```

Não use `Instalar.ps1` para instalar sobre a base copiada; esse script gere a instalação local. Não execute os testes do backend na base Neon que contém os dados copiados.

## 4. Ligar à interface de testes no Cloudflare

O Worker recebe apenas `BACKEND_ORIGIN`, o endereço HTTPS da API. A ligação ao Neon é uma configuração privada **da API Python**, nunca do navegador. Consulte [Cloudflare](CLOUDFLARE.md).

Enquanto a API estiver no PC, o túnel para a porta 8001 e o PC têm de estar ligados. Se usar a interface `https://gpgest.gpintoforex.workers.dev`, adicione essa origem a `ALLOWED_ORIGINS` e use `COOKIE_SECURE=true` em `.local/neon-runtime.env`, reiniciando a API. Essa definição destina-se ao acesso HTTPS; para os testes em `http://127.0.0.1:5174`, use `COOKIE_SECURE=false`.

Quando alojar a API online, transfira as definições para os segredos desse alojamento e preserve a chave de cifragem. A configuração privada Google `.local/google.json`, os certificados e-Fatura que existirem e o endereço de retorno OAuth também precisam de configuração no novo servidor. A base PostgreSQL não substitui esses ficheiros.

## Verificação do procedimento

Os testes de cópia verificam TLS, ligação direta, proteção das credenciais, seleção explícita do ambiente e preservação da chave. Para testar também a recuperação do último backup numa base **local temporária**, sem alterar a aplicação:

```powershell
$env:GPGEST_TEST_RESTORE = '1'
try {
    .\.venv\Scripts\python.exe scripts\test-neon-migration.py
} finally {
    Remove-Item Env:GPGEST_TEST_RESTORE -ErrorAction SilentlyContinue
}
```

O teste completo exige o ambiente local de demonstração e remove apenas a base temporária que ele próprio criou.
