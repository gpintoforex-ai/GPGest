# Template visual GPGest

Implementado na aplicação React/TypeScript com Vite, Lucide e CSS existente.
Não altera modelos, rotas de API, permissões ou regras financeiras. Não requer
migrações nem novas dependências.

## Base comum

`frontend/src/design-system.css` define as cores, fontes, espaçamentos, bordas,
sombras e tamanhos de navegação. É carregado depois dos estilos existentes em
`main.tsx`, permitindo modernizar a apresentação sem remover estilos dos módulos.

Usar `var(--color-primary)` para ações (turquesa escuro da marca no tema claro),
`--color-nav` para navegação e as famílias
`--color-success`, `--color-warning`, `--color-danger` para estados. Superfícies
brancas e fundo cinzento; fontes DM Sans/Manrope com fallback Segoe UI. As fontes
Google existentes são opcionais: sem Internet, o browser usa as fontes locais.

`components/BusinessUI.tsx` disponibiliza `MetricCard` e `StatusBadge`. Os badges
sempre incluem texto. Os estilos comuns uniformizam botões, campos, tabelas,
separadores, modais, estados vazios e mensagens. `utils/format.ts` formata cêntimos
como `1.234,56 €`, preserva sinais e mostra `—` quando o valor não está disponível.

No tema claro, o menu lateral usa um cinzento neutro inspirado nas Definições do
Windows, com texto escuro, logótipo sem fundo e sombra discreta; no tema escuro
mantém o fundo azul-petróleo. Os botões primários claros usam turquesa da marca
com texto escuro para conservar contraste e sombra turquesa subtil, reforçada
no estado hover.
O menu lateral é recolhível em computador, com nomes acessíveis e títulos nos
ícones. Em tablet/telemóvel, o botão de menu abre a navegação sobre a página;
o fundo permite fechá-la. O cabeçalho mostra empresa, módulo, página, filtro de
loja e menu de utilizador com saída de sessão funcional.

## Conferência documental

Abra **Contabilidade → Conferência documental**. Os indicadores usam os dados
efetivamente devolvidos pela API e respeitam a loja selecionada:

- Importados: documentos com `import_id`.
- Relacionados: estado ERP `confirmada`.
- Não encontrados: estado ERP `sem_correspondencia`.
- Sem anexo: estado `sem_anexo`.
- Divergências: estado ERP `divergencias`.
- A aguardar aprovação: propostas `preparado`, descritas como propostas por rever.
  Não existe aprovação formal no backend; a legenda indica essa limitação.

Os indicadores não são totais gerais de toda a base: correspondem ao conjunto
de documentos acessível ao utilizador e carregado pela API. As outras pendências
existentes estão num painel expansível. Período, fornecedor e estados continuam
a filtrar a lista. Data, fornecedor, documento e total têm ordenação acessível
por botões, com `aria-sort`. Mantém-se paginação de 20 linhas.

**Conferir** abre o painel de detalhe com dados locais e ERP lado a lado,
divergências, pré-visualização de anexos, observações e histórico existentes.
Escape fecha o painel; o diálogo nativo mantém o foco no detalhe. A pré-visualização
da linha continua disponível. ERP, anexo, tratamento e entrega mantêm estados
independentes. Desfazer uma associação ERP pede confirmação e conserva o motivo.

Tabelas largas têm deslocação horizontal dentro do próprio painel, sem alargar
a página. Os seis indicadores passam a duas colunas em ecrãs pequenos; filtros
e comparação de documentos passam a uma coluna em telemóvel.

## Verificação

```powershell
cd frontend
npm.cmd run build
cd ..
.\.venv\Scripts\python.exe scripts\test-visual-template-ui.py
```

O teste visual precisa do Vite na porta 5174 e do Chromium do Playwright já
instalado. Interceta as chamadas API com dados fictícios identificados como
**Empresa de demonstração visual**. Não acede ao ERP nem altera a base de dados.
Verifica indicadores, pesquisa, ordenação, paginação, navegação, painel, Escape,
estado vazio, falha/repetição e ausência de overflow em 1600, 820 e 390 píxeis.
Guarda capturas em `.local/visual-template/`.

O teste visual não substitui testes de importação, escrita e integrações reais.
Os estilos antigos continuam nos módulos ainda não reorganizados; as variáveis
e componentes comuns devem ser usados em novas páginas e na migração gradual
dessas vistas.
