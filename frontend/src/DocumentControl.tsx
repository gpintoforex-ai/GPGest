import { useEffect, useRef, useState } from "react";
import { api } from "./App";
import Purchases from "./Purchases";
import GoogleInvoiceInbox from "./GoogleInvoiceInbox";
import DocumentAssistant from "./DocumentAssistant";
import DriveAutoMatch from "./DriveAutoMatch";
import InvoiceSidePreview from "./InvoiceSidePreview";
import { ScanButton } from "./DocumentScanner";
import ErpAssistantStep from "./ErpAssistantStep";
import InvoiceWorkflow from "./InvoiceWorkflow";
import { MetricCard, StatusBadge } from "./components/BusinessUI";
import AssistantIdentity, { ASSISTANTS } from "./components/AssistantIdentity";
import { Bot } from "lucide-react";
import { formatEuro } from "./utils/format";
import "./DocumentControl.css";

type Row = Record<string, any>;
const euro = formatEuro;
const day = (value: string) =>
  value?.slice(0, 10).split("-").reverse().join("/") || "—";
const today = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Lisbon",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
const months = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];
const monthRange = (year: string, month: string) => {
  const prefix = `${year}-${month.padStart(2, "0")}`;
  const lastDay = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
  return { from: `${prefix}-01`, to: `${prefix}-${lastDay}` };
};
const labels: Record<string, string> = {
  por_verificar: "Por verificar",
  fisico: "Documento físico disponível",
  digital: "Documento digital disponível",
  nao_localizado: "Documento não localizado",
  solicitado: "Solicitado ao fornecedor",
  sem_anexo: "Sem anexo",
  com_anexo: "Com anexo",
  confirmada: "Confirmada",
  sugerida: "Sugerida — por rever",
  sem_correspondencia: "Sem correspondência",
  divergencias: "Com divergências",
  por_tratar: "Por tratar",
  preparar_erp: "Preparar despesa no ERP",
  contabilidade: "Diretamente para a contabilidade",
  nao_aplicavel: "Não aplicável",
  por_preparar: "Por preparar",
  pronto: "Pronto para entrega",
  entregue: "Entregue",
  confirmado: "Confirmado pela contabilidade",
  por_pagar: "Por pagar",
  pago: "Pago",
  parcial: "Parcial",
  pendente: "Por reconciliar",
  reconciliado: "Reconciliado",
  preparado: "Proposta preparada",
  revisto: "Proposta revista",
  exportado: "Proposta exportada — lançamento por confirmar",
};
const option = (values: string[]) =>
  values.map((value) => (
    <option key={value} value={value}>
      {labels[value]}
    </option>
  ));
const cents = (value: string) => {
  const text = value.trim().replace(",", ".");
  if (!/^\d+(?:\.\d{1,2})?$/.test(text))
    throw new Error("Indique um valor em euros com até duas casas decimais.");
  return Math.round(Number(text) * 100);
};

export default function DocumentControl({
  stores,
  store,
  manager,
  admin,
  integration,
}: {
  stores: Row[];
  store: string;
  manager: boolean;
  admin: boolean;
  integration?: Row;
}) {
  const canReadErp = (integration?.capabilities?.read_documents ?? true) && integration?.resources?.includes("purchase_documents") !== false;
  const [rows, setRows] = useState<Row[]>([]),
    [catalog, setCatalog] = useState<Row[]>([]),
    [payments, setPayments] = useState<Row[]>([]),
    [sources, setSources] = useState<Row[]>([]),
    [banks, setBanks] = useState<Row[]>([]);
  const [tab, setTab] = useState("conferencia"),
    [tools, setTools] = useState(false);
  const [gmail, setGmail] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<{ key: string; ascending: boolean }>({ key: "document_date", ascending: false });
  const [detailPreview, setDetailPreview] = useState(true);
  const [assistant, setAssistant] = useState<Row | null>(null);
  const [assistantStage, setAssistantStage] = useState(1);
  const [sideDocument, setSideDocument] = useState<Row | null>(null);
  const [listPreview, setListPreview] = useState<Row | null>(null);
  const [assistantDocId, setAssistantDocId] = useState("");
  const [assistantFrom, setAssistantFrom] = useState(today().slice(0, 8) + "01");
  const [assistantTo, setAssistantTo] = useState(today());
  const [filters, setFilters] = useState<Row>(() => ({
    ...monthRange(today().slice(0, 4), today().slice(5, 7)),
    search: "",
    availability: "",
    attachment_status: "",
    erp_status: "",
    treatment: "",
    accounting: "",
    payment_status: "",
    bank_status: "",
  }));
  const [selection, setSelection] = useState<string[]>([]),
    [page, setPage] = useState(1);
  const [modal, setModal] = useState(""),
    [selected, setSelected] = useState<Row | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState<Row | null>(null);
  const [erpId, setErpId] = useState(""),
    [reason, setReason] = useState(""),
    [draft, setDraft] = useState<Row | null>(null),
    [review, setReview] = useState(false);
  const [payment, setPayment] = useState<Row | null>(null),
    [distribution, setDistribution] = useState<Record<string, string>>({});
  const [batchChange, setBatchChange] = useState<Row>({
      availability: "fisico",
    }),
    [batchPreview, setBatchPreview] = useState<Row | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const selectedStore = store || stores[0]?.id || "";
  const filterYear = (filters.from || today()).slice(0, 4);
  const rangeMonth = (filters.from || today()).slice(5, 7);
  const range = monthRange(filterYear, rangeMonth);
  const filterMonth = filters.from === range.from && filters.to === range.to ? rangeMonth : "";
  const currentYear = Number(today().slice(0, 4));
  const years = [...new Set([
    ...Array.from({ length: 11 }, (_, i) => String(currentYear - 5 + i)),
    ...rows.map(row => String(row.document_date || "").slice(0, 4)).filter(year => /^\d{4}$/.test(year)),
    filterYear,
  ])].sort((a, b) => Number(b) - Number(a));
  async function load() {
    const [documents, erp, identified, recon] = await Promise.all([
      api("/documents"),
      api("/documents/erp-catalog"),
      api("/document-payments"),
      api("/reconciliation"),
    ]);
    setRows(documents);
    setCatalog(erp);
    setPayments(identified);
    setBanks(recon.banks);
    setSources(
      recon.internals.filter((r: Row) =>
        ["movement", "wintouch"].includes(r.source),
      ),
    );
  }
  useEffect(() => {
    load().catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    setModal("");
    setSelection([]);
    setSelected(null);
  }, [store]);
  useEffect(() => {
    setPage(1);
  }, [filters, tab, store]);
  useEffect(() => {
    if (modal && !dialog.current?.open) dialog.current?.showModal();
  }, [modal]);
  function close() {
    if (!busy) {
      setModal("");
      setError("");
    }
  }
  async function act(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Não foi possível concluir.");
    } finally {
      setBusy(false);
    }
  }
  async function refreshDetail() {
    await load();
    if (selected) setSelected(await api(`/documents/${selected.id}/detail`));
  }
  async function openDoc(row: Row) {
    await act(async () => {
      setSelected(await api(`/documents/${row.id}/detail`));
      setErpId("");
      setReason("");
      setDraft(null);
      setReview(false);
      setDetailPreview(true);
      setModal("detail");
    });
  }
  async function download(path: string, body: Row, name: string) {
    const response = await fetch(`/api${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(
        typeof result.detail === "string"
          ? result.detail
          : "Falha na exportação.",
      );
    }
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }
  async function importFile(confirm: boolean, insideAssistant = false) {
    if (!file) return;
    await act(async () => {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(
        `/api/purchases/import/${selectedStore}?confirm=${confirm}`,
        { method: "POST", body, credentials: "same-origin" },
      );
      const result = await response.json();
      if (!response.ok)
        throw new Error(result.detail || "Falha na importação.");
      if (!confirm) setPreview(result);
      else {
        const dates = result.rows.map((r: Row) => r.document_date).sort();
        await load();
        setFilters({
          ...filters,
          from: dates[0] || "",
          to: dates.at(-1) || "",
        });
        if (insideAssistant) { setAssistantStage(2); setModal("assistant"); }
        else setModal("");
        setSelection([]);
        setNotice(
          `${result.new_count} documentos importados; ${result.duplicate_count} duplicados ignorados.`,
        );
      }
    });
  }
  function prepareDraft(source: Row | null = selected) {
    if (!source) return;
    setReview(false);
    setDraft(
      source.draft?.payload || {
        supplier_nif: source.supplier_nif,
        supplier_name: source.supplier_name,
        document: source.document,
        document_type: source.document_type,
        document_date: source.document_date,
        due_date: source.document_date,
        total: (Math.abs(source.amount_cents) / 100).toFixed(2),
        reason: "",
        lines: [
          {
            description: source.document,
            category: "",
            base: (
              Math.abs(
                source.base_cents ??
                  Math.abs(source.amount_cents) -
                    Math.abs(source.tax_cents),
              ) / 100
            ).toFixed(2),
            rate: "",
            tax: (Math.abs(source.tax_cents) / 100).toFixed(2),
          },
        ],
      },
    );
  }
  function openPayment(doc?: Row, standalone = false) {
    const docs = standalone
      ? []
      : doc
        ? [doc]
        : rows.filter((r) => selection.includes(r.id));
    if (
      docs.some(
        (row) =>
          row.store_id !== docs[0].store_id ||
          Math.sign(row.amount_cents) !== Math.sign(docs[0].amount_cents) ||
          row.origin === "efatura_testes",
      )
    ) {
      setError(
        "Selecione documentos da mesma loja e sentido. Documentos do ambiente de testes e-Fatura não podem receber pagamentos reais.",
      );
      return;
    }
    const amount = docs.reduce((n, r) => n + r.remaining_cents, 0);
    setDistribution(
      Object.fromEntries(
        docs.map((r) => [r.id, (r.remaining_cents / 100).toFixed(2)]),
      ),
    );
    setPayment({
      store_id: docs[0]?.store_id || selectedStore,
      payment_date: today(),
      amount: (amount / 100).toFixed(2),
      direction: docs[0]?.amount_cents < 0 ? 1 : -1,
      entity: doc?.supplier_name || "",
      reference: "",
      method: "transferencia",
      kind: "pagamento",
      source: "manual",
      source_id: null,
      existing_id: "",
      request_key: crypto.randomUUID(),
    });
    setModal("payment");
    setError("");
  }
  const storeRows = rows.filter((r) => !store || r.store_id === store);
  const filtered = storeRows.filter(
    (r) =>
      (!filters.from || r.document_date >= filters.from) &&
      (!filters.to || r.document_date <= filters.to) &&
      `${r.supplier_name} ${r.supplier_nif} ${r.document}`
        .toLocaleLowerCase("pt-PT")
        .includes(filters.search.toLocaleLowerCase("pt-PT")) &&
      Object.entries(filters)
        .filter(([k]) => !["from", "to", "search"].includes(k))
        .every(([k, v]) => !v || r[k] === v) &&
      (tab !== "despesas" || r.erp_status !== "confirmada") &&
      (tab !== "contabilidade" || r.treatment !== "nao_aplicavel"),
  ).sort((a, b) => {
    const result = sort.key === "amount_cents"
      ? a.amount_cents - b.amount_cents
      : String(a[sort.key] || "").localeCompare(String(b[sort.key] || ""), "pt-PT", { numeric: true });
    return (sort.ascending ? result : -result) || String(a.id).localeCompare(String(b.id));
  });
  function sortBy(key: string) {
    setSort({ key, ascending: sort.key === key ? !sort.ascending : true });
    setPage(1);
  }
  const pages = Math.max(1, Math.ceil(filtered.length / 20)),
    currentPage = Math.min(page, pages);
  const affected = rows.filter((r) => selection.includes(r.id));
  const selectedLabels = affected
    .map((r) => `${r.supplier_name}: ${r.document}`)
    .join(" · ");
  if (gmail)
    return <><button onClick={() => { setGmail(false); load().catch(e => setError(e.message)); }}>Voltar à conferência documental</button><GoogleInvoiceInbox /></>;
  if (tools)
    return (
      <>
        <button
          onClick={() => {
            setTools(false);
            load().catch((e) => setError(e.message));
          }}
        >
          Voltar à conferência documental
        </button>
        <Purchases
          stores={stores}
          store={store}
          manager={manager}
          admin={admin}
          embedded
        />
      </>
    );
  return (
    <>
      <div className="page-intro doc-dashboard-header">
        <div>
          <h1>Conferência documental</h1>
          <p>
            Acompanhe as faturas, valide correspondências e prepare a entrega à contabilidade.
          </p>
        </div>
        {manager && (
          <div className="actions">
            <button className="primary" disabled={busy || !stores.length}
              onClick={() => { setError(""); setFile(null); setPreview(null); setAssistantDocId(selection.length === 1 ? selection[0] : ""); setAssistantStage(1); setModal("assistant"); }}>
              <Bot size={16} aria-hidden="true" />{ASSISTANTS.clara.name} · Verificar documentos
            </button>
            <button
              disabled={busy || !selectedStore}
              onClick={() => {
                setFile(null);
                setPreview(null);
                setError("");
                setModal("import");
              }}
            >
              Importar e-Fatura
            </button>
            <button onClick={() => setModal("new")}>Registar documento</button><ScanButton store={selectedStore} onSaved={load} />
            {admin && canReadErp && (
              <button onClick={() => setModal("sync")}>
                Consultar compras no ERP
              </button>
            )}
            <button onClick={() => openPayment(undefined, true)}>
              Identificar pagamento ou comissão
            </button>
            <button onClick={() => setTools(true)}>Acessos e-Fatura</button>
            <button onClick={() => setGmail(true)}>Anexos Gmail / Drive</button>
          </div>
        )}
      </div>
      <AssistantIdentity assistant="clara" />
      <div className="doc-overview-grid">
        <MetricCard label="Documentos importados" value={loading ? "—" : storeRows.filter(r => Boolean(r.import_id)).length} description="Documentos associados a uma importação" />
        <MetricCard label="Relacionados com o ERP" value={loading ? "—" : storeRows.filter(r => r.erp_status === "confirmada").length} description="Correspondências confirmadas" tone="success" />
        <MetricCard label="Não encontrados no ERP" value={loading ? "—" : storeRows.filter(r => r.erp_status === "sem_correspondencia").length} description="Sem correspondência nos dados consultados" tone="warning" />
        <MetricCard label="Sem anexo" value={loading ? "—" : storeRows.filter(r => r.attachment_status === "sem_anexo").length} description="Cópia digital ainda por associar" tone="warning" />
        <MetricCard label="Com divergências" value={loading ? "—" : storeRows.filter(r => r.erp_status === "divergencias").length} description="Diferenças que exigem revisão" tone="danger" />
        <MetricCard label="A aguardar aprovação" value={loading ? "—" : storeRows.filter(r => r.draft?.status === "preparado").length} description="Propostas preparadas por rever; sem aprovação formal" />
      </div>
      <details className="pending-breakdown">
      <summary>Outras pendências a acompanhar</summary>
      <div className="doc-dashboard-section-title"><h2>Pendências a acompanhar</h2><span>{store ? stores.find(s => s.id === store)?.name : "Todas as lojas"} · Indicadores da seleção de loja</span></div>
      {!catalog.some((row) => !store || row.store_id === store) && (
        <p>
          Sem documentos ERP disponíveis para conferência nesta seleção.
          Consulte o ERP para atualizar as correspondências.
        </p>
      )}
      <div className="doc-pending-grid">
        {[
          [
            "Não localizados",
            storeRows.filter((r) => r.availability === "nao_localizado").length,
          ],
          [
            "Papel por digitalizar",
            storeRows.filter(
              (r) => r.availability === "fisico" && !r.attachments.length,
            ).length,
          ],
          [
            "Sem correspondência ERP",
            storeRows.filter((r) => r.erp_status === "sem_correspondencia")
              .length,
          ],
          [
            "Divergências ERP",
            storeRows.filter((r) => r.erp_status === "divergencias").length,
          ],
          [
            "Despesas por tratar",
            storeRows.filter(
              (r) =>
                r.erp_status !== "confirmada" && r.treatment === "por_tratar",
            ).length,
          ],
          [
            "Por entregar",
            storeRows.filter(
              (r) =>
                ["por_preparar", "pronto"].includes(r.accounting) &&
                r.treatment !== "nao_aplicavel",
            ).length,
          ],
          [
            "Banco por reconciliar",
            banks.filter(
              (row) =>
                (!store || row.store_id === store) &&
                ["pendente", "parcial"].includes(row.status),
            ).length,
          ],
        ].map(([title, count]) => (
          <div className={`panel doc-pending-card ${Number(count) > 0 ? "has-pending" : "is-clear"}`} key={title}>
            <strong>{count}</strong>
            <p>{title}</p>
          </div>
        ))}
      </div>
      </details>
      <div className="actions doc-tabs" aria-label="Áreas da conferência documental">
        {[
          ["conferencia", "Conferência documental"],
          ["anexos", "Documentos e anexos"],
          ["despesas", "Despesas por tratar"],
          ["contabilidade", "Entrega à contabilidade"],
        ].map(([id, title]) => (
          <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>
            {title}
          </button>
        ))}
      </div>
      {error && !modal && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && <p className="doc-notice" role="status">{notice}</p>}
      {loading && <p role="status">A carregar documentos e estados de conferência…</p>}
      {error && !modal && <button onClick={() => { setLoading(true); setError(""); load().catch(e => setError(e.message)).finally(() => setLoading(false)); }}>Tentar novamente</button>}
      <section className="panel">
        <div className="doc-filters">
          <label>
            Mês
            <select aria-label="Mês" value={filterMonth} onChange={e => { if (e.target.value) setFilters({ ...filters, ...monthRange(filterYear, e.target.value) }); }}>
              <option value="" disabled>Período personalizado</option>
              {months.map((month, index) => <option key={month} value={String(index + 1).padStart(2, "0")}>{month}</option>)}
            </select>
          </label>
          <label>
            Ano
            <select aria-label="Ano" value={filterYear} onChange={e => setFilters({ ...filters, ...monthRange(e.target.value, filterMonth || rangeMonth) })}>
              {years.map(year => <option key={year} value={year}>{year}</option>)}
            </select>
          </label>
          <button onClick={() => setFilters({ ...filters, ...monthRange(today().slice(0, 4), today().slice(5, 7)) })}>Mês atual</button>
          <label>
            Desde
            <input
              type="date"
              value={filters.from}
              onChange={(e) => setFilters({ ...filters, from: e.target.value })}
            />
          </label>
          <label>
            Até
            <input
              type="date"
              value={filters.to}
              onChange={(e) => setFilters({ ...filters, to: e.target.value })}
            />
          </label>
          <label>
            Fornecedor ou documento
            <input
              value={filters.search}
              onChange={(e) =>
                setFilters({ ...filters, search: e.target.value })
              }
            />
          </label>
          {[
            [
              "availability",
              "Disponibilidade",
              [
                "por_verificar",
                "fisico",
                "digital",
                "nao_localizado",
                "solicitado",
              ],
            ],
            ["attachment_status", "Anexos", ["sem_anexo", "com_anexo"]],
            [
              "erp_status",
              "ERP",
              ["confirmada", "sugerida", "sem_correspondencia", "divergencias"],
            ],
            [
              "treatment",
              "Tratamento",
              ["por_tratar", "preparar_erp", "contabilidade", "nao_aplicavel"],
            ],
            [
              "accounting",
              "Contabilidade",
              ["por_preparar", "pronto", "entregue", "confirmado"],
            ],
            ["payment_status", "Pagamento", ["por_pagar", "parcial", "pago"]],
            ["bank_status", "Banco", ["pendente", "parcial", "reconciliado"]],
          ].map(([field, title, values]) => (
            <label key={field as string}>
              {title as string}
              <select
                aria-label={title as string}
                value={filters[field as string]}
                onChange={(e) =>
                  setFilters({ ...filters, [field as string]: e.target.value })
                }
              >
                <option value="">Todos</option>
                {option(values as string[])}
              </select>
            </label>
          ))}
        </div>
        <div className="actions">
          <button
            onClick={() =>
              setSelection(filtered.slice(0, 200).map((r) => r.id))
            }
          >
            Selecionar filtrados (até 200)
          </button>
          <button onClick={() => setSelection([])}>Limpar seleção</button>
          {manager && (
            <button
              disabled={!selection.length || busy}
              onClick={() => {
                setBatchPreview(null);
                setModal("batch");
              }}
            >
              Ação em lote ({selection.length})
            </button>
          )}
          <button
            disabled={!selection.length || busy}
            onClick={() =>
              act(async () => {
                await download(
                  "/documents/export-package",
                  { ids: selection, change: {}, confirm: false },
                  "entrega-contabilidade.zip",
                );
                setNotice(
                  "Pacote exportado. Registe a entrega separadamente depois de a efetuar.",
                );
              })
            }
          >
            Exportar documentos e controlo
          </button>
          {manager && (
            <button
              disabled={!selection.length || busy}
              onClick={() => openPayment()}
            >
              Identificar pagamento selecionado
            </button>
          )}
        </div>
        <p>
          {filtered.length} documentos · Total:{" "}
          {euro(filtered.reduce((n, r) => n + r.amount_cents, 0))}
        </p>
        <p>Clique numa linha para conferir o documento ou em Anexo para visualizar o original ao lado.</p>
        <div className={`invoice-comparison-layout ${listPreview ? "with-preview" : ""}`}>
        <div className="purchase-table">
          <table>
            <thead>
              <tr>
                <th>Selecionar</th>
                {[ ["document_date", "Data"], ["supplier_name", "Fornecedor"], ["document", "Documento"], ["amount_cents", "Total"] ].map(([key, label]) => <th key={key} aria-sort={sort.key === key ? sort.ascending ? "ascending" : "descending" : "none"}><button onClick={() => sortBy(key)}>{label} {sort.key === key ? sort.ascending ? "↑" : "↓" : "↕"}</button></th>)}
                <th>Origem</th>
                <th>Disponibilidade / anexos</th>
                <th>ERP</th>
                <th>Tratamento / contabilidade</th>
                <th>Pagamento / banco</th>
                <th>Ações</th>
              </tr>
            </thead>
            <tbody>
              {filtered
                .slice((currentPage - 1) * 20, currentPage * 20)
                .map((r) => (
                  <tr key={r.id} className={`invoice-preview-row erp-invoice-${r.erp_status === "confirmada" || (r.erp_candidates?.length === 1 && !r.erp_candidates[0].differences?.length && !r.erp_candidates[0].unavailable && !r.erp_candidates[0].cancelled && r.erp_candidates[0].confidence >= 70) ? "safe" : r.erp_status === "sem_correspondencia" ? "missing" : "review"}`} tabIndex={0} aria-label={`Conferir documento ${r.document}`} onClick={e => { if (!(e.target as HTMLElement).closest("button, a, input")) openDoc(r); }} onKeyDown={e => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDoc(r); } }}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Selecionar ${r.document}`}
                        checked={selection.includes(r.id)}
                        onChange={(e) =>
                          setSelection(
                            e.target.checked
                              ? [...selection, r.id]
                              : selection.filter((id) => id !== r.id),
                          )
                        }
                      />
                    </td>
                    <td>{day(r.document_date)}</td>
                    <td>{r.supplier_name}<br /><small>NIF {r.supplier_nif || "Não indicado"}</small></td>
                    <td><strong>{r.document}</strong></td>
                    <td className="numeric">{euro(r.amount_cents)}</td>
                    <td>{r.origin === "efatura_testes" ? "e-Fatura (teste)" : r.origin === "efatura" ? "e-Fatura" : r.origin === "manual" ? "Manual" : r.origin}</td>
                    <td>
                      {labels[r.availability]}
                      <br />
                      <small>
                        <StatusBadge tone={r.attachment_status === "com_anexo" ? "success" : "warning"}>{labels[r.attachment_status]}</StatusBadge>{" "}
                        {r.location || "Localização por indicar"}
                      </small>
                      {r.availability === "fisico" && !r.attachments.length && (
                        <p className="doc-attention">Digitalização pendente</p>
                      )}
                      {r.pending_days > 0 && (
                        <p>{r.pending_days} dias de pendência</p>
                      )}
                    </td>
                    <td><span className={`erp-status erp-status-${r.erp_status}`}>{labels[r.erp_status]}</span></td>
                    <td>
                      {labels[r.treatment]}
                      <br />
                      <small>{labels[r.accounting]}</small>
                    </td>
                    <td>
                      {labels[r.payment_status]}
                      <br />
                      <small>Banco: {labels[r.bank_status]}</small>
                    </td>
                    <td>
                      <button className="primary" disabled={busy} onClick={() => openDoc(r)}>
                        Conferir
                      </button>
                      <button disabled={busy} onClick={() => act(async () => setListPreview(await api(`/documents/${r.id}/detail`)))}>Anexo</button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        {!loading && !filtered.length && <div className="empty"><h3>{error ? "Não foi possível carregar os documentos" : "Sem documentos para estes filtros"}</h3><p>{error ? "Verifique a ligação e tente novamente." : "Importe documentos ou ajuste o período, fornecedor e estados."}</p></div>}
        <InvoiceSidePreview key={listPreview?.id || "no-preview"} document={listPreview} onClose={() => setListPreview(null)} />
        </div>
        {pages > 1 && (
          <div className="actions">
            <button
              disabled={currentPage <= 1}
              onClick={() => setPage(currentPage - 1)}
            >
              Anterior
            </button>
            <span>
              {currentPage} / {pages}
            </span>
            <button
              disabled={currentPage >= pages}
              onClick={() => setPage(currentPage + 1)}
            >
              Seguinte
            </button>
          </div>
        )}
      </section>
      {assistant && <DocumentAssistant document={assistant} admin={admin} canReadErp={canReadErp}
        onClose={() => { setAssistant(null); load().catch(e => setError(e.message)); }}
        onPrepare={async () => { const fresh = await api(`/documents/${assistant.id}/detail`); setSelected(fresh); setAssistant(null); setModal("detail"); prepareDraft(fresh); }} />}
      {modal && (
        <dialog
          className={`doc-dialog ${modal === "detail" ? "invoice-detail-dialog" : ""}`}
          ref={dialog}
          onCancel={(e) => {
            e.preventDefault();
            close();
          }}
          aria-label={modal === "assistant" ? `${ASSISTANTS.clara.name} · Conferência e-Fatura` : "Conferência de documentos"}
        >
          <div className="panel-title">
            <h2>
              {modal === "assistant" ? `${ASSISTANTS.clara.name} · Conferência e-Fatura` : modal === "detail"
                ? selected?.document
                : modal === "import"
                  ? "Importar documentos e-Fatura"
                  : modal === "payment"
                    ? "Identificar pagamento"
                    : modal === "sync"
                      ? "Consultar documentos ERP"
                      : modal === "batch"
                        ? "Rever ação em lote"
                        : "Registar documento"}
            </h2>
            <button disabled={busy} onClick={close} aria-label="Fechar">
              ×
            </button>
          </div>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {modal === "assistant" && <p>{ASSISTANTS.clara.mission}</p>}
          {modal === "assistant" && assistantStage === 1 && <div className="doc-form">
            <h3>1.º passo — Parâmetros e importação</h3>
            <div className="form-grid">
              <label>Data início<input type="date" value={assistantFrom} onChange={e => setAssistantFrom(e.target.value)} /></label>
              <label>Data fim<input type="date" value={assistantTo} onChange={e => setAssistantTo(e.target.value)} /></label>
            </div>
            <p>Loja de importação: {stores.find(s => s.id === selectedStore)?.name || "Selecione uma loja na página principal"}.</p>
            <p>As sugestões comparam NIF, montante, data e número. As associações serão revistas antes de confirmar.</p>
            <label>Ficheiro e-Fatura (opcional)<input type="file" accept=".csv,.xlsx" disabled={busy} onChange={e => { setFile(e.target.files?.[0] || null); setPreview(null); }} /></label>
            <p>A confirmação importa todo o ficheiro. O período filtra os documentos a conferir, sem excluir linhas da importação.</p>
            <button disabled={busy || !file || !selectedStore} onClick={() => importFile(false)}>Pré-visualizar ficheiro</button>
            {preview && <>
              <p>{preview.new_count} novos · {preview.duplicate_count} duplicados · {preview.conflict_count} conflitos</p>
              <div className="table-wrap"><table><thead><tr><th>Fornecedor</th><th>Documento ERP</th><th>Doc. de refer?ncia</th><th>Data</th><th>Total</th></tr></thead><tbody>{preview.rows.slice(0,20).map((r: Row) => <tr key={r.line}><td>{r.supplier_name}</td><td>{r.document}</td><td>{r.document_date}</td><td>{euro(r.amount_cents)}</td></tr>)}</tbody></table></div>
              <button className="primary" disabled={busy || !!preview.conflict_count || !preview.rows.length || !assistantFrom || !assistantTo || assistantFrom > assistantTo} onClick={() => importFile(true, true)}>Confirmar importação e continuar</button>
            </>}
            <button disabled={busy || !assistantFrom || !assistantTo || assistantFrom > assistantTo} onClick={() => setAssistantStage(2)}>Continuar com documentos existentes</button>
          </div>}
          {modal === "assistant" && assistantStage === 2 && <div className="doc-form">
            <InvoiceWorkflow start={assistantFrom} end={assistantTo} store={store} admin={admin} manager={manager} onUpdated={load} onReview={async row => { const detail=await api(`/documents/${row.id}/detail`); setSelected(detail); setModal("detail"); }} />
            <button type="button" disabled={busy} onClick={() => setAssistantStage(1)}>Voltar aos parâmetros</button>
            <details><summary>Ferramentas anteriores de conferência</summary>
              <DriveAutoMatch documents={storeRows.filter(r => r.document_date >= assistantFrom && r.document_date <= assistantTo)} onUpdated={load} />
              <button type="button" onClick={() => setAssistantStage(3)}>Abrir conferência ERP detalhada</button>
            </details>
          </div>}
          {modal === "assistant" && assistantStage === 3 && <div className="doc-form">
            <ErpAssistantStep documents={storeRows.filter(r => r.document_date >= assistantFrom && r.document_date <= assistantTo)} admin={admin && canReadErp} start={assistantFrom} end={assistantTo} onUpdated={load} onReview={doc => { setSelected(doc); setModal("detail"); }} />
            <button type="button" disabled={busy} onClick={() => setAssistantStage(2)}>Voltar aos anexos Drive</button>
            <button type="button" onClick={close}>Concluir assistente</button>
          </div>}
          {modal === "import" && (
            <div className="doc-form">
              <p>
                Formato confirmado: CSV exportado do e-Fatura. Também aceita
                XLSX com as colunas do modelo. Os dados importados não
                substituem o PDF ou o papel da fatura.
              </p>
              <a href="/api/purchases/import-template">Modelo CSV</a>
              <label>
                Ficheiro e-Fatura
                <input
                  type="file"
                  accept=".csv,.xlsx"
                  disabled={busy}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] || null);
                    setPreview(null);
                  }}
                />
              </label>
              <button
                disabled={busy || !file}
                onClick={() => importFile(false)}
              >
                Pré-visualizar
              </button>
              {preview && (
                <>
                  <p>
                    {preview.new_count} novos · {preview.duplicate_count}{" "}
                    duplicados · {preview.conflict_count} conflitos
                  </p>
                  <button
                    className="primary"
                    disabled={
                      busy || !preview.rows.length || !!preview.conflict_count
                    }
                    onClick={() => importFile(true)}
                  >
                    Confirmar importação
                  </button>
                  <div className="purchase-table">
                    <table>
                      <thead>
                        <tr>
                          <th>Documento</th>
                          <th>Fornecedor</th>
                          <th>Data</th>
                          <th>Total</th>
                          <th>Estado</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.rows.slice(0, 20).map((r: Row) => (
                          <tr key={r.line}>
                            <td>{r.document}</td>
                            <td>{r.supplier_name}</td>
                            <td>{day(r.document_date)}</td>
                            <td>{euro(r.amount_cents)}</td>
                            <td>{r.status}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}
          {modal === "new" && (
            <form
              className="doc-form"
              onSubmit={(e) => {
                e.preventDefault();
                const values = Object.fromEntries(
                  new FormData(e.currentTarget),
                );
                act(async () => {
                  await api("/purchases", values);
                  await load();
                  setModal("");
                  setNotice(
                    "Documento registado. Disponibilidade e pagamento continuam por verificar.",
                  );
                });
              }}
            >
              <label>
                Loja
                <select name="store_id" defaultValue={selectedStore}>
                  {stores.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                NIF fornecedor
                <input name="supplier_nif" pattern="[0-9]{9}" required />
              </label>
              <label>
                Fornecedor
                <input name="supplier_name" required />
              </label>
              <label>
                Documento
                <input name="document" required />
              </label>
              <label>
                Tipo
                <select name="document_type">
                  <option>FT</option>
                  <option>FS</option>
                  <option>FR</option>
                  <option>NC</option>
                  <option>ND</option>
                </select>
              </label>
              <label>
                Data de emissão
                <input
                  type="date"
                  name="document_date"
                  defaultValue={today()}
                  required
                />
              </label>
              <label>
                Total (€)
                <input inputMode="decimal" name="amount" required />
              </label>
              <button disabled={busy}>Guardar documento</button>
            </form>
          )}
          {modal === "sync" && (
            <form
              className="doc-form"
              onSubmit={(e) => {
                e.preventDefault();
                const values = Object.fromEntries(
                  new FormData(e.currentTarget),
                );
                act(async () => {
                  const result = await api("/documents/erp-sync", {
                    ...values,
                    confirm_demo: values.confirm_demo === "on",
                  });
                  await load();
                  setModal("");
                  setNotice(result.message);
                });
              }}
            >
              <p>
                Consulta apenas documentos de compra na Wintouch. Não grava
                despesas nem altera documentos no ERP.
              </p>
              <label>
                Loja
                <select name="store_id" defaultValue={selectedStore}>
                  {stores.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Desde
                <input
                  type="date"
                  name="start"
                  defaultValue={filters.from || `${today().slice(0, 7)}-01`}
                  required
                />
              </label>
              <label>
                Até
                <input
                  type="date"
                  name="end"
                  defaultValue={filters.to || today()}
                  required
                />
              </label>
              <label className="doc-check">
                <input name="confirm_demo" type="checkbox" />
                Confirmo a consulta de documentos reais, incluindo se esta
                empresa estiver em demonstração.
              </label>
              <button disabled={busy}>
                Consultar e guardar cópia para conferência
              </button>
            </form>
          )}
          {modal === "batch" && (
            <div className="doc-form">
              <p>
                <strong>{selection.length} documentos selecionados</strong>
              </p>
              <p>{selectedLabels}</p>
              <label>
                Alteração
                <select
                  aria-label="Alteração em lote"
                  value={Object.keys(batchChange)[0]}
                  onChange={(e) => {
                    setBatchPreview(null);
                    setBatchChange({
                      [e.target.value]:
                        e.target.value === "availability"
                          ? "fisico"
                          : e.target.value === "accounting"
                            ? "pronto"
                            : "contabilidade",
                    });
                  }}
                >
                  <option value="availability">
                    Disponibilidade do documento
                  </option>
                  <option value="accounting">Entrega à contabilidade</option>
                  <option value="treatment">Tratamento da despesa</option>
                </select>
              </label>
              <label>
                Novo estado
                <select
                  value={Object.values(batchChange)[0] as string}
                  onChange={(e) => {
                    setBatchPreview(null);
                    setBatchChange({
                      [Object.keys(batchChange)[0]]: e.target.value,
                    });
                  }}
                >
                  {option(
                    Object.keys(batchChange)[0] === "availability"
                      ? [
                          "por_verificar",
                          "fisico",
                          "digital",
                          "nao_localizado",
                          "solicitado",
                        ]
                      : Object.keys(batchChange)[0] === "accounting"
                        ? ["por_preparar", "pronto", "entregue", "confirmado"]
                        : ["por_tratar", "preparar_erp", "contabilidade"],
                  )}
                </select>
              </label>
              <button
                disabled={busy}
                onClick={() =>
                  act(async () =>
                    setBatchPreview(
                      await api("/documents/batch", {
                        ids: selection,
                        change: batchChange,
                        confirm: false,
                      }),
                    ),
                  )
                }
              >
                Rever documentos afetados
              </button>
              {batchPreview && (
                <>
                  <p>
                    {batchPreview.documents.length} documentos serão alterados
                    para{" "}
                    <strong>
                      {labels[Object.values(batchChange)[0] as string]}
                    </strong>
                    . Os restantes estados mantêm-se independentes.
                  </p>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        await api("/documents/batch", {
                          ids: batchPreview.documents.map((r: Row) => r.id),
                          change: batchChange,
                          confirm: true,
                        });
                        await load();
                        setModal("");
                        setNotice("Ação em lote registada na auditoria.");
                      })
                    }
                  >
                    Confirmar ação em lote
                  </button>
                </>
              )}
            </div>
          )}
          {modal === "payment" && payment && (
            <form
              className="doc-form"
              onSubmit={(e) => {
                e.preventDefault();
                act(async () => {
                  const lines = Object.entries(distribution)
                    .filter(([, value]) => value && cents(value) > 0)
                    .map(([purchase_id, value]) => ({
                      purchase_id,
                      amount_cents: cents(value),
                    }));
                  if (payment.existing_id)
                    await api(
                      `/document-payments/${payment.existing_id}/allocate`,
                      { lines },
                    );
                  else {
                    const values = Object.fromEntries(
                      [
                        "store_id",
                        "payment_date",
                        "direction",
                        "entity",
                        "reference",
                        "kind",
                        "method",
                        "source",
                        "source_id",
                        "request_key",
                      ].map((key) => [key, payment[key]]),
                    );
                    await api("/document-payments", {
                      ...values,
                      amount_cents: cents(payment.amount),
                      lines,
                    });
                  }
                  await load();
                  setModal("");
                  setNotice(
                    "Pagamento identificado. A reconciliação bancária é efetuada separadamente.",
                  );
                });
              }}
            >
              <p>
                Confirme que o pagamento ocorreu. Este registo não envia
                dinheiro nem reconcilia automaticamente o banco.
              </p>
              <label>
                Origem do pagamento
                <select
                  aria-label="Origem do pagamento"
                  value={
                    payment.existing_id
                      ? `existing:${payment.existing_id}`
                      : payment.source_id
                        ? `${payment.source}:${payment.source_id}`
                        : "manual"
                  }
                  onChange={(e) => {
                    const [source, id] = e.target.value.split(":");
                    if (source === "manual")
                      setPayment({
                        ...payment,
                        source: "manual",
                        source_id: null,
                        existing_id: "",
                      });
                    else if (source === "existing") {
                      const r = payments.find((p) => p.id === id)!;
                      setPayment({
                        ...payment,
                        ...r,
                        amount: (r.amount_cents / 100).toFixed(2),
                        existing_id: id,
                      });
                    } else {
                      const r = sources.find(
                        (s) => s.source === source && s.id === id,
                      )!;
                      setPayment({
                        ...payment,
                        source,
                        source_id: id,
                        existing_id: "",
                        amount: (Math.abs(r.amount_cents) / 100).toFixed(2),
                        direction: Math.sign(r.amount_cents),
                        entity: r.entity,
                        reference: r.reference,
                        payment_date: r.date,
                        method: [
                          "cartao",
                          "transferencia",
                          "numerario",
                        ].includes(r.payment)
                          ? r.payment
                          : "outro",
                      });
                    }
                  }}
                >
                  <option value="manual">
                    Pagamento confirmado manualmente
                  </option>
                  {payments
                    .filter((p) => p.store_id === payment.store_id)
                    .map((p) => (
                      <option key={p.id} value={`existing:${p.id}`}>
                        Pagamento existente · {day(p.payment_date)} ·{" "}
                        {euro(p.amount_cents)} · {p.reference}
                      </option>
                    ))}
                  {sources
                    .filter((s) => s.store_id === payment.store_id)
                    .map((s) => (
                      <option
                        key={`${s.source}:${s.id}`}
                        value={`${s.source}:${s.id}`}
                      >
                        {s.source === "wintouch"
                          ? "Wintouch"
                          : "Movimento interno"}{" "}
                        · {day(s.date)} · {s.entity} · {euro(s.amount_cents)}
                      </option>
                    ))}
                </select>
              </label>
              <div className="doc-fields">
                <label>
                  Data do pagamento
                  <input
                    type="date"
                    required
                    value={payment.payment_date}
                    disabled={
                      payment.source !== "manual" || !!payment.existing_id
                    }
                    onChange={(e) =>
                      setPayment({ ...payment, payment_date: e.target.value })
                    }
                  />
                </label>
                <label>
                  Total pago (€)
                  <input
                    inputMode="decimal"
                    required
                    value={payment.amount}
                    disabled={
                      payment.source !== "manual" || !!payment.existing_id
                    }
                    onChange={(e) =>
                      setPayment({ ...payment, amount: e.target.value })
                    }
                  />
                </label>
                <label>
                  Sentido
                  <select
                    value={payment.direction}
                    disabled={
                      payment.source !== "manual" || !!payment.existing_id
                    }
                    onChange={(e) =>
                      setPayment({
                        ...payment,
                        direction: Number(e.target.value),
                      })
                    }
                  >
                    <option value={-1}>Pagamento / saída</option>
                    <option value={1}>Recebimento / entrada</option>
                  </select>
                </label>
                <label>
                  Meio
                  <select
                    value={payment.method}
                    onChange={(e) =>
                      setPayment({ ...payment, method: e.target.value })
                    }
                  >
                    <option value="transferencia">Transferência</option>
                    <option value="cartao">Cartão</option>
                    <option value="numerario">Numerário</option>
                    <option value="outro">Outro</option>
                  </select>
                </label>
                <label>
                  Referência
                  <input
                    value={payment.reference}
                    onChange={(e) =>
                      setPayment({ ...payment, reference: e.target.value })
                    }
                  />
                </label>
                <label>
                  Entidade
                  <input
                    value={payment.entity}
                    onChange={(e) =>
                      setPayment({ ...payment, entity: e.target.value })
                    }
                  />
                </label>
              </div>
              <label>
                Natureza
                <select
                  value={payment.kind}
                  disabled={
                    !!payment.existing_id || payment.source !== "manual"
                  }
                  onChange={(e) => {
                    setDistribution({});
                    setPayment({ ...payment, kind: e.target.value });
                  }}
                >
                  <option value="pagamento">Pagamento de documentos</option>
                  <option value="comissao">Comissão</option>
                  <option value="juros">Juros</option>
                  <option value="sem_fatura">Movimento sem fatura</option>
                </select>
              </label>
              <h3>Distribuir por documentos</h3>
              {rows
                .filter(
                  (r) =>
                    payment.kind === "pagamento" &&
                    r.store_id === payment.store_id &&
                    r.remaining_cents > 0 &&
                    -r.amount_cents * payment.direction > 0 &&
                    r.origin !== "efatura_testes",
                )
                .map((r) => (
                  <label key={r.id}>
                    {r.supplier_name} · {r.document} · Em aberto:{" "}
                    {euro(r.remaining_cents)}
                    <input
                      inputMode="decimal"
                      aria-label={`Distribuir ${r.document}`}
                      placeholder="0,00"
                      value={distribution[r.id] || ""}
                      onChange={(e) =>
                        setDistribution({
                          ...distribution,
                          [r.id]: e.target.value,
                        })
                      }
                    />
                  </label>
                ))}
              <button className="primary" disabled={busy}>
                Confirmar pagamento e distribuição
              </button>
            </form>
          )}
          {modal === "detail" && selected && (
            <>
              <div className="document-comparison-grid">
              <section className="doc-section">
                <h3>Dados da fatura</h3>{manager && <ScanButton initialDocument={selected} onSaved={load} />}
                <p>
                  {selected.supplier_name} · NIF {selected.supplier_nif} ·{" "}
                  {selected.document_type} · {day(selected.document_date)}
                </p>
                <p>
                  Base:{" "}
                  {selected.base_cents == null
                    ? "Não fornecida"
                    : euro(selected.base_cents)}{" "}
                  · IVA: {euro(selected.tax_cents)} · Total:{" "}
                  <strong>{euro(selected.amount_cents)}</strong>
                </p>
                <p>
                  Origem: {selected.origin}{" "}
                  {selected.import &&
                    `· ${selected.import.filename} · Importado em ${day(selected.import.created_at)}`}
                </p>
              </section>
              <section className="doc-section">
                <h3>Correspondência com o ERP</h3>
                <p>
                  <strong className={`erp-status erp-status-${selected.erp_status}`}>{labels[selected.erp_status]}</strong>. Apenas o NIF,
                  número e tipo identificam a fatura; data e montante confirmam
                  a correspondência.
                </p>
                {selected.erp_candidates.map((c: Row) => (
                  <div key={c.id} className={`invoice-erp-card ${!c.differences?.length && !c.cancelled && !c.unavailable ? "erp-match-confident" : "erp-match-review"}`}>
                    <div className="invoice-erp-fields">
                      <div><small>Documento ERP</small><strong>{c.internal_document || "—"}</strong></div>
                      <div><small>Doc. de referência</small><strong>{c.document || "Não fornecida"}</strong></div>
                      <div><small>Data</small><strong>{day(c.document_date)}</strong></div>
                      <div><small>Total</small><strong>{euro(c.amount_cents)}</strong></div>
                    </div>
                    <p><strong>Confiança: {c.confidence || 0}/100</strong> · {c.reasons.join("; ")}</p>
                    <small>Consultado em {day(c.synced_at)}</small>
                    {!!c.differences?.length && <p className="error">Divergências: {c.differences.join(" · ")}</p>}
                  </div>
                ))}
                {manager && (
                  <div className="doc-form">
                    {!selected.erp_link_id && (
                      <label>
                        Documento ERP
                        <select
                          aria-label="Documento ERP"
                          value={erpId}
                          onChange={(e) => setErpId(e.target.value)}
                        >
                          <option value="">Selecionar após conferência</option>
                          {catalog
                            .filter(
                              (c) =>
                                c.store_id === selected.store_id &&
                                !c.cancelled,
                            )
                            .map((c) => (
                              <option key={c.id} value={c.id}>
                                {c.supplier_name} ·{" "}
                                {c.document || c.internal_document} ·{" "}
                                {euro(c.amount_cents)}
                              </option>
                            ))}
                        </select>
                      </label>
                    )}
                    <label>
                      Motivo da associação ou reversão
                      <input
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                      />
                    </label>
                    <button
                      className={selected.erp_link_id ? "danger" : "primary"}
                      disabled={
                        busy ||
                        reason.trim().length < 3 ||
                        (!selected.erp_link_id && !erpId)
                      }
                      onClick={() =>
                        act(async () => {
                          if (selected.erp_link_id && !window.confirm("Desfazer a associação deste documento ao ERP? O motivo indicado ficará no histórico.")) return;
                          await api(
                            `/documents/${selected.id}/${selected.erp_link_id ? "erp-unlink" : "erp-link"}`,
                            selected.erp_link_id
                              ? { reason }
                              : { erp_id: erpId, reason },
                          );
                          await refreshDetail();
                          setReason("");
                        })
                      }
                    >
                      {selected.erp_link_id
                        ? "Desfazer associação ERP"
                        : "Confirmar associação ERP"}
                    </button>
                  </div>
                )}
              </section>
              </div>
              <section className="doc-section">
                <h3>Pré-visualização do original</h3>
                {detailPreview ? <InvoiceSidePreview key={selected.id} document={selected} onClose={() => setDetailPreview(false)} /> : <button onClick={() => setDetailPreview(true)}>Mostrar anexo</button>}
              </section>
              <section className="doc-section">
                <h3>Disponibilidade e localização</h3>
                {manager ? (
                  <form
                    className="doc-form"
                    key={`state:${selected.id}:${selected.verified_at}`}
                    onSubmit={(e) => {
                      e.preventDefault();
                      const values = Object.fromEntries(
                        new FormData(e.currentTarget),
                      );
                      act(async () => {
                        await api(`/documents/${selected.id}/state`, values);
                        await refreshDetail();
                      });
                    }}
                  >
                    <label>
                      Disponibilidade
                      <select
                        aria-label="Disponibilidade do documento"
                        name="availability"
                        defaultValue={selected.availability}
                      >
                        {option([
                          "por_verificar",
                          "fisico",
                          "digital",
                          "nao_localizado",
                          "solicitado",
                        ])}
                      </select>
                    </label>
                    <label>
                      Localização do papel ou ficheiro
                      <input
                        name="location"
                        defaultValue={selected.location}
                        maxLength={500}
                      />
                    </label>
                    <label>
                      Observações da verificação
                      <textarea
                        name="notes"
                        defaultValue={selected.notes}
                        maxLength={5000}
                      />
                    </label>
                    <button disabled={busy}>
                      Guardar verificação documental
                    </button>
                  </form>
                ) : (
                  <p>
                    {labels[selected.availability]} · {selected.location} ·{" "}
                    {selected.notes}
                  </p>
                )}
                <p>
                  Última verificação: {day(selected.verified_at)} ·{" "}
                  {selected.verified_by_name || "—"} · Pendência aberta há{" "}
                  {selected.pending_days} dias.
                </p>
                {selected.requested_at && (
                  <p>
                    Solicitado ao fornecedor em {day(selected.requested_at)}.
                    Este estado regista a solicitação; não envia mensagens.
                  </p>
                )}
              </section>
              <section className="doc-section">
                <h3>Tratamento da despesa</h3>
                {manager ? (
                  <form
                    className="doc-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const values = Object.fromEntries(
                        new FormData(e.currentTarget),
                      );
                      act(async () => {
                        await api(`/documents/${selected.id}/state`, values);
                        await refreshDetail();
                      });
                    }}
                  >
                    <label>
                      Tratamento
                      <select
                        aria-label="Tratamento da despesa"
                        name="treatment"
                        defaultValue={selected.treatment}
                      >
                        {option([
                          "por_tratar",
                          "preparar_erp",
                          "contabilidade",
                          "nao_aplicavel",
                        ])}
                      </select>
                    </label>
                    <label>
                      Justificação do tratamento
                      <input
                        name="treatment_reason"
                        defaultValue={selected.treatment_reason}
                        maxLength={2000}
                      />
                    </label>
                    <button disabled={busy}>Guardar tratamento</button>
                  </form>
                ) : (
                  <p>
                    {labels[selected.treatment]} · {selected.treatment_reason}
                  </p>
                )}
                {manager && selected.erp_status === "sem_correspondencia" && (
                  <button onClick={() => prepareDraft()}>
                    Preparar proposta de despesa
                  </button>
                )}
                {selected.draft && (
                  <p>
                    {labels[selected.draft.status]} ·{" "}
                    {selected.draft.payload.erp_check?.warning ||
                      "A exportação não confirma um lançamento no ERP."}
                  </p>
                )}
                {draft && (
                  <form
                    className="doc-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      act(async () => {
                        await api(`/documents/${selected.id}/draft`, {
                          ...draft,
                          erp_check: undefined,
                          reviewed: review,
                        });
                        setDraft(null);
                        await refreshDetail();
                      });
                    }}
                  >
                    <p>
                      Proposta para revisão. A gravação de faturas de despesa na
                      Wintouch não está disponível neste adaptador. Preencha
                      categorias e taxas de IVA; a base inicial pode ser uma
                      proposta calculada quando não foi fornecida no ficheiro.
                    </p>
                    <div className="doc-fields">
                      {[
                        ["supplier_nif", "NIF"],
                        ["supplier_name", "Fornecedor"],
                        ["document", "Número"],
                        ["document_type", "Tipo"],
                        ["document_date", "Emissão"],
                        ["due_date", "Vencimento"],
                        ["total", "Total (€)"],
                      ].map(([field, title]) => (
                        <label key={field}>
                          {title}
                          <input
                            type={field.includes("date") ? "date" : "text"}
                            value={draft[field]}
                            required
                            onChange={(e) =>
                              setDraft({ ...draft, [field]: e.target.value })
                            }
                          />
                        </label>
                      ))}
                    </div>
                    {draft.lines.map((line: Row, index: number) => (
                      <fieldset className="doc-fields" key={index}>
                        <legend>Linha {index + 1}</legend>
                        {[
                          ["description", "Descrição"],
                          ["category", "Categoria"],
                          ["base", "Base (€)"],
                          ["rate", "IVA (%)"],
                          ["tax", "IVA (€)"],
                        ].map(([field, title]) => (
                          <label key={field}>
                            {title}
                            <input
                              required
                              value={line[field]}
                              onChange={(e) =>
                                setDraft({
                                  ...draft,
                                  lines: draft.lines.map((r: Row, i: number) =>
                                    i === index
                                      ? { ...r, [field]: e.target.value }
                                      : r,
                                  ),
                                })
                              }
                            />
                          </label>
                        ))}
                        {draft.lines.length > 1 && (
                          <button
                            type="button"
                            onClick={() =>
                              setDraft({
                                ...draft,
                                lines: draft.lines.filter(
                                  (_: Row, i: number) => i !== index,
                                ),
                              })
                            }
                          >
                            Remover linha
                          </button>
                        )}
                      </fieldset>
                    ))}
                    <button
                      type="button"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          lines: [
                            ...draft.lines,
                            {
                              description: "",
                              category: "",
                              base: "0",
                              rate: "",
                              tax: "0",
                            },
                          ],
                        })
                      }
                    >
                      Adicionar linha / taxa de IVA
                    </button>
                    <label>
                      Justificação de alterações
                      <input
                        value={draft.reason}
                        onChange={(e) =>
                          setDraft({ ...draft, reason: e.target.value })
                        }
                      />
                    </label>
                    <label className="doc-check">
                      <input
                        type="checkbox"
                        checked={review}
                        onChange={(e) => setReview(e.target.checked)}
                      />
                      Revisei fornecedor, documento, datas, categorias, bases,
                      taxas e total.
                    </label>
                    <button disabled={busy}>
                      {review
                        ? "Confirmar proposta revista"
                        : "Guardar proposta preparada"}
                    </button>
                  </form>
                )}
                {manager &&
                  selected.draft &&
                  ["revisto", "exportado"].includes(selected.draft.status) && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        act(async () => {
                          await download(
                            `/documents/${selected.id}/draft-export`,
                            {},
                            "proposta-despesa.csv",
                          );
                          await refreshDetail();
                        })
                      }
                    >
                      Exportar proposta revista
                    </button>
                  )}
              </section>
              <section className="doc-section">
                <h3>Entrega à contabilidade</h3>
                {manager ? (
                  <form
                    className="doc-form"
                    key={`accounting:${selected.accounting}`}
                    onSubmit={(e) => {
                      e.preventDefault();
                      const values = Object.fromEntries(
                        new FormData(e.currentTarget),
                      );
                      act(async () => {
                        await api(`/documents/${selected.id}/state`, values);
                        await refreshDetail();
                      });
                    }}
                  >
                    <label>
                      Estado da entrega
                      <select
                        aria-label="Estado da entrega"
                        name="accounting"
                        defaultValue={selected.accounting}
                      >
                        {option([
                          "por_preparar",
                          "pronto",
                          "entregue",
                          "confirmado",
                        ])}
                      </select>
                    </label>
                    <button disabled={busy}>Registar estado da entrega</button>
                  </form>
                ) : (
                  <p>{labels[selected.accounting]}</p>
                )}
                <p>
                  Exportar um pacote não equivale a entregar nem a lançar na
                  contabilidade.
                </p>
              </section>
              <section className="doc-section">
                <h3>Pagamentos associados e banco</h3>
                <p>
                  Pagamento: <strong>{labels[selected.payment_status]}</strong>{" "}
                  · Pago: {euro(selected.paid_cents)} · Em aberto:{" "}
                  {euro(selected.remaining_cents)} · Banco:{" "}
                  <strong>{labels[selected.bank_status]}</strong>
                </p>
                {selected.payments.map((p: Row) => (
                  <p key={p.allocation_id}>
                    {day(p.payment_date)} · {p.reference} · Nesta fatura:{" "}
                    {euro(p.document_amount_cents)} · Pagamento total:{" "}
                    {euro(p.amount_cents)} · Banco: {labels[p.bank_status]} (
                    {euro(p.reconciled_cents)}){" "}
                    {manager && !p.reconciled_cents && (
                      <button
                        disabled={busy || reason.trim().length < 3}
                        onClick={() =>
                          act(async () => {
                            await api(
                              `/document-payments/${p.id}/allocations/${p.allocation_id}/remove`,
                              { reason },
                            );
                            await refreshDetail();
                          })
                        }
                      >
                        Desfazer distribuição (usar motivo acima)
                      </button>
                    )}
                  </p>
                ))}
                {selected.legacy_bank_payment_cents > 0 && (
                  <p>
                    {euro(selected.legacy_bank_payment_cents)} em associações
                    bancárias anteriores, já confirmadas.
                  </p>
                )}
                {manager && selected.remaining_cents > 0 && (
                  <button onClick={() => openPayment(selected)}>
                    Identificar pagamento desta fatura
                  </button>
                )}
              </section>
              <section className="doc-section">
                <h3>Histórico de ações</h3>
                {selected.history.map((a: Row) => (
                  <p key={a.id}>
                    <strong>{a.action.replaceAll("_", " ")}</strong> · {a.actor}{" "}
                    ·{" "}
                    {new Date(a.created_at).toLocaleString("pt-PT", {
                      timeZone: "Europe/Lisbon",
                    })}
                    {a.detail.reason && ` · ${a.detail.reason}`}
                    {a.detail.after?.availability &&
                      ` · ${labels[a.detail.after.availability]}`}
                    {a.detail.after?.accounting &&
                      ` · ${labels[a.detail.after.accounting]}`}
                  </p>
                ))}
              </section>
            </>
          )}
        </dialog>
      )}
    </>
  );
}
