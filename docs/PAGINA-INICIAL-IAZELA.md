# Página pública IAZELA

A raiz `/` apresenta a página comercial a visitantes sem sessão e mantém o dashboard existente para utilizadores autenticados. `/entrar` apresenta o acesso da empresa. As ligações antigas com `?nif=` ou `?empresa=` mantêm o acesso direto; `/plataforma` permanece reservado à administração. Não foram alteradas a autenticação, a base de dados, as integrações ou as regras financeiras.

`HomePage.tsx` reutiliza os logótipos oficiais, o seletor de temas, a tipografia do projeto e os ícones Lucide existentes. A página tem navegação móvel, módulos, exemplo de dashboard, exemplos interativos de documentos/bancos/validades, etapas de configuração e modos de funcionamento. Todos os dados demonstrativos estão identificados. Não são emitidas operações, comunicações ou pedidos reais pela demonstração.

Não existe ainda email comercial público: o botão secundário é «Ver demonstração», ligado ao exemplo interativo. Não existe formulário fictício. Para ativar pedidos de demonstração ou contactos é necessário fornecer o canal comercial. Não foram inventados preços nem resultados comerciais. O domínio público definitivo é necessário para completar URLs absolutas de partilha e URL canónico; os restantes metadados estão definidos no HTML.

Validação: `npm run build` e `scripts/test-homepage-ui.py`, com navegação, alternância da demonstração, ausência de overflow em 390 px, temas, erros JavaScript e rotas de acesso. Capturas locais em `.local/iazela-home-*.png`.

O teste dedicado verifica ainda, com respostas fictícias e sem alterar dados reais, que uma sessão autenticada na raiz continua a abrir a aplicação interna e permite entrar na Conferência documental. Uma execução adicional do teste global legado `test-visual-template-ui.py` não concluiu: os seus seletores não distinguem os botões «Fechar» do detalhe e da pré-visualização e não acompanham os diálogos de descarte/menu atuais. Esse teste legado foi mantido sem alterações; não é apresentado como aprovado.
