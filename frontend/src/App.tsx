import {
  useEffect,
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
  ArrowLeftRight,
  Landmark,
  Package,
  FileBarChart,
  Settings,
  Plug,
  LogOut,
  Menu,
  CheckCircle,
  ShieldCheck,
  Plus,
  Clock,
  ChevronRight,
  Receipt,
  X,
} from "lucide-react";

type Row = Record<string, any>;
import TenantIntegrations from "./TenantIntegrations";
import PlannedMovements from "./PlannedMovements";
import WintouchSales from "./WintouchSales";
type Bootstrap = {
  user: Row;
  mode: string;
  company: Row;
  stores: Row[];
  registers: Row[];
  accounts: Row[];
  categories: Row[];
  users: Row[];
};
const money = (v: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    v / 100,
  );
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
  fornecedor: "Pagamento a fornecedor",
  despesa: "Pequena despesa",
  reembolso: "Reembolso",
  deposito: "Retirada para depósito",
  reforco: "Reforço de fundo",
  transferencia_caixa: "Transferência entre caixas",
  numerario: "Numerário",
  cartao: "Multibanco / cartão",
  transferencia: "Transferência",
  outro: "Outro",
};
const denominations = [
  50000, 20000, 10000, 5000, 2000, 1000, 500, 200, 100, 50, 20, 10, 5, 2, 1,
];
export async function api(path: string, body?: unknown) {
  const r = await fetch("/api" + path, {
    credentials: "same-origin",
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
  label: string;
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
  const [boot, setBoot] = useState<Bootstrap | null>(null),
    [loading, setLoading] = useState(true),
    [page, setPage] = useState("Dashboard"),
    [sessions, setSessions] = useState<Row[]>([]),
    [movements, setMovements] = useState<Row[]>([]),
    [dashboard, setDashboard] = useState<Row>({}),
    [audit, setAudit] = useState<Row[]>([]),
    [modal, setModal] = useState(""),
    [selected, setSelected] = useState<Row | null>(null),
    [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [mobile, setMobile] = useState(false),
    [counts, setCounts] = useState<Record<string, number>>({}),
    [store, setStore] = useState(""),
    [requestKey, setRequestKey] = useState(crypto.randomUUID());
  async function reload() {
    const b = await api("/bootstrap");
    setBoot(b);
    const [s, m, d] = await Promise.all([
      api("/sessions"),
      api("/movements"),
      api("/dashboard"),
    ]);
    setSessions(s);
    setMovements(m);
    setDashboard(d);
    setAudit(b.user.role !== "operador" ? await api("/audit") : []);
  }
  useEffect(() => {
    const expire = () => {
      setBoot(null);
      setSessions([]);
      setMovements([]);
      setAudit([]);
      setModal("");
    };
    window.addEventListener("gpgest-expired", expire);
    return () => window.removeEventListener("gpgest-expired", expire);
  }, []);
  useEffect(() => {
    reload()
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);
  const save = async (path: string, data: unknown) => {
    await api(path, data);
    await reload();
    setModal("");
    setSelected(null);
    setNotice("Operação concluída com sucesso.");
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
          <span className="eyebrow">GESTÃO FINANCEIRA E OPERACIONAL</span>
          <h1>
            O dia a dia da sua loja.
            <br />
            Com tudo em ordem.
          </h1>
          <p>Caixa, movimentos e controlo financeiro num único espaço.</p>
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
              await api("/company/select", { slug: company });
              await api("/auth/login", credentials);
              await reload();
            }}
          >
            <Field label="Empresa">
              <input
                name="company"
                required
                defaultValue={
                  new URLSearchParams(window.location.search).get("empresa") ||
                  "demo"
                }
                autoComplete="organization"
              />
            </Field>
            <small>Use o identificador fornecido pelo administrador.</small>
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
  const uname = (id: string) =>
    boot.users.find((u) => u.id === id)?.name ?? "Utilizador";
  const visibleSessions = sessions.filter(
    (s) =>
      !store ||
      boot.registers.find((r) => r.id === s.register_id)?.store_id === store,
  );
  const visibleMovements = movements.filter((m) =>
    visibleSessions.some((s) => s.id === m.session_id),
  );
  const open = visibleSessions.filter((s) => !s.closed_at);
  const menu = [
    ["Dashboard", LayoutDashboard],
    ["Caixa diária", Wallet],
    ["Movimentos reais", ArrowLeftRight],
    ["Movimentos previstos", Clock],
    ["Depósitos", Landmark],
    ["Bancos", Landmark],
    ["Reconciliação", CheckCircle],
    ["Artigos e validades", Package],
    ["Receitas e despesas", Receipt],
    ["Relatórios", FileBarChart],
    ["Integrações", Plug],
    ["Configurações", Settings],
  ] as const;
  const openModal = (kind: string, row: Row | null = null) => {
    setSelected(row);
    setCounts({});
    setRequestKey(crypto.randomUUID());
    setModal(kind);
  };
  const filteredCash = open.reduce((sum, s) => sum + s.balance_cents, 0);
  return (
    <div className="shell">
      <aside className={mobile ? "sidebar visible" : "sidebar"}>
        <div className="brand">
          GP<span>Gest</span>
          <small>{boot.company?.name || "GESTÃO DE LOJA"}</small>
        </div>
        <nav>
          {menu.map(([label, Icon]) => (
            <button
              key={label}
              className={page === label ? "active" : ""}
              onClick={() => {
                setPage(label);
                setMobile(false);
                setError("");
              }}
            >
              <Icon size={19} />
              {label}
              {page === label && <ChevronRight size={15} />}
            </button>
          ))}
        </nav>
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
          <div>
            <span className="breadcrumb">GPGest / Gestão</span>
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
                    onClick={() => setPage("Caixa diária")}
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
                      <small>Aguarda implementação da fase 4.</small>
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
          {page === "Caixa diária" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>Caixa diária</h1>
                  <p>
                    Abertura, responsabilidade e fecho com contagem de
                    numerário.
                  </p>
                </div>
                {write && (
                  <button className="primary" onClick={() => openModal("open")}>
                    <Plus size={18} /> Abrir caixa
                  </button>
                )}
              </div>
              <div className="cash-grid">
                {visibleSessions.map((s) => (
                  <section className="panel cash-card" key={s.id}>
                    <div className="panel-title">
                      <h3>{rname(s.register_id)}</h3>
                      <span
                        className={"pill " + (s.closed_at ? "neutral" : "")}
                      >
                        {s.closed_at ? "Fechada" : "Aberta"}
                      </span>
                    </div>
                    <p>
                      {uname(s.operator_id)} · {date(s.opened_at)}
                    </p>
                    <strong className="cash-value">
                      {money(s.balance_cents)}
                    </strong>
                    <small>
                      Saldo esperado em numerário · Fundo{" "}
                      {money(s.opening_cents)}
                    </small>
                    <div className="payment-totals">
                      {Object.entries(s.totals).map(([k, v]) => (
                        <span key={k}>
                          {labels[k]} <b>{money(v as number)}</b>
                        </span>
                      ))}
                    </div>
                    {s.closed_at && (
                      <p>
                        Contado: {money(s.counted_cents)} · Diferença:{" "}
                        {money(s.counted_cents - s.expected_cents)}
                      </p>
                    )}
                    <div className="actions">
                      {!s.closed_at && write && (
                        <>
                          <button onClick={() => openModal("close", s)}>
                            Fechar caixa
                          </button>
                          <button onClick={() => openModal("handover", s)}>
                            Passar caixa
                          </button>
                        </>
                      )}
                      {s.closed_at && (
                        <a
                          className="download-link"
                          href={"/api/sessions/" + s.id + "/report"}
                        >
                          Relatório PDF
                        </a>
                      )}
                      {s.closed_at && manager && (
                        <button onClick={() => openModal("reopen", s)}>
                          Reabrir
                        </button>
                      )}
                    </div>
                  </section>
                ))}
              </div>
              {!visibleSessions.length && (
                <Empty text="Abra a primeira sessão de caixa" />
              )}
            </>
          )}
          {page === "Movimentos previstos" && (
            <>
              <PlannedMovements
                boot={boot}
                sessions={sessions}
                store={store}
                reload={reload}
              />
              <WintouchSales kind="previsto" store={store} />
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
              <section className="panel">
                <MovementTable
                  rows={visibleMovements}
                  onReverse={
                    manager ? (r) => openModal("reverse", r) : undefined
                  }
                />
              </section>
              <WintouchSales kind="real" store={store} />
            </>
          )}
          {page === "Bancos" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>Contas bancárias</h1>
                  <p>Identificação da conta, sem credenciais de homebanking.</p>
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
                      <span className="pill neutral">Sem ligação bancária</span>
                      <p>
                        <small>
                          Saldo e atualização indisponíveis até importar
                          extratos.
                        </small>
                      </p>
                    </section>
                  ))}
              </div>
              {!boot.accounts.length && <Empty />}
            </>
          )}
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
                        labels[m.payment],
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
            </>
          )}
          {page === "Configurações" && (
            <>
              <div className="page-intro">
                <div>
                  <h1>A sua organização</h1>
                  <p>Lojas, caixas, categorias e acessos por perfil.</p>
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
                    <h3>Categorias</h3>
                    {manager && (
                      <button onClick={() => openModal("category")}>
                        Adicionar
                      </button>
                    )}
                  </div>
                  {boot.categories.map((c) => (
                    <div className="setting-row" key={c.id}>
                      {c.name}
                    </div>
                  ))}
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
          {[
            "Depósitos",
            "Reconciliação",
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
                {page === "Depósitos"
                  ? "As retiradas para depósito podem ser registadas na caixa. O controlo de trânsito e talões estará disponível na fase 2."
                  : page === "Artigos e validades"
                    ? "Lotes, FEFO e alertas de validade estão previstos para a fase 3."
                    : page === "Reconciliação"
                      ? "A importação de extratos, deteção de duplicados e correspondências agrupadas estão previstas para a fase 4."
                      : "O reconhecimento de receitas, custos e despesas será separado dos movimentos de dinheiro na fase 2."}
              </p>
              <button onClick={() => setPage("Caixa diária")}>
                Ir para caixa diária
              </button>
            </section>
          )}
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
                    open: "Abrir caixa",
                    movement: "Novo movimento",
                    close: "Fechar caixa",
                    handover: "Passar responsabilidade",
                    reopen: "Reabrir caixa",
                    reverse: "Estornar movimento",
                    transfer: "Transferência entre caixas",
                    store: "Nova loja",
                    register: "Nova caixa",
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
                writable={
                  write &&
                  !sessions.find((s) => s.id === selected.session_id)?.closed_at
                }
              />
            )}
            {modal === "open" && (
              <Form onSubmit={(v) => save("/sessions", v)}>
                <Field label="Caixa">
                  <Select
                    name="register_id"
                    rows={boot.registers.filter(
                      (r) =>
                        !sessions.some(
                          (s) => s.register_id === r.id && !s.closed_at,
                        ),
                    )}
                  />
                </Field>
                <Field label="Fundo inicial (€)">
                  <input
                    name="opening"
                    inputMode="decimal"
                    required
                    defaultValue="0,00"
                  />
                </Field>
              </Form>
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
                      {["numerario", "cartao", "transferencia", "outro"].map(
                        (k) => (
                          <option key={k} value={k}>
                            {labels[k]}
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
            {modal === "close" && selected && (
              <Form
                onSubmit={(v) =>
                  save("/sessions/" + selected.id + "/close", {
                    counts,
                    reason: v.reason,
                  })
                }
              >
                <p>
                  Saldo esperado: <b>{money(selected.balance_cents)}</b>
                </p>
                <div className="denominations">
                  {denominations.map((d) => (
                    <Field key={d} label={money(d)}>
                      <input
                        aria-label={"Quantidade de " + money(d)}
                        type="number"
                        min="0"
                        max="100000"
                        value={counts[d] || ""}
                        onChange={(e) =>
                          setCounts({ ...counts, [d]: Number(e.target.value) })
                        }
                      />
                    </Field>
                  ))}
                </div>
                <div className="count-result">
                  <span>
                    Total contado{" "}
                    <b>
                      {money(
                        Object.entries(counts).reduce(
                          (s, [k, v]) => s + Number(k) * v,
                          0,
                        ),
                      )}
                    </b>
                  </span>
                  <span>
                    Diferença{" "}
                    <b>
                      {money(
                        Object.entries(counts).reduce(
                          (s, [k, v]) => s + Number(k) * v,
                          0,
                        ) - selected.balance_cents,
                      )}
                    </b>
                  </span>
                </div>
                <Field label="Justificação (obrigatória se existir diferença)">
                  <textarea name="reason" maxLength={2000} />
                </Field>
                <p className="footnote">
                  O fecho bloqueia alterações. A gestão pode reabrir com motivo
                  registado.
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
            {["store", "category", "register", "account"].includes(modal) && (
              <Form
                onSubmit={(v) =>
                  save(
                    "/" +
                      {
                        store: "stores",
                        category: "categories",
                        register: "registers",
                        account: "accounts",
                      }[modal],
                    v,
                  )
                }
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
                    <Field label="Banco">
                      <input name="bank" required minLength={2} />
                    </Field>
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
              <th>Documento</th>
              <th>Entidade</th>
              <th>Pagamento</th>
              <th>Montante</th>
              <th>Estado</th>
              <th>Ações</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id}>
                <td>{date(m.occurred_at)}</td>
                <td>
                  <strong>{labels[m.kind] || m.kind}</strong>
                  <small>
                    {m.entity || m.document || m.reference || "Manual"}
                  </small>
                </td>
                <td>{m.document || "—"}</td>
                <td>{m.entity || "—"}</td>
                <td>{labels[m.payment]}</td>
                <td className={m.direction > 0 ? "positive" : "negative"}>
                  {m.direction > 0 ? "+" : "−"}
                  {money(m.amount_cents)}
                </td>
                <td>
                  <span
                    className={
                      "pill " +
                      (m.reversed_by || m.reversal_of ? "neutral" : "")
                    }
                  >
                    {m.reversed_by
                      ? "Estornado"
                      : m.reversal_of
                        ? "Estorno"
                        : "Confirmado"}
                  </span>
                </td>
                <td>
                  <button onClick={() => openModal("attachments", m)}>
                    Anexos
                  </button>
                  {onReverse &&
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
}: {
  movement: Row;
  writable: boolean;
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
          <a href={"/api/attachments/" + r.id}>{r.name} · Descarregar</a>
        </p>
      ))}
      {!rows.length && <p>Sem anexos.</p>}
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
