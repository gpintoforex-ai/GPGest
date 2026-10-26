# Administração da plataforma

Abra http://127.0.0.1:5174/plataforma. Esta página não aparece no menu das empresas. As credenciais exclusivas estão em `.local/ACESSO-PLATAFORMA.txt`, ignorado pelo Git. A ausência de um link não constitui a proteção: todos os endpoints exigem a sessão própria do administrador da plataforma. Administradores de lojas não têm acesso.

## Criar uma empresa

1. Escolha **Criar empresa** e indique nome, identificador único, nome/email do primeiro administrador e uma palavra-passe inicial com pelo menos 12 caracteres.
2. É criado um esquema PostgreSQL exclusivo, com uma loja, uma caixa e categorias, sem movimentos financeiros. O email pode repetir-se em empresas diferentes.
3. Envie ao cliente o endereço apresentado no cartão (`/?empresa=identificador`) e as suas credenciais, por um canal privado. A página de entrada também aceita o identificador manualmente.
4. O administrador da empresa gere a sua própria equipa e lojas. A empresa `demo` mantém os dados e acessos anteriores.

Desativar uma empresa impede novos acessos e pedidos autenticados, incluindo sessões já abertas. Não elimina os dados. A sessão da plataforma dura duas horas; as sessões das empresas duram oito horas.

## Reinicializar movimentos e documentos

Em **Configurar empresa**, a opção **Reinicializar movimentos e documentos** permite recomeçar mantendo as tabelas e os bancos. Exige escrever o NIF (ou o nome, quando não há NIF) e confirmar o aviso.

- Mantém utilizadores e palavras-passe, funcionários, lojas, caixas, rubricas, clientes, fornecedores, artigos, instituições e contas bancárias, terminais, meios de pagamento, associações aos bancos e configurações de integração.
- Retira os dados operacionais: folhas e movimentos de caixa, extratos bancários, importações e documentos e-Fatura/compras, documentos a crédito, anexos guardados na aplicação, pagamentos, depósitos, reconciliações, saldos de referência e histórico operacional.
- Termina as sessões da empresa e regista a operação na auditoria da plataforma. Não altera as outras empresas nem envia eliminações para o ERP, AT ou Google Drive.

A mudança é transacional: cria um novo conjunto de dados e conserva o anterior numa cópia interna. Se falhar, a empresa continua a usar os dados anteriores. Não há recuperação automática nesta janela. **Reinicializar todos os dados** continua disponível separadamente, com o comportamento anterior, mais abrangente sobre os cadastros.

Esta funcionalidade não executa nenhuma reinicialização por si só: só atua após a confirmação do administrador da plataforma. A disponibilização da opção não necessita de nova migração.

### Reinicialização individual de tabelas

Em **Plataforma → Configurar empresa → Reinicializar uma tabela**, existem opções independentes para **Artigos**, **Clientes** e **Fornecedores**. A primeira ação apenas consulta uma pré-visualização: total de fichas, fichas a retirar e fichas preservadas por histórico ou associações, com exemplos.

- São preservadas referências SQL, identificadores em texto/JSON e referências históricas por nome, código ou NIF. Referências ambíguas são tratadas de forma conservadora e podem impedir a retirada de uma ficha.
- Numa entidade simultaneamente cliente e fornecedor, retira-se apenas a função selecionada quando não existem associações; a outra função é mantida.
- A confirmação exige o NIF/nome da empresa, aceitação explícita e a versão da pré-visualização. Alterações entretanto ocorridas obrigam a consultar uma nova pré-visualização.
- Na mesma transação, bloqueiam-se escritas, copiam-se os dados para um novo esquema, altera-se apenas a tabela selecionada e muda-se a empresa para esse esquema. O esquema original fica arquivado; falhas revertem a cópia e a mudança da empresa. As sessões da empresa terminam; a sessão de administração da plataforma mantém-se.
- Não há escrita na Wintouch nem alteração de ficheiros externos. Uma sincronização posterior pode voltar a importar fichas do ERP. As outras empresas, tabelas e documentos mantêm-se.

A cópia interna não tem recuperação automática nesta janela. Adicionar a funcionalidade não reinicializa dados reais e não exige migração. Testes: `backend/tests/test_catalog_reset.py`, regressão com `backend/tests/test_platform_data_reset.py` e `scripts/test-platform-reset-ui.py`, usando empresas de teste e APIs simuladas.

Verificação: `backend/tests/test_platform_data_reset.py` cobre tabelas/bancos preservados, documentos e movimentos retirados, isolamento, confirmação, permissões, arquivo e reversão transacional de falhas. `scripts/test-platform-reset-ui.py` verifica a confirmação e a conservação dos campos após erro, em computador e telemóvel, com API simulada e sem reinicializar dados reais.

## Ligação Wintouch

Em **Configurar empresa**, indique o URL da API, a chave e o identificador Wintouch enviado no cabeçalho `x-current-enterprise`. Ative a ligação e guarde antes de testar. Por defeito só é autorizado `https://api.wintouchcloud.com` na porta HTTPS. Outros servidores exigem configuração explícita de `WINTOUCH_ALLOWED_HOSTS` no servidor, sem curingas.

A chave é cifrada com Fernet usando `PLATFORM_ENCRYPTION_KEY`, independente da base de dados. Nunca é devolvida ao navegador nem incluída na auditoria. Deixar o campo vazio preserva a chave; existe uma opção explícita para a remover. Configuração, criação de empresas e testes ficam registados na auditoria central.

**Testar ligação de leitura** faz apenas `GET /products?limit=1&skip=0`, com `Authorization: ApiKey ...` e o identificador da empresa. Não segue redirecionamentos, não importa dados nem emite documentos. Uma resposta HTTP 200 com JSON confirma comunicação, mas não valida todas as permissões nem o formato dos restantes endpoints. A importação financeira continua por implementar; consulte [INTEGRACAO-WINTOUCH.md](INTEGRACAO-WINTOUCH.md).

## Instalação, isolamento e recuperação

Para importar artigos, clientes/fornecedores e caixas, entre na empresa e abra **Integrações → Wintouch**. O administrador da empresa usa **Sincronizar agora** e consulta o histórico sem precisar do acesso central. Na demonstração exige confirmação explícita. Consulte [SINCRONIZACAO-WINTOUCH.md](SINCRONIZACAO-WINTOUCH.md) para paginação, campos e limites. A administração central mantém a configuração das credenciais e o teste de ligação, sem painel de sincronização.

O instalador executa as migrações até à versão atual (`0003`), gera a chave de encriptação e as credenciais privadas, e regista a empresa inicial sem substituir dados existentes. Em instalações manuais existentes:

```powershell
cd 'C:\Projetos\Site Loja\GPGest'
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe scripts\setup-platform.py
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe platform_seed.py
```

Reinicie o backend depois de instalar. O ambiente local mantém todas as empresas em demonstração. Não altere o modo de uma base de demonstração para a reutilizar como produção; prepare uma base real separada.

Os dados operacionais usam esquemas `gpgest_c_<uuid>` exclusivos; os registos da plataforma ficam em `public`, juntamente com a empresa inicial. A seleção no cookie determina o esquema no servidor, e a sessão de uma empresa não autentica noutra. A conta PostgreSQL é partilhada pela aplicação: este isolamento é aplicado pelo backend, não por utilizadores PostgreSQL distintos. A criação de empresas requer permissão para criar esquemas.

As cópias de segurança incluem a plataforma e todos os esquemas de empresas, excluindo os esquemas temporários de testes. Guarde também `.env`, sobretudo `PLATFORM_ENCRYPTION_KEY`, numa cópia privada e protegida: sem essa chave as credenciais Wintouch guardadas não podem ser recuperadas. Não a substitua após configurar clientes. O teste de recuperação compara tabelas e contagens de todas as empresas numa base temporária.

Antes de futuras alterações às tabelas operacionais, prepare e teste a migração de **todos** os esquemas existentes. A migração `0003` cria as [tabelas Wintouch](TABELAS-WINTOUCH.md) em todas as empresas registadas; futuras migrações devem seguir o mesmo cuidado. Os esquemas de novas empresas são criados com o modelo corrente. Execute as migrações com o servidor parado para impedir criação concorrente de empresas. Para alojamento público são ainda necessários HTTPS, `COOKIE_SECURE=true`, origens permitidas e configuração de produção adequada. Esta entrega mantém o arranque local.
