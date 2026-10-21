# Contraste e legibilidade — IAZELA

Revisão de 18/10/2026. Alterações exclusivamente na apresentação; sem escritas em dados reais, autenticação, regras financeiras ou integrações ERP. Logótipos e fundos de marca preservados.

## Diagnóstico confirmado no navegador

- O assistente tinha cartões brancos/cinza-claro, mas recebia cores claras de texto e labels do tema escuro. No passo **Meios de pagamento**, várias labels mediam **1,12:1** e o título **1,16:1**.
- Um seletor global do modo escuro aplicava `color: inherit` a títulos, labels e células. A cor herdada nem sempre correspondia à superfície do componente.
- O seletor `:is(input,select,textarea,button,.download-link)` adquiria a especificidade da classe `.download-link`, mesmo quando aplicado a inputs. Impedia a aplicação das bordas de contraste dos campos. Foi separado em campos e ações.
- Resumos do dashboard, o texto auxiliar do aviso de demonstração e os filtros avançados tinham cores fixas com contraste insuficiente. O rodapé da tabela de meios de pagamento mantinha fundo claro com texto claro no modo escuro.
- Havia texto operacional a 11 px e estilos de opacidade para botões desativados, em vez de cores explícitas de estado.

## Sistema semântico

`frontend/src/contrast.css` define os pares abaixo. Os nomes `--color-*` existentes são aliases, evitando substituir os componentes. Os contextos `.surface-light` e `.surface-dark` definem também o esquema dos controlos nativos. Uma superfície clara permanece clara e legível mesmo dentro do tema escuro.

| Token | Claro | Escuro |
|---|---|---|
| `text-primary` | `#172B36` | `#F5FAFC` |
| `text-secondary` | `#526572` | `#CBD8DF` |
| `text-muted` | `#526572` | `#BDCDD6` |
| `text-label` | `#304653` | `#E0EAF0` |
| `text-placeholder` | `#526572` | `#B3C5CF` |
| `text-disabled` | `#65757F` | `#9DB0BB` |
| `surface-primary` | `#FFFFFF` | `#102C39` |
| `surface-secondary` | `#F5F7F9` | `#071F2B` |
| `surface-elevated` | `#FFFFFF` | `#143442` |
| `border-default` (decoração) | `#DCE5E9` | `#355361` |
| `border-control` (campos) | `#7C8F9A` | `#72929F` |
| `border-focus` | `#087765` | `#53E0C6` |
| `icon-primary` | `#304653` | `#E0EAF0` |
| `icon-secondary` | `#526572` | `#CBD8DF` |
| `action-primary` | `#0F6B5E` | `#0E7469` |
| Botão primário claro | `#32CBB0` com texto `#102C39` | — |
| `action-danger` | `#AD2935` | `#FFACB4` |

Estados informativos, sucesso, aviso e erro conservam os pares semânticos e os seus textos/ícones; não dependem apenas da cor. Botões inativos têm cores próprias, sem reduzir a opacidade do cartão. Checkboxes e radios mantêm inputs nativos no DOM, com seleção, contorno e foco visíveis, sem alterar os eventos ou permissões.

## Componentes corrigidos

- Assistente da empresa e cartão de início: contexto claro explícito, incluindo títulos, etapas, labels, «Obrigatório para a gaveta», checkboxes, inputs, seletores, ajuda e rodapé.
- Formulários e diálogos partilhados: texto, placeholders, bordas, foco e estados desativados.
- Dashboard: títulos dos indicadores e aviso de demonstração.
- Movimentos reais: totais e rodapé do resumo de pagamentos.
- Conferência documental: descrições dos indicadores, filtros avançados e tipografia de estados/tabelas.
- Notificações: fundos e texto semânticos, incluindo conteúdo descendente.
- Aparência: amostras de tema com contexto próprio, independentemente do tema ativo.
- Texto operacional: labels e campos 14 px, auxiliares 13 px, estados/cabeçalhos compactos com mínimo de 12 px; títulos do assistente mantidos a 20–24 px.

## Cálculos de referência

Fórmula de luminância sRGB e contraste da [WCAG 2.2](https://www.w3.org/TR/WCAG22/), critérios 1.4.3 e 1.4.11. Texto normal: 4,5:1; texto grande e elementos não textuais necessários: 3:1. Componentes realmente inativos são contados separadamente.

| Par | Contraste |
|---|---:|
| Principal claro / branco | 14,63:1 |
| Secundário claro / branco | 6,06:1 |
| Label clara / branco | 9,87:1 |
| Principal escuro / superfície escura | 13,85:1 |
| Secundário escuro / superfície escura | 10,01:1 |
| Label escura / superfície escura | 11,93:1 |
| Borda de campo clara / branco | 3,36:1 |
| Borda de campo escura / superfície escura | 4,39:1 |
| Branco / ação principal escura | 5,64:1 |
| Texto escuro / botão turquesa da marca | 7,15:1 |
| Texto escuro / hover turquesa | 8,26:1 |

## Verificação reproduzível

```powershell
.\.venv\Scripts\python.exe scripts/test-contrast-ui.py
.\.venv\Scripts\python.exe scripts/test-homepage-ui.py
npm run build --prefix frontend
```

As APIs são interceptadas com respostas demonstrativas, sem acesso de escrita a empresas reais. A auditoria mede as cores computadas após a cascata, calcula a luminância e recolhe capturas em **375×812, 430×932, 768×1024, 1366×768 e 1920×1080**, nos dois temas.

A matriz inclui o assistente nos estados normal, hover e foco (com opções selecionadas e campos realmente desativados), dashboard, movimentos reais, contas bancárias, conferência documental, clientes, artigos/stock com registos demonstrativos, regime de IVA, receitas/despesas e relatórios. Não é uma certificação integral de acessibilidade: conteúdos externos, imagens, PDFs, todos os registos reais e todos os diálogos possíveis não são enumerados por este teste. Os fundos com gradiente são também inspecionados visualmente; o cálculo DOM usa a sua cor-base computada, sem amostrar pixels das imagens.

Capturas e dados locais:

- [Antes — configuração, escuro](../.local/contrast-audit/before-setup-dark-1366.png).
- [Depois — configuração, escuro](../.local/contrast-audit/after-setup-dark-1366.png).
- [Resultados anteriores](../.local/contrast-audit/before.json).
- [Resultados finais da matriz](../.local/contrast-audit/after.json).

O resultado final e os mínimos medidos são resumidos em `docs/CONTRASTE-RESULTADOS.json`, gerado a partir da matriz final. As capturas completas ficam em `.local/contrast-audit/`.

Resultado final: **120 combinações de ecrã/estado**, **10 240 medições de texto**, **1 694 medições não textuais**, **zero falhas de contraste**, **zero textos operacionais abaixo de 12 px** e **zero ecrãs com overflow horizontal global** na amostra. Mínimo observado de texto: **4,65:1**; mínimo não textual: **3,13:1**. O assistente usa o mesmo contexto claro nos dois temas.

## Ficheiros desta revisão

`frontend/src/contrast.css`, `frontend/src/themes.css`, `frontend/src/main.tsx`, `frontend/src/CompanySetup.tsx`, `frontend/src/ThemeSettings.tsx`, `scripts/contrast-audit.js`, `scripts/test-contrast-ui.py`, este relatório e o resumo JSON. Nenhuma dependência adicional foi instalada.
