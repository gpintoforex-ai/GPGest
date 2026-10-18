import Brand from "./components/Brand";
import EmailSettings from "./EmailSettings";
import SaftSales from "./SaftSales";
import { ActionButton, AppDialog, Notice, confirmAction } from "./components/InteractionUI";
import Deposits from "./Deposits";
import Liquidity from "./Liquidity";
import IntegrationSettings from "./IntegrationSettings";
import {
  useEffect,
  lazy,
  Suspense,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  LayoutDashboard,
  Wallet,
  CreditCard,
  ArrowLeftRight,
  Landmark,
  Package,
  FileBarChart,
  FileUp,
  Settings,
  Plug,
  Monitor,
  Info,
  LogOut,
  Menu,
  CheckCircle,
  ShieldCheck,
  Plus,
  Pencil,
  Trash2,
  Mail,
  Building2,
  SlidersHorizontal,
  Clock,
  ChevronRight,
  ChevronDown,
  Receipt,
  Camera,
  Users,
} from "lucide-react";

type Row = Record<string, any>;
import TenantIntegrations from "./TenantIntegrations";
import PlannedMovements from "./PlannedMovements";
import RealMovements from "./RealMovements";
import CashSheet from "./CashSheet";
import LocalCashSheet from "./LocalCashSheet";
import CashSheetList from "./CashSheetList";
import PaymentMethods, { type PaymentMethod, type PaymentBankDefault } from "./PaymentMethods";
import Employees from "./Employees";
import ReadAloud from "./ReadAloud";
import { CashPreferencesProvider } from "./CashPreferences";
import OpenCashForm from "./OpenCashForm";
import BankStatements from "./BankStatements";
import BanksCatalog from "./BanksCatalog";
import Reconciliation from "./Reconciliation";
import DocumentControl from "./DocumentControl";
import Entities from "./Entities";
import CompanySetupPanel from "./CompanySetup";
import AttachmentSettings from "./AttachmentSettings";
import FiscalSettings from "./FiscalSettings";
import ThemeSettings from "./ThemeSettings";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { formatEuro } from "./utils/format";
import { formatDateTime as date, movementLabels as labels } from "./utils/movements";
import { api } from "./lib/api";
import { Empty, Field, Form, Select } from "./components/Form";
import AttachmentPanel from "./AttachmentPanel";
import MovementTable from "./MovementTable";
import AccountingDelivery from "./AccountingDelivery";
import DocumentScanner, { ScanSettings } from "./DocumentScanner";
import { InvoiceWorkflowSettings } from "./InvoiceWorkflow";
import Rubrics from "./Rubrics";
import { CloseButton, confirmFormExit, dismissDisclosure, useOverlayFocus } from "./components/InteractionUI";
const Inventory = lazy(() => import("./Inventory"));
const HomePage = lazy(() => import("./HomePage"));
const Warehouses = lazy(() => import("./Warehouses"));
type Bootstrap = {
  payment_methods?: Record<string, string>;
  payment_method_catalog?: PaymentMethod[] | null;
  payment_method_bank_defaults?: PaymentBankDefault[];
  payment_terminals?: Row[];
  integration?: Row;
  user: Row;
  mode: string;
  company: Row;
  stores: Row[];
  registers: Row[];
  accounts: Row[];
  bank_institutions?: Row[];
  categories: Row[];
  users: Row[];
};
const money = formatEuro;
export default function App() {
  type PaymentReturn = { page: string; scrollY: number; focus: HTMLElement | null };
  type DataTarget = "bootstrap" | "sessions" | "movements" | "dashboard";
  const dataVersions = useRef<Record<DataTarget, number>>({ bootstrap: 0, sessions: 0, movements: 0, dashboard: 0 });
  const [paymentReturn, setPaymentReturn] = useState<PaymentReturn | null>(null);
  const pendingPaymentReturn = useRef<PaymentReturn | null>(null);
  const removingRegister = useRef(false);
  const [registerBusy, setRegisterBusy] = useState("");
  const [boot, setBoot] = useState<Bootstrap | null>(null),
    [loading, setLoading] = useState(true),
    [page, setPage] = useState("Dashboard"),
    [sessions, setSessions] = useState<Row[]>([]),
    [movements, setMovements] = useState<Row[]>([]),
    [dashboard, setDashboard] = useState<Row>({}),
    [audit, setAudit] = useState<Row[]>([]),
    [modal, setModal] = useState(""),
    [movementPayment, setMovementPayment] = useState("numerario"),
    [movementSession, setMovementSession] = useState(""),
    [customBank, setCustomBank] = useState(false),
    [selected, setSelected] = useState<Row | null>(null),
    [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [mobile, setMobile] = useState(false),
    [sidebarCollapsed, setSidebarCollapsed] = useState(false),
    [financeOpen, setFinanceOpen] = useState(false),
    [accountingOpen, setAccountingOpen] = useState(false),
    [configurationOpen, setConfigurationOpen] = useState(false),
    [parametersOpen, setParametersOpen] = useState(false),
    [scanMenuOpen, setScanMenuOpen] = useState(false),
    [banksOpen, setBanksOpen] = useState(false),

    [tablesOpen, setTablesOpen] = useState(false),
    [store, setStore] = useState(""),
    [requestKey, setRequestKey] = useState(crypto.randomUUID());
  useOverlayFocus(mobile, ".sidebar", () => setMobile(false));
  const pageTitle = page === "Dashboard" ? "Visão geral" : page;
  useEffect(() => {
    document.title = boot ? `${pageTitle} · IAZELA` : "Entrar · IAZELA";
  }, [pageTitle, !!boot]);
  const navigationPending = useRef(false);
  async function navigatePage(next: string) {
    if (next === page || navigationPending.current) return;
    navigationPending.current = true;
    try {
    if (!(await confirmFormExit(document.querySelector(".workspace")))) return;
    if (next === "Organização" || [
      "Configuração inicial",
      "Integrações",
      "Regime de IVA",
      "Apar?ncia",
      "Anexos",
      "Digitalização",
      "Verificação automática e-Fatura",
    ].includes(next)) {
      setConfigurationOpen(true);
      setParametersOpen(next !== "Organização");
    }
    if (next === "Meios de pagamento" && page !== next) {
      setPaymentReturn({ page, scrollY: window.scrollY, focus: document.activeElement as HTMLElement | null });
    } else if (next !== "Meios de pagamento") {
      setPaymentReturn(null);
    }
    setPage(next);
    } finally { navigationPending.current = false; }
  }
  function closePaymentMethods() {
    const destination = paymentReturn || { page: "Dashboard", scrollY: 0, focus: null };
    pendingPaymentReturn.current = destination;
    setPaymentReturn(null);
    setPage(destination.page);
    setMobile(false);
  }
  useLayoutEffect(() => {
    const destination = pendingPaymentReturn.current;
    if (destination && page === destination.page) {
      pendingPaymentReturn.current = null;
      destination.focus?.focus({ preventScroll: true });
      window.scrollTo({ top: destination.scrollY, behavior: "instant" });
    } else if (page === "Meios de pagamento") {
      window.scrollTo({ top: 0, behavior: "instant" });
    }
  }, [page]);
  async function reload(targets: DataTarget[] = ["bootstrap", "sessions", "movements", "dashboard"]) {
    const results = await Promise.all(targets.map(async target => {
      const version = ++dataVersions.current[target];
      return { target, version, data: await api("/" + target) };
    }));
    for (const { target, version, data } of results) {
      if (dataVersions.current[target] !== version) continue;
      if (target === "bootstrap") setBoot(data);
      else if (target === "sessions") setSessions(data);
      else if (target === "movements") setMovements(data);
      else setDashboard(data);
    }
  }
  function updateSavedSheet(saved: Row) {
    // Count/reconcile responses are returned only after the transaction commits.
    // Invalidate older list requests so they cannot replace this saved version.
    ++dataVersions.current.sessions;
    setSessions(previous => previous.map(row => row.id === saved.id ? saved : row));
  }
  function invalidateDataRequests() {
    for (const target of Object.keys(dataVersions.current) as DataTarget[]) ++dataVersions.current[target];
  }
  useEffect(() => {
    const settings = () => { setModal(""); setSelected(null); navigatePage("Anexos"); };
    window.addEventListener("gpgest-attachment-settings", settings);
    return () => window.removeEventListener("gpgest-attachment-settings", settings);
  }, []);
  useEffect(() => {
    const expire = () => {
      invalidateDataRequests();
      setBoot(null);
      setSessions([]);
      setMovements([]);
      setAudit([]);
      setModal("");
      setPaymentReturn(null);
      pendingPaymentReturn.current = null;
    };
    window.addEventListener("gpgest-expired", expire);
    return () => window.removeEventListener("gpgest-expired", expire);
  }, []);
  useEffect(() => {
    const nif = new URLSearchParams(window.location.search).get("nif")?.trim();
    const initialize = async () => {
      if (nif && /^[0-9]{9}$/.test(nif)) await api("/company/select", { nif });
      await reload();
    };
    initialize()
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    const refreshSheets = (event: Event) => {
      if ((event as CustomEvent).detail?.appRefreshed) return;
      reload().catch((e) => setError((e as Error).message));
    };
    window.addEventListener("gpgest-sales-updated", refreshSheets);
    return () =>
      window.removeEventListener("gpgest-sales-updated", refreshSheets);
  }, []);
  useEffect(() => {
    if (page === "Folha de caixa")
      reload(["bootstrap", "sessions"]).catch((e) => setError((e as Error).message));
  }, [page]);
  useEffect(() => {
    if (page !== "Relatórios" || !boot || boot.user.role === "operador") return;
    let active = true;
    api("/audit").then(rows => { if (active) setAudit(rows); })
      .catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [page, boot]);
  const save = async (path: string, data: unknown) => {
    const saved = await api(path, data);
    if (/^\/registers\/[^/]+$/.test(path)) setBoot(current => current ? { ...current, registers: current.registers.map(row => row.id === saved.id ? saved : row) } : current);
    setModal("");
    setSelected(null);
    setNotice("Operação concluída com sucesso.");
    try {
      if (path === "/auth/password") return;
      if (["/stores", "/registers", "/accounts", "/categories", "/users"].includes(path) || /^\/(accounts|registers)\/[^/]+$/.test(path)) await reload(["bootstrap"]);
      else if (path === "/cash/payment-methods") await reload(["bootstrap", "sessions"]);
      else await reload();
    } catch {
      setError("A operação foi guardada, mas não foi possível atualizar os dados do ecrã. Atualize a página para consultar o registo.");
    }
  };
  const act = async (fn: () => Promise<void>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  if (loading) return <div className="loading" role="status">A iniciar a IAZELA…</div>;
  if (!boot && window.location.pathname === "/" && !new URLSearchParams(window.location.search).has("nif") && !new URLSearchParams(window.location.search).has("empresa")) return <Suspense fallback={<div className="loading" role="status">A carregar a IAZELA…</div>}><HomePage /></Suspense>;
  if (!boot)
    return (
      <div className="login">
        <div className="login-story">
          <div className="brand">
            <Brand />
          </div>
          <span className="eyebrow">GESTÃO EMPRESARIAL</span>
          <h1>
            O seu negócio,
            <br />
            com clareza.
          </h1>
          <p>
            Caixa, bancos e documentos num único espaço de trabalho.
            Acompanhe os movimentos, confira os valores e mantenha a informação organizada.
          </p>
          <div className="login-note">
            <ShieldCheck /> Acesso reservado à sua equipa
          </div>
        </div>
        <div className="login-form">
          <span className="eyebrow">BEM-VINDO</span>
          <h2>Entrar na IAZELA</h2>
          <p>Utilize as credenciais da sua empresa.</p>
          <Form
            submitLabel="Entrar"
            onSubmit={async (v) => {
              const { company, ...credentials } = v;
              const identifier = String(company).trim();
              await api("/company/select", /^[0-9]{9}$/.test(identifier) ? { nif: identifier } : { slug: identifier });
              await api("/auth/login", credentials);
              await reload();
            }}
          >
            <Field label="NIF da empresa">
              <input
                name="company"
                required
                defaultValue={
                  new URLSearchParams(window.location.search).get("nif") ||
                  new URLSearchParams(window.location.search).get("empresa") || ""
                }
                inputMode="numeric"
                pattern={new URLSearchParams(window.location.search).has("empresa") ? undefined : "[0-9]{9}"}
                autoComplete="organization"
              />
            </Field>
            <small>Introduza o NIF da empresa e as suas credenciais de utilizador.</small>
            <Field label="Email">
              <input
                name="email"
                type="email"
                autoComplete="username"
                required
              />
            </Field>
            <Field label="Palavra-passe">
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </Field>
          </Form>
          <ActionButton type="button" onClick={async () => {
            await api("/company/clear", {});
            window.location.reload();
          }}>Limpar acesso anterior</ActionButton>
          <small>
            Ferramenta de gestão interna. Não emite documentos fiscais.
          </small>
        </div>
      </div>
    );
  const manager = ["administrador", "gestor"].includes(boot.user.role),
    write = boot.user.role !== "consulta";
  const rname = (id: string) =>
    boot.registers.find((r) => r.id === id)?.name ?? "Caixa";
  const registerStore = (id: string) =>
    boot.stores.find(s => s.id === boot.registers.find(r => r.id === id)?.store_id)?.name || "Estabelecimento";
  const uname = (id: string) =>
    boot.users.find((u) => u.id === id)?.name ?? "Utilizador";
  const visibleSessions = sessions.filter(
    (s) =>
      !store ||
      boot.registers.find((r) => r.id === s.register_id)?.store_id === store,
  );
  const visibleMovements = movements.filter((m) =>
    visibleSessions.some((s) => s.id === m.session_id) || (!m.session_id && boot.registers.some(r => r.id === m.register_id && (!store || r.store_id === store))),
  );
  const open = visibleSessions.filter((s) => !s.closed_at);
  const menu = [
    ["Dashboard", LayoutDashboard],
    ["Tesouraria", Wallet],

    ["Bancos", Landmark],
    ["Contabilidade", FileBarChart],
    ["Tabelas", Package],
    ["Receitas e despesas", Receipt],
    ["Relatórios", FileBarChart],
    ["Configurações", Settings],
  ] as const;
  const configurationMenu = [
    ["Organização", Building2],
    ["Email", Mail],
    ["Parâmetros", SlidersHorizontal],
  ] as const;
  const parameterMenu = [
    ["Configuração inicial", Settings],
    ["Integrações", Plug],
    ["Regime de IVA", FileBarChart],
    ["Aparência", Settings],
    ["Anexos", Package],
    ["Digitalização", Camera],
    ["Verificação automática e-Fatura", CheckCircle],
  ] as const;
  const financeMenu = [
    ["Folha de caixa", Wallet],
    ["Movimentos reais", ArrowLeftRight],
    ["Movimentos previstos", Clock],
    ["Depósitos", Landmark],
    ["Previsão de liquidez", Clock],
    ...(boot.integration?.mode === "local" ? [["Importar SAF-T de vendas", FileUp] as const] : []),
  ] as const;

  const banksMenu = [
    ["Múltiplas contas", Landmark],
    ["Extratos", Receipt],
    ["Reconciliação automática", CheckCircle],
  ] as const;
  const accountingMenu = [["Conferência documental", Receipt], ["Digitalizar documento", Camera], ["Entrega à contabilidade", FileBarChart]] as const;
  const tablesMenu = [
    ["Clientes", Receipt],
    ["Fornecedores", Landmark],
    ["Bancos", Landmark],
    ["Artigos e validades", Package],
    ["Armazéns", Package],
    ["Rubricas", FileBarChart],
    ["Caixas", Wallet],
    ["Meios de pagamento", CreditCard],
    ["Operadores / funcionários", Users],
  ] as const;
  const openModal = async (kind: string, row: Row | null = null) => {
    if (kind === "movement") { setMovementPayment("numerario"); setMovementSession(""); }
    if (kind === "account" || kind === "edit-account") setCustomBank(kind === "edit-account" && !boot.bank_institutions?.some(bank => bank.name === row?.bank));
    if (kind === "open") {
      try {
        await reload();
      } catch (e) {
        setError((e as Error).message);
        return;
      }
    }
    setSelected(row);
    setRequestKey(crypto.randomUUID());
    setModal(kind);
  };
  async function removeRegister(row: Row) {
    if (removingRegister.current) return;
    removingRegister.current = true;
    try {
      if (!(await confirmAction({
        title: "Remover caixa?",
        message: `Pretende remover a caixa «${row.name}» de «${boot!.stores.find(item => item.id === row.store_id)?.name || "Estabelecimento"}»? Esta ação não pode ser anulada. A remoção só é permitida quando não existe histórico nem associações protegidas.`,
        confirmLabel: "Remover caixa", tone: "danger",
      }))) return;
      setRegisterBusy(row.id); setError(""); setNotice("");
      await api(`/registers/${encodeURIComponent(row.id)}/delete`, { confirmed: true });
      setBoot(current => current ? { ...current, registers: current.registers.filter(item => item.id !== row.id) } : current);
      setNotice(`Caixa «${row.name}» removida.`);
      try { await reload(["bootstrap"]); }
      catch { setError("A caixa foi removida, mas não foi possível atualizar todos os dados do ecrã. Atualize a página."); }
    } catch (e) { setError((e as Error).message); }
    finally { setRegisterBusy(""); removingRegister.current = false; }
  }
  const filteredCash = open.reduce((sum, s) => sum + s.balance_cents, 0);
  const integratedMode = boot.integration?.mode === "integrated";
  const operatingMode = integratedMode
    ? "Modo integrado com ERP"
    : "Modo local autónomo";
  const paymentMethods = boot.payment_methods || Object.fromEntries(
    ["numerario", "cartao", "transferencia", "outro"].map(key => [key, labels[key]]),
  );
  return (
    <div className={`shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      {mobile && <ActionButton className="sidebar-backdrop" aria-label="Fechar menu" title="Fechar menu" onClick={() => setMobile(false)} />}
      <aside className={mobile ? "sidebar visible" : "sidebar"} onKeyDown={event => { if (mobile && event.key === "Escape") { event.preventDefault(); setMobile(false); document.querySelector<HTMLElement>('.header-tools button[aria-label],.topbar button[aria-label]')?.focus(); } }}>
        {mobile && <CloseButton guard={false} className="ui-mobile-menu-close" onClick={() => setMobile(false)} />}
        <div className="brand">
          <span className="brand-full"><Brand /></span>
          <span className="brand-compact"><Brand compact /></span>
          <small>GESTÃO EMPRESARIAL</small>
        </div>
        <ActionButton className="sidebar-collapse" aria-label={sidebarCollapsed ? "Expandir menu lateral" : "Recolher menu lateral"} aria-expanded={!sidebarCollapsed} onClick={() => setSidebarCollapsed(!sidebarCollapsed)}>{sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}<span className="nav-label">Recolher menu</span></ActionButton>
        <nav aria-label="Navegação principal">
          {menu.map(([label, Icon]) => {
            const group = label === "Configurações"
              ? { items: configurationMenu, expanded: configurationOpen, toggle: () => setConfigurationOpen(!configurationOpen), id: "configuration-submenu" }
              : label === "Tabelas"
              ? { items: tablesMenu, expanded: tablesOpen, toggle: () => setTablesOpen(!tablesOpen), id: "tables-submenu" }
              : label === "Bancos"
                ? { items: banksMenu, expanded: banksOpen, toggle: () => setBanksOpen(!banksOpen), id: "banks-submenu" }
              : label === "Contabilidade"
                ? { items: accountingMenu, expanded: accountingOpen, toggle: () => setAccountingOpen(!accountingOpen), id: "accounting-submenu" }
                : { items: financeMenu, expanded: financeOpen, toggle: () => setFinanceOpen(!financeOpen), id: "finance-submenu" };
            const groupedMenu = group.items;
            const expanded = group.expanded;
            const submenuId = group.id;
            return label === "Tesouraria" || label === "Bancos" || label === "Contabilidade" || label === "Tabelas" || label === "Configurações" ? (
              <div className="finance-menu" key={label}>
                <ActionButton
                  className={
                    groupedMenu.some(([name]) => name === page) ||
                    label === "Configurações" && parameterMenu.some(([name]) => name === page)
                      ? "active"
                      : ""
                  }
                  aria-expanded={expanded}
                  aria-controls={submenuId}
                  aria-label={label}
                  title={label}
                  onClick={group.toggle}
                >
                  <Icon size={19} /> <span className="nav-label">{label}</span>
                  {expanded ? (
                    <ChevronDown size={15} />
                  ) : (
                    <ChevronRight size={15} />
                  )}
                </ActionButton>
                <div
                  id={submenuId}
                  className="finance-submenu"
                  hidden={!expanded}
                >
                  {groupedMenu.map(([name, FinanceIcon]) => (
                    name === "Parâmetros" && manager ? (
                      <div className="finance-menu" key={name}>
                        <ActionButton
                          className={parameterMenu.some(([parameter]) => parameter === page) ? "active" : ""}
                          aria-expanded={parametersOpen}
                          aria-controls="parameters-submenu"
                          aria-label={name}
                          title={name}
                          onClick={() => setParametersOpen(!parametersOpen)}
                        >
                          <FinanceIcon size={17} /> <span className="nav-label">{name}</span>
                          {parametersOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                        </ActionButton>
                        <div id="parameters-submenu" className="finance-submenu" hidden={!parametersOpen}>
                          {manager && parameterMenu.map(([parameter, ParameterIcon]) => (
                            <ActionButton
                              key={parameter}
                              className={page === parameter ? "active" : ""}
                              aria-current={page === parameter ? "page" : undefined}
                              aria-label={parameter}
                              title={parameter}
                              onClick={() => {
                                navigatePage(parameter);
                                setMobile(false);
                                setError("");
                              }}
                            >
                              <ParameterIcon size={17} /> <span className="nav-label">{parameter}</span>
                              {page === parameter && <ChevronRight size={15} />}
                            </ActionButton>
                          ))}
                        </div>
                      </div>
                    ) : name !== "Parâmetros" ? (
                      <ActionButton
                        key={name}
                        className={page === name ? "active" : ""}
                        aria-current={page === name ? "page" : undefined}
                        aria-label={name}
                        title={name}
                        onClick={() => {
                          navigatePage(name);
                          setMobile(false);
                          setError("");
                        }}
                      >
                        <FinanceIcon size={17} /> <span className="nav-label">{name}</span>
                        {page === name && <ChevronRight size={15} />}
                      </ActionButton>
                    ) : null
                  ))}
                </div>
              </div>
            ) : (
              <ActionButton
                key={label}
                className={page === label ? "active" : ""}
                aria-current={page === label ? "page" : undefined}
                aria-label={label === "Dashboard" ? "Visão geral" : label}
                title={label === "Dashboard" ? "Visão geral" : label}
                onClick={() => {
                  navigatePage(label);
                  setMobile(false);
                  setError("");
                }}
              >
                <Icon size={19} />
                <span className="nav-label">{label === "Dashboard" ? "Visão geral" : label}</span>
                {page === label && <ChevronRight size={15} />}
              </ActionButton>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <div
            className={`sidebar-mode ${integratedMode ? "integrated" : "local"}`}
            role="status"
            aria-label={`Modo configurado: ${operatingMode}`}
            title={operatingMode}
          >
            {integratedMode ? <Plug size={16} aria-hidden="true" /> : <Monitor size={16} aria-hidden="true" />}
            <span className="sidebar-mode-label">{operatingMode}</span>
            <span className="sidebar-mode-compact" aria-hidden="true">{integratedMode ? "ERP" : "Local"}</span>
          </div>
          <div className="sidebar-bottom">
            <span className="avatar">
              {boot.user.name.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <strong>{boot.user.name}</strong>
              <small>{boot.user.role}</small>
            </div>
            <ActionButton
              aria-label="Terminar sessão"
              onClick={() =>
                act(async () => {
                  await api("/auth/logout", {});
                  invalidateDataRequests();
                  setBoot(null);
                  setSessions([]);
                  setMovements([]);
                  setAudit([]);
                  setModal("");
                })
              }
            >
              <LogOut size={18} />
            </ActionButton>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header>
          <ActionButton
            className="mobile-toggle"
            onClick={() => setMobile(!mobile)}
            aria-label="Abrir menu"
          >
            <Menu />
          </ActionButton>
          <div className="header-context">
            <span className="active-company">{boot.company?.name || "IAZELA"}</span><small className="operating-mode-label">{boot.integration?.mode === "integrated" ? `Modo integrado · ${boot.integration.provider} · ${boot.integration.active ? boot.integration.writes ? "Ligação configurada" : "Apenas leitura" : "Ligação desativada"}` : "Modo local autónomo"}</small>
            <span className="breadcrumb">
              IAZELA /{" "}
              {financeMenu.some(([name]) => name === page)
                ? "Tesouraria"
                : banksMenu.some(([name]) => name === page)
                  ? "Bancos"
                : accountingMenu.some(([name]) => name === page)
                  ? "Contabilidade"
                  : tablesMenu.some(([name]) => name === page) ? "Tabelas" : "Gestão"}
            </span>
            <h2>{pageTitle}</h2>
          </div>
          <div className="header-tools">
            <ThemeSettings compact />
            <select
              aria-label="Filtrar loja"
              value={store}
              onChange={async (e) => {
                const next = e.target.value;
                if (navigationPending.current) return;
                navigationPending.current = true;
                try {
                  if (await confirmFormExit(document.querySelector(".workspace"))) setStore(next);
                } finally { navigationPending.current = false; }
              }}
            >
              <option value="">Todas as lojas</option>
              {boot.stores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <span className="today">
              {new Intl.DateTimeFormat("pt-PT", {
                day: "numeric",
                month: "long",
                timeZone: "Europe/Lisbon",
              }).format(new Date())}
            </span>
            <details className="user-menu" onKeyDown={event => { if (event.key === "Escape") dismissDisclosure(event); }}>
              <summary aria-label="Menu do utilizador"><span className="avatar">{boot.user.name.slice(0, 2).toUpperCase()}</span><span className="user-name">{boot.user.name}</span><ChevronDown size={15} /></summary>
              <div className="user-menu-panel"><div className="ui-popover-heading"><strong>{boot.user.name}</strong><CloseButton guard={false} onClick={dismissDisclosure} /></div><small>{boot.user.role}</small><ActionButton onClick={() => act(async () => { await api("/auth/logout", {}); invalidateDataRequests(); setBoot(null); setSessions([]); setMovements([]); setAudit([]); setModal(""); })}><LogOut size={16} />Terminar sessão</ActionButton></div>
            </details>
          </div>
        </header>
        <main>
          {boot.mode === "demonstracao" && (
            <div className="demo">
              <span className="dot" /> Modo de demonstração{" "}
              <span>
                Dados locais de teste, separados da futura utilização real.
              </span>
            </div>
          )}
          {notice && (
            <Notice onDismiss={() => setNotice("")} className="success" role="status">
              {notice}
            </Notice>
          )}
          {error && (
            <Notice className="error" role="alert">
              {error}
            </Notice>
          )}
          {page === "Dashboard" && (
            <>
              <CompanySetupPanel boot={boot} reload={reload} hasOpenSession={open.length > 0} onNavigate={navigatePage} onAction={openModal} />
              <div className="page-intro">
                <div>
                  <span className="eyebrow">VISÃO GERAL</span>
                  <h1>Visão geral do negócio</h1>
                  <p>Acompanhe a operação e mantenha a caixa sob controlo.</p>
                </div>
                {write && (
                  <ActionButton
                    className="primary"
                    onClick={() => openModal("movement")}
                  >
                    <Plus size={18} /> Novo movimento
                  </ActionButton>
                )}
              </div>
              <div className="stats">
                <article className="stat green">
                  <span>
                    Saldo de numerário <Wallet size={20} />
                  </span>
                  <strong>{money(filteredCash)}</strong>
                  <small>{open.length} caixa(s) aberta(s)</small>
                </article>
                <article className="stat">
                  <span>
                    Contas bancárias <Landmark size={20} />
                  </span>
                  <strong>{boot.accounts.filter(account => !store || account.store_id === store).length}</strong>
                  <small>Contas do contexto selecionado</small>
                </article>
                <article className="stat">
                  <span>
                    Caixas abertas <Wallet size={20} />
                  </span>
                  <strong>{open.length}</strong>
                  <small>Disponíveis para conferência</small>
                </article>
                <article className="stat">
                  <span>
                    Movimentos na consulta <ArrowLeftRight size={20} />
                  </span>
                  <strong>{visibleMovements.length}</strong>
                  <small>Registos carregados nesta consulta</small>
                </article>
              </div>
              <div className="dashboard-grid">
                <section className="panel">
                  <div className="panel-title">
                    <h3>Operação de caixa</h3>
                    <span className="pill">Hoje e sessões pendentes</span>
                  </div>
                  {open.length ? (
                    open.map((s) => (
                      <div className="list-row" key={s.id}>
                        <div className="icon-box">
                          <Wallet size={20} />
                        </div>
                        <div>
                          <strong>{rname(s.register_id)}</strong>
                          <small>
                            {uname(s.operator_id)} · {date(s.opened_at)}
                          </small>
                        </div>
                        <strong>{money(s.balance_cents)}</strong>
                        <span className="pill">Aberta</span>
                      </div>
                    ))
                  ) : (
                    <Empty text="Nenhuma caixa aberta" />
                  )}
                  <ActionButton
                    className="text-button"
                    onClick={() => navigatePage("Folha de caixa")}
                  >
                    Gerir caixas <ChevronRight size={16} />
                  </ActionButton>
                </section>
                <section className="panel">
                  <div className="panel-title">
                    <h3>Prioridades</h3>
                    <Clock size={20} />
                  </div>
                  <div className="priority">
                    <b>{open.length}</b>
                    <div>
                      <strong>Caixas por fechar</strong>
                      <small>Consulte o saldo e conte o numerário.</small>
                    </div>
                  </div>
                  <div className="priority">
                    <b>—</b>
                    <div>
                      <strong>Reconciliação bancária</strong>
                      <small>Sugestões automáticas com confirmação das associações.</small>
                      <ActionButton className="text-button" onClick={() => navigatePage("Reconciliação automática")}>Consultar reconciliação</ActionButton>
                    </div>
                  </div>
                  <div className="priority">
                    <b>—</b>
                    <div>
                      <strong>Conferência documental</strong>
                      <small>Reveja as faturas e os anexos da empresa.</small>
                      <ActionButton className="text-button" onClick={() => navigatePage("Conferência documental")}>Consultar documentos</ActionButton>
                    </div>
                  </div>
                </section>
              </div>
              <section className="panel">
                <div className="panel-title">
                  <h3>Últimos movimentos</h3>
                  <ActionButton
                    className="text-button"
                    onClick={() => navigatePage("Movimentos reais")}
                  >
                    Ver todos <ChevronRight size={16} />
                  </ActionButton>
                </div>
                <MovementTable categories={boot.categories} onAttachments={(m) => openModal("attachments", m)} rows={visibleMovements.slice(0, 5)} />
              </section>
            </>
          )}
          {(page === "Folha de caixa" || (page === "Meios de pagamento" && paymentReturn?.page === "Folha de caixa")) && (
            <div hidden={page !== "Folha de caixa"}>
            <CashPreferencesProvider
              key={JSON.stringify([boot.company.id || boot.company.nif || boot.company.name, boot.user.id])}
              scope={JSON.stringify([boot.company.id || boot.company.nif || boot.company.name, boot.user.id])}
            >
              <CashSheetList
                rows={visibleSessions}
                store={store}
                information={
                  <details
                    className="cash-page-info"
                    name="cash-page-options"
                    onKeyDown={(e) => {
                      if (e.key === "Escape") {
                        e.currentTarget.open = false;
                        e.currentTarget.querySelector("summary")?.focus();
                      }
                    }}
                  >
                    <summary aria-label="Informação sobre a folha de caixa" title="Informação sobre a folha de caixa">
                      <Info size={22} aria-hidden="true" />
                    </summary>
                    <div className="cash-page-info-content">
                      <div className="ui-popover-heading"><strong>Informação</strong><CloseButton guard={false} onClick={dismissDisclosure} /></div>
                      <ReadAloud active={page === "Folha de caixa"}>
                        <p>
                          Fundo de maneio, movimentos por meio de pagamento e contagem
                          com apuramento de diferenças.
                        </p>
                        <p>
                          {boot.integration?.mode === "local"
                            ? "Declare o realizado do dia e confira os recebimentos registados. Conte separadamente o numerário físico da gaveta."
                            : "Os valores incluem os créditos e débitos reais da loja nesse dia, manuais e ERP. Introduza o fundo de maneio e a contagem."}
                        </p>
                      </ReadAloud>
                    </div>
                  </details>
                }
                configuration={manager && (
                  <>
                    <ActionButton type="button" onClick={() => { setTablesOpen(true); void navigatePage("Caixas"); }}>
                      <Wallet size={18} aria-hidden="true" /> Configurar caixas
                    </ActionButton>
                    <ActionButton type="button" onClick={() => { setTablesOpen(true); navigatePage("Meios de pagamento"); }}>
                      <CreditCard size={18} aria-hidden="true" /> Configurar meios de pagamento
                    </ActionButton>
                  </>
                )}
                actions={write && (
                  <ActionButton className="primary" onClick={() => openModal("open")}>
                    <Plus size={18} /> Nova folha de caixa
                  </ActionButton>
                )}
              >
                {(s) => (
                  s.sheet.workflow_mode === "local" ? <LocalCashSheet
                    key={s.id} session={s} register={rname(s.register_id)}
                    store={registerStore(s.register_id)}
                    operator={uname(s.operator_id)} writable={write && (manager || s.operator_id === boot.user.id)}
                    manager={manager} reload={reload} onSaved={updateSavedSheet} onAction={openModal} active={page === "Folha de caixa"}
                  /> : <CashSheet
                    key={s.id}
                    session={s}
                    register={rname(s.register_id)}
                    store={registerStore(s.register_id)}
                    operator={uname(s.operator_id)}
                    writable={
                      write && (manager || s.operator_id === boot.user.id)
                    }
                    manager={manager}
                    canImportErp={boot.user.role === "administrador" && (boot.integration?.capabilities.read_documents ?? true) && (boot.integration?.resources.includes("sales") ?? true)}
                    demo={boot.mode === "demonstracao"}
                    reload={reload}
                    onSaved={updateSavedSheet}
                    onAction={openModal}
                  />
                )}
              </CashSheetList>
              {!visibleSessions.length && (
                <Empty text="Abra a primeira sessão de caixa" />
              )}
            </CashPreferencesProvider>
            </div>
          )}
          {page === "Movimentos previstos" && (
            <>
              <PlannedMovements
                boot={boot}
                sessions={sessions}
                store={store}
                reload={reload}
              />
            </>
          )}
          {page === "Movimentos reais" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>Movimentos reais</h1>
                  <p>
                    Os estornos preservam o registo original e a justificação.
                  </p>
                </div>
                <div className="actions">
                  {manager && (
                    <ActionButton onClick={() => openModal("transfer")}>
                      Transferir entre caixas
                    </ActionButton>
                  )}
                  {write && (
                    <ActionButton
                      className="primary"
                      onClick={() => openModal("movement")}
                    >
                      <Plus size={18} /> Registar movimento
                    </ActionButton>
                  )}
                </div>
              </div>
              <RealMovements
                accounts={boot.accounts}
                localRows={movements}
                store={store}
                registers={boot.registers}
                integrated={integratedMode}
              >
                {(pageRows) => (
                  <MovementTable categories={boot.categories} onAttachments={(m) => openModal("attachments", m)}
                    rows={pageRows}
                    onReverse={
                      manager ? (r) => openModal("reverse", r) : undefined
                    }
                  />
                )}
              </RealMovements>
            </>
          )}
          {page === "Múltiplas contas" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>Contas bancárias</h1>
                  <p>Gira as contas bancárias das suas lojas e consulte os respetivos extratos.</p>
                </div>
                {manager && (
                  <ActionButton
                    className="primary"
                    onClick={() => openModal("account")}
                  >
                    <Plus size={18} /> Nova conta
                  </ActionButton>
                )}
              </div>
              <div className="cash-grid">
                {boot.accounts
                  .filter((a) => !store || a.store_id === store)
                  .map((a) => (
                    <section className="panel" key={a.id}>
                      <Landmark className="bank-icon" />
                      <h3>{a.name}</h3>
                      <p>{a.bank}</p>
                      <p className="iban">IBAN ···· {a.iban_last4}</p>
                      <span className="pill neutral">Importação de extratos</span>
                      <p>
                        <small>
                          Consulte os movimentos e o saldo na área de extratos.
                        </small>
                      </p>
                      {manager && <ActionButton onClick={() => openModal("edit-account", a)} aria-label={`Editar conta ${a.name}`}><Pencil size={16} />Editar conta</ActionButton>}
                    </section>
                  ))}
              </div>
              {!boot.accounts.some(a => !store || a.store_id === store) && <Empty text="Ainda não existem contas bancárias nesta seleção" />}
              <ActionButton onClick={() => navigatePage("Extratos")}>Consultar extratos</ActionButton>
            </>
          )}
          {page === "Bancos" && <BanksCatalog manager={manager} onChanged={() => reload(["bootstrap"])} />}
          {page === "Extratos" && (
            <>
              <div className="page-intro"><div><h1>Extratos bancários</h1><p>Selecione uma conta para importar e consultar os movimentos do banco.</p></div><ActionButton onClick={() => navigatePage("Múltiplas contas")}>Gerir contas</ActionButton></div>
              <BankStatements accounts={boot.accounts.filter(a => !store || a.store_id === store)} manager={manager} />
            </>
          )}
          {page === "Reconciliação automática" && (
            <Reconciliation stores={boot.stores} store={store} manager={manager} admin={boot.user.role === "administrador"} />
          )}
          {page === "Conferência documental" && <DocumentControl stores={boot.stores} store={store} manager={manager} admin={boot.user.role === "administrador"} integration={boot.integration} />}
          {page === "Entrega à contabilidade" && <AccountingDelivery admin={boot.user.role === "administrador"} company={boot.company || {}} stores={boot.stores} store={store} onEmailSettings={() => navigatePage("Email")} onSettings={() => navigatePage(manager ? "Regime de IVA" : "Organização")} />}
          {page === "Email" && <EmailSettings admin={boot.user.role === "administrador"} company={boot.company || {}} stores={boot.stores}/>}
          {page === "Importar SAF-T de vendas" && <SaftSales stores={boot.stores} store={store} admin={boot.user.role === "administrador"} local={boot.integration?.mode === "local"}/>}
          {page === "Digitalizar documento" && <><div className="page-intro"><div><h1>Digitalizar documento</h1><p>Fotografe ou selecione páginas, reveja o PDF e guarde-o na pasta cloud da empresa.</p></div>{manager && <ActionButton onClick={() => setScanMenuOpen(true)}><Camera size={18} />Digitalizar documento</ActionButton>}</div>{!manager && <p>A digitalização está disponível para administradores e gestores.</p>}{manager && scanMenuOpen && <DocumentScanner store={store} onClose={() => setScanMenuOpen(false)} onSaved={reload} />}</>}
          {page === "Relatórios" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>Histórico e auditoria</h1>
                  <p>Registos financeiros e alterações de responsabilidade.</p>
                </div>
                <ActionButton
                  onClick={() => {
                    const rows = [
                      [
                        "Data",
                        "Caixa",
                        "Tipo",
                        "Pagamento",
                        "Montante EUR",
                        "Origem",
                      ],
                      ...visibleMovements.map((m) => [
                        date(m.created_at),
                        rname(
                          sessions.find((s) => s.id === m.session_id)
                            ?.register_id,
                        ),
                        labels[m.kind],
                        m.payment_name || labels[m.payment] || m.payment,
                        ((m.amount_cents * m.direction) / 100).toFixed(2),
                        m.origin,
                      ]),
                    ];
                    const csv =
                      "\ufeff" +
                      rows
                        .map((row) =>
                          row
                            .map(
                              (v) =>
                                '"' +
                                String(v ?? "")
                                  .replace(/^[=+\-@\t\r]/, "'$&")
                                  .replaceAll('"', '""') +
                                '"',
                            )
                            .join(";"),
                        )
                        .join("\r\n");
                    const url = URL.createObjectURL(
                      new Blob([csv], { type: "text/csv;charset=utf-8" }),
                    );
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "gpgest-movimentos.csv";
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  Exportar movimentos CSV
                </ActionButton>
              </div>
              <section className="panel">
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Data</th>
                        <th>Ação</th>
                        <th>Utilizador</th>
                        <th>Detalhe</th>
                      </tr>
                    </thead>
                    <tbody>
                      {audit.map((a) => (
                        <tr key={a.id}>
                          <td>{date(a.created_at)}</td>
                          <td>{a.action.replaceAll("_", " ")}</td>
                          <td>{uname(a.user_id)}</td>
                          <td className="audit-detail">{a.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!audit.length && (
                  <Empty
                    text={
                      boot.user.role === "operador"
                        ? "Auditoria reservada à gestão"
                        : "Sem alterações registadas"
                    }
                  />
                )}
              </section>
              <p className="footnote">
                Saldo esperado = fundo inicial + entradas em numerário − saídas
                em numerário. Cartão e transferências não alteram o saldo
                físico. Movimentos de dinheiro não representam automaticamente
                receitas ou despesas.
              </p>
            </>
          )}
          {page === "Integrações" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>Integrações</h1>
                  <p>
                    Ligações independentes, ativadas apenas com documentação
                    confirmada.
                  </p>
                </div>
              </div>
              <div className="cash-grid">
                <TenantIntegrations
                  canManage={boot.user.role === "administrador"}
                />
                <section className="panel">
                  <Landmark />
                  <h3>Open Banking</h3>
                  <span className="pill neutral">Não configurado</span>
                  <p>
                    A ligação depende da escolha de um prestador autorizado,
                    cobertura dos bancos e consentimento.
                  </p>
                  <p>A aplicação não pede credenciais de homebanking.</p>
                </section>
              </div>
              {manager && <IntegrationSettings boot={boot} reload={reload} />}
            </>
          )}
          {page === "Rubricas" && (
            <Rubrics policy={boot.integration} manager={manager} admin={boot.user.role === "administrador"} reload={reload} />
          )}
          {page === "Caixas" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>Caixas</h1>
                  <p>Caixas configuradas por estabelecimento.</p>
                </div>
                {manager && (
                  <ActionButton className="primary" onClick={() => openModal("register")}>
                    <Plus size={18} /> Nova caixa
                  </ActionButton>
                )}
              </div>
              <section className="panel">
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Caixa</th><th>Estabelecimento</th>{manager && <th>Ações</th>}</tr></thead>
                    <tbody>
                      {boot.registers.filter(r => !store || r.store_id === store).map(r => (
                        <tr key={r.id}>
                          <td><span className="cash-payment-label"><Wallet size={18} aria-hidden="true" />{r.name}</span></td>
                          <td>{boot.stores.find(s => s.id === r.store_id)?.name || "—"}</td>
                          {manager && <td><div className="actions">
                            <ActionButton disabled={!!registerBusy} aria-label={`Editar caixa ${r.name}`} onClick={() => openModal("edit-register", r)}><Pencil size={16} />Editar</ActionButton>
                            <ActionButton className="danger" disabled={!!registerBusy} aria-label={`Remover caixa ${r.name}`} onClick={() => removeRegister(r)}><Trash2 size={16} />{registerBusy === r.id ? "A remover…" : "Remover"}</ActionButton>
                          </div></td>}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!boot.registers.some(r => !store || r.store_id === store) && <Empty text="Ainda não existem caixas nesta seleção" />}
              </section>
            </>
          )}
          {page === "Meios de pagamento" && (
            <PaymentMethods
              stores={boot.stores as { id: string; name: string }[]}
              accounts={boot.accounts as { id: string; store_id: string; name: string; bank: string; iban_last4: string }[]}
              bankDefaults={boot.payment_method_bank_defaults || []}
              store={store}
              methods={boot.payment_method_catalog || Object.entries(paymentMethods).map(([id, name]): PaymentMethod => ({
                id, name, active: true, kind: id.startsWith("pm_") ? "custom" : "base", required: id === "numerario",
              }))}
              local={boot.integration?.mode === "local"}
              manager={manager}
              reload={() => reload(["bootstrap", "sessions"])}
              onNew={() => openModal("payment-method")}
              onClose={closePaymentMethods}
            />
          )}
          {page === "Operadores / funcionários" && (
            <Employees users={boot.users as { id: string; name: string; email: string; role: string; active?: boolean; store_ids?: string[] }[]} stores={boot.stores as { id: string; name: string }[]} store={store} admin={boot.user.role === "administrador"} onNewUser={() => openModal("user")} />
          )}
          {page === "Organização" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>A sua organização</h1>
                  <p>Lojas, caixas e acessos por perfil.</p>
                </div>
              </div>
              <div className="cash-grid">
                <section className="panel">
                  <div className="panel-title">
                    <h3>Lojas e caixas</h3>
                    {boot.user.role === "administrador" && (
                      <ActionButton onClick={() => openModal("store")}>
                        Nova loja
                      </ActionButton>
                    )}
                  </div>
                  {boot.stores.map((s) => (
                    <div className="setting-row" key={s.id}>
                      <strong>{s.name}</strong>
                      <small>
                        {boot.registers
                          .filter((r) => r.store_id === s.id)
                          .map((r) => r.name)
                          .join(", ") || "Sem caixas"}
                      </small>
                    </div>
                  ))}
                  {manager && (
                    <ActionButton onClick={() => openModal("register")}>
                      Adicionar caixa
                    </ActionButton>
                  )}
                </section>

                <section className="panel">
                  <div className="panel-title">
                    <h3>Equipa</h3>
                    {boot.user.role === "administrador" && (
                      <ActionButton onClick={() => openModal("user")}>
                        Novo utilizador
                      </ActionButton>
                    )}
                  </div>
                  {boot.users.map((u) => (
                    <div className="setting-row" key={u.id}>
                      <strong>{u.name}</strong>
                      <small>
                        {u.role} · {u.email}
                      </small>
                    </div>
                  ))}
                </section>
                <section className="panel">
                  <ShieldCheck />
                  <h3>Segurança e dados</h3>
                  <ActionButton onClick={() => openModal("password")}>
                    Alterar palavra-passe
                  </ActionButton>
                  <p>
                    Sessões de 8 horas, palavras-passe protegidas com Argon2 e
                    permissões verificadas no servidor.
                  </p>
                  <p>
                    Consulte o guia do projeto para cópias de segurança e
                    recuperação.
                  </p>
                </section>
              </div>
            </>
          )}
          {page === "Depósitos" && <Deposits boot={boot} store={store} reload={reload} />}
          {page === "Previsão de liquidez" && <Liquidity boot={boot} store={store} />}
          {page === "Armazéns" && <Suspense fallback={<p role="status">A carregar armazéns…</p>}><Warehouses stores={boot.stores} /></Suspense>}
          {page === "Artigos e validades" && <Suspense fallback={<p role="status">A carregar artigos e stock…</p>}><Inventory stores={boot.stores} admin={boot.user.role === "administrador"} /></Suspense>}
          {[
            "Receitas e despesas",
          ].includes(page) && (
            <section className="panel roadmap">
              <span className="eyebrow">ÁREA INDISPONÍVEL</span>
              <h1>{page}</h1>
              <p>
                {page === "Artigos e validades"
                  ? "O controlo de lotes e validades ainda não está disponível nesta versão."
                  : "A análise de receitas e despesas ainda não está disponível nesta versão."}
              </p>
              <p>
                Pode continuar a consultar os movimentos, conferir a caixa e organizar os documentos nos restantes módulos.
              </p>
              <ActionButton onClick={() => navigatePage("Folha de caixa")}>
                Ir para folha de caixa
              </ActionButton>
            </section>
          )}
          {(page === "Clientes" || page === "Fornecedores") && <Entities kind={page} manager={manager} categories={boot.categories.map(c => ({ id: c.id, name: c.name }))} />}
          {page === "Regime de IVA" && manager && <FiscalSettings />}
          {page === "Aparência" && <ThemeSettings />}
          {page === "Configuração inicial" && <CompanySetupPanel boot={boot} reload={reload} hasOpenSession={open.length > 0} onNavigate={navigatePage} onAction={openModal} standalone />}
          {page === "Anexos" && manager && <AttachmentSettings nif={boot.company?.nif} />}
          {page === "Digitalização" && manager && <ScanSettings admin={boot.user.role === "administrador"} />}
          {page === "Verificação automática e-Fatura" && manager && <InvoiceWorkflowSettings admin={boot.user.role === "administrador"} />}
          <footer>
            IAZELA · Gestão empresarial <span>Dados da empresa selecionada</span>
          </footer>
        </main>
      </div>
      {modal && (
          <AppDialog autoOpen key={modal} onRequestClose={() => setModal("")} initialFocus={["close", "reopen", "reverse"].includes(modal) ? "cancel" : "first"}
            className="modal"
            aria-labelledby="modal-title"
          >
            <div className="panel-title">
              <h2 id="modal-title">
                {
                  {
                    open: "Nova folha de caixa",
                    movement: "Novo movimento",
                    close: "Fechar caixa",
                    handover: "Passar responsabilidade",
                    reopen: "Reabrir caixa",
                    reverse: "Estornar movimento",
                    transfer: "Transferência entre caixas",
                    store: "Nova loja",
                    register: "Nova caixa",
                    "edit-register": "Editar caixa",
                    "payment-method": "Novo meio de pagamento",
                    account: "Nova conta bancária",
                    "edit-account": "Editar conta bancária",
                    category: "Nova categoria",
                    user: "Novo utilizador",
                    attachments: "Anexos do movimento",
                    password: "Alterar palavra-passe",
                  }[modal]
                }
              </h2>

            </div>
            {modal === "password" && (
              <Form onSubmit={(v) => save("/auth/password", v)}>
                <Field label="Palavra-passe atual">
                  <input
                    type="password"
                    name="current_password"
                    autoComplete="current-password"
                    required
                  />
                </Field>
                <Field label="Nova palavra-passe">
                  <input
                    type="password"
                    name="new_password"
                    autoComplete="new-password"
                    minLength={12}
                    required
                  />
                </Field>
                <p>As outras sessões deste utilizador serão terminadas.</p>
              </Form>
            )}
            {modal === "attachments" && selected && (
              <AttachmentPanel
                movement={selected}
                canScan={manager}
                writable={
                  write &&
                  !sessions.find((s) => s.id === selected.session_id)?.closed_at
                }
              />
            )}
            {modal === "open" && (
              <OpenCashForm
                registers={boot.registers.filter(
                  (r) =>
                    !sessions.some(
                      (s) => s.register_id === r.id && !s.closed_at,
                    ),
                )}
                store={store}
                onSubmit={(v) => save("/sessions", v)}
              />
            )}
            {modal === "movement" && (
              <Form
                onSubmit={(v) =>
                  save("/movements", {
                    ...v,
                    terminal_id: v.terminal_id || null,
                    occurred_at: new Date(v.occurred_at).toISOString(),
                    request_key: requestKey,
                  })
                }
              >
                <Field label="Sessão aberta">
                  <Select
                    name="session_id"
                    onChange={event => setMovementSession(event.target.value)}
                    rows={sessions
                      .filter(
                        (s) =>
                          !s.closed_at &&
                          (manager || s.operator_id === boot.user.id),
                      )
                      .map((s) => ({ ...s, name: rname(s.register_id) }))}
                  />
                </Field>
                <div className="form-grid">
                  <Field label="Tipo">
                    <select name="kind">
                      {Object.keys(labels)
                        .filter((k) =>
                          [
                            "venda",
                            "cliente",
                            "entrada_extra",
                            "fornecedor",
                            "despesa",
                            "reembolso",
                            "deposito",
                            "reforco",
                          ].includes(k),
                        )
                        .map((k) => (
                          <option key={k} value={k}>
                            {labels[k]}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <Field label="Meio de pagamento">
                    <select name="payment" value={movementPayment} onChange={event => setMovementPayment(event.target.value)}>
                      {Object.entries(paymentMethods).map(
                        ([k, name]) => (
                          <option key={k} value={k}>
                            {name}
                          </option>
                        ),
                      )}
                    </select>
                  </Field>
                  {["cartao", "mbway"].includes(movementPayment) && !!boot.payment_terminals?.length && <Field label="Terminal de pagamento"><select name="terminal_id" defaultValue=""><option value="">Sem terminal / conta por defeito</option>{boot.payment_terminals.filter(t => (!movementSession || !t.register_id || sessions.find(s => s.id === movementSession)?.register_id === t.register_id) && (movementPayment !== "mbway" || t.mbway)).map(t => <option key={t.id} value={t.id}>{t.name} · {boot.accounts.find(a => a.id === t.account_id)?.name || "Conta de destino"}</option>)}</select></Field>}
                  <Field label="Montante (€)">
                    <input
                      name="amount"
                      required
                      inputMode="decimal"
                      placeholder="0,00"
                    />
                  </Field>
                  <Field label="Categoria">
                    <Select name="category_id" rows={boot.categories} />
                  </Field>
                  <Field label="Cliente / fornecedor">
                    <input name="entity" maxLength={200} />
                  </Field>
                  <Field label="Documento">
                    <input name="document" maxLength={200} />
                  </Field>
                </div>
                <Field label="Data e hora do movimento">
                  <input
                    type="datetime-local"
                    step="0.001"
                    name="occurred_at"
                    required
                    defaultValue={new Date(
                      Date.now() - new Date().getTimezoneOffset() * 60000,
                    )
                      .toISOString()
                      .slice(0, 23)}
                  />
                </Field>
                <Field label="Referência externa">
                  <input name="reference" maxLength={200} />
                </Field>
                <Field label="Observações">
                  <textarea name="notes" maxLength={2000} />
                </Field>
                <p className="footnote">
                  Apenas numerário altera o saldo físico. Origem: manual. A data
                  introduzida usa o fuso horário do dispositivo.
                </p>
              </Form>
            )}
            {["handover", "reopen", "reverse"].includes(modal) && selected && (
              <Form
                submitLabel={modal === "reopen" ? "Confirmar reabertura" : modal === "reverse" ? "Estornar movimento" : "Confirmar passagem"}
                destructive={modal === "reverse"}
                onCancel={() => setModal("")}
                onSubmit={(v) =>
                  save(
                    modal === "reverse"
                      ? "/movements/" + selected.id + "/reverse"
                      : "/sessions/" +
                          selected.id +
                          "/" +
                          (modal === "handover" ? "handover" : "reopen"),
                    v,
                  )
                }
              >
                <p>{modal === "reverse" ? `O movimento «${labels[selected.kind] || selected.kind} · ${selected.document || selected.reference || selected.id}» de ${money(selected.amount_cents)} será estornado. O original fica no histórico e será criado o movimento inverso.` : modal === "reopen" ? `A caixa «${rname(selected.register_id)}» de ${selected.sheet?.day?.split("-").reverse().join("/") || date(selected.opened_at)} será reaberta. Os movimentos gerados por esse fecho serão anulados com histórico, para permitir nova conferência sem duplicações. Movimentos registados antes do fecho serão preservados.` : "A responsabilidade desta caixa passará para o operador escolhido. A operação ficará no histórico."}</p>
                {modal === "handover" && (
                  <Field label="Novo operador">
                    <Select
                      name="operator_id"
                      rows={boot.users.filter((u) => u.role !== "consulta")}
                    />
                  </Field>
                )}
                <Field label="Motivo">
                  <textarea
                    name="reason"
                    required
                    minLength={5}
                    maxLength={2000}
                  />
                </Field>
                <p className="footnote">
                  A alteração ficará no histórico de auditoria.
                </p>
              </Form>
            )}
            {modal === "transfer" && (
              <Form
                onSubmit={(v) =>
                  save("/transfers", { ...v, request_key: requestKey })
                }
              >
                <Field label="Caixa de origem">
                  <Select
                    name="source_id"
                    rows={sessions
                      .filter((s) => !s.closed_at)
                      .map((s) => ({ ...s, name: rname(s.register_id) }))}
                  />
                </Field>
                <Field label="Caixa de destino">
                  <Select
                    name="destination_id"
                    rows={sessions
                      .filter((s) => !s.closed_at)
                      .map((s) => ({ ...s, name: rname(s.register_id) }))}
                  />
                </Field>
                <Field label="Montante (€)">
                  <input name="amount" inputMode="decimal" required />
                </Field>
                <Field label="Categoria">
                  <Select name="category_id" rows={boot.categories} />
                </Field>
                <Field label="Motivo">
                  <textarea name="reason" required minLength={5} />
                </Field>
                <p className="footnote">
                  Cria uma saída e uma entrada ligadas, na mesma transação. Não
                  constitui receita nem despesa.
                </p>
              </Form>
            )}
            {["store", "category", "register", "edit-register", "account", "edit-account", "payment-method"].includes(modal) && (
              <Form
                submitLabel={["edit-account", "edit-register"].includes(modal) ? "Guardar alterações" : "Guardar"}
                onCancel={() => setModal("")}
                onSubmit={(v) => {
                  let payload = v;
                  if (["account", "edit-account"].includes(modal) && v.bank_choice) {
                    const { bank_choice, ...account } = v;
                    payload = { ...account, bank: bank_choice === "__other__" ? account.bank : bank_choice };
                  }
                  if (modal === "edit-account") payload = { ...payload, principal: v.principal === "on", opening_date: v.opening_date || null } as typeof payload;
                  return save(
                    modal === "edit-register" ? `/registers/${encodeURIComponent(selected!.id)}` : modal === "edit-account" ? `/accounts/${encodeURIComponent(selected!.id)}` : "/" +
                      {
                        store: "stores",
                        category: "categories",
                        register: "registers",
                        "payment-method": "cash/payment-methods",
                        account: "accounts",
                      }[modal],
                    payload,
                  );
                }}
              >
                <Field label="Nome">
                  <input name="name" required minLength={2} maxLength={100} defaultValue={["edit-account", "edit-register"].includes(modal) ? selected?.name : ""} />
                </Field>
                {["register", "account"].includes(modal) && (
                  <Field label="Loja">
                    <Select name="store_id" rows={boot.stores} />
                  </Field>
                )}
                {modal === "edit-register" && <>
                  <Field label="Estabelecimento"><select name="store_id" required defaultValue={selected?.store_id}>{boot.stores.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
                  <p className="footnote">As caixas com histórico ou associações protegidas mantêm o estabelecimento. O nome pode ser alterado.</p>
                </>}
                {modal === "edit-account" && <div className="form-grid">
                  <Field label="Estabelecimento"><select disabled defaultValue={selected?.store_id}>{boot.stores.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
                  <Field label="Moeda"><input readOnly value={selected?.currency || "EUR"} /></Field>
                  <Field label="IBAN completo"><input name="iban" maxLength={42} placeholder={selected?.iban_configured ? "Deixe vazio para manter o IBAN guardado" : "Opcional"} /></Field>
                  <Field label={`Saldo inicial (${selected?.currency || "EUR"})`}><input name="opening" inputMode="decimal" placeholder="Opcional" /></Field>
                  <Field label="Data do saldo inicial"><input name="opening_date" type="date" /></Field>
                  <label className="checkbox"><input name="principal" type="checkbox" defaultChecked={!!JSON.parse(selected?.settings || "{}").principal} /> Conta principal</label>
                  <p className="footnote">O estabelecimento e a moeda mantêm-se para preservar o histórico. Um saldo inicial já registado é preservado; alterações são revistas em Saldos iniciais.</p>
                </div>}
                {["account", "edit-account"].includes(modal) && (
                  <>
                    {boot.bank_institutions?.length ? <>
                      <Field label="Banco">
                        <select name="bank_choice" required defaultValue={modal === "edit-account" ? boot.bank_institutions.some(bank => bank.name === selected?.bank) ? selected?.bank : "__other__" : ""} onChange={(event) => setCustomBank(event.target.value === "__other__")}>
                          <option value="">Selecionar banco…</option>
                          {boot.bank_institutions.map((bank) => <option key={bank.id} value={bank.name}>{bank.name}</option>)}
                          <option value="__other__">Outro banco…</option>
                        </select>
                      </Field>
                      {customBank && <Field label="Nome do banco"><input name="bank" required minLength={2} maxLength={100} defaultValue={modal === "edit-account" ? selected?.bank : ""} /></Field>}
                    </> : <Field label="Banco"><input name="bank" required minLength={2} maxLength={100} defaultValue={modal === "edit-account" ? selected?.bank : ""} /></Field>}
                    <Field label="Últimos 4 algarismos do IBAN">
                      <input
                        name="iban_last4"
                        required={modal === "account"}
                        readOnly={modal === "edit-account" && !!selected?.iban_configured}
                        pattern="[0-9]{4}"
                        maxLength={4}
                        inputMode="numeric"
                        defaultValue={modal === "edit-account" ? selected?.iban_last4 : ""}
                      />
                    </Field>
                    {modal === "edit-account" && selected?.iban_configured && <p className="footnote">Pode substituir o IBAN no campo «IBAN completo». Os últimos quatro algarismos são atualizados automaticamente.</p>}
                  </>
                )}
              </Form>
            )}
            {modal === "user" && (
              <Form
                onSubmit={(v) =>
                  save("/users", {
                    ...v,
                    store_ids: v.store_id ? [v.store_id] : [],
                    store_id: undefined,
                  })
                }
              >
                <Field label="Nome">
                  <input name="name" required minLength={2} />
                </Field>
                <Field label="Email">
                  <input name="email" type="email" required />
                </Field>
                <Field label="Palavra-passe inicial (mínimo 12 caracteres)">
                  <input
                    name="password"
                    type="password"
                    required
                    minLength={12}
                  />
                </Field>
                <Field label="Perfil">
                  <select name="role">
                    {["operador", "gestor", "consulta", "administrador"].map(
                      (r) => (
                        <option key={r}>{r}</option>
                      ),
                    )}
                  </select>
                </Field>
                <Field label="Loja autorizada">
                  <Select name="store_id" rows={boot.stores} />
                </Field>
              </Form>
            )}
          </AppDialog>
      )}
    </div>
  );

}
