# Publicar o GPGest no Cloudflare

O repositório inclui um Worker chamado `gpgest`, que serve a interface React compilada e encaminha `/api` para o servidor Python existente. A base PostgreSQL, os ficheiros temporários, o agendamento e as integrações continuam nesse servidor. Este Worker não instala nem executa o backend Python.

## Corrigir a implantação da captura

Em **Workers e Pages > gpgest > Configurações > Build**, configurar:

| Campo | Valor |
| --- | --- |
| Branch de produção | `main` |
| Diretório raiz | `/` (raiz do repositório) |
| Comando da build | Nenhum |
| Comando de implantação | `npm run deploy` |

`npm run deploy` instala as dependências do frontend a partir do seu lockfile, executa TypeScript/Vite e publica `frontend/dist` com Wrangler. O `package.json` e o lockfile da raiz fixam a versão do Wrangler. Depois de guardar, iniciar uma implantação com o commit que inclui `wrangler.jsonc`.

Em alternativa, usar **Comando da build** `npm run build` e **Comando de implantação** `npx wrangler deploy`. Não combinar `npm run build` com `npm run deploy`, pois compilaria duas vezes. O Worker deve manter o nome `gpgest`, igual ao projeto do painel.

As builds Cloudflare não executam o campo de custom build do Wrangler; os comandos acima são explícitos no painel. Referência: [Workers Builds](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/).

## Ligar a API

Em **Configurações > Variáveis e segredos**, adicionar uma variável de execução:

```text
BACKEND_ORIGIN=https://api.example.com
```

Substituir o exemplo pelo endereço HTTPS real do servidor FastAPI, sem `/api`, query, utilizador ou palavra-passe. Esta é uma variável de **execução**, não uma variável exclusiva da build. O Worker preserva as variáveis configuradas no painel nas implantações seguintes (`keep_vars`).

No servidor Python, manter `ALLOWED_ORIGINS` com o endereço HTTPS público exato do GPGest, preservando as outras origens utilizadas, e definir `COOKIE_SECURE=true`. Reiniciar a API depois de mudar estas variáveis. As verificações de origem, autenticação e empresa continuam na API; o Worker não as substitui nem reescreve o cabeçalho `Origin`.

Sem um backend configurado, a interface pode ser publicada, mas `/api` devolve 503 com uma mensagem de configuração em falta. Uma falha de ligação devolve 502. O Worker nunca repete pagamentos ou outras escritas automaticamente. Cookies de sessão e anexos passam pela mesma origem pública, sem cache das respostas API.

### Se o servidor ainda está no PC

`127.0.0.1:8001` é apenas local e não é um endereço que o Worker consiga alcançar. Para um teste, é necessário disponibilizar a API através de um túnel HTTPS autorizado e manter o PC, a API e o túnel ligados. Configurar `BACKEND_ORIGIN` com esse endereço e acrescentar a origem pública da interface a `ALLOWED_ORIGINS`.

Para produção, usar um servidor persistente para FastAPI e PostgreSQL conforme a secção **Dados reais e alojamento** do README. A publicação da interface no Cloudflare não migra a base de demonstração do PC. As integrações Google também têm de ter o callback e as credenciais adequados ao servidor público; a configuração local atual usa um callback em localhost.

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
