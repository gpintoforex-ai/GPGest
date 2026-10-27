# Publicar o GPGest no Cloudflare

O repositório inclui um Worker chamado `gpgest`, que serve a interface React compilada e encaminha `/api` para o servidor Python existente. A API, o agendamento e as integrações continuam no PC durante os testes; os dados PostgreSQL já podem estar no Neon, conforme [Dados no Neon](NEON.md). Este Worker não instala nem executa o backend Python.

## Corrigir a implantação da captura

Em **Workers e Pages > gpgest > Configurações > Build**, configurar:

| Campo | Valor |
| --- | --- |
| Branch de produção | `main` |
| Diretório raiz | `/` (raiz do repositório) |
| Variável da build | `SKIP_DEPENDENCY_INSTALL=1` |
| Comando da build | Nenhum |
| Comando de implantação | `npm run deploy` |

Adicione `SKIP_DEPENDENCY_INSTALL` com valor `1` em **Configurações > Build > Variáveis e segredos da build**. É uma variável da fase de build: impede a instalação automática que estava a executar `pip` antes do comando de implantação. Referência: [Build image — Skip dependency install](https://developers.cloudflare.com/workers/ci-cd/builds/build-image/#skip-dependency-install).

`npm run deploy` instala explicitamente as dependências Node da raiz e do frontend a partir dos seus lockfiles, executa TypeScript/Vite e publica `frontend/dist` com Wrangler. O `package.json` e o lockfile da raiz fixam a versão do Wrangler. Depois de guardar, iniciar uma implantação do commit mais recente de `main`. O Worker deve manter o nome `gpgest`, igual ao projeto do painel.

Em alternativa, usar **Comando da build** `npm ci && npm run build` e **Comando de implantação** `npx wrangler deploy`, com a mesma variável `SKIP_DEPENDENCY_INSTALL=1`. Não combinar `npm run build` com `npm run deploy`, pois compilaria duas vezes.

As builds Cloudflare não executam o campo de custom build do Wrangler; os comandos acima são explícitos no painel. Referência: [Workers Builds](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/).

### Erro ao instalar a raiz como pacote Python

Se o log mostrar `Failed to build 'file:///opt/buildhome/repo' when getting requirements to build wheel` antes de executar o comando de implantação, a instalação automática está a tentar instalar a raiz como pacote Python.

A raiz é o projeto Node.js que publica o frontend. A configuração do Ruff está em `ruff.toml`; o antigo `pyproject.toml`, usado apenas para configurar esse analisador, foi removido para não desencadear a instalação Python. As dependências da API mantêm-se em `backend/requirements.txt`, instaladas no PC.

Inicie uma **nova build do commit mais recente de `main`**. Repetir uma build de um commit antigo volta a usar o `pyproject.toml` anterior. Confirme o diretório raiz `/`, o comando de build vazio, a implantação `npm run deploy` e a variável da build `SKIP_DEPENDENCY_INSTALL=1`.

Se o erro persistir depois de atualizar o commit, desative a instalação automática em **Configurações > Build > Variáveis e segredos da build**:

| Campo | Valor |
| --- | --- |
| Variável de build | `SKIP_DEPENDENCY_INSTALL=1` |
| Comando da build | Nenhum |
| Comando de implantação | `npm run deploy` |

O comando `npm run deploy` já executa `npm ci` para instalar explicitamente o Wrangler da raiz antes de compilar e publicar. A variável precisa de estar guardada antes de iniciar a nova build, pois a falha acontece antes de executar qualquer comando do repositório.

## Builds de pré-visualização

O `wrangler.jsonc` inclui `"previews": {}` para permitir `wrangler preview` nas branches de teste. Os assets, o entrypoint e a compatibilidade mantêm-se no nível principal. Este bloco não publica a branch em produção.

Na configuração de builds do Cloudflare, a produção usa `npm run deploy`. Se uma branch de teste usar `npx wrangler preview`, compile previamente os assets com `npm ci && npm run build`. Uma falha a pedir o bloco `previews` indica uma build de pré-visualização, não um erro do backend Python.

## Ligar a API

Em **Configurações > Variáveis e segredos**, adicionar uma variável de execução:

```text
BACKEND_ORIGIN=https://api.example.com
```

Substituir o exemplo pelo endereço HTTPS real do servidor FastAPI, sem `/api`, query, utilizador ou palavra-passe. Esta é uma variável de **execução**, não uma variável exclusiva da build. O Worker preserva as variáveis configuradas no painel nas implantações seguintes (`keep_vars`).

No servidor Python, manter `ALLOWED_ORIGINS` com o endereço HTTPS público exato do GPGest, preservando as outras origens utilizadas, e definir `COOKIE_SECURE=true`. Reiniciar a API depois de mudar estas variáveis. As verificações de origem, autenticação e empresa continuam na API; o Worker não as substitui nem reescreve o cabeçalho `Origin`.

Sem um backend configurado, a interface pode ser publicada, mas `/api` devolve 503 com uma mensagem de configuração em falta. Uma falha de ligação devolve 502. O Worker nunca repete pagamentos ou outras escritas automaticamente. Cookies de sessão e anexos passam pela mesma origem pública, sem cache das respostas API.

### Se o servidor ainda está no PC

Para passar apenas a API para outro computador, mantendo os dados no Neon e preservando a instalação local atual, consulte [Mudar apenas a API para outro PC](MIGRAR-API-PC.md).

`127.0.0.1:8001` é apenas local e não é um endereço que o Worker consiga alcançar. Para um teste, é necessário disponibilizar a API através de um túnel HTTPS autorizado e manter o PC, a API e o túnel ligados. Configurar `BACKEND_ORIGIN` com esse endereço e acrescentar a origem pública da interface a `ALLOWED_ORIGINS`.

Para produção, usar um servidor persistente para FastAPI e PostgreSQL conforme a secção **Dados reais e alojamento** do README. A publicação da interface no Cloudflare não migra a base de demonstração do PC. As integrações Google também precisam das credenciais no servidor e do retorno OAuth da origem pública registado (ver [Gmail e Drive](GMAIL-DRIVE-ANEXOS.md)).

## Validar sem publicar

Na raiz do repositório:

No Windows, executar esta validação com o servidor Vite parado enquanto as dependências do frontend são reinstaladas, para evitar bloquear `esbuild.exe`.

```powershell
npm.cmd ci
npm.cmd run test:cloudflare
npm.cmd run build
npm.cmd run check:deploy
```

O último comando prepara a implantação em modo `--dry-run`, sem publicar nem exigir credenciais Cloudflare. Após a implantação real, verificar `/api/health`, o login, uma consulta autenticada e um anexo. Um dry-run não confirma a ligação a um backend real.

Os ficheiros `.env`, `.local`, `.dev.vars`, a base de dados e os tokens não devem ser incluídos no GitHub nem nos assets públicos. Os assets são exclusivamente `frontend/dist`.
