# Conector da IAZELA para o Claude (MCP)

Permite fazer perguntas sobre os dados da empresa diretamente no chat do Claude (claude.ai, app do telemóvel, app
para computador e Claude Code): vendas, artigos mais vendidos, clientes, IVA, validades, reposição e documentos em
falta. O Claude responde com os números reais da IAZELA e pode juntar-lhes gráficos e análises.

O conector **só consulta**: não cria, altera nem apaga nada. As alterações com confirmação (preços, imagens,
descritivos, publicação no site) continuam no [assistente da IAZELA](ASSISTENTE-IAZELA.md).

## Como funciona

- Servidor MCP (protocolo «Streamable HTTP», sem estado) em `POST /api/mcp`, na API da IAZELA. Na versão
  publicada, o Cloudflare Worker já encaminha `/api/*` e também os endereços de descoberta OAuth
  (`/.well-known/oauth-authorization-server/api/mcp`, `/.well-known/oauth-protected-resource/api/mcp` e
  `/.well-known/openid-configuration/api/mcp`).
- Autorização OAuth 2.1 com registo dinâmico de clientes e PKCE S256. Ao ligar, o Claude abre uma página da
  própria IAZELA onde se entra com o **NIF da empresa**, o **email** e a **palavra-passe**. O Claude nunca vê a
  palavra-passe. Só **administradores e gestores**, como o assistente.
- As ferramentas são as de consulta do assistente (`comparativo_vendas`, `artigos_mais_vendidos`,
  `compras_do_cliente`, `clientes_sem_compras`, `previsao_iva`, `documentos_em_falta_contabilidade`,
  `artigos_a_terminar_validade`, `produtos_a_encomendar`, `procurar_artigos`), com as permissões e as lojas do
  utilizador. Os números são os mesmos dos ecrãs e do assistente.
- Tokens assinados com uma chave derivada de `MCP_SECRET`, sem tabelas novas: acesso válido 1 hora, renovação 30
  dias (com rotação). Desativar o utilizador, mudar-lhe o perfil para operador/consulta ou desativar a empresa
  corta o acesso no pedido seguinte. **Mudar `MCP_SECRET` revoga todas as ligações.**
- Auditoria: `mcp_autorizado` quando alguém liga o conector e `mcp_consulta` em cada consulta (ferramenta e
  argumentos).

## Ativar

1. No servidor da API (o PC onde corre o FastAPI), acrescentar ao `.env`:

   ```text
   MCP_SECRET=<texto aleatório com 48 caracteres ou mais>
   ```

   Para gerar: `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"`.
   Sem esta linha (ou com menos de 32 caracteres) o conector fica desligado e os endereços respondem 404.
2. Confirmar que `ALLOWED_ORIGINS` inclui o endereço público HTTPS da IAZELA (já necessário para a app no
   Cloudflare). Opcional: `PUBLIC_API_ORIGIN=https://<endereço público>` fixa o endereço anunciado ao Claude;
   sem ele, usa o que o Worker indica (`X-Forwarded-Host`).
3. Reiniciar a API e publicar o Worker atualizado (`npm run deploy`, ou a build automática do Cloudflare).
4. No Claude: **Definições → Conectores → Adicionar conector personalizado** e indicar
   `https://<endereço público da IAZELA>/api/mcp`. Não é preciso Client ID nem Client Secret.
5. **Ligar**: abre a página da IAZELA; entrar com NIF, email e palavra-passe e carregar em **Autorizar**.

Depois, no chat: «Analisa as vendas deste ano», «Quais os 10 artigos mais vendidos em outubro?», «Que clientes não
compram há 90 dias?».

## Limites

- O Claude liga-se a partir da cloud da Anthropic: só funciona com a versão publicada (Cloudflare). A instalação
  apenas local, em `127.0.0.1`, não é alcançável.
- A API tem de estar ligada e acessível ao Worker. Com o PC desligado, o conector não responde.
- Cada ferramenta devolve no máximo 50 linhas, como no assistente.
- As perguntas e os dados consultados são enviados à Anthropic. Inclua-a como subcontratante na política de
  privacidade e no registo de tratamentos, como para o assistente.
- Os códigos de autorização de uso único são controlados em memória: com mais do que um processo da API, um
  código ainda por expirar (2 minutos) podia ser trocado duas vezes. Com um só processo (a instalação atual) não
  se aplica.

## Verificação

- `backend/tests/test_mcp_server.py`: desligado sem chave, descoberta (401 com `resource_metadata`), metadados,
  registo só com os endereços de retorno do Claude, entrada recusada (palavra-passe, NIF, perfil), PKCE, código de
  uso único, rotação da renovação, consultas só de leitura, auditoria, sessões internas fechadas e revogação.
- `scripts/test-cloudflare-worker.mjs`: o Worker encaminha só os três endereços de descoberta do conector.
- Ensaio com o cliente MCP oficial (biblioteca `mcp`) contra a API local: descoberta, registo dinâmico, página de
  entrada, PKCE, tokens, `initialize`, `tools/list` e `tools/call`.
