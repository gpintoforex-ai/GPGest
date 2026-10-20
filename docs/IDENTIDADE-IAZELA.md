# Identidade IAZELA

A aplicação adota a marca **IAZELA**. O projeto técnico continua na pasta `GPGest`; a identidade visual não modifica dados, cálculos, permissões ou contratos de integração.

## Logótipo oficial

O componente `Brand` utiliza os logótipos e ícones oficiais guardados em `frontend/public/brand/iazela/`. Usa variantes para fundos claros e escuros, sem alterar os ficheiros de origem. O favicon e o ícone para dispositivos móveis usam os recursos IAZELA existentes.

O componente `Brand` usa a versão horizontal no acesso, navegação e plataforma, e o ícone quadrado na navegação recolhida. Esta identidade mantém o tema da aplicação e as configurações existentes.

## Sistema visual

As regras partilhadas ficam em `frontend/src/design-system.css`, com os componentes de interação em `components/InteractionUI`.

| Função | Cor |
| --- | --- |
| Azul-marinho da marca / ação principal | `#0b2952` |
| Turquesa / seleção e detalhe | `#00bfc1` |
| Turquesa escuro para texto e foco | `#086c72` |
| Fundo de trabalho | `#f5f7f9` |
| Superfície | `#ffffff` |
| Texto | `#192b41` |
| Texto secundário | `#526174` |

O turquesa claro é um detalhe visual, não texto sobre branco. Verde, laranja e vermelho continuam a indicar sucesso, aviso e erro, acompanhados por texto. O tema mantém superfícies claras; os fundos escuros estão limitados ao acesso e às sobreposições.

A tipografia usa a família do sistema (`Segoe UI` no Windows), sem pedidos externos a fontes. Títulos, valores tabulares, etiquetas e texto auxiliar têm dimensões e pesos consistentes. Os ícones continuam a usar Lucide.

## Áreas revistas

- Entrada da empresa e da plataforma, logótipo, título do navegador e favicon.
- Navegação completa/recolhida/móvel, empresa selecionada, modo de trabalho e página atual.
- Visão geral com saldo de numerário e contagens de contas, caixas abertas e movimentos carregados, prioridades e acessos a caixa, bancos e conferência documental. Todos os indicadores usam dados da consulta; informações indisponíveis não são substituídas por valores inventados.
- Tabelas financeiras, filtros, totais, documentos e extratos; valores monetários alinhados à direita, mantendo a disposição dos controlos de contagem de caixa.
- Indicadores documentais compactos e filtros avançados recolhíveis, com contagem dos filtros ativos e conservação das escolhas ao recolher.
- Assistentes, configuração inicial, contabilidade, integrações e plataforma.
- Botões, campos, etiquetas de estado, mensagens e janelas; conservam-se X, Escape, proteção de rascunhos, confirmações e bloqueio de submissões repetidas.

Os nomes de páginas continuam em português de Portugal. A chave interna `Dashboard` apresenta-se como **Visão geral**, sem alterar a navegação técnica.

## Compatibilidade

Mantêm-se cookies, eventos `gpgest-*`, chaves de armazenamento, esquemas PostgreSQL, variáveis `GPGEST_*`, nomes técnicos de ficheiros e valores contratuais de origem da API. Por exemplo, a origem técnica histórica `GPGest` é apresentada como **IAZELA** na lista de entidades. As credenciais e nomes de empresas definidos pelos utilizadores não são alterados.

## Verificação

`npm.cmd --prefix frontend run build` e `npm.cmd --prefix frontend run check:ui` verificam compilação e utilização dos componentes acessíveis. O teste `scripts/test-interactions-ui.py` usa exclusivamente uma base local descartável e inclui capturas do acesso, navegação, visão geral, bancos, folha de caixa, contabilidade, documentos e configuração em computador/telemóvel. Mantém testes de validação, foco, teclado, confirmações, rascunhos e importação.

Foi verificada a igualdade binária entre o logótipo original fornecido e a cópia integral incluída. Os pares principais de texto/fundo e ação principal cumprem contraste de pelo menos 4,5:1; o azul-marinho sobre branco tem 14,45:1 e o turquesa escuro sobre branco 6,18:1. O turquesa claro não é usado como texto sobre branco.

Os percursos de configuração inicial autónomo e com ERP simulado estão em `scripts/test-company-setup-ui.py`. A plataforma tem verificação com API simulada em `scripts/test-platform-reset-ui.py`. Nenhum destes testes deve executar operações financeiras em produção.

As capturas de validação ficam na pasta privada `.local/interactions-ui`. A validação de outros navegadores, leitores de ecrã e dispositivos físicos continua manual. As integrações externas reais não são exercitadas nos testes visuais.
