import { useEffect, useState } from "react";
import {
  ClipboardCheck,
  Wand2,
  Target,
  LayoutDashboard,
  Users,
  ShoppingCart,
  Package,
  Wallet,
  FileText,
  BarChart3,
  Settings,
  ChevronDown,
  ChevronRight,
  ArrowLeftRight,
  Clock,
  Landmark,
  Receipt,
  CreditCard,
  FileBarChart,
  FileUp,
  CheckCircle,
  Inbox,
  Camera,
  Link2,
  AlertTriangle,
  FolderArchive,
  ShieldCheck,
  Building2,
  Mail,
  Plug,
  Calculator,
  ClipboardList,
  CalendarClock,
  Truck,
  Bell,
  Sparkles,
} from "lucide-react";
import { ActionButton } from "./InteractionUI";
import type { NavigationGroup, NavigationItem } from "../navigationCatalog";

const icons: Record<string, typeof LayoutDashboard> = {
  dashboard: LayoutDashboard,
  comercial: Users,
  entidades: Users,
  compras: ShoppingCart,
  stocks: Package,
  financeiro: Wallet,
  contabilidade: FileText,
  analise: BarChart3,
  admin: Settings,
  encomendas: ClipboardList,
  artigos: Package,
  fornecedores: Truck,
  vendas: Receipt,
  obrigacoes: CalendarClock,
};

// Preserve the former page icons, including destinations moved to a new area.
const pageIcons: Record<string, typeof LayoutDashboard> = {
  Dashboard: LayoutDashboard,
  "Folha de caixa": Wallet,
  "Movimentos reais": ArrowLeftRight,
  "Movimentos previstos": Clock,
  Depósitos: Landmark,
  "Previsão de liquidez": Clock,
  "Orçamento": Target,
  "Múltiplas contas": Landmark,
  Extratos: Receipt,
  "Reconciliação automática": CheckCircle,
  "Meios de pagamento": CreditCard,
  "Prazos de pagamento": Clock,
  "Faturação e recebimentos": Receipt,
  "Importar SAF-T de vendas": FileUp,
  "Centro de Conferência": ClipboardCheck,
  "Conferência documental": Receipt,
  Rastreabilidade: Link2,
  "Classificação de rubricas": Wand2,
  "Despesas por rubrica": FileBarChart,
  "Obrigações e Prazos": CalendarClock,
  "Mapas de gestão": FileBarChart,
  "Entrega à contabilidade": FileBarChart,
  "Arquivo — Visão geral": LayoutDashboard,
  "Caixa de entrada": Inbox,
  "Todos os documentos": FileText,
  "Digitalizar documento": Camera,
  Associações: Link2,
  Pendências: AlertTriangle,
  "Dossiês contabilísticos": FolderArchive,
  "Arquivo — Auditoria": ShieldCheck,
  "Arquivo — Configurações": Settings,
  "Compras e Stock — Visão geral": LayoutDashboard,
  "Encomendas de clientes": Receipt,
  "Encomendas a fornecedores": Receipt,
  "Receções de mercadoria": Package,
  "Devoluções a fornecedores": ArrowLeftRight,
  "Movimentos de stock": ArrowLeftRight,
  Compras: ShoppingCart,
  Clientes: Receipt,
  Fornecedores: Landmark,
  Bancos: Landmark,
  "Artigos e validades": Package,
  Armazéns: Package,
  Rubricas: FileBarChart,
  Caixas: Wallet,
  "Tipos de Documentos": FileText,
  "Operadores / funcionários": Users,
  Organização: Building2,
  Email: Mail,
  "Configuração inicial": Settings,
  Integrações: Plug,
  "Regime de IVA": FileBarChart,
  Aparência: Settings,
  Anexos: Package,
  Digitalização: Camera,
  "Verificação automática e-Fatura": CheckCircle,
  Relatórios: FileBarChart,
};
const taskIcons: Record<string, typeof LayoutDashboard> = {
  "Visão geral": LayoutDashboard,
  "Pedidos de clientes": Users,
  "Planeamento de compras": ClipboardList,
  "Catálogo de artigos": Package,
  "Arquivo documental": FolderArchive,
  "Levantamentos e contactos": Bell,
  "Histórico": Clock,
  "Simulador de encomendas": Calculator,
  "Necessidades de compra": ClipboardList,
  "Centro de Encomendas": ShoppingCart,
  "Importações de stock": FileUp,
  Inventários: ClipboardList,
  "Lotes e validades": CalendarClock,
  "Existências / rotatividade": BarChart3,
  "Indicadores de encomendas": BarChart3,
  "Notificações a clientes": Bell,
  "Histórico e fecho": Clock,
  "Condições comerciais": Settings,
};
function DestinationIcon({ item }: { item: NavigationItem }) {
  const Icon = taskIcons[item.label] || pageIcons[item.page] || FileText;
  return (
    <Icon size={17} aria-hidden="true" className="workspace-destination-icon" />
  );
}

export default function WorkspaceNavigation({
  groups,
  activeId,
  activeGroup,
  collapsed,
  onExpand,
  onNavigate,
}: {
  groups: NavigationGroup[];
  activeId?: string;
  activeGroup?: string;
  collapsed: boolean;
  onExpand: () => void;
  onNavigate: (
    group: NavigationGroup,
    item: NavigationItem,
  ) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(activeGroup);
  useEffect(() => {
    setOpen(activeGroup);
  }, [activeGroup]);
  return (
    <>
      <nav aria-label="Navegação principal" className="workspace-navigation">
        {groups.map((group) => {
          const Icon = icons[group.id] || LayoutDashboard;
          const single = group.id === "dashboard";
          const expanded = open === group.id && !collapsed;
          return (
            <div className="finance-menu" key={group.id}>
              <ActionButton
                type="button"
                inferAction={false}
                aria-label={group.label}
                title={group.label}
                aria-current={
                  single && group.items[0].id === activeId ? "page" : undefined
                }
                aria-expanded={single ? undefined : expanded}
                aria-controls={single ? undefined : `workspace-nav-${group.id}`}
                className={
                  activeGroup === group.id
                    ? "active workspace-group"
                    : "workspace-group"
                }
                onClick={() => {
                  if (single) void onNavigate(group, group.items[0]);
                  else {
                    if (collapsed) onExpand();
                    setOpen(
                      open === group.id && !collapsed ? undefined : group.id,
                    );
                  }
                }}
              >
                <Icon size={19} aria-hidden="true" />
                <span className="nav-label">{group.label}</span>
                {!single &&
                  (expanded ? (
                    <ChevronDown size={15} aria-hidden="true" />
                  ) : (
                    <ChevronRight size={15} aria-hidden="true" />
                  ))}
              </ActionButton>
              {!single && (
                <div
                  id={`workspace-nav-${group.id}`}
                  className="finance-submenu"
                  hidden={!expanded}
                >
                  {group.items.map((item) => (
                    <ActionButton
                      type="button"
                      inferAction={false}
                      key={item.id}
                      aria-label={item.label}
                      title={item.label}
                      aria-current={item.id === activeId ? "page" : undefined}
                      className={
                        item.id === activeId
                          ? "active workspace-destination"
                          : "workspace-destination"
                      }
                      onClick={() => {
                        void onNavigate(group, item);
                      }}
                    >
                      <DestinationIcon item={item} />
                      <span className="nav-label">{item.label}</span>
                      {item.id === activeId && (
                        <ChevronRight size={14} aria-hidden="true" />
                      )}
                    </ActionButton>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      <details className="navigation-help">
        <summary>
          <span className="navigation-help-icon">
            <Sparkles size={19} aria-hidden="true" />
          </span>
          <span>
            <strong>Assistente IAZELA</strong>
            <small>Precisa de ajuda?</small>
          </span>
          <ChevronRight size={15} aria-hidden="true" />
        </summary>
        <div>
          <p>Escolha a tarefa que pretende continuar.</p>
          {[
            [
              "compras:Necessidades de compra",
              "Planear compras e consultar necessidades",
            ],
            [
              "comercial:Encomendas de clientes",
              "Registar e acompanhar pedidos",
            ],
            ["encomendas:historico", "Consultar o histórico e fecho"],
          ].map(([id, label]) => {
            const target = groups
              .flatMap((group) => group.items.map((item) => ({ group, item })))
              .find((target) => target.item.id === id);
            return (
              target && (
                <ActionButton
                  inferAction={false}
                  key={id}
                  onClick={async (event) => {
                    const disclosure = event.currentTarget.closest("details");
                    if (await onNavigate(target.group, target.item))
                      disclosure?.removeAttribute("open");
                  }}
                >
                  {label}
                </ActionButton>
              )
            );
          })}
        </div>
      </details>
    </>
  );
}
