import { useEffect, useId, useRef, useState } from "react";
import { Plus, RefreshCw, Package, Mail, Send, FileText } from "lucide-react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import {
  ActionButton,
  ActionLink,
  AppDialog,
  ManagedForm,
  Notice,
  confirmAction,
} from "./components/InteractionUI";
import {
  DetailDrawer,
  ModuleTabs,
  PageHeader,
  LoadingState,
  useWorkspaceTab,
} from "./components/WorkspaceUI";
import { DataTable } from "./components/DataTable";
import { MetricCard } from "./components/BusinessUI";
import "./CustomerOrders.css";

type Row = Record<string, any>;
const tabs = [
  ["resumo", "Resumo"],
  ["pedidos", "Pedidos de clientes"],
  ["pendentes", "Por encomendar"],
  ["fornecedores", "Fornecedores"],
  ["simulacoes", "Simulações"],
  ["rececoes", "Receções"],
  ["entregas", "Entregas"],
  ["historico", "Histórico"],
];
const qty = (value: unknown) =>
  new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 6 }).format(
    Number(value || 0),
  );
const money = (value: number, currency = "EUR") =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(
    (value || 0) / 100,
  );
const day = (value: string) =>
  value
    ? new Date(
        value.length === 10 ? value + "T12:00:00" : value,
      ).toLocaleDateString("pt-PT")
    : "—";
const initialTerms = () => ({
  currency: "EUR",
  minimum_cents: 0,
  minimum_basis: "net",
  discount: "0",
  tiers: [],
  shipping_cents: 0,
  free_shipping_cents: null,
  shipping_vat: "23",
  vat: "23",
  lead_days: 7,
  pending_days: 7,
  confirmation_days: 3,
  pickup_days: 7,
  email: "",
  notes: "",
  prices: {},
});
const blankLine = () => ({
  key: crypto.randomUUID(),
  article_id: "",
  description: "",
  quantity: "1",
  supplier_id: "",
  brand: "",
  reference: "",
  ean: "",
  sale_price_cents: null,
  notes: "",
});

function ArticlePicker({
  articles,
  value,
  onPick,
  id: inputId,
}: {
  articles: Row[];
  value?: string;
  onPick: (article: Row | null) => void;
  id?: string;
}) {
  const id = useId();
  const label = (a: Row) => `${a.code} · ${a.name}`;
  const [text, setText] = useState("");
  useEffect(() => {
    if (!value) return;
    setText(
      label(
        articles.find((a) => a.id === value) || { code: "", name: "" },
      ).replace(/^ · $/, ""),
    );
  }, [value, articles]);
  return (
    <>
      <input
        id={inputId}
        type="search"
        aria-label="Pesquisar artigo por código, descrição ou EAN"
        list={id}
        value={text}
        autoComplete="off"
        placeholder="Código, descrição ou EAN"
        onChange={(e) => {
          setText(e.target.value);
          const match = articles.find((a) =>
            [label(a), a.code, ...(a.barcodes || [])].includes(e.target.value),
          );
          onPick(match || null);
        }}
      />
      <datalist id={id}>
        {articles.flatMap((a) => [
          <option key={a.id} value={label(a)} />,
          ...(a.barcodes || []).map((code: string) => (
            <option key={a.id + code} value={code}>
              {a.name}
            </option>
          )),
        ])}
      </datalist>
    </>
  );
}

function CustomerPicker({
  customers,
  value,
  onPick,
  id,
}: {
  customers: Row[];
  value?: string;
  onPick: (customer: Row | null) => void;
  id?: string;
}) {
  const list = useId();
  const [text, setText] = useState("");
  const label = (c: Row) => `${c.name}${c.phone ? " · " + c.phone : ""}`;
  useEffect(() => {
    const customer = customers.find((c) => c.id === value);
    if (customer) setText(label(customer));
  }, [value, customers]);
  return (
    <>
      <input
        type="search"
        id={id}
        list={list}
        value={text}
        autoComplete="off"
        placeholder="Pesquisar nome, telefone ou email"
        onChange={(e) => {
          setText(e.target.value);
          onPick(
            customers.find((c) =>
              [label(c), c.phone, c.email].includes(e.target.value),
            ) || null,
          );
        }}
      />
      <datalist id={list}>
        {customers.flatMap((c) => [
          <option key={c.id} value={label(c)}>
            {c.email}
          </option>,
          ...(c.email
            ? [
                <option key={c.id + "-email"} value={c.email}>
                  {c.name}
                </option>,
              ]
            : []),
        ])}
      </datalist>
    </>
  );
}

function TermsFields({
  value,
  change,
  articles,
}: {
  value: Row;
  change: (value: Row) => void;
  articles: Row[];
}) {
  const set = (key: string, val: unknown) => change({ ...value, [key]: val });
  const euro = (key: string, label: string, optional = false) => (
    <Field key={key} label={label}>
      <input
        type="number"
        min="0"
        step="0.01"
        value={value[key] === null ? "" : Number(value[key]) / 100}
        onChange={(e) =>
          set(
            key,
            optional && e.target.value === ""
              ? null
              : Math.round(Number(e.target.value) * 100),
          )
        }
      />
    </Field>
  );
  return (
    <>
      <div className="co-grid">
        <Field label="Moeda">
          <input
            required
            pattern="[A-Z]{3}"
            maxLength={3}
            value={value.currency}
            onChange={(e) => set("currency", e.target.value.toUpperCase())}
          />
        </Field>
        {euro("minimum_cents", "Mínimo de encomenda")}
        <Field label="Critério do mínimo">
          <select
            value={value.minimum_basis}
            onChange={(e) => set("minimum_basis", e.target.value)}
          >
            <option value="gross">Bruto sem IVA</option>
            <option value="net">Líquido após descontos, sem IVA</option>
            <option value="net_vat">Líquido com IVA, sem portes</option>
            <option value="total">Total com IVA e portes</option>
          </select>
        </Field>
        <Field label="Desconto comercial (%)">
          <input
            type="number"
            min="0"
            max="100"
            step="0.0001"
            value={value.discount}
            onChange={(e) => set("discount", e.target.value)}
          />
        </Field>
        <Field label="IVA dos produtos (%)">
          <input
            type="number"
            min="0"
            max="100"
            step="0.001"
            value={value.vat}
            onChange={(e) => set("vat", e.target.value)}
          />
        </Field>
      </div>
      <details>
        <summary>Escalões de desconto e portes</summary>
        <p className="footnote">
          O escalão aplica-se ao valor após o desconto comercial, sem IVA. Os
          descontos são sucessivos; os portes gratuitos usam o líquido final sem
          IVA.
        </p>
        {(value.tiers || []).map((t: Row, i: number) => (
          <div className="co-grid" key={i}>
            <Field label={`Escalão ${i + 1} — desde`}>
              <input
                type="number"
                min="0"
                step="0.01"
                value={t.threshold_cents / 100}
                onChange={(e) =>
                  set(
                    "tiers",
                    value.tiers.map((r: Row, n: number) =>
                      n === i
                        ? {
                            ...r,
                            threshold_cents: Math.round(
                              Number(e.target.value) * 100,
                            ),
                          }
                        : r,
                    ),
                  )
                }
              />
            </Field>
            <Field label="Desconto adicional (%)">
              <input
                type="number"
                min="0"
                max="100"
                step="0.0001"
                value={t.discount}
                onChange={(e) =>
                  set(
                    "tiers",
                    value.tiers.map((r: Row, n: number) =>
                      n === i ? { ...r, discount: e.target.value } : r,
                    ),
                  )
                }
              />
            </Field>
            <ActionButton
              type="button"
              onClick={() =>
                set(
                  "tiers",
                  value.tiers.filter((_: Row, n: number) => n !== i),
                )
              }
            >
              Remover escalão
            </ActionButton>
          </div>
        ))}
        <ActionButton
          type="button"
          onClick={() =>
            set("tiers", [
              ...value.tiers,
              { threshold_cents: 0, discount: "0" },
            ])
          }
        >
          Adicionar escalão
        </ActionButton>
        <div className="co-grid">
          {euro("shipping_cents", "Portes")}
          {euro(
            "free_shipping_cents",
            "Portes gratuitos desde (vazio: sem condição)",
            true,
          )}
          <Field label="IVA dos portes (%)">
            <input
              type="number"
              min="0"
              max="100"
              step="0.001"
              value={value.shipping_vat}
              onChange={(e) => set("shipping_vat", e.target.value)}
            />
          </Field>
        </div>
      </details>
      <details>
        <summary>Prazos, alertas, contacto e preços acordados</summary>
        <div className="co-grid">
          {[
            ["lead_days", "Prazo de entrega (dias)"],
            ["pending_days", "Alertar pedido pendente após (dias)"],
            ["confirmation_days", "Alertar falta de confirmação após (dias)"],
            ["pickup_days", "Alertar falta de levantamento após (dias)"],
          ].map(([key, label]) => (
            <Field key={key} label={label}>
              <input
                type="number"
                min={key === "lead_days" ? 0 : 1}
                max="3650"
                value={value[key]}
                onChange={(e) => set(key, Number(e.target.value))}
              />
            </Field>
          ))}
          <Field label="Email comercial">
            <input
              type="email"
              value={value.email}
              onChange={(e) => set("email", e.target.value)}
            />
          </Field>
          <Field label="Observações comerciais">
            <textarea
              value={value.notes}
              onChange={(e) => set("notes", e.target.value)}
            />
          </Field>
        </div>
        <Field label="Adicionar preço acordado">
          <select
            value=""
            onChange={(e) => {
              if (e.target.value)
                set("prices", { ...value.prices, [e.target.value]: "0" });
            }}
          >
            <option value="">Selecionar artigo</option>
            {articles.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} · {a.name}
              </option>
            ))}
          </select>
        </Field>
        {Object.entries(value.prices || {}).map(([id, price]) => (
          <div className="co-inline" key={id}>
            <Field label={articles.find((a) => a.id === id)?.name || id}>
              <input
                type="number"
                min="0"
                step="0.000001"
                value={String(price)}
                onChange={(e) =>
                  set("prices", { ...value.prices, [id]: e.target.value })
                }
              />
            </Field>
            <ActionButton
              type="button"
              onClick={() => {
                const next = { ...value.prices };
                delete next[id];
                set("prices", next);
              }}
            >
              Remover preço
            </ActionButton>
          </div>
        ))}
      </details>
    </>
  );
}

function SimulationTotals({ totals, terms }: { totals: Row; terms: Row }) {
  const currency = totals.currency || "EUR";
  return (
    <section className="co-totals" aria-label="Resultado da simulação">
      <dl>
        {[
          ["Bruto", totals.gross_cents],
          ["Descontos", totals.discount_cents],
          ["Líquido", totals.net_cents],
          ["IVA dos produtos", totals.vat_cents],
          ["Portes", totals.shipping_cents],
          ["IVA dos portes", totals.shipping_vat_cents],
          ["Total previsto", totals.total_cents],
          ["Elegível para o mínimo", totals.eligible_cents],
        ].map(([label, val]) => (
          <div key={String(label)}>
            <dt>{label}</dt>
            <dd>{money(Number(val), currency)}</dd>
          </div>
        ))}
      </dl>
      <label>
        Progresso do mínimo{" "}
        <progress
          max={Math.max(terms.minimum_cents, 1)}
          value={Math.min(
            totals.eligible_cents,
            Math.max(terms.minimum_cents, 1),
          )}
        />
      </label>
      <p role="status">
        <strong>
          {totals.missing_cents
            ? `Faltam ${money(totals.missing_cents, currency)} para atingir o mínimo de encomenda.`
            : "Mínimo de encomenda atingido."}
        </strong>
      </p>
      <small>Simulação: não cria nem envia encomendas reais.</small>
    </section>
  );
}

export default function CustomerOrders({
  stores,
  store = "",
  company = {},
  navigate,
}: {
  stores: Row[];
  store?: string;
  company?: Row;
  navigate: (page: string) => void;
}) {
  const [data, setData] = useState<Row | null>(null),
    [catalog, setCatalog] = useState<Row>({
      articles: [],
      warehouses: [],
      customers: [],
      suppliers: [],
    });
  const [history, setHistory] = useState<Row[]>([]),
    [receipts, setReceipts] = useState<Row[]>([]);
  const [area, setArea] = useWorkspaceTab(
    "pedidos-clientes",
    "resumo",
    tabs.map(([id]) => id),
  );
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState(""),
    [simId, setSimId] = useState("");
  const [modal, setModal] = useState(""),
    [draft, setDraft] = useState<Row>({}),
    [preview, setPreview] = useState<Row | null>(null),
    [checks, setChecks] = useState<string[]>([]);
  const [mail, setMail] = useState<Row | null>(null),
    [contact, setContact] = useState<Row | null>(null);
  const keys = useRef(new Map<string, { body: string; key: string }>());
  const orders: Row[] = data?.orders || [],
    simulations: Row[] = data?.simulations || [];
  const active = orders.find((o) => o.id === selected),
    activeSim = simulations.find((s) => s.id === simId);
  const pending = orders.flatMap((o) =>
    o.lines
      .filter((l: Row) => Number(l.quantities.to_order) > 0)
      .map((l: Row) => ({ ...l, order: o })),
  );
  const warehouses = (id: string) =>
    catalog.warehouses.filter((w: Row) => w.store_id === id);
  const supplierName = (id: string) =>
    catalog.suppliers.find((s: Row) => s.id === id)?.name || "Sem fornecedor";
  const canBuy = !!data?.can_buy;

  async function load() {
    const query = store ? "?store_id=" + encodeURIComponent(store) : "";
    const [next, stock, people, events] = await Promise.all([
      api("/customer-orders" + query),
      api("/stock/catalog"),
      api("/customer-orders/catalog"),
      api("/customer-orders/history" + query),
    ]);
    setData(next);
    setCatalog({ ...stock, ...people });
    setHistory(events);
    if (next.can_buy)
      setReceipts(
        (await api("/procurement")).documents.filter(
          (d: Row) => d.kind === "receipt" && d.confirmed_at,
        ),
      );
  }
  useEffect(() => {
    let alive = true;
    void load().catch((e) => {
      if (alive) setError(e.message);
    });
    return () => {
      alive = false;
    };
  }, [store]);
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function mutate(
    path: string,
    body: Row,
    method: "POST" | "PUT" = "POST",
  ) {
    const encoded = JSON.stringify(body),
      previous = keys.current.get(path);
    const key = previous?.body === encoded ? previous.key : crypto.randomUUID();
    keys.current.set(path, { body: encoded, key });
    const result = await api(path, { ...body, request_key: key }, { method });
    keys.current.delete(path);
    return result;
  }
  const feedback = (
    <>
      {error && <Notice tone="error">{error}</Notice>}
      {notice && <Notice tone="success">{notice}</Notice>}
    </>
  );
  function open(kind: string, value: Row) {
    setModal(kind);
    setDraft(value);
    setPreview(null);
    setMail(null);
    setContact(null);
    setChecks([]);
    setError("");
    setNotice("");
  }
  function newOrder() {
    open("order", {
      store_id: store || stores[0]?.id || "",
      customer_id: "",
      customer_name: "",
      phone: "",
      email: "",
      preferred_channel: "none",
      contact_allowed: false,
      due_date: "",
      priority: "normal",
      notes: "",
      requested_at: new Date(
        Date.now() - new Date().getTimezoneOffset() * 60000,
      )
        .toISOString()
        .slice(0, 16),
      lines: [blankLine()],
    });
  }
  function newSim() {
    open("simulation", {
      store_id: store || stores[0]?.id || "",
      supplier_id: "",
      terms: initialTerms(),
      lines: [],
      notes: "",
    });
  }
  const editLine = (index: number, changes: Row) => {
    setDraft((d) => ({
      ...d,
      lines: d.lines.map((l: Row, i: number) =>
        i === index ? { ...l, ...changes } : l,
      ),
    }));
    setPreview(null);
  };
  function addPending() {
    const lines: Row[] = structuredClone(draft.lines),
      used = new Set(
        lines.flatMap((l) => l.allocations.map((a: Row) => a.line_id)),
      );
    for (const line of pending.filter(
      (l) =>
        checks.includes(l.id) &&
        !used.has(l.id) &&
        l.supplier_id === draft.supplier_id &&
        l.order.store_id === draft.store_id,
    )) {
      const match = lines.find((l) =>
        line.article_id
          ? l.article_id === line.article_id
          : !l.article_id && l.description === line.description,
      );
      const amount = Number(line.quantities.to_order),
        article = catalog.articles.find((a: Row) => a.id === line.article_id);
      const allocation = {
        line_id: line.id,
        quantity: line.quantities.to_order,
      };
      if (match) {
        match.quantity = String(
          Number((Number(match.quantity) + amount).toFixed(6)),
        );
        match.allocations.push(allocation);
      } else
        lines.push({
          article_id: line.article_id,
          description: line.description,
          quantity: String(amount),
          unit_cost:
            draft.terms.prices?.[line.article_id] ?? article?.last_cost ?? "0",
          discount: null,
          vat: article?.vat_rate ?? null,
          allocations: [allocation],
        });
    }
    setDraft({ ...draft, lines });
    setChecks([]);
    setPreview(null);
  }
  async function saveOrder() {
    const body = {
      ...draft,
      customer_id: draft.customer_id || null,
      due_date: draft.due_date || null,
      requested_at: new Date(draft.requested_at).toISOString(),
      lines: draft.lines.map(({ key, ...l }: Row) => ({
        ...l,
        article_id: l.article_id || null,
        supplier_id: l.supplier_id || null,
      })),
    };
    const row = await mutate("/customer-orders", body);
    await load();
    setSelected(row.id);
    setModal("");
    setNotice("Pedido registado. Não foi enviada nenhuma mensagem.");
  }
  async function saveSim() {
    const {
      id,
      revision = 0,
      store_id,
      supplier_id,
      terms,
      lines,
      notes,
    } = draft;
    await mutate(
      "/customer-orders/simulations" + (id ? "/" + id : ""),
      { revision, store_id, supplier_id, terms, lines, notes },
      id ? "PUT" : "POST",
    );
    await load();
    setModal("");
    setArea("simulacoes");
    setNotice("Simulação guardada sem criar uma encomenda real.");
  }
  async function prepareSim(sim: Row) {
    open("prepare", {
      simulation_id: sim.id,
      revision: sim.revision,
      warehouse_id: warehouses(sim.store_id)[0]?.id || "",
      allow_below_minimum: false,
    });
  }
  function contactOrder(row: Row) {
    const products = row.lines
      .filter((l: Row) => Number(l.quantities.available) > 0)
      .map((l: Row) => `${l.description} (${qty(l.quantities.available)})`)
      .join(", ");
    open("contact", {
      order_id: row.id,
      revision: row.revision,
      channel:
        row.preferred_channel === "none" ? "phone" : row.preferred_channel,
      message: `Olá ${row.customer_name},\n\nInformamos que ${products}, do pedido de ${day(row.requested_at)}, já se encontra disponível para levantamento.\n\nObrigado pela preferência.\n${company.name || ""}`,
      subject: "O seu pedido está disponível",
    });
    const prepared = history.find(
      (h) => h.order_id === row.id && h.dispatch_id && h.status === "prepared",
    );
    if (prepared)
      void run(async () => {
        const saved = await api(
          `/customer-orders/${row.id}/email/${prepared.dispatch_id}`,
        );
        setMail(saved);
      });
  }
  function supplierEmail(sim: Row) {
    open("supplier-email", {
      simulation_id: sim.id,
      procurement_id: sim.procurement_id,
      revision: sim.procurement.revision,
      to:
        sim.terms.email ||
        catalog.suppliers.find((s: Row) => s.id === sim.supplier_id)?.email ||
        "",
      subject: `Encomenda ${sim.procurement.document} · ${company.name || ""}`,
      message: `${company.name || "Empresa"}${company.nif ? " · NIF " + company.nif : ""}\nFornecedor: ${sim.supplier_name}\nEncomenda: ${sim.procurement.document}\nData: ${sim.procurement.document_date}\n\n${sim.lines.map((l: Row) => `${catalog.articles.find((a: Row) => a.id === l.article_id)?.code || ""} · ${l.description} · ${qty(l.quantity)} · ${money(Math.round(Number(l.unit_cost) * 100), sim.terms.currency)}`).join("\n")}\n\n${sim.notes || ""}\nSolicitamos confirmação de disponibilidade e prazo de entrega.\nSegue a encomenda em PDF.`,
    });
    const prepared = sim.procurement.email_history?.find(
      (e: Row) => e.status === "prepared",
    );
    if (prepared)
      void run(async () =>
        setMail(
          await api(
            `/customer-orders/simulations/${sim.id}/email/${prepared.id}`,
          ),
        ),
      );
  }
  const display = (
    rows: Row[],
    type: "orders" | "pending" | "simulations" | "history",
  ) => (
    <DataTable<Row>
      label={
        {
          orders: "pedidos de clientes",
          pending: "produtos por encomendar",
          simulations: "simulações de fornecedores",
          history: "histórico de encomendas",
        }[type]
      }
      rows={rows}
      rowKey={(r) => r.id}
      searchText={(r) =>
        type === "orders"
          ? `${r.number} ${r.customer_name} ${r.phone} ${r.email} ${r.lines.map((l: Row) => l.description).join(" ")}`
          : type === "pending"
            ? `${r.description} ${r.order.customer_name} ${supplierName(r.supplier_id)}`
            : type === "simulations"
              ? `${r.supplier_name} ${r.id} ${r.status}`
              : `${r.action} ${r.user} ${r.order_id || ""} ${r.detail?.message || ""}`
      }
      columns={
        type === "orders"
          ? [
              {
                id: "customer",
                label: "Pedido / cliente",
                sortValue: (r) => r.customer_name,
                render: (r) => (
                  <>
                    <strong>{r.number}</strong>
                    <div>{r.customer_name}</div>
                    <small>
                      {r.priority === "urgent" ? "Prioridade urgente · " : ""}
                      {day(r.requested_at)}
                    </small>
                  </>
                ),
              },
              {
                id: "products",
                label: "Produtos / estado",
                render: (r) => (
                  <>
                    {r.lines.map((l: Row) => (
                      <div key={l.id}>
                        {l.description} · {qty(l.quantities.requested)}
                        <small>{l.state_label}</small>
                      </div>
                    ))}
                  </>
                ),
              },
              {
                id: "due",
                label: "Entrega prevista",
                sortValue: (r) => r.due_date || "",
                render: (r) => (
                  <>
                    {day(r.due_date)}
                    {r.late && (
                      <strong className="co-warning">Em atraso</strong>
                    )}
                  </>
                ),
              },
              {
                id: "ready",
                label: "Levantamento",
                render: (r) =>
                  r.ready
                    ? r.notified
                      ? "Cliente notificado"
                      : "Cliente por contactar"
                    : r.closed
                      ? "Concluído"
                      : "A aguardar produtos",
              },
              {
                id: "actions",
                label: "Ações",
                render: (r) => (
                  <div className="actions">
                    <ActionButton onClick={() => setSelected(r.id)}>
                      Abrir pedido
                    </ActionButton>
                    {r.ready && data?.can_write && (
                      <ActionButton onClick={() => contactOrder(r)}>
                        Contactar cliente
                      </ActionButton>
                    )}
                  </div>
                ),
              },
            ]
          : type === "pending"
            ? [
                {
                  id: "product",
                  label: "Produto",
                  render: (r) => (
                    <>
                      {r.description}
                      <small>
                        {r.provisional ? "Provisório" : r.article_code}
                      </small>
                    </>
                  ),
                },
                {
                  id: "customer",
                  label: "Cliente / pedido",
                  render: (r) => (
                    <>
                      {r.order.customer_name}
                      <small>{r.order.number}</small>
                    </>
                  ),
                },
                {
                  id: "supplier",
                  label: "Fornecedor",
                  render: (r) => supplierName(r.supplier_id),
                },
                {
                  id: "quantity",
                  label: "Por encomendar",
                  render: (r) => qty(r.quantities.to_order),
                },
                {
                  id: "actions",
                  label: "Ações",
                  render: (r) => (
                    <ActionButton onClick={() => setSelected(r.order.id)}>
                      Abrir pedido
                    </ActionButton>
                  ),
                },
              ]
            : type === "simulations"
              ? [
                  {
                    id: "supplier",
                    label: "Fornecedor",
                    sortValue: (r) => r.supplier_name,
                    render: (r) => (
                      <>
                        {r.supplier_name}
                        <small>
                          {r.procurement?.document || r.id.slice(0, 8)}
                        </small>
                      </>
                    ),
                  },
                  {
                    id: "state",
                    label: "Estado",
                    render: (r) =>
                      r.status === "cancelled"
                        ? "Simulação cancelada"
                        : r.procurement?.status === "cancelled"
                          ? "Encomenda cancelada"
                          : r.supplier_confirmation
                            ? "Confirmação registada"
                            : r.procurement?.email_history?.some((e: Row) =>
                                  ["accepted", "received"].includes(e.status),
                                )
                              ? "Enviada ao fornecedor"
                              : r.procurement_id
                                ? "Encomenda preparada"
                                : "Rascunho de simulação",
                  },
                  {
                    id: "total",
                    label: "Total / mínimo",
                    render: (r) => (
                      <>
                        {money(r.totals.total_cents, r.terms.currency)}
                        <small>
                          {r.totals.missing_cents
                            ? `Faltam ${money(r.totals.missing_cents, r.terms.currency)}`
                            : "Mínimo atingido"}
                        </small>
                      </>
                    ),
                  },
                  {
                    id: "actions",
                    label: "Ações",
                    render: (r) => (
                      <ActionButton onClick={() => setSimId(r.id)}>
                        Abrir simulação
                      </ActionButton>
                    ),
                  },
                ]
              : [
                  {
                    id: "date",
                    label: "Data",
                    sortValue: (r) => r.at,
                    render: (r) => new Date(r.at).toLocaleString("pt-PT"),
                  },
                  { id: "action", label: "Operação", render: (r) => r.action },
                  { id: "user", label: "Responsável", render: (r) => r.user },
                  {
                    id: "details",
                    label: "Detalhe / contacto",
                    render: (r) => (
                      <>
                        {r.detail?.channel && (
                          <strong>
                            {r.detail.channel} · {r.status}
                          </strong>
                        )}
                        <div>
                          {r.detail?.message ||
                            r.detail?.reason ||
                            r.detail?.document ||
                            "Registo interno"}
                        </div>
                        {r.message_ids?.map((id: string) => (
                          <small key={id}>{id}</small>
                        ))}
                      </>
                    ),
                  },
                ]
      }
    />
  );

  if (!data && !error)
    return <LoadingState>A carregar encomendas…</LoadingState>;
  const outstanding = orders.filter((o) => !o.closed),
    ready = orders.filter((o) => o.ready);
  const sum = (key: string) =>
    orders.reduce(
      (n, o) =>
        n +
        o.lines.reduce(
          (s: number, l: Row) => s + Number(l.quantities[key] || 0),
          0,
        ),
      0,
    );
  return (
    <section className="customer-orders">
      <PageHeader
        title="Encomendas de clientes"
        description="Do pedido ao fornecedor e ao levantamento, com quantidades e contactos acompanhados."
        actions={
          <>
            <ActionButton disabled={busy} onClick={() => void run(load)}>
              <RefreshCw size={16} />
              Atualizar
            </ActionButton>
            {data?.can_write && (
              <ActionButton className="primary" onClick={newOrder}>
                <Plus size={16} />
                Novo pedido
              </ActionButton>
            )}
          </>
        }
      />
      {feedback}
      <ModuleTabs
        label="Áreas das encomendas de clientes"
        tabs={tabs.map(([id, label]) => ({ id, label }))}
        value={area}
        onChange={setArea}
      />
      <p className="footnote">{data?.integration_note}</p>
      {area === "resumo" && (
        <>
          <div className="co-metrics">
            {[
              ["Pedidos recebidos", orders.length],
              ["Produtos por encomendar", qty(sum("to_order"))],
              [
                "Encomendas enviadas",
                simulations.filter((s) =>
                  s.procurement?.email_history?.some((e: Row) =>
                    ["accepted", "received"].includes(e.status),
                  ),
                ).length,
              ],
              [
                "Por confirmar no fornecedor",
                simulations.filter(
                  (s) =>
                    s.procurement?.email_history?.some((e: Row) =>
                      ["accepted", "received"].includes(e.status),
                    ) && !s.supplier_confirmation,
                ).length,
              ],
              [
                "Produtos em trânsito",
                qty(Math.max(0, sum("sent") - sum("received"))),
              ],
              ["Produtos recebidos", qty(sum("received"))],
              [
                "Clientes por contactar",
                ready.filter((o) => !o.notified).length,
              ],
              ["Prontos para levantamento", qty(sum("available"))],
              ["Pedidos atrasados", outstanding.filter((o) => o.late).length],
              [
                "Parcialmente entregues",
                outstanding.filter((o) =>
                  o.lines.some((l: Row) => Number(l.quantities.delivered) > 0),
                ).length,
              ],
            ].map(([label, value]) => (
              <MetricCard
                key={String(label)}
                label={String(label)}
                value={String(value)}
                description="Pedidos das lojas autorizadas"
              />
            ))}
          </div>
          <section className="panel">
            <h2>Precisa de atenção</h2>
            {!outstanding.length && <p>Sem pedidos pendentes.</p>}
            {outstanding.map((o) => {
              const age = Math.floor(
                (Date.now() - new Date(o.requested_at).getTime()) / 86400000,
              );
              const limits = o.lines.map(
                (l: Row) =>
                  catalog.suppliers.find((s: Row) => s.id === l.supplier_id)
                    ?.terms?.pending_days || 7,
              );
              const warnings = [
                o.late && "Entrega em atraso",
                o.ready &&
                  !o.notified &&
                  "Produto disponível; cliente por contactar",
                o.lines.some(
                  (l: Row) =>
                    !l.supplier_id && Number(l.quantities.to_order) > 0,
                ) && "Produto sem fornecedor",
                age >= Math.min(...limits) && "Pedido pendente há vários dias",
                o.ready &&
                  o.notified &&
                  age >=
                    Math.max(
                      ...o.lines.map(
                        (l: Row) =>
                          catalog.suppliers.find(
                            (s: Row) => s.id === l.supplier_id,
                          )?.terms?.pickup_days || 7,
                      ),
                    ) &&
                  "Cliente notificado; confirmar levantamento",
              ].filter(Boolean);
              return warnings.length ? (
                <div className="co-alert" key={o.id}>
                  <div>
                    <strong>{o.customer_name}</strong>
                    <p>{warnings.join(" · ")}</p>
                  </div>
                  <ActionButton onClick={() => setSelected(o.id)}>
                    Abrir pedido
                  </ActionButton>
                </div>
              ) : null;
            })}
            {simulations
              .filter(
                (s) =>
                  s.status !== "cancelled" &&
                  (s.totals.missing_cents > 0 ||
                    (s.procurement_id &&
                      !s.supplier_confirmation &&
                      Date.now() - new Date(s.created_at).getTime() >
                        (s.terms.confirmation_days || 3) * 86400000)),
              )
              .map((s) => (
                <div className="co-alert" key={s.id}>
                  <p>
                    {s.supplier_name} ·{" "}
                    {s.totals.missing_cents
                      ? `Encomenda abaixo do mínimo: faltam ${money(s.totals.missing_cents, s.terms.currency)}`
                      : "Fornecedor ainda não confirmou"}
                  </p>
                  <ActionButton onClick={() => setSimId(s.id)}>
                    Abrir simulação
                  </ActionButton>
                </div>
              ))}
          </section>
        </>
      )}
      {area === "pedidos" && display(orders, "orders")}
      {area === "pendentes" && (
        <>
          {canBuy && (
            <ActionButton className="primary" onClick={newSim}>
              Simular compra por fornecedor
            </ActionButton>
          )}
          {display(pending, "pending")}
        </>
      )}
      {area === "fornecedores" && (
        <>
          {canBuy ? (
            <DataTable<Row>
              label="condições dos fornecedores"
              rows={catalog.suppliers}
              rowKey={(r) => r.id}
              searchText={(r) => r.name}
              columns={[
                { id: "name", label: "Fornecedor", render: (r) => r.name },
                {
                  id: "minimum",
                  label: "Mínimo",
                  render: (r) => money(r.terms.minimum_cents, r.terms.currency),
                },
                {
                  id: "lead",
                  label: "Prazo",
                  render: (r) => `${r.terms.lead_days} dias`,
                },
                {
                  id: "actions",
                  label: "Ações",
                  render: (r) => (
                    <ActionButton
                      onClick={() =>
                        open("terms", {
                          supplier_id: r.id,
                          revision: r.revision,
                          terms: structuredClone(r.terms),
                        })
                      }
                    >
                      Condições comerciais
                    </ActionButton>
                  ),
                },
              ]}
            />
          ) : (
            <Notice>
              As condições comerciais e custos estão reservados à gestão
              autorizada.
            </Notice>
          )}
          <div className="actions">
            <ActionButton onClick={() => navigate("Fornecedores")}>
              Gerir fornecedores
            </ActionButton>
            <ActionButton onClick={() => navigate("Artigos e validades")}>
              Gerir artigos
            </ActionButton>
          </div>
        </>
      )}
      {area === "simulacoes" && (
        <>
          {canBuy ? (
            <>
              <ActionButton className="primary" onClick={newSim}>
                Nova simulação
              </ActionButton>
              {display(simulations, "simulations")}
            </>
          ) : (
            <Notice>
              As simulações de compra estão reservadas à gestão autorizada.
            </Notice>
          )}
        </>
      )}
      {area === "rececoes" && (
        <>
          {canBuy ? (
            <>
              {display(
                simulations.filter(
                  (s) =>
                    s.procurement_id && s.procurement?.status !== "cancelled",
                ),
                "simulations",
              )}
              <p>
                Abra a encomenda para registar quantidades efetivamente
                recebidas ou associar uma receção já confirmada.
              </p>
              <ActionButton onClick={() => navigate("Receções de mercadoria")}>
                Circuito de receções de mercadoria
              </ActionButton>
            </>
          ) : (
            <Notice>As receções estão reservadas à gestão autorizada.</Notice>
          )}
        </>
      )}
      {area === "entregas" && display(ready, "orders")}
      {area === "historico" && display(history, "history")}

      {active && (
        <DetailDrawer
          aria-label={`Pedido ${active.number}`}
          onRequestClose={() => setSelected("")}
          busy={busy}
        >
          <h2>
            {active.number} · {active.customer_name}
          </h2>
          <p>
            {day(active.requested_at)} · Entrega prevista:{" "}
            {day(active.due_date)} ·{" "}
            {active.priority === "urgent" ? "Urgente" : "Normal"}
          </p>
          <p>
            {active.phone} · {active.email}
          </p>
          <p>
            Canal: {active.preferred_channel} · Contacto{" "}
            {active.contact_allowed
              ? "autorizado para este pedido"
              : "não autorizado"}
          </p>
          <p>{active.notes}</p>
          {data?.can_write && (
            <ActionButton onClick={() => open("details", { ...active })}>
              Editar dados do pedido
            </ActionButton>
          )}
          {feedback}
          {active.lines.map((l: Row) => (
            <section className="panel co-product" key={l.id}>
              <h3>{l.description}</h3>
              <p>
                {l.provisional
                  ? "Produto provisório; sem criação automática no ERP"
                  : l.article_code}{" "}
                · {supplierName(l.supplier_id)}
              </p>
              <strong>{l.state_label}</strong>
              <dl className="co-quantities">
                {[
                  ["requested", "Pedida"],
                  ["reserved", "Reservada"],
                  ["available", "Disponível válida"],
                  ["to_order", "Por encomendar"],
                  ["simulated", "Em simulação"],
                  ["ordered", "Encomendada"],
                  ["confirmed", "Confirmada"],
                  ["received", "Recebida"],
                  ["delivered", "Entregue"],
                  ["cancelled", "Cancelada"],
                ].map(([key, label]) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd>{qty(l.quantities[key])}</dd>
                  </div>
                ))}
              </dl>
              <p>{l.notes}</p>
              {data?.can_write && (
                <div className="actions">
                  <ActionButton
                    onClick={() =>
                      open("quantity", {
                        line_id: l.id,
                        order_id: active.id,
                        revision: active.revision,
                        action: "adjust",
                        quantity: l.quantities.requested,
                        reason: "",
                      })
                    }
                  >
                    Alterar quantidade pedida
                  </ActionButton>
                  {(!l.article_id || !l.supplier_id) && (
                    <ActionButton
                      onClick={() =>
                        open("associate", {
                          line_id: l.id,
                          revision: active.revision,
                          article_id: l.article_id || "",
                          supplier_id: l.supplier_id || "",
                        })
                      }
                    >
                      Associar artigo / fornecedor
                    </ActionButton>
                  )}
                  {l.article_id && Number(l.quantities.to_order) > 0 && (
                    <ActionButton
                      onClick={() =>
                        open("quantity", {
                          line_id: l.id,
                          order_id: active.id,
                          revision: active.revision,
                          action: "reserve",
                          quantity: l.quantities.to_order,
                          warehouse_id:
                            warehouses(active.store_id)[0]?.id || "",
                          reason: "Reserva para o cliente",
                        })
                      }
                    >
                      Reservar stock
                    </ActionButton>
                  )}
                  {Number(l.quantities.reserved) > 0 && (
                    <ActionButton
                      onClick={() =>
                        open("quantity", {
                          line_id: l.id,
                          order_id: active.id,
                          revision: active.revision,
                          action: "release",
                          quantity: l.quantities.reserved,
                          reason: "",
                        })
                      }
                    >
                      Libertar reserva
                    </ActionButton>
                  )}
                  {Number(l.quantities.available) > 0 && (
                    <ActionButton
                      className="primary"
                      onClick={() =>
                        open("quantity", {
                          line_id: l.id,
                          order_id: active.id,
                          revision: active.revision,
                          action: "deliver",
                          quantity: l.quantities.available,
                          reason: "Levantamento no balcão",
                        })
                      }
                    >
                      Registar entrega
                    </ActionButton>
                  )}
                  {Number(l.quantities.requested) >
                    Number(l.quantities.delivered) +
                      Number(l.quantities.cancelled) && (
                    <ActionButton
                      className="danger"
                      onClick={() =>
                        open("quantity", {
                          line_id: l.id,
                          order_id: active.id,
                          revision: active.revision,
                          action: "cancel",
                          quantity: String(
                            Number(l.quantities.requested) -
                              Number(l.quantities.delivered) -
                              Number(l.quantities.cancelled),
                          ),
                          reason: "",
                        })
                      }
                    >
                      Cancelar quantidade
                    </ActionButton>
                  )}
                </div>
              )}
            </section>
          ))}
          {active.ready && data?.can_write && (
            <ActionButton onClick={() => contactOrder(active)}>
              Preparar contacto ao cliente
            </ActionButton>
          )}
          <h3>Histórico deste pedido</h3>
          {history
            .filter((h) => h.order_id === active.id)
            .map((h) => (
              <p key={h.id}>
                {day(h.at)} · {h.user} · {h.action}{" "}
                {h.detail?.channel && `· ${h.detail.channel} · ${h.status}`}
              </p>
            ))}
        </DetailDrawer>
      )}

      {activeSim && (
        <DetailDrawer
          aria-label="Detalhe da simulação"
          busy={busy}
          onRequestClose={() => setSimId("")}
        >
          <h2>{activeSim.supplier_name}</h2>
          {feedback}
          <SimulationTotals totals={activeSim.totals} terms={activeSim.terms} />
          {activeSim.lines.map((l: Row, i: number) => (
            <section className="co-product" key={i}>
              <h3>{l.description}</h3>
              <p>
                {qty(l.quantity)} ·{" "}
                {money(
                  Math.round(Number(l.unit_cost) * 100),
                  activeSim.terms.currency,
                )}{" "}
                por unidade
              </p>
              <p>
                Clientes:{" "}
                {qty(
                  l.allocations.reduce(
                    (n: number, a: Row) => n + Number(a.quantity),
                    0,
                  ),
                )}{" "}
                · Reposição:{" "}
                {qty(
                  Number(l.quantity) -
                    l.allocations.reduce(
                      (n: number, a: Row) => n + Number(a.quantity),
                      0,
                    ),
                )}
              </p>
              {l.allocations.map((a: Row) => {
                const o = orders.find((o) =>
                  o.lines.some((line: Row) => line.id === a.line_id),
                );
                return (
                  <p key={a.line_id}>
                    {o?.customer_name || "Pedido"} · {qty(a.quantity)}{" "}
                    <ActionButton
                      onClick={() => {
                        setSimId("");
                        if (o) setSelected(o.id);
                      }}
                    >
                      Pedido original
                    </ActionButton>
                  </p>
                );
              })}
            </section>
          ))}
          <div className="actions">
            {activeSim.status === "draft" && (
              <>
                <ActionButton
                  onClick={() => open("simulation", structuredClone(activeSim))}
                >
                  Editar simulação
                </ActionButton>
                <ActionButton
                  className="primary"
                  onClick={() => void prepareSim(activeSim)}
                >
                  Preparar encomenda real
                </ActionButton>
                <ActionButton
                  className="danger"
                  onClick={() =>
                    void run(async () => {
                      if (
                        !(await confirmAction({
                          title: "Cancelar simulação?",
                          message:
                            "As quantidades deixam de estar atribuídas a esta proposta. Não cancela compras reais.",
                          confirmLabel: "Cancelar simulação",
                          tone: "danger",
                        }))
                      )
                        return;
                      await mutate(
                        `/customer-orders/simulations/${activeSim.id}/cancel`,
                        { revision: activeSim.revision },
                      );
                      await load();
                    })
                  }
                >
                  Cancelar simulação
                </ActionButton>
              </>
            )}
            {activeSim.procurement_id &&
              activeSim.procurement?.status !== "cancelled" && (
                <>
                  <ActionLink
                    href={`/api/procurement/${activeSim.procurement_id}/pdf`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <FileText size={16} />
                    PDF da encomenda
                  </ActionLink>
                  <ActionButton onClick={() => supplierEmail(activeSim)}>
                    <Mail size={16} />
                    Preparar email ao fornecedor
                  </ActionButton>
                  <ActionButton
                    onClick={() =>
                      open("supplier-confirm", {
                        simulation_id: activeSim.id,
                        revision: activeSim.revision,
                        quantities: activeSim.lines.map((l: Row) => l.quantity),
                        note: "",
                      })
                    }
                  >
                    Registar confirmação do fornecedor
                  </ActionButton>
                  <ActionButton
                    className="primary"
                    onClick={() =>
                      open("receive", {
                        simulation_id: activeSim.id,
                        revision: activeSim.revision,
                        document: "",
                        receipt_id: "",
                        quantities: activeSim.lines.map(() => "0"),
                        lots: activeSim.lines.map(() => ""),
                        expiries: activeSim.lines.map(() => ""),
                      })
                    }
                  >
                    <Package size={16} />
                    Receber produtos
                  </ActionButton>
                </>
              )}
          </div>
          {activeSim.supplier_confirmation && (
            <p>
              Fornecedor confirmou em {day(activeSim.supplier_confirmation.at)}:{" "}
              {activeSim.supplier_confirmation.note}
            </p>
          )}
          {activeSim.procurement?.email_history?.map((e: Row) => (
            <p key={e.id}>
              Email · {e.status} · {day(e.created_at)}
            </p>
          ))}
        </DetailDrawer>
      )}

      {modal && (
        <AppDialog
          aria-label={
            {
              order: "Novo pedido de cliente",
              details: "Editar dados do pedido",
              simulation: "Simulador de compra",
              terms: "Condições comerciais",
              prepare: "Preparar encomenda real",
              quantity: "Atualizar quantidade",
              associate: "Associar artigo ao pedido",
              contact: "Contactar cliente",
              "supplier-email": "Email ao fornecedor",
              "supplier-confirm": "Confirmação do fornecedor",
              receive: "Receção de produtos",
            }[modal]
          }
          className="co-dialog"
          busy={busy}
          onRequestClose={() => setModal("")}
        >
          {feedback}
          <ManagedForm
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                if (modal === "order") await saveOrder();
                else if (modal === "details") {
                  const {
                    customer_name,
                    phone,
                    email,
                    preferred_channel,
                    contact_allowed,
                    due_date,
                    priority,
                    notes,
                    revision,
                  } = draft;
                  await mutate(
                    `/customer-orders/${draft.id}/details`,
                    {
                      customer_name,
                      phone,
                      email,
                      preferred_channel,
                      contact_allowed,
                      due_date: due_date || null,
                      priority,
                      notes,
                      revision,
                    },
                    "PUT",
                  );
                  await load();
                  setModal("");
                } else if (modal === "simulation") await saveSim();
                else if (modal === "terms") {
                  await mutate(
                    `/customer-orders/suppliers/${draft.supplier_id}/terms`,
                    { revision: draft.revision, terms: draft.terms },
                  );
                  await load();
                  setModal("");
                } else if (modal === "associate") {
                  await mutate(
                    `/customer-orders/lines/${draft.line_id}/associate`,
                    {
                      revision: draft.revision,
                      article_id: draft.article_id,
                      supplier_id: draft.supplier_id || null,
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "quantity") {
                  if (
                    draft.action !== "reserve" &&
                    !(await confirmAction({
                      title:
                        draft.action === "deliver"
                          ? "Confirmar entrega física?"
                          : draft.action === "adjust"
                            ? "Alterar quantidade pedida?"
                            : draft.action === "release"
                              ? "Libertar reserva?"
                              : "Cancelar quantidade?",
                      message: `${qty(draft.quantity)} unidade(s). ${draft.reason}`,
                      confirmLabel: "Confirmar",
                      tone: draft.action === "cancel" ? "danger" : "warning",
                    }))
                  )
                    return;
                  await mutate(
                    `/customer-orders/lines/${draft.line_id}/${draft.action}`,
                    {
                      revision: draft.revision,
                      quantity: draft.quantity,
                      warehouse_id: draft.warehouse_id || null,
                      reason: draft.reason,
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "prepare") {
                  if (
                    !(await confirmAction({
                      title: "Criar encomenda real?",
                      message:
                        "Será criada uma encomenda no circuito de compras. Não envia emails nem confirma o fornecedor.",
                      confirmLabel: "Criar encomenda",
                    }))
                  )
                    return;
                  await mutate(
                    `/customer-orders/simulations/${draft.simulation_id}/prepare`,
                    {
                      revision: draft.revision,
                      warehouse_id: draft.warehouse_id,
                      confirmed: true,
                      allow_below_minimum: draft.allow_below_minimum,
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "supplier-confirm") {
                  await mutate(
                    `/customer-orders/simulations/${draft.simulation_id}/supplier-confirm`,
                    {
                      revision: draft.revision,
                      quantities: draft.quantities,
                      note: draft.note,
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "receive") {
                  if (
                    !(await confirmAction({
                      title: "Confirmar receção física?",
                      message: draft.receipt_id
                        ? "A receção existente será distribuída pelos clientes sem repetir a entrada de stock."
                        : "As quantidades indicadas serão registadas no stock local e reservadas aos pedidos relacionados.",
                      confirmLabel: "Confirmar receção",
                    }))
                  )
                    return;
                  await mutate(
                    `/customer-orders/simulations/${draft.simulation_id}/receive`,
                    {
                      revision: draft.revision,
                      confirmed: true,
                      receipt_id: draft.receipt_id || null,
                      document: draft.document,
                      quantities: draft.quantities,
                      lots: draft.lots,
                      expiries: draft.expiries.map((v: string) => v || null),
                    },
                  );
                  await load();
                  setModal("");
                } else if (modal === "supplier-email") {
                  const result = await api(
                    `/procurement/${draft.procurement_id}/email-preview`,
                    {
                      revision: draft.revision,
                      to: draft.to,
                      subject: draft.subject,
                      message: draft.message,
                    },
                  );
                  setMail(result);
                  await load();
                  setDraft({ ...draft, revision: draft.revision + 1 });
                } else if (modal === "contact") {
                  const result = await mutate(
                    `/customer-orders/${draft.order_id}/contact`,
                    {
                      revision: draft.revision,
                      channel: draft.channel,
                      subject: draft.subject,
                      message: draft.message,
                    },
                  );
                  setContact(result);
                  setMail(result.email);
                  await load();
                  setDraft({ ...draft, revision: draft.revision + 1 });
                }
              });
            }}
          >
            <fieldset disabled={busy || !!mail || !!contact}>
              {modal === "details" && (
                <>
                  <h2>Editar dados do pedido</h2>
                  <div className="co-grid">
                    {[
                      ["customer_name", "Nome do cliente", "text"],
                      ["phone", "Telefone (com indicativo)", "tel"],
                      ["email", "Email", "email"],
                      ["due_date", "Entrega pretendida", "date"],
                    ].map(([key, label, type]) => (
                      <Field key={key} label={label}>
                        <input
                          required={key === "customer_name"}
                          type={type}
                          value={draft[key] || ""}
                          onChange={(e) =>
                            setDraft({ ...draft, [key]: e.target.value })
                          }
                        />
                      </Field>
                    ))}
                    <Field label="Canal preferido">
                      <select
                        value={draft.preferred_channel}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            preferred_channel: e.target.value,
                          })
                        }
                      >
                        {[
                          ["none", "Sem contacto"],
                          ["email", "Email"],
                          ["whatsapp", "WhatsApp"],
                          ["phone", "Telefone"],
                        ].map(([id, label]) => (
                          <option key={id} value={id}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Prioridade">
                      <select
                        value={draft.priority}
                        onChange={(e) =>
                          setDraft({ ...draft, priority: e.target.value })
                        }
                      >
                        <option value="normal">Normal</option>
                        <option value="urgent">Urgente</option>
                      </select>
                    </Field>
                  </div>
                  <label className="doc-check">
                    <input
                      type="checkbox"
                      checked={draft.contact_allowed}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          contact_allowed: e.target.checked,
                        })
                      }
                    />
                    Cliente autorizou contacto sobre o pedido
                  </label>
                  <Field label="Observações">
                    <textarea
                      value={draft.notes}
                      onChange={(e) =>
                        setDraft({ ...draft, notes: e.target.value })
                      }
                    />
                  </Field>
                </>
              )}
              {modal === "order" && (
                <>
                  <h2>Novo pedido de cliente</h2>
                  <div className="co-grid">
                    <Field label="Estabelecimento">
                      <select
                        required
                        value={draft.store_id}
                        onChange={(e) =>
                          setDraft({ ...draft, store_id: e.target.value })
                        }
                      >
                        {stores.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Cliente existente (opcional)">
                      <CustomerPicker
                        customers={catalog.customers}
                        value={draft.customer_id}
                        onPick={(c) => {
                          setDraft({
                            ...draft,
                            customer_id: c?.id || "",
                            ...(c
                              ? {
                                  customer_name: c.name,
                                  phone: c.phone,
                                  email: c.email,
                                }
                              : {}),
                          });
                        }}
                      />
                    </Field>
                    {[
                      ["customer_name", "Nome do cliente", "text"],
                      ["phone", "Telefone (com indicativo)", "tel"],
                      ["email", "Email", "email"],
                      [
                        "requested_at",
                        "Data e hora do pedido",
                        "datetime-local",
                      ],
                      ["due_date", "Entrega pretendida", "date"],
                    ].map(([key, label, type]) => (
                      <Field key={key} label={label}>
                        <input
                          type={type}
                          required={
                            key === "customer_name" || key === "requested_at"
                          }
                          value={draft[key]}
                          onChange={(e) =>
                            setDraft({ ...draft, [key]: e.target.value })
                          }
                        />
                      </Field>
                    ))}
                    <Field label="Canal preferido">
                      <select
                        value={draft.preferred_channel}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            preferred_channel: e.target.value,
                          })
                        }
                      >
                        <option value="none">Sem contacto autorizado</option>
                        <option value="email">Email</option>
                        <option value="whatsapp">WhatsApp</option>
                        <option value="phone">Telefone</option>
                      </select>
                    </Field>
                    <Field label="Prioridade">
                      <select
                        value={draft.priority}
                        onChange={(e) =>
                          setDraft({ ...draft, priority: e.target.value })
                        }
                      >
                        <option value="normal">Normal</option>
                        <option value="urgent">Urgente</option>
                      </select>
                    </Field>
                  </div>
                  <label className="doc-check">
                    <input
                      type="checkbox"
                      checked={draft.contact_allowed}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          contact_allowed: e.target.checked,
                        })
                      }
                    />
                    Cliente autorizou contactos sobre este pedido pelo canal
                    escolhido
                  </label>
                  <Field label="Observações do pedido">
                    <textarea
                      value={draft.notes}
                      onChange={(e) =>
                        setDraft({ ...draft, notes: e.target.value })
                      }
                    />
                  </Field>
                  {draft.lines.map((l: Row, i: number) => (
                    <fieldset className="co-product" key={l.key}>
                      <legend>Produto {i + 1}</legend>
                      <Field label="Artigo existente">
                        <ArticlePicker
                          articles={catalog.articles}
                          value={l.article_id}
                          onPick={(a) =>
                            editLine(
                              i,
                              a
                                ? {
                                    article_id: a.id,
                                    description: a.name,
                                    supplier_id: a.supplier_id || "",
                                    brand: a.brand || "",
                                    reference: a.reference || "",
                                    ean: a.barcodes?.[0] || "",
                                    sale_price_cents: a.sale_net
                                      ? Math.round(Number(a.sale_net) * 100)
                                      : null,
                                  }
                                : { article_id: "" },
                            )
                          }
                        />
                      </Field>
                      {!l.article_id && (
                        <p className="footnote">
                          Produto provisório: preencha a descrição e os dados
                          conhecidos. Não cria um artigo no ERP.
                        </p>
                      )}
                      <div className="co-grid">
                        {[
                          ["description", "Descrição"],
                          ["brand", "Marca"],
                          ["reference", "Referência"],
                          ["ean", "EAN"],
                        ].map(([key, label]) => (
                          <Field key={key} label={label}>
                            <input
                              required={key === "description"}
                              value={l[key]}
                              onChange={(e) =>
                                editLine(i, { [key]: e.target.value })
                              }
                            />
                          </Field>
                        ))}
                        <Field label="Quantidade pedida">
                          <input
                            required
                            type="number"
                            min="0.000001"
                            step="0.000001"
                            value={l.quantity}
                            onChange={(e) =>
                              editLine(i, { quantity: e.target.value })
                            }
                          />
                        </Field>
                        <Field label="Preço de venda sem IVA (quando conhecido)">
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={
                              l.sale_price_cents === null
                                ? ""
                                : l.sale_price_cents / 100
                            }
                            onChange={(e) =>
                              editLine(i, {
                                sale_price_cents:
                                  e.target.value === ""
                                    ? null
                                    : Math.round(Number(e.target.value) * 100),
                              })
                            }
                          />
                        </Field>
                        <Field label="Fornecedor preferencial">
                          <select
                            value={l.supplier_id}
                            onChange={(e) =>
                              editLine(i, { supplier_id: e.target.value })
                            }
                          >
                            <option value="">Por identificar</option>
                            {catalog.suppliers.map((s: Row) => (
                              <option key={s.id} value={s.id}>
                                {s.name}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field label="Notas do produto">
                          <input
                            value={l.notes}
                            onChange={(e) =>
                              editLine(i, { notes: e.target.value })
                            }
                          />
                        </Field>
                      </div>
                      <ActionButton
                        type="button"
                        disabled={draft.lines.length === 1}
                        onClick={() =>
                          setDraft({
                            ...draft,
                            lines: draft.lines.filter(
                              (_: Row, n: number) => n !== i,
                            ),
                          })
                        }
                      >
                        Remover produto
                      </ActionButton>
                    </fieldset>
                  ))}
                  <ActionButton
                    type="button"
                    onClick={() =>
                      setDraft({
                        ...draft,
                        lines: [...draft.lines, blankLine()],
                      })
                    }
                  >
                    Adicionar produto
                  </ActionButton>
                  <label className="doc-check">
                    <input
                      type="checkbox"
                      checked={!!draft.allow_duplicate}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          allow_duplicate: e.target.checked,
                        })
                      }
                    />
                    Confirmo a criação de outro pedido igual, caso já exista um
                    em aberto
                  </label>
                </>
              )}
              {(modal === "simulation" || modal === "terms") && (
                <>
                  <h2>
                    {modal === "terms"
                      ? "Condições comerciais do fornecedor"
                      : "Simulador de compra"}
                  </h2>
                  {modal === "simulation" && (
                    <>
                      <div className="co-grid">
                        <Field label="Estabelecimento">
                          <select
                            required
                            disabled={!!draft.id}
                            value={draft.store_id}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                store_id: e.target.value,
                                lines: [],
                              })
                            }
                          >
                            {stores.map((s) => (
                              <option key={s.id} value={s.id}>
                                {s.name}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field label="Fornecedor">
                          <select
                            required
                            disabled={!!draft.id}
                            value={draft.supplier_id}
                            onChange={(e) => {
                              const s = catalog.suppliers.find(
                                (s: Row) => s.id === e.target.value,
                              );
                              setDraft({
                                ...draft,
                                supplier_id: e.target.value,
                                terms: structuredClone(
                                  s?.terms || initialTerms(),
                                ),
                                lines: [],
                              });
                              setPreview(null);
                            }}
                          >
                            <option value="">Selecionar fornecedor</option>
                            {catalog.suppliers.map((s: Row) => (
                              <option key={s.id} value={s.id}>
                                {s.name}
                              </option>
                            ))}
                          </select>
                        </Field>
                      </div>
                      <details open>
                        <summary>Pedidos pendentes deste fornecedor</summary>
                        {pending
                          .filter(
                            (l) =>
                              l.supplier_id === draft.supplier_id &&
                              l.order.store_id === draft.store_id &&
                              !draft.lines.some((r: Row) =>
                                r.allocations.some(
                                  (a: Row) => a.line_id === l.id,
                                ),
                              ),
                          )
                          .map((l) => (
                            <label key={l.id} className="doc-check">
                              <input
                                type="checkbox"
                                checked={checks.includes(l.id)}
                                onChange={(e) =>
                                  setChecks(
                                    e.target.checked
                                      ? [...checks, l.id]
                                      : checks.filter((id) => id !== l.id),
                                  )
                                }
                              />
                              {l.order.customer_name} · {l.description} ·{" "}
                              {qty(l.quantities.to_order)}
                            </label>
                          ))}
                        <ActionButton
                          type="button"
                          disabled={!checks.length}
                          onClick={addPending}
                        >
                          Adicionar pedidos selecionados
                        </ActionButton>
                      </details>
                      {draft.lines.map((l: Row, i: number) => (
                        <fieldset key={i} className="co-product">
                          <legend>Produto de compra {i + 1}</legend>
                          <Field label="Artigo de reposição">
                            <ArticlePicker
                              articles={catalog.articles}
                              value={l.article_id}
                              onPick={(a) => {
                                if (!l.allocations.length)
                                  editLine(
                                    i,
                                    a
                                      ? {
                                          article_id: a.id,
                                          description: a.name,
                                          unit_cost:
                                            draft.terms.prices?.[a.id] ??
                                            a.last_cost ??
                                            "0",
                                          vat: a.vat_rate ?? null,
                                        }
                                      : { article_id: null },
                                  );
                              }}
                            />
                          </Field>
                          <div className="co-grid">
                            <Field label="Descrição">
                              <input
                                required
                                value={l.description}
                                onChange={(e) =>
                                  editLine(i, { description: e.target.value })
                                }
                              />
                            </Field>
                            <Field label="Quantidade de compra">
                              <input
                                required
                                type="number"
                                min="0.000001"
                                step="0.000001"
                                value={l.quantity}
                                onChange={(e) => {
                                  let remaining = Number(e.target.value);
                                  const allocations = l.allocations
                                    .map((a: Row) => {
                                      const q = Math.min(
                                        remaining,
                                        Number(a.quantity),
                                      );
                                      remaining -= q;
                                      return {
                                        ...a,
                                        quantity: String(Number(q.toFixed(6))),
                                      };
                                    })
                                    .filter((a: Row) => Number(a.quantity) > 0);
                                  editLine(i, {
                                    quantity: e.target.value,
                                    allocations,
                                  });
                                }}
                              />
                            </Field>
                            <Field label="Preço de custo unitário">
                              <input
                                required
                                type="number"
                                min="0"
                                step="0.000001"
                                value={l.unit_cost}
                                onChange={(e) =>
                                  editLine(i, { unit_cost: e.target.value })
                                }
                              />
                            </Field>
                            <Field label="Desconto da linha (%) — vazio: fornecedor">
                              <input
                                type="number"
                                min="0"
                                max="100"
                                step="0.0001"
                                value={l.discount ?? ""}
                                onChange={(e) =>
                                  editLine(i, {
                                    discount:
                                      e.target.value === ""
                                        ? null
                                        : e.target.value,
                                  })
                                }
                              />
                            </Field>
                            <Field label="IVA da linha (%) — vazio: fornecedor">
                              <input
                                type="number"
                                min="0"
                                max="100"
                                step="0.001"
                                value={l.vat ?? ""}
                                onChange={(e) =>
                                  editLine(i, {
                                    vat:
                                      e.target.value === ""
                                        ? null
                                        : e.target.value,
                                  })
                                }
                              />
                            </Field>
                          </div>
                          <p>
                            Destinado a clientes:{" "}
                            {qty(
                              l.allocations.reduce(
                                (n: number, a: Row) => n + Number(a.quantity),
                                0,
                              ),
                            )}{" "}
                            · Reposição adicional:{" "}
                            {qty(
                              Number(l.quantity) -
                                l.allocations.reduce(
                                  (n: number, a: Row) => n + Number(a.quantity),
                                  0,
                                ),
                            )}
                          </p>
                          <ActionButton
                            type="button"
                            onClick={() => {
                              setDraft({
                                ...draft,
                                lines: draft.lines.filter(
                                  (_: Row, n: number) => n !== i,
                                ),
                              });
                              setPreview(null);
                            }}
                          >
                            Remover linha de compra
                          </ActionButton>
                        </fieldset>
                      ))}
                      <ActionButton
                        type="button"
                        onClick={() =>
                          setDraft({
                            ...draft,
                            lines: [
                              ...draft.lines,
                              {
                                article_id: null,
                                description: "",
                                quantity: "1",
                                unit_cost: "0",
                                discount: null,
                                vat: null,
                                allocations: [],
                              },
                            ],
                          })
                        }
                      >
                        Adicionar reposição de stock
                      </ActionButton>
                    </>
                  )}
                  <TermsFields
                    articles={catalog.articles}
                    value={draft.terms}
                    change={(terms) => {
                      setDraft({ ...draft, terms });
                      setPreview(null);
                    }}
                  />
                  {modal === "simulation" && (
                    <>
                      <Field label="Observações da compra">
                        <textarea
                          value={draft.notes}
                          onChange={(e) =>
                            setDraft({ ...draft, notes: e.target.value })
                          }
                        />
                      </Field>
                      <ActionButton
                        type="button"
                        disabled={!draft.lines.length || !draft.supplier_id}
                        onClick={() =>
                          void run(async () =>
                            setPreview(
                              await api(
                                "/customer-orders/simulations/preview",
                                {
                                  store_id: draft.store_id,
                                  supplier_id: draft.supplier_id,
                                  terms: draft.terms,
                                  lines: draft.lines,
                                  notes: draft.notes,
                                  revision: draft.revision || 0,
                                  request_key: crypto.randomUUID(),
                                },
                                { preserveDraft: true },
                              ),
                            ),
                          )
                        }
                      >
                        Calcular simulação
                      </ActionButton>
                      {preview && (
                        <SimulationTotals
                          totals={preview}
                          terms={draft.terms}
                        />
                      )}
                    </>
                  )}
                </>
              )}
              {modal === "associate" && (
                <>
                  <h2>Associar produto e fornecedor</h2>
                  <Field label="Artigo real">
                    <ArticlePicker
                      articles={catalog.articles}
                      value={draft.article_id}
                      onPick={(a) =>
                        setDraft({ ...draft, article_id: a?.id || "" })
                      }
                    />
                  </Field>
                  <Field label="Fornecedor do produto">
                    <select
                      value={draft.supplier_id}
                      onChange={(e) =>
                        setDraft({ ...draft, supplier_id: e.target.value })
                      }
                    >
                      <option value="">Por identificar</option>
                      {catalog.suppliers.map((s: Row) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <p>
                    O registo provisório e o histórico são preservados. Não cria
                    artigos no ERP.
                  </p>
                </>
              )}
              {modal === "quantity" && (
                <>
                  <h2>
                    {draft.action === "reserve"
                      ? "Reservar unidades disponíveis"
                      : draft.action === "deliver"
                        ? "Registar entrega ao cliente"
                        : draft.action === "adjust"
                          ? "Alterar quantidade pedida"
                          : draft.action === "release"
                            ? "Libertar reserva"
                            : "Cancelar quantidade pendente"}
                  </h2>
                  <Field label="Quantidade">
                    <input
                      required
                      type="number"
                      min="0.000001"
                      step="0.000001"
                      value={draft.quantity}
                      onChange={(e) =>
                        setDraft({ ...draft, quantity: e.target.value })
                      }
                    />
                  </Field>
                  {draft.action === "reserve" && (
                    <Field label="Armazém">
                      <select
                        required
                        value={draft.warehouse_id}
                        onChange={(e) =>
                          setDraft({ ...draft, warehouse_id: e.target.value })
                        }
                      >
                        {warehouses(
                          orders.find((o) => o.id === draft.order_id)?.store_id,
                        ).map((w: Row) => (
                          <option key={w.id} value={w.id}>
                            {w.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                  )}
                  <Field label="Motivo / referência">
                    <textarea
                      required={draft.action !== "reserve"}
                      minLength={3}
                      value={draft.reason}
                      onChange={(e) =>
                        setDraft({ ...draft, reason: e.target.value })
                      }
                    />
                  </Field>
                  <p>
                    {draft.action === "deliver"
                      ? "A entrega física local dá baixa de stock. Não emite fatura nem cria recebimentos financeiros."
                      : "Os produtos já entregues e o histórico mantêm-se."}
                  </p>
                </>
              )}
              {modal === "prepare" && (
                <>
                  <h2>Preparar encomenda real</h2>
                  <Field label="Armazém de destino">
                    <select
                      required
                      value={draft.warehouse_id}
                      onChange={(e) =>
                        setDraft({ ...draft, warehouse_id: e.target.value })
                      }
                    >
                      {warehouses(
                        simulations.find((s) => s.id === draft.simulation_id)
                          ?.store_id,
                      ).map((w: Row) => (
                        <option key={w.id} value={w.id}>
                          {w.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <label className="doc-check">
                    <input
                      type="checkbox"
                      checked={draft.allow_below_minimum}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          allow_below_minimum: e.target.checked,
                        })
                      }
                    />
                    Autorizo preparar abaixo do mínimo, se aplicável
                  </label>
                  <p>
                    Reutiliza o circuito de compras. Não envia email nem
                    confirma a disponibilidade do fornecedor.
                  </p>
                </>
              )}
              {(modal === "supplier-confirm" || modal === "receive") && (
                <>
                  <h2>
                    {modal === "receive"
                      ? "Receber produtos"
                      : "Confirmação do fornecedor"}
                  </h2>
                  {modal === "receive" && (
                    <>
                      <Field label="Associar receção já confirmada">
                        <select
                          value={draft.receipt_id}
                          onChange={(e) =>
                            setDraft({ ...draft, receipt_id: e.target.value })
                          }
                        >
                          <option value="">Registar nova receção física</option>
                          {receipts
                            .filter((r) =>
                              r.lines.some(
                                (l: Row) =>
                                  l.source_id ===
                                  simulations.find(
                                    (s) => s.id === draft.simulation_id,
                                  )?.procurement_id,
                              ),
                            )
                            .map((r) => (
                              <option key={r.id} value={r.id}>
                                {r.document}
                              </option>
                            ))}
                        </select>
                      </Field>
                      {!draft.receipt_id && (
                        <Field label="Documento de compra ou guia">
                          <input
                            required
                            value={draft.document}
                            onChange={(e) =>
                              setDraft({ ...draft, document: e.target.value })
                            }
                          />
                        </Field>
                      )}
                    </>
                  )}
                  {!draft.receipt_id &&
                    simulations
                      .find((s) => s.id === draft.simulation_id)
                      ?.lines.map((l: Row, i: number) => (
                        <section className="co-product" key={i}>
                          <Field
                            label={`${l.description} — quantidade ${modal === "receive" ? "recebida" : "confirmada"}`}
                          >
                            <input
                              required
                              type="number"
                              min="0"
                              step="0.000001"
                              value={draft.quantities[i]}
                              onChange={(e) =>
                                setDraft({
                                  ...draft,
                                  quantities: draft.quantities.map(
                                    (q: string, n: number) =>
                                      n === i ? e.target.value : q,
                                  ),
                                })
                              }
                            />
                          </Field>
                          {modal === "receive" && (
                            <div className="co-grid">
                              <Field label="Lote (quando aplicável)">
                                <input
                                  value={draft.lots[i]}
                                  onChange={(e) =>
                                    setDraft({
                                      ...draft,
                                      lots: draft.lots.map(
                                        (v: string, n: number) =>
                                          n === i ? e.target.value : v,
                                      ),
                                    })
                                  }
                                />
                              </Field>
                              <Field label="Validade">
                                <input
                                  type="date"
                                  value={draft.expiries[i]}
                                  onChange={(e) =>
                                    setDraft({
                                      ...draft,
                                      expiries: draft.expiries.map(
                                        (v: string, n: number) =>
                                          n === i ? e.target.value : v,
                                      ),
                                    })
                                  }
                                />
                              </Field>
                            </div>
                          )}
                        </section>
                      ))}
                  {modal === "supplier-confirm" && (
                    <Field label="Referência da confirmação">
                      <textarea
                        required
                        minLength={3}
                        value={draft.note}
                        onChange={(e) =>
                          setDraft({ ...draft, note: e.target.value })
                        }
                      />
                    </Field>
                  )}
                  <p>
                    {modal === "receive"
                      ? "Registe apenas quantidades efetivamente recebidas. Uma receção existente não gera outro movimento de stock."
                      : "A confirmação comercial não regista receção nem torna o produto disponível."}
                  </p>
                </>
              )}
              {(modal === "contact" || modal === "supplier-email") && (
                <>
                  <h2>
                    {modal === "contact"
                      ? "Preparar contacto ao cliente"
                      : "Pré-visualizar email ao fornecedor"}
                  </h2>
                  {modal === "contact" ? (
                    <Field label="Canal de contacto">
                      <select
                        value={draft.channel}
                        onChange={(e) =>
                          setDraft({ ...draft, channel: e.target.value })
                        }
                      >
                        <option value="email">Email</option>
                        <option value="whatsapp">WhatsApp</option>
                        <option value="phone">Telefone</option>
                      </select>
                    </Field>
                  ) : (
                    <Field label="Email do fornecedor">
                      <input
                        required
                        type="email"
                        value={draft.to}
                        onChange={(e) =>
                          setDraft({ ...draft, to: e.target.value })
                        }
                      />
                    </Field>
                  )}
                  <Field label="Assunto">
                    <input
                      required
                      value={draft.subject}
                      onChange={(e) =>
                        setDraft({ ...draft, subject: e.target.value })
                      }
                    />
                  </Field>
                  <Field label="Mensagem preparada">
                    <textarea
                      required
                      rows={8}
                      value={draft.message}
                      onChange={(e) =>
                        setDraft({ ...draft, message: e.target.value })
                      }
                    />
                  </Field>
                  <p>
                    A preparação não envia mensagens. O envio SMTP exige
                    confirmação; abrir WhatsApp não confirma o envio.
                  </p>
                </>
              )}
              <div className="workspace-savebar">
                <ActionButton
                  className="primary"
                  type="submit"
                  disabled={
                    busy ||
                    (modal === "simulation" && !draft.lines.length) ||
                    (modal === "associate" && !draft.article_id)
                  }
                >
                  {["contact", "supplier-email"].includes(modal)
                    ? "Preparar pré-visualização"
                    : modal === "receive"
                      ? "Confirmar receção"
                      : modal === "simulation"
                        ? "Guardar simulação"
                        : "Guardar"}
                </ActionButton>
              </div>
            </fieldset>
          </ManagedForm>
          {mail && (
            <section className="panel">
              <h3>Email preparado · {mail.status}</h3>
              <p>Destinatário: {mail.draft.to.join(", ")}</p>
              <p>{mail.draft.subject}</p>
              <pre className="co-message">{mail.draft.message}</pre>
              <p>
                {mail.manifest.files.length} anexo(s) ·{" "}
                {mail.manifest.plan?.messages || 1} mensagem(ns)
              </p>
              <ActionButton
                className="primary"
                disabled={
                  busy ||
                  ["accepted", "received", "sending"].includes(mail.status)
                }
                onClick={() =>
                  void run(async () => {
                    if (
                      !(await confirmAction({
                        title: "Enviar este email?",
                        message: `Destinatário: ${mail.draft.to.join(", ")}. O envio será efetuado com a configuração SMTP guardada.`,
                        confirmLabel: "Enviar email",
                      }))
                    )
                      return;
                    const path =
                      modal === "contact"
                        ? `/customer-orders/${draft.order_id}/email/${mail.id}/send`
                        : `/procurement/${draft.procurement_id}/email-send/${mail.id}`;
                    const result = await mutate(path, {
                      revision: mail.revision,
                      confirmed: true,
                    });
                    setMail(result);
                    await load();
                  })
                }
              >
                <Send size={16} />
                Confirmar envio por email
              </ActionButton>
            </section>
          )}
          {contact && !mail && (
            <section className="panel">
              <h3>
                Contacto{" "}
                {contact.result === "confirmed"
                  ? "confirmado manualmente"
                  : "preparado"}
              </h3>
              <pre className="co-message">{contact.message}</pre>
              {contact.url && (
                <ActionLink
                  href={contact.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Abrir WhatsApp com a mensagem
                </ActionLink>
              )}
              <p>A abertura da ligação não confirma o envio.</p>
              <ActionButton
                disabled={busy || contact.result === "confirmed"}
                onClick={() =>
                  void run(async () => {
                    if (
                      !(await confirmAction({
                        title: "Confirmar contacto realizado?",
                        message:
                          "Confirme apenas se enviou a mensagem ou contactou efetivamente o cliente.",
                        confirmLabel: "Confirmar contacto",
                      }))
                    )
                      return;
                    const result = await mutate(
                      `/customer-orders/${draft.order_id}/contact`,
                      {
                        revision: draft.revision,
                        channel: draft.channel,
                        subject: draft.subject,
                        message: draft.message,
                        result: "confirmed",
                        prepared_event_id: contact.event_id,
                      },
                    );
                    setContact(result);
                    await load();
                    setDraft({ ...draft, revision: draft.revision + 1 });
                  })
                }
              >
                Confirmar contacto realizado
              </ActionButton>
            </section>
          )}
        </AppDialog>
      )}
    </section>
  );
}
