# Mudar apenas a API para outro PC, mantendo Cloudflare e Neon

Este procedimento muda apenas a máquina que executa a API usada pelo Cloudflare. A interface continua no Cloudflare e os dados continuam na mesma base Neon. A instalação local no PC atual é preservada. Os exemplos assumem Windows no novo PC.

| Acesso | API | Dados |
| --- | --- | --- |
| Local, no PC atual | API atual em `127.0.0.1:8001` | Base e configuração atuais, preservadas |
| `https://gpgest.gpintoforex.workers.dev` | API no novo PC, acessível por túnel HTTPS | A mesma base Neon já utilizada |

A interface local usa o proxy configurado em `frontend/vite.config.ts`, que aponta para a API deste PC. A variável `BACKEND_ORIGIN` pertence ao Worker e controla apenas o destino dos pedidos feitos pela interface no Cloudflare.

## 1. Manter os dados no Neon

Não é necessário criar outra base, criar um branch ou copiar dados. A API do novo PC deve usar o mesmo `DATABASE_URL` Neon da API atualmente ligada ao Cloudflare, mantendo os parâmetros TLS.

Não altere o `DATABASE_URL` nem os ficheiros de arranque da instalação local atual. Se essa instalação já usa a mesma base Neon, ambos os acessos veem e alteram os mesmos dados. Se usa PostgreSQL local, continua a usar essa base local, sem sincronização automática com o Neon.

As configurações das empresas e das integrações guardadas no Neon também são partilhadas. Desativar uma integração nessa base afeta os outros acessos à mesma base; esta mudança de máquina não requer alterar essas configurações.

## 2. Copiar apenas as pastas necessárias à API

Não é necessário copiar a pasta inteira `Site Loja` nem o frontend. Crie, por exemplo, `C:\GPGest-API` e mantenha esta estrutura:

```text
C:\GPGest-API\
  backend\                     ← copiar esta pasta do GPGest atual
  .local\
    api-cloudflare.env         ← configuração privada, conforme o passo 3
    google.json                ← apenas se utilizar Google/Drive
    cloudflared\
      cloudflared.exe          ← executável do túnel
```

Para preparar `api-cloudflare.env`, pode copiar privadamente `.local\neon-runtime.env` e dar esse nome à cópia no novo PC, seguindo o passo 3. Da pasta `.local`, transfira apenas os ficheiros necessários indicados, em vez de copiar a pasta inteira. Se houver certificados ou outros ficheiros externos configurados nas integrações, transfira-os também e ajuste os respetivos caminhos no novo PC.

Copie a pasta `backend` atual, incluindo as alterações locais necessárias: uma cópia obtida apenas do GitHub não inclui trabalho que ainda não foi enviado. Use a mesma versão de código compatível com a base existente e com a interface publicada.

Recrie as dependências no novo computador. Não transfira a `.venv`, `node_modules`, `.git`, backups, caches (`__pycache__`, `.pytest_cache`), ficheiros `.pid`, logs ou a pasta dos dados PostgreSQL em utilização. Para executar apenas a API, não é necessário iniciar o frontend nem instalar Node.js nesse PC.

Instale Python 3.13 e, no PowerShell do **novo PC**, execute:

```powershell
Set-Location -LiteralPath 'C:\GPGest-API'
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
New-Item -ItemType Directory -Path '.local' -Force
```

## 3. Transferir a configuração privada da API

No novo PC, prepare `.local\api-cloudflare.env` a partir da configuração privada usada atualmente pela API ligada ao Neon, preservando as restantes definições e o modo da aplicação. Em particular:

```dotenv
DATABASE_URL=MESMO_URL_POSTGRESQL_NEON_ATUAL
COOKIE_SECURE=true
ALLOWED_ORIGINS=https://gpgest.gpintoforex.workers.dev
PLATFORM_ENCRYPTION_KEY=MESMA_CHAVE_ATUAL
```

Substitua os exemplos pelos valores privados. O perfil atualmente utilizado para o Neon está em `.local\neon-runtime.env`; transfira as definições por um meio privado. `COOKIE_SECURE=true` destina-se ao acesso HTTPS pelo Cloudflare. O ficheiro selecionado por `GPGEST_ENV_FILE` tem precedência sobre a ligação herdada do terminal.

Conserve a mesma `PLATFORM_ENCRYPTION_KEY`. Gerar outra chave impede a leitura das credenciais e dos documentos cifrados existentes. Essa chave e os URLs PostgreSQL não devem ir para o GitHub ou para o Worker.

A simples mudança da API para outro PC não requer instalar, restaurar ou reinicializar a base. Não execute `Instalar.ps1`, inicializadores, seeds ou migrações de esquema como parte desta transferência da mesma versão.

## 4. Iniciar apenas a API no novo PC

Num terminal do **novo PC**:

```powershell
Set-Location -LiteralPath 'C:\GPGest-API'
$env:GPGEST_ENV_FILE = (Resolve-Path -LiteralPath '.local\api-cloudflare.env').Path
Set-Location -LiteralPath 'backend'
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Mantenha esse terminal aberto. Num segundo terminal, confirme que `http://127.0.0.1:8001/api/health` responde. A resposta de saúde confirma o processo, mas ainda é necessário validar o login, a empresa, consultas autenticadas e anexos. O agendador existente inicia com esta API; a mudança não requer criar novos agendamentos no Windows para cada integração.

## 5. Criar o túnel do novo PC

Instale `cloudflared` nesse computador ou copie o executável existente em `.local\cloudflared\cloudflared.exe` para a mesma localização no novo projeto. Num segundo terminal do **novo PC**:

```powershell
Set-Location -LiteralPath 'C:\GPGest-API'
.\.local\cloudflared\cloudflared.exe tunnel --url http://127.0.0.1:8001
```

O comando apresenta um endereço `https://....trycloudflare.com`. Abra esse endereço seguido de `/api/health` e confira a resposta antes de mudar o Worker. Não precisa de abrir uma porta de entrada no router para este túnel.

O Quick Tunnel destina-se a testes: o endereço muda quando é criado um novo túnel e deixa de funcionar quando o processo termina. Para um endereço estável, configure um túnel gerido com um domínio apropriado. O PC, a API, o túnel e a ligação à Internet precisam de estar disponíveis enquanto houver acesso externo.

Referências: [Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/) e [túnel gerido](https://developers.cloudflare.com/tunnel/get-started/).

## 6. Alterar o destino no Cloudflare

Guarde o valor atual de `BACKEND_ORIGIN` para poder regressar ao destino anterior durante a validação. Mantenha o túnel antigo ativo até confirmar a mudança.

No Cloudflare, abra **Workers e Pages > gpgest > Configurações > Variáveis e segredos**. Edite a variável de **execução**:

```text
BACKEND_ORIGIN=https://NOVO-ENDERECO.trycloudflare.com
```

Use o novo endereço sem `/api` no fim e guarde/implante a alteração. Esta definição fica fora da secção **Build**. O endereço público da interface mantém-se `https://gpgest.gpintoforex.workers.dev`.

Entre novamente pela interface pública com as mesmas credenciais e confirme a empresa, os dados e os anexos existentes no Neon. Confirme também o acesso local no PC atual. Para validar a independência do novo alojamento da API, pode parar apenas o túnel antigo e testar novamente o acesso público. Se houver algum problema antes desse passo, restaure o valor anterior de `BACKEND_ORIGIN` enquanto o túnel anterior estiver ativo.

Depois da validação, pode terminar apenas o processo do **túnel antigo**. Mantenha a API, o frontend e, se aplicável, o PostgreSQL do PC atual a funcionar. `Parar.ps1` pára a instalação local e não deve ser usado nesse PC para concluir esta mudança de túnel.

## Integrações e funcionamento diário

Configure o arranque automático da API e do túnel no novo PC se pretender acesso sem abrir terminais manualmente. Uma suspensão ou encerramento do novo PC interrompe apenas o acesso Cloudflare a essa API; a instalação local continua a depender dos seus próprios serviços e da sua base.

As definições Google também exigem atenção: transfira privadamente `.local\google.json` se a integração for utilizada. Para autorizar a partir da interface pública, registe `https://gpgest.gpintoforex.workers.dev/api/integrations/attachments/google/callback` no cliente OAuth da Google Cloud Console. Acrescente o mesmo URI a `redirect_uris` em `.local\google.json` no PC da API (ver [Gmail e Drive](GMAIL-DRIVE-ANEXOS.md)). Outros certificados ou ficheiros privados necessários às integrações também têm de ser configurados no novo PC.

Não é necessário contratar alojamento para a API enquanto ela executar nessa máquina. O novo PC assume a função de servidor: tem de permanecer ligado, sem suspensão, e com Internet para o acesso pelo Cloudflare funcionar. Os dados continuam no Neon e a interface continua no Cloudflare.
