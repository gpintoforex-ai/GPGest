/** Approved navigation: stable page identities, contextual task links, existing access rules. */
export const ARCHIVE_PAGES = [
  "Arquivo — Visão geral",
  "Caixa de entrada",
  "Todos os documentos",
  "Associações",
  "Pendências",
  "Dossiês contabilísticos",
  "Arquivo — Auditoria",
  "Arquivo — Configurações",
];
export const APP_PAGES: readonly string[] = [
  "Encomendas de clientes",
  ...ARCHIVE_PAGES,
  ...[
    "Dashboard",
    "Compras e Stock — Visão geral",
    "Meios de pagamento",
    "Prazos de pagamento",
    "Tipos de Documentos",
    "Folha de caixa",
    "Movimentos previstos",
    "Movimentos reais",
    "Múltiplas contas",
    "Bancos",
    "Extratos",
    "Reconciliação automática",
    "Conferência documental",
    "Despesas por rubrica",
    "Mapas de gestão",
    "Entrega à contabilidade",
    "Email",
    "Importar SAF-T de vendas",
    "Digitalizar documento",
    "Relatórios",
    "Integrações",
    "Rubricas",
    "Caixas",
    "Operadores / funcionários",
    "Organização",
    "Depósitos",
    "Previsão de liquidez",
    "Faturação e recebimentos",
    "Armazéns",
    "Artigos e validades",
    "Clientes",
    "Fornecedores",
    "Regime de IVA",
    "Aparência",
    "Configuração inicial",
    "Anexos",
    "Digitalização",
    "Verificação automática e-Fatura",
    "Tesouraria",
    "Contabilidade",
    "Arquivo digital",
    "Compras e Stock",
    "Tabelas",
    "Receitas e despesas",
    "Configurações",
    "Parâmetros",
    "Arquivo — Visão geral",
    "Caixa de entrada",
    "Todos os documentos",
    "Associações",
    "Pendências",
    "Dossiês contabilísticos",
    "Arquivo — Auditoria",
    "Arquivo — Configurações",
    "Encomendas a fornecedores",
    "Receções de mercadoria",
    "Compras",
    "Devoluções a fornecedores",
    "Movimentos de stock",
  ],
];
export const MANAGER_PAGES: readonly string[] = [
  "Despesas por rubrica",
  "Mapas de gestão",
  "Configuração inicial",
  "Regime de IVA",
  "Anexos",
  "Digitalização",
  "Verificação automática e-Fatura",
  "Arquivo — Auditoria",
  "Arquivo — Configurações",
];
export const canViewPage = (name: string, role: string) =>
  ["administrador", "gestor"].includes(role) || !MANAGER_PAGES.includes(name);
export const PAGE_ALIASES: Record<string, string> = {
  Tesouraria: "Folha de caixa",
  Contabilidade: "Conferência documental",
  "Arquivo digital": "Arquivo — Visão geral",
  "Compras e Stock": "Compras e Stock — Visão geral",
  Tabelas: "Clientes",
  Configurações: "Organização",
  Parâmetros: "Configuração inicial",
  "Receitas e despesas": "Mapas de gestão",
};
export type NavigationTarget = {
  id: string;
  label: string;
  page: string;
  params?: Record<string, string>;
  managerOnly?: boolean;
  localOnly?: boolean;
};
export type NavigationItem = NavigationTarget & { relatedTargets?: NavigationTarget[] };
export type NavigationGroup = {
  id: string;
  label: string;
  description: string;
  items: NavigationItem[];
};
const BASE_NAVIGATION: NavigationGroup[] = [
  {
    id: "dashboard",
    label: "Dashboard",
    description: "Prioridades do dia, indicadores e atalhos.",
    items: [
      {
        id: "dashboard:Dashboard",
        label: "Visão geral",
        page: "Dashboard",
      },
    ],
  },
  {
    id: "comercial",
    label: "Comercial",
    description: "Relação com clientes e satisfação dos pedidos.",
    items: [
      {
        id: "comercial:Artigos e preços",
        label: "Artigos e preços",
        page: "Artigos e validades",
        params: {
          "area.stock": "Artigos",
        },
      },
      {
        id: "comercial:Clientes",
        label: "Clientes",
        page: "Clientes",
      },
      {
        id: "comercial:Encomendas de clientes",
        label: "Encomendas de clientes",
        page: "Encomendas de clientes",
        params: {
          "area.pedidos-clientes": "pedidos",
        },
      },
      {
        id: "comercial:Faturação e recebimentos",
        label: "Faturação e recebimentos",
        page: "Faturação e recebimentos",
        localOnly: true,
      },
      {
        id: "comercial:Importar SAF-T de vendas",
        label: "Importar SAF-T de vendas",
        page: "Importar SAF-T de vendas",
        localOnly: true,
      },
    ],
  },
  {
    id: "compras",
    label: "Compras e Abastecimento",
    description: "Do que falta comprar à receção e conferência.",
    items: [
      {
        id: "compras:Simulador de encomendas",
        label: "Simulador de encomendas",
        page: "Encomendas de clientes",
        params: {
          "area.pedidos-clientes": "simulacoes",
        },
        managerOnly: true,
      },
      {
        id: "compras:Necessidades de compra",
        label: "Necessidades de compra",
        page: "Encomendas de clientes",
        params: {
          "area.pedidos-clientes": "centro",
          "filtro.abastecimento": "buy",
        },
        managerOnly: true,
      },
      {
        id: "compras:Centro de Encomendas",
        label: "Centro de Encomendas",
        page: "Encomendas de clientes",
        params: {
          "area.pedidos-clientes": "centro",
          "filtro.abastecimento": "all",
        },
        managerOnly: true,
      },
      {
        id: "compras:Compras e Stock — Visão geral",
        label: "Resumo de compras",
        page: "Compras e Stock — Visão geral",
      },
      {
        id: "compras:Compras",
        label: "Compras",
        page: "Compras",
      },
      {
        id: "compras:Encomendas a fornecedores",
        label: "Encomendas a fornecedores",
        page: "Encomendas a fornecedores",
      },
      {
        id: "compras:Receções de mercadoria",
        label: "Receções de mercadoria",
        page: "Receções de mercadoria",
      },
      {
        id: "compras:Devoluções a fornecedores",
        label: "Devoluções a fornecedores",
        page: "Devoluções a fornecedores",
      },
      {
        id: "compras:Fornecedores",
        label: "Fornecedores",
        page: "Fornecedores",
      },
    ],
  },
  {
    id: "stocks",
    label: "Stocks",
    description: "Existências, lotes, inventários e movimentos.",
    items: [
      {
        id: "stocks:Importações de stock",
        label: "Importações de stock",
        page: "Artigos e validades",
        params: {
          "area.stock": "Importações",
        },
      },
      {
        id: "stocks:Inventários",
        label: "Inventários",
        page: "Artigos e validades",
        params: {
          "area.stock": "Inventários",
        },
      },
      {
        id: "stocks:Lotes e validades",
        label: "Lotes e validades",
        page: "Artigos e validades",
        params: {
          "area.stock": "Lotes e validades",
        },
      },
      {
        id: "stocks:Existências / rotatividade",
        label: "Existências / rotatividade",
        page: "Artigos e validades",
        params: {
          "area.stock": "Painel",
        },
      },
      {
        id: "stocks:Artigos e validades",
        label: "Artigos e validades",
        page: "Artigos e validades",
        params: {
          "area.stock": "Artigos",
        },
      },
      {
        id: "stocks:Movimentos de stock",
        label: "Movimentos de stock",
        page: "Movimentos de stock",
      },
      {
        id: "stocks:Armazéns",
        label: "Armazéns",
        page: "Armazéns",
      },
    ],
  },
  {
    id: "financeiro",
    label: "Financeiro",
    description: "Caixa, contas, vencimentos e reconciliação.",
    items: [
      {
        id: "financeiro:Folha de caixa",
        label: "Folha de caixa",
        page: "Folha de caixa",
      },
      {
        id: "financeiro:Movimentos reais",
        label: "Movimentos reais",
        page: "Movimentos reais",
      },
      {
        id: "financeiro:Movimentos previstos",
        label: "Movimentos previstos",
        page: "Movimentos previstos",
      },
      {
        id: "financeiro:Depósitos",
        label: "Depósitos",
        page: "Depósitos",
      },
      {
        id: "financeiro:Previsão de liquidez",
        label: "Previsão de liquidez",
        page: "Previsão de liquidez",
      },
      {
        id: "financeiro:Múltiplas contas",
        label: "Contas bancárias",
        page: "Múltiplas contas",
      },
      {
        id: "financeiro:Extratos",
        label: "Extratos",
        page: "Extratos",
      },
      {
        id: "financeiro:Reconciliação automática",
        label: "Reconciliação bancária",
        page: "Reconciliação automática",
        params: { "area.reconciliacao": "movimentos", "area.reconciliacao-modo": "grelha" },
      },
      {
        id: "financeiro:Meios de pagamento",
        label: "Meios de pagamento",
        page: "Meios de pagamento",
      },
      {
        id: "financeiro:Prazos de pagamento",
        label: "Prazos de pagamento",
        page: "Prazos de pagamento",
      },
    ],
  },
  {
    id: "contabilidade",
    label: "Contabilidade",
    description: "Conferência, arquivo, classificação e entrega.",
    items: [
      {
        id: "contabilidade:Conferência documental",
        label: "Conferência documental",
        page: "Conferência documental",
      },
      {
        id: "contabilidade:Despesas por rubrica",
        label: "Despesas por rubrica",
        page: "Despesas por rubrica",
      },
      {
        id: "contabilidade:Entrega à contabilidade",
        label: "Entrega à contabilidade",
        page: "Entrega à contabilidade",
      },
      {
        id: "contabilidade:Arquivo — Visão geral",
        label: "Visão geral",
        page: "Arquivo — Visão geral",
      },
      {
        id: "contabilidade:Caixa de entrada",
        label: "Caixa de entrada",
        page: "Caixa de entrada",
      },
      {
        id: "contabilidade:Todos os documentos",
        label: "Todos os documentos",
        page: "Todos os documentos",
      },
      {
        id: "contabilidade:Digitalizar documento",
        label: "Digitalizar documento",
        page: "Digitalizar documento",
      },
      {
        id: "contabilidade:Associações",
        label: "Associações",
        page: "Associações",
      },
      {
        id: "contabilidade:Pendências",
        label: "Pendências",
        page: "Pendências",
      },
      {
        id: "contabilidade:Dossiês contabilísticos",
        label: "Dossiês contabilísticos",
        page: "Dossiês contabilísticos",
      },
      {
        id: "contabilidade:Rubricas",
        label: "Rubricas",
        page: "Rubricas",
      },
    ],
  },
  {
    id: "analise",
    label: "Análise",
    description: "Relatórios e mapas com origem e metodologia.",
    items: [
      {
        id: "analise:encomendas",
        label: "Indicadores de encomendas",
        page: "Encomendas de clientes",
        params: {
          "area.pedidos-clientes": "analise",
        },
        managerOnly: true,
      },
      {
        id: "analise:Relatórios",
        label: "Relatórios",
        page: "Relatórios",
      },
      {
        id: "analise:Mapas de gestão",
        label: "Mapas de gestão",
        page: "Mapas de gestão",
      },
    ],
  },
  {
    id: "admin",
    label: "Administração",
    description: "Configuração e integrações, com os acessos atuais.",
    items: [
      {
        id: "admin:Organização",
        label: "Organização",
        page: "Organização",
      },
      {
        id: "admin:Email",
        label: "Email",
        page: "Email",
      },
      {
        id: "admin:Configuração inicial",
        label: "Configuração inicial",
        page: "Configuração inicial",
        managerOnly: true,
      },
      {
        id: "admin:Integrações",
        label: "Integrações",
        page: "Integrações",
        managerOnly: true,
      },
      {
        id: "admin:Regime de IVA",
        label: "Regime de IVA",
        page: "Regime de IVA",
        managerOnly: true,
      },
      {
        id: "admin:Aparência",
        label: "Aparência",
        page: "Aparência",
      },
      {
        id: "admin:Anexos",
        label: "Anexos",
        page: "Anexos",
        managerOnly: true,
      },
      {
        id: "admin:Digitalização",
        label: "Digitalização",
        page: "Digitalização",
        managerOnly: true,
      },
      {
        id: "admin:Verificação automática e-Fatura",
        label: "Verificação automática e-Fatura",
        page: "Verificação automática e-Fatura",
        managerOnly: true,
      },
      {
        id: "admin:Bancos",
        label: "Bancos (catálogo)",
        page: "Bancos",
      },
      {
        id: "admin:Caixas",
        label: "Caixas",
        page: "Caixas",
      },
      {
        id: "admin:Tipos de Documentos",
        label: "Tipos de Documentos",
        page: "Tipos de Documentos",
      },
      {
        id: "admin:Operadores / funcionários",
        label: "Operadores / funcionários",
        page: "Operadores / funcionários",
      },
      {
        id: "admin:Arquivo — Auditoria",
        label: "Auditoria",
        page: "Arquivo — Auditoria",
        managerOnly: true,
      },
      {
        id: "admin:Arquivo — Configurações",
        label: "Configurações",
        page: "Arquivo — Configurações",
        managerOnly: true,
      },
    ],
  },
];
// Each main destination owns a task. Related views remain searchable and addressable.
function referenceNavigation(): NavigationGroup[] {
  const base = (id: string) => BASE_NAVIGATION.find(group => group.id === id)!;
  const item = (id: string) => BASE_NAVIGATION.flatMap(group => group.items).find(item => item.id === id)!;
  const articleIds = ["comercial:Artigos e preços", "stocks:Artigos e validades", "stocks:Lotes e validades"];
  const orderIds = ["compras:Centro de Encomendas", "compras:Necessidades de compra", "compras:Encomendas a fornecedores", "compras:Simulador de encomendas", "comercial:Encomendas de clientes"];
  const referenceIds = ["financeiro:Meios de pagamento", "financeiro:Prazos de pagamento", "contabilidade:Rubricas"];
  const archiveIds = ["contabilidade:Arquivo — Visão geral", "contabilidade:Caixa de entrada", "contabilidade:Todos os documentos", "contabilidade:Associações", "contabilidade:Pendências"];
  return [
    base("dashboard"),
    { id: "encomendas", label: "Encomendas", description: "Do pedido do cliente ao fornecimento e à entrega.", items: [
      { ...item("compras:Centro de Encomendas"), label: "Visão geral" },
      { ...item("comercial:Encomendas de clientes"), label: "Pedidos de clientes", params: { "area.pedidos-clientes": "pedidos", "filtro.pedidos": "abertos" } },
      { ...item("compras:Necessidades de compra"), label: "Planeamento de compras", params: { "area.pedidos-clientes": "planeamento", "area.planeamento-compras": "necessidades", "filtro.abastecimento": "buy" }, relatedTargets: [
        { ...item("compras:Simulador de encomendas"), label: "Simulações de compra", params: { "area.pedidos-clientes": "planeamento", "area.planeamento-compras": "simulacoes" } },
      ] },
      item("compras:Encomendas a fornecedores"),
      { id: "encomendas:notificacoes", label: "Levantamentos e contactos", page: "Encomendas de clientes", params: { "area.pedidos-clientes": "pedidos", "filtro.pedidos": "prontos" } },
      { id: "encomendas:historico", label: "Histórico", page: "Encomendas de clientes", params: { "area.pedidos-clientes": "historico" } },
    ] },
    { id: "artigos", label: "Artigos", description: "Catálogo, preços e validades.", items: [
      { ...item("comercial:Artigos e preços"), label: "Catálogo de artigos" }, item("stocks:Lotes e validades"),
    ] },
    { id: "entidades", label: "Entidades", description: "Clientes, fornecedores e condições comerciais.", items: [
      item("comercial:Clientes"),
      { ...item("compras:Fornecedores"), relatedTargets: [{ id: "fornecedores:condicoes", label: "Condições comerciais dos fornecedores", page: "Encomendas de clientes", params: { "area.pedidos-clientes": "fornecedores" }, managerOnly: true }] },
    ] },
    { ...base("stocks"), items: base("stocks").items.filter(item => !articleIds.includes(item.id)) },
    { ...base("compras"), label: "Compras", items: base("compras").items.filter(item => !orderIds.includes(item.id) && item.id !== "compras:Fornecedores").map(entry => entry.id === "compras:Receções de mercadoria" ? { ...entry, relatedTargets: [{ id: "compras:Conferência de receções", label: "Receção e conferência", page: "Encomendas de clientes", params: { "area.pedidos-clientes": "rececoes" } }] } : entry) },
    { id: "vendas", label: "Vendas", description: "Faturação, recebimentos e importação de vendas.", items: base("comercial").items.filter(item => !articleIds.includes(item.id) && !orderIds.includes(item.id) && item.id !== "comercial:Clientes") },
    { ...base("financeiro"), items: base("financeiro").items.filter(item => !referenceIds.includes(item.id)) },
    { ...base("contabilidade"), items: [
      ...base("contabilidade").items.filter(item => !archiveIds.includes(item.id) && !referenceIds.includes(item.id)),
      { ...item("contabilidade:Todos os documentos"), label: "Arquivo documental", relatedTargets: archiveIds.filter(id => id !== "contabilidade:Todos os documentos").map(item) },
    ] },
    { ...base("analise"), label: "Relatórios" },
    { ...base("admin"), label: "Configurações", items: [
      ...base("admin").items.map(entry => entry.id === "admin:Arquivo — Configurações" ? { ...entry, label: "Configurações do arquivo" } : entry),
      ...referenceIds.map(item),
    ] },
  ];
}
export const NAVIGATION = referenceNavigation();
const defaults: Record<string, string> = {
  "area.pedidos-clientes": "centro",
  "area.stock": "Painel",
  "area.mapas": "costs",
  "filtro.pedidos": "abertos",
  "filtro.abastecimento": "all",
  "area.planeamento-compras": "necessidades",
};
export const PROCUREMENT_PAGES = [
  "Compras e Stock — Visão geral",
  "Encomendas a fornecedores",
  "Receções de mercadoria",
  "Compras",
  "Devoluções a fornecedores",
  "Movimentos de stock",
];
export function visibleNavigation(role: string, mode: string) {
  const permitted = (item: NavigationTarget) => canViewPage(item.page, role) &&
    (!item.managerOnly || ["administrador", "gestor"].includes(role)) && (!item.localOnly || mode === "local");
  return NAVIGATION.map(group => ({ ...group, items: group.items.filter(permitted).map(item => ({
    ...item, relatedTargets: item.relatedTargets?.filter(permitted),
  })) })).filter(group => group.items.length);
}
export function activeNavigation(groups: NavigationGroup[], page: string, search: string) {
  const params = new URLSearchParams(search);
  const candidates = groups.flatMap(group => group.items.flatMap(item => [item, ...(item.relatedTargets || [])]
    .filter(target => target.page === (PAGE_ALIASES[page] || page)).map(target => ({ group, item, target }))));
  const matches = candidates.filter(({ target }) => Object.entries(target.params || {}).every(
    ([key, value]) => (params.get(key) || defaults[key] || "") === value));
  return candidates.find(({ item, target }) =>
    (item.id === params.get("menu.destino") || target.id === params.get("menu.destino")) &&
    Object.entries(target.params || {}).filter(([key]) => key.startsWith("area.")).every(
      ([key, value]) => (params.get(key) || defaults[key] || "") === value)) ||
    matches.find(m => m.item.id === params.get("menu.destino") || m.target.id === params.get("menu.destino")) ||
    matches.find(m => m.group.id === params.get("menu.area")) ||
    matches.sort((a, b) => Object.keys(b.target.params || {}).length - Object.keys(a.target.params || {}).length)[0] || candidates[0];
}
export function navigationParams(group: NavigationGroup, item: NavigationTarget) {
  return { "menu.area": group.id, "menu.destino": item.id, ...item.params };
}
