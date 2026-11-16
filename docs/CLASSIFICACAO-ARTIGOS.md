# Famílias, subfamílias e marcas

No menu **Artigos**, as tabelas **Famílias**, **Subfamílias** e **Marcas** permitem criar, editar,
ativar, desativar e remover classificações. Uma subfamília pertence a uma família; a mesma
designação pode existir em famílias diferentes. Administradores e gestores podem gerir estas tabelas.

Na ficha de artigo, escolha a família, a subfamília dessa família e a marca. Ao mudar de família,
a seleção da subfamília é limpa para evitar associações incorretas. As classificações são opcionais.
Administradores e gestores podem usar **+** e **lápis** junto de cada campo para criar ou editar
diretamente num painel da ficha, sem navegar para outra página. A nova classificação fica selecionada;
uma subfamília nova recebe a família escolhida na ficha. O painel guarda apenas a classificação;
os restantes dados continuam em edição até guardar o artigo. Cancelar o painel preserva esses dados.
Uma alteração de designação mantém a revisão da ficha aberta coerente com a atualização dos artigos,
sem ignorar alterações concorrentes feitas por outras pessoas.
Uma classificação desativada mantém-se nos artigos existentes e deixa de estar disponível para novas
associações. Registos com artigos ou subfamílias associados não podem ser eliminados.

Alterar a designação de uma classificação atualiza os artigos que a usam. Mudar uma subfamília
de família atualiza também a família dos seus artigos. Estas operações ficam na auditoria e alteram
a revisão das fichas; uma ficha aberta antes da alteração tem de ser atualizada antes de guardar.

## Sincronização com o site

As marcas que já existem na Raizpura são importadas para a IAZELA em cada sincronização do conector,
incluindo marcas sem produtos. Também é possível executar apenas esta operação em **WordPress →
WooCommerce → GPGest → Importar marcas para a IAZELA** (plugin Raizpura Commerce 1.3.2).
A comparação ignora maiúsculas/minúsculas e espaços repetidos: uma marca existente é reutilizada,
incluindo quando está inativa. Esta importação acrescenta nomes; não renomeia ou elimina marcas
locais, não as reativa e não altera a classificação de artigos. Nomes com mais de 100 caracteres
ficam sinalizados para revisão, sem serem truncados.

A classificação segue com a ficha do artigo nos circuitos existentes de integração e de
[publicação online](PUBLICACAO-ONLINE.md). No circuito de publicação, uma alteração da classificação
muda o conteúdo e o hash do artigo, que volta a ser enviado na próxima sincronização do conector.
O produto mantém a associação existente ao site. A família corresponde à categoria principal, a
subfamília a uma categoria filha e a marca à taxonomia de marcas da Raizpura/WooCommerce.

Criar uma classificação sem artigos não a publica isoladamente no site. Desativar uma classificação
não retira os artigos publicados; impede novas associações na aplicação. Termos antigos da loja
não são eliminados por esta operação.

## Atualização da base de dados

Aplicar a migração **0097** com `alembic upgrade head` a partir de `backend`.
A migração cria as tabelas em cada empresa e aproveita as classificações existentes nas fichas,
sem modificar os artigos. Uma subfamília antiga sem família mantém-se na ficha até ser classificada.
Novas classificações provenientes de importações são acrescentadas às tabelas ao guardar os artigos;
a pré-visualização não grava classificações.
