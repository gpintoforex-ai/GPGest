import Deposits from "./Deposits";
import Liquidity from "./Liquidity";
import IntegrationSettings from "./IntegrationSettings";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useId,
  cloneElement,
  isValidElement,
  type ReactElement,
  type FormEvent,
} from "react";
import {
  LayoutDashboard,
  Wallet,
  CreditCard,
  ArrowLeftRight,
  Landmark,
  Package,
  FileBarChart,
  Settings,
  Plug,
  Monitor,
  Info,
  LogOut,
  Menu,
  CheckCircle,
  ShieldCheck,
  Plus,
  Building2,
  SlidersHorizontal,
  Clock,
  ChevronRight,
  ChevronDown,
  Receipt,
  Camera,
  X,
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
import AttachmentSettings from "./AttachmentSettings";
import FiscalSettings from "./FiscalSettings";
import { formatEuro } from "./utils/format";
import AccountingDelivery from "./AccountingDelivery";
import DocumentScanner, { ScanButton, ScanSettings } from "./DocumentScanner";
import { InvoiceWorkflowSettings } from "./InvoiceWorkflow";
import Rubrics from "./Rubrics";
import RubricBadge from "./components/RubricBadge";
type Bootstrap = {
  payment_methods?: Record<string, string>;
  payment_method_catalog?: PaymentMethod[] | null;
  payment_method_bank_defaults?: PaymentBankDefault[];
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
const date = (v: string) =>
  new Intl.DateTimeFormat("pt-PT", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Lisbon",
  }).format(new Date(v));
const labels: Record<string, string> = {
  venda: "Recebimento de venda",
  cliente: "Recebimento de cliente",
  entrada_extra: "Entrada extraordinária",
  entrada_caixa: "Entrada de caixa",
  saida_caixa: "Saída de caixa",
  fornecedor: "Pagamento a fornecedor",
  despesa: "Pequena despesa",
  reembolso: "Reembolso",
  diferenca_caixa: "Diferença de caixa",
  deposito: "Retirada para depósito",
  reforco: "Reforço de fundo",
  transferencia_caixa: "Transferência entre caixas",
  numerario: "Numerário",
  cartao: "Multibanco / cartão",
  mbway: "MB WAY",
  transferencia: "Transferência",
  outro: "Outro",
  cheque: "Cheques",
  misto: "Numerário e cheques",
};
export async function api(path: string, body?: unknown, options?: { timeoutMs?: number }) {
  const r = await fetch("/api" + path, {
    credentials: "same-origin",
    ...(options?.timeoutMs ? { signal: AbortSignal.timeout(options.timeoutMs) } : {}),
    ...(body !== undefined
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
  const text = await r.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error("O servidor não devolveu uma resposta válida.");
  }
  if (r.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Verifique os campos introduzidos.",
    );
  return data;
}
export function Form({
  onSubmit,
  children,
  submitLabel = "Guardar",
}: {
  submitLabel?: string;
  onSubmit: (v: Row) => Promise<void>;
  children: React.ReactNode;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    setBusy(true);
    setError("");
    try {
      await onSubmit(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit}>
      {children}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button className="primary" disabled={busy}>
        {busy ? "A processar…" : submitLabel}
      </button>
    </form>
  );
}
export function Field({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {isValidElement(children)
        ? cloneElement(children as ReactElement<{ id?: string }>, { id })
        : children}
    </div>
  );
}
function Select({
  name,
  rows,
  labelKey = "name",
  id,
}: {
  name: string;
  rows: Row[];
  labelKey?: string;
  id?: string;
}) {
  return (
    <select id={id} name={name} required>
      <option value="">Selecionar…</option>
      {rows.map((r) => (
        <option key={r.id} value={r.id}>
          {r[labelKey]}
        </option>
      ))}
    </select>
  );
}
function Empty({ text = "Ainda não existem registos." }: { text?: string }) {
  return (
    <div className="empty">
      <Receipt size={32} />
      <h3>{text}</h3>
      <p>Os dados aparecerão aqui assim que forem registados.</p>
    </div>
  );
}

export default function App() {
  type PaymentReturn = { page: string; scrollY: number; focus: HTMLElement | null };
  type DataTarget = "bootstrap" | "sessions" | "movements" | "dashboard";
  const dataVersions = useRef<Record<DataTarget, number>>({ bootstrap: 0, sessions: 0, movements: 0, dashboard: 0 });
  const [paymentReturn, setPaymentReturn] = useState<PaymentReturn | null>(null);
  const pendingPaymentReturn = useRef<PaymentReturn | null>(null);
  const [boot, setBoot] = useState<Bootstrap | null>(null),
    [loading, setLoading] = useState(true),
    [page, setPage] = useState("Dashboard"),
    [sessions, setSessions] = useState<Row[]>([]),
    [movements, setMovements] = useState<Row[]>([]),
    [dashboard, setDashboard] = useState<Row>({}),
    [audit, setAudit] = useState<Row[]>([]),
    [modal, setModal] = useState(""),
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
  function navigatePage(next: string) {
    if (next === "Organização" || [
      "Integrações",
      "Regime de IVA",
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
    await api(path, data);
    setModal("");
    setSelected(null);
    setNotice("Operação concluída com sucesso.");
    try {
      if (path === "/auth/password") return;
      if (["/stores", "/registers", "/accounts", "/categories", "/users"].includes(path)) await reload(["bootstrap"]);
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
  if (loading) return <div className="loading">A iniciar o GPGest…</div>;
  if (!boot)
    return (
      <div className="login">
        <div className="login-story">
          <div className="brand">
            GP<span>Gest</span>
          </div>
          <span className="eyebrow">ANÁLISE E CONTROLO DO NEGÓCIO</span>
          <h1>
            Conheça a sua empresa.
            <br />
            Decida com confiança.
          </h1>
          <p>
            Uma solução que vai além do registo de operações: analisa a situação
            da empresa, identifica problemas, recomenda ações e ajuda o
            administrador a manter o controlo do negócio.
          </p>
          <div className="login-note">
            <ShieldCheck /> Acesso reservado à sua equipa
          </div>
        </div>
        <div className="login-form">
          <span className="eyebrow">BEM-VINDO</span>
          <h2>Entrar no GPGest</h2>
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
          <button type="button" onClick={async () => {
            await api("/company/clear", {});
            window.location.reload();
          }}>Limpar acesso anterior</button>
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
    ["Parâmetros", SlidersHorizontal],
  ] as const;
  const parameterMenu = [
    ["Integrações", Plug],
    ["Regime de IVA", FileBarChart],
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
    ["Rubricas", FileBarChart],
    ["Caixas", Wallet],
    ["Meios de pagamento", CreditCard],
    ["Operadores / funcionários", Users],
  ] as const;
  const openModal = async (kind: string, row: Row | null = null) => {
    if (kind === "account") setCustomBank(false);
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
      {mobile && <button className="sidebar-backdrop" aria-label="Fechar menu" onClick={() => setMobile(false)} />}
      <aside className={mobile ? "sidebar visible" : "sidebar"}>
        <div className="brand">
          <span className="brand-full">GP<span>Gest</span></span>
          <span className="brand-compact" aria-label="GPGest">GP</span>
          <small>{boot.company?.name || "GESTÃO DE LOJA"}</small>
        </div>
        <button className="sidebar-collapse" aria-label={sidebarCollapsed ? "Expandir menu lateral" : "Recolher menu lateral"} aria-expanded={!sidebarCollapsed} onClick={() => setSidebarCollapsed(!sidebarCollapsed)}><Menu size={18} /><span className="nav-label">Recolher menu</span></button>
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
                <button
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
                </button>
                <div
                  id={submenuId}
                  className="finance-submenu"
                  hidden={!expanded}
                >
                  {groupedMenu.map(([name, FinanceIcon]) => (
                    name === "Parâmetros" && manager ? (
                      <div className="finance-menu" key={name}>
                        <button
                          className={parameterMenu.some(([parameter]) => parameter === page) ? "active" : ""}
                          aria-expanded={parametersOpen}
                          aria-controls="parameters-submenu"
                          aria-label={name}
                          title={name}
                          onClick={() => setParametersOpen(!parametersOpen)}
                        >
                          <FinanceIcon size={17} /> <span className="nav-label">{name}</span>
                          {parametersOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                        </button>
                        <div id="parameters-submenu" className="finance-submenu" hidden={!parametersOpen}>
                          {manager && parameterMenu.map(([parameter, ParameterIcon]) => (
                            <button
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
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : name !== "Parâmetros" ? (
                      <button
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
                      </button>
                    ) : null
                  ))}
                </div>
              </div>
            ) : (
              <button
                key={label}
                className={page === label ? "active" : ""}
                aria-current={page === label ? "page" : undefined}
                aria-label={label}
                title={label}
                onClick={() => {
                  navigatePage(label);
                  setMobile(false);
                  setError("");
                }}
              >
                <Icon size={19} />
                <span className="nav-label">{label}</span>
                {page === label && <ChevronRight size={15} />}
              </button>
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
            <button
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
            </button>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header>
          <button
            className="mobile-toggle"
            onClick={() => setMobile(!mobile)}
            aria-label="Abrir menu"
          >
            <Menu />
          </button>
          <div className="header-context">
            <span className="active-company">{boot.company?.name || "GPGest"}</span><small className="operating-mode-label">{boot.integration?.mode === "integrated" ? `Modo integrado · ${boot.integration.provider} · ${boot.integration.active ? boot.integration.writes ? "Ligação configurada" : "Apenas leitura" : "Ligação desativada"}` : "Modo local autónomo"}</small>
            <span className="breadcrumb">
              GPGest /{" "}
              {financeMenu.some(([name]) => name === page)
                ? "Tesouraria"
                : banksMenu.some(([name]) => name === page)
                  ? "Bancos"
                : accountingMenu.some(([name]) => name === page)
                  ? "Contabilidade"
                  : tablesMenu.some(([name]) => name === page) ? "Tabelas" : "Gestão"}
            </span>
            <h2>{page}</h2>
          </div>
          <div className="header-tools">
            <select
              aria-label="Filtrar loja"
              value={store}
              onChange={(e) => setStore(e.target.value)}
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
            <details className="user-menu">
              <summary aria-label="Menu do utilizador"><span className="avatar">{boot.user.name.slice(0, 2).toUpperCase()}</span><span className="user-name">{boot.user.name}</span><ChevronDown size={15} /></summary>
              <div className="user-menu-panel"><strong>{boot.user.name}</strong><small>{boot.user.role}</small><button onClick={() => act(async () => { await api("/auth/logout", {}); invalidateDataRequests(); setBoot(null); setSessions([]); setMovements([]); setAudit([]); setModal(""); })}><LogOut size={16} />Terminar sessão</button></div>
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
            <div className="success" role="status">
              {notice}
              <button onClick={() => setNotice("")} aria-label="Fechar aviso">
                <X size={16} />
              </button>
            </div>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {page === "Dashboard" && (
            <>
              <div className="page-intro">
                <div>
                  <span className="eyebrow">VISÃO GERAL</span>
                  <h1>Uma visão clara do seu negócio.</h1>
                  <p>Acompanhe a operação e mantenha a caixa sob controlo.</p>
                </div>
                {write && (
                  <button
                    className="primary"
                    onClick={() => openModal("movement")}
                  >
                    <Plus size={18} /> Novo movimento
                  </button>
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
                    Saldo bancário <Landmark size={20} />
                  </span>
                  <strong>—</strong>
                  <small>Sem extrato importado</small>
                </article>
                <article className="stat">
                  <span>
                    Dinheiro em trânsito <ArrowLeftRight size={20} />
                  </span>
                  <strong>—</strong>
                  <small>Disponível na fase de depósitos</small>
                </article>
                <article className="stat">
                  <span>
                    Resultado de gestão <FileBarChart size={20} />
                  </span>
                  <strong>—</strong>
                  <small>Requer receitas e custos reconhecidos</small>
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
                  <button
                    className="text-button"
                    onClick={() => setPage("Folha de caixa")}
                  >
                    Gerir caixas <ChevronRight size={16} />
                  </button>
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
                    </div>
                  </div>
                  <div className="priority">
                    <b>—</b>
                    <div>
                      <strong>Alertas de validade</strong>
                      <small>Aguarda implementação da fase 3.</small>
                    </div>
                  </div>
                </section>
              </div>
              <section className="panel">
                <div className="panel-title">
                  <h3>Últimos movimentos</h3>
                  <button
                    className="text-button"
                    onClick={() => setPage("Movimentos reais")}
                  >
                    Ver todos <ChevronRight size={16} />
                  </button>
                </div>
                <MovementTable rows={visibleMovements.slice(0, 5)} />
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
                    <button type="button" onClick={() => { setTablesOpen(true); setPage("Caixas"); }}>
                      <Wallet size={18} aria-hidden="true" /> Configurar caixas
                    </button>
                    <button type="button" onClick={() => { setTablesOpen(true); navigatePage("Meios de pagamento"); }}>
                      <CreditCard size={18} aria-hidden="true" /> Configurar meios de pagamento
                    </button>
                  </>
                )}
                actions={write && (
                  <button className="primary" onClick={() => openModal("open")}>
                    <Plus size={18} /> Nova folha de caixa
                  </button>
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
                    <button onClick={() => openModal("transfer")}>
                      Transferir entre caixas
                    </button>
                  )}
                  {write && (
                    <button
                      className="primary"
                      onClick={() => openModal("movement")}
                    >
                      <Plus size={18} /> Registar movimento
                    </button>
                  )}
                </div>
              </div>
              <RealMovements
                localRows={movements}
                store={store}
                registers={boot.registers}
              >
                {(pageRows) => (
                  <MovementTable
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
                  <button
                    className="primary"
                    onClick={() => openModal("account")}
                  >
                    <Plus size={18} /> Nova conta
                  </button>
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
                    </section>
                  ))}
              </div>
              {!boot.accounts.some(a => !store || a.store_id === store) && <Empty text="Ainda não existem contas bancárias nesta seleção" />}
              <button onClick={() => setPage("Extratos")}>Consultar extratos</button>
            </>
          )}
          {page === "Bancos" && <BanksCatalog manager={manager} onChanged={() => reload(["bootstrap"])} />}
          {page === "Extratos" && (
            <>
              <div className="page-intro"><div><h1>Extratos bancários</h1><p>Selecione uma conta para importar e consultar os movimentos do banco.</p></div><button onClick={() => setPage("Múltiplas contas")}>Gerir contas</button></div>
              <BankStatements accounts={boot.accounts.filter(a => !store || a.store_id === store)} manager={manager} />
            </>
          )}
          {page === "Reconciliação automática" && (
            <Reconciliation stores={boot.stores} store={store} manager={manager} admin={boot.user.role === "administrador"} />
          )}
          {page === "Conferência documental" && <DocumentControl stores={boot.stores} store={store} manager={manager} admin={boot.user.role === "administrador"} integration={boot.integration} />}
          {page === "Entrega à contabilidade" && <AccountingDelivery admin={boot.user.role === "administrador"} onSettings={() => navigatePage(manager ? "Regime de IVA" : "Organização")} />}
          {page === "Digitalizar documento" && <><div className="page-intro"><div><h1>Digitalizar documento</h1><p>Fotografe ou selecione páginas, reveja o PDF e guarde-o na pasta cloud da empresa.</p></div>{manager && <button onClick={() => setScanMenuOpen(true)}><Camera size={18} />Digitalizar documento</button>}</div>{!manager && <p>A digitalização está disponível para administradores e gestores.</p>}{manager && scanMenuOpen && <DocumentScanner store={store} onClose={() => setScanMenuOpen(false)} onSaved={reload} />}</>}
          {page === "Relatórios" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>Histórico e auditoria</h1>
                  <p>Registos financeiros e alterações de responsabilidade.</p>
                </div>
                <button
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
                </button>
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
                  <button className="primary" onClick={() => openModal("register")}>
                    <Plus size={18} /> Nova caixa
                  </button>
                )}
              </div>
              <section className="panel">
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Caixa</th><th>Estabelecimento</th></tr></thead>
                    <tbody>
                      {boot.registers.filter(r => !store || r.store_id === store).map(r => (
                        <tr key={r.id}>
                          <td><span className="cash-payment-label"><Wallet size={18} aria-hidden="true" />{r.name}</span></td>
                          <td>{boot.stores.find(s => s.id === r.store_id)?.name || "—"}</td>
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
                      <button onClick={() => openModal("store")}>
                        Nova loja
                      </button>
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
                    <button onClick={() => openModal("register")}>
                      Adicionar caixa
                    </button>
                  )}
                </section>

                <section className="panel">
                  <div className="panel-title">
                    <h3>Equipa</h3>
                    {boot.user.role === "administrador" && (
                      <button onClick={() => openModal("user")}>
                        Novo utilizador
                      </button>
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
                  <button onClick={() => openModal("password")}>
                    Alterar palavra-passe
                  </button>
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
          {[
            "Artigos e validades",
            "Receitas e despesas",
          ].includes(page) && (
            <section className="panel roadmap">
              <span className="eyebrow">DESENVOLVIMENTO POR FASES</span>
              <h1>{page}</h1>
              <p>
                Este módulo será implementado numa fase seguinte. A Fase 1 já
                permite gerir lojas, equipa, caixa, movimentos, fechos e
                auditoria.
              </p>
              <p>
                {page === "Clientes" || page === "Fornecedores"
                  ? `A tabela de ${page.toLowerCase()} será disponibilizada neste espaço.`
                  : page === "Depósitos"
                  ? "As retiradas para depósito podem ser registadas na caixa. O controlo de trânsito e talões estará disponível na fase 2."
                  : page === "Artigos e validades"
                    ? "Lotes, FEFO e alertas de validade estão previstos para a fase 3."
                    : page === "Reconciliação"
                      ? "A importação de extratos, deteção de duplicados e correspondências agrupadas estão previstas para a fase 4."
                      : "O reconhecimento de receitas, custos e despesas será separado dos movimentos de dinheiro na fase 2."}
              </p>
              <button onClick={() => setPage("Folha de caixa")}>
                Ir para folha de caixa
              </button>
            </section>
          )}
          {(page === "Clientes" || page === "Fornecedores") && <Entities kind={page} manager={manager} categories={boot.categories.map(c => ({ id: c.id, name: c.name }))} />}
          {page === "Regime de IVA" && manager && <FiscalSettings />}
          {page === "Anexos" && manager && <AttachmentSettings nif={boot.company?.nif} />}
          {page === "Digitalização" && manager && <ScanSettings admin={boot.user.role === "administrador"} />}
          {page === "Verificação automática e-Fatura" && manager && <InvoiceWorkflowSettings admin={boot.user.role === "administrador"} />}
          <footer>
            GPGest · Gestão interna <span>Europe/Lisbon · EUR · Fase 1</span>
          </footer>
        </main>
      </div>
      {modal && (
        <div
          className="overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) setModal("");
          }}
        >
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
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
                    "payment-method": "Novo meio de pagamento",
                    account: "Nova conta bancária",
                    category: "Nova categoria",
                    user: "Novo utilizador",
                    attachments: "Anexos do movimento",
                    password: "Alterar palavra-passe",
                  }[modal]
                }
              </h2>
              <button onClick={() => setModal("")} aria-label="Fechar">
                <X />
              </button>
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
                    occurred_at: new Date(v.occurred_at).toISOString(),
                    request_key: requestKey,
                  })
                }
              >
                <Field label="Sessão aberta">
                  <Select
                    name="session_id"
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
                    <select name="payment">
                      {Object.entries(paymentMethods).map(
                        ([k, name]) => (
                          <option key={k} value={k}>
                            {name}
                          </option>
                        ),
                      )}
                    </select>
                  </Field>
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
            {["store", "category", "register", "account", "payment-method"].includes(modal) && (
              <Form
                onSubmit={(v) => {
                  let payload = v;
                  if (modal === "account" && v.bank_choice) {
                    const { bank_choice, ...account } = v;
                    payload = { ...account, bank: bank_choice === "__other__" ? account.bank : bank_choice };
                  }
                  return save(
                    "/" +
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
                  <input name="name" required minLength={2} maxLength={100} />
                </Field>
                {["register", "account"].includes(modal) && (
                  <Field label="Loja">
                    <Select name="store_id" rows={boot.stores} />
                  </Field>
                )}
                {modal === "account" && (
                  <>
                    {boot.bank_institutions?.length ? <>
                      <Field label="Banco">
                        <select name="bank_choice" required defaultValue="" onChange={(event) => setCustomBank(event.target.value === "__other__")}>
                          <option value="">Selecionar banco…</option>
                          {boot.bank_institutions.map((bank) => <option key={bank.id} value={bank.name}>{bank.name}</option>)}
                          <option value="__other__">Outro banco…</option>
                        </select>
                      </Field>
                      {customBank && <Field label="Nome do banco"><input name="bank" required minLength={2} maxLength={100} /></Field>}
                    </> : <Field label="Banco"><input name="bank" required minLength={2} /></Field>}
                    <Field label="Últimos 4 algarismos do IBAN">
                      <input
                        name="iban_last4"
                        required
                        pattern="[0-9]{4}"
                        maxLength={4}
                      />
                    </Field>
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
          </section>
        </div>
      )}
    </div>
  );
  function MovementTable({
    rows,
    onReverse,
  }: {
    rows: Row[];
    onReverse?: (r: Row) => void;
  }) {
    return rows.length ? (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Data</th>
              <th>Movimento</th>
              <th>Rubrica</th>
              <th>Documento</th>
              <th>Entidade</th>
              <th>Pagamento</th>
              <th>Montante</th>
              <th>Origem</th>
              <th>Estado</th>
              <th>Ações</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id}>
                <td>
                  {m.external && !/(Z|[+-]\d{2}:?\d{2})$/.test(m.occurred_at)
                    ? m.occurred_at
                        .slice(0, 10)
                        .split("-")
                        .reverse()
                        .join("/") +
                      " " +
                      m.occurred_at.slice(11, 16)
                    : date(m.occurred_at)}
                </td>
                <td>
                  <strong>
                    {m.kind === "documento"
                      ? "Documento de venda"
                      : labels[m.kind] || m.kind}
                  </strong>
                  <small>
                    {m.entity || m.document || m.reference || "Manual"}
                  </small>
                </td>
                <td>{(() => { const category = boot?.categories.find(row => row.id === m.category_id || (m.category_name && row.name.toLocaleLowerCase("pt-PT") === m.category_name.toLocaleLowerCase("pt-PT"))); return <RubricBadge name={m.category_name || category?.name || (m.external ? "Não indicada na origem" : "—")} icon={category?.icon} color={category?.color} />; })()}</td>
                <td>{m.document || "—"}</td>
                <td>{m.entity || "—"}</td>
                <td>{m.payment_name || labels[m.payment] || m.payment || "—"}</td>
                <td className={m.direction > 0 ? "positive" : "negative"}>
                  {m.direction > 0 ? "+" : "−"}
                  {money(m.amount_cents)}
                </td>
                <td>
                  <span className="pill neutral">
                    {m.origin === "fecho_local" ? "Fecho de caixa" : m.external ? "Wintouch" : "GPGest"}
                  </span>
                </td>
                <td>
                  <span
                    className={
                      "pill " +
                      (m.reversed_by || m.reversal_of ? "neutral" : "")
                    }
                  >
                    {m.cancelled
                      ? "Anulado"
                      : m.kind === "documento"
                        ? "Documento"
                        : m.reversed_by
                          ? "Estornado"
                          : m.reversal_of
                            ? "Estorno"
                            : "Confirmado"}
                  </span>
                </td>
                <td>
                  {!m.external && (
                    <button onClick={() => openModal("attachments", m.movement_id ? { ...m, id: m.movement_id } : m)}>
                      Anexos
                    </button>
                  )}
                  {m.external && <small>Gerido na Wintouch</small>}
                  {m.origin === "fecho_local" && <small>Gerido na folha de caixa</small>}
                  {onReverse &&
                    !m.external &&
                    m.origin !== "fecho_caixa" &&
                    m.origin !== "fecho_local" &&
                    !m.reversed_by &&
                    !m.reversal_of &&
                    !m.transfer_id && (
                      <button onClick={() => onReverse(m)}>Estornar</button>
                    )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <Empty text="Sem movimentos registados" />
    );
  }
}

function AttachmentPanel({
  movement,
  writable,
  canScan = false,
}: {
  movement: Row;
  writable: boolean;
  canScan?: boolean;
}) {
  const [rows, setRows] = useState<Row[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const load = () =>
    api("/movements/" + movement.id + "/attachments").then(setRows);
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [movement.id]);
  return (
    <div>
      {rows.map((r) => (
        <p key={r.id}>
          <a href={r.view_url || "/api/attachments/" + r.id}>{r.name} · Descarregar</a>
        </p>
      ))}
      {!rows.length && <p>Sem anexos.</p>}
      {canScan && writable && <ScanButton movement={movement} onSaved={load} />}
      {rows.filter(r => r.scan_id && r.validation_status !== "validado").map(r => <p key={r.id}>Anexo por validar: {r.name}</p>)}
      {writable && (
        <Field label="Adicionar PDF, JPG ou PNG (até 5 MB, máximo 3)">
          <input
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            disabled={busy}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              setBusy(true);
              setError("");
              try {
                if (file.size > 5 * 1024 * 1024)
                  throw new Error("O ficheiro excede 5 MB.");
                const data = new FormData();
                data.append("file", file);
                const r = await fetch(
                  "/api/movements/" + movement.id + "/attachments",
                  { method: "POST", body: data },
                );
                if (!r.ok) {
                  const v = await r.json();
                  throw new Error(
                    v.detail || "Não foi possível guardar o anexo.",
                  );
                }
                await load();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          />
        </Field>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
