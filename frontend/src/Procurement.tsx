import { RecordWorkspace } from "./components/RecordWorkspace";
import { RecordGroup, RecordIndicator } from "./components/RecordSheet";
import { Truck, Package, FileText, History, User, ShoppingCart, Save, Euro } from "lucide-react";
import OperationsOverview from "./components/OperationsOverview";
import { formatQuantity } from "./lib/quantity";
import { currentMonthRange } from "./components/DatePresets";
import { updateLocationParam } from "./components/WorkspaceUI";
import DatePresets from "./components/DatePresets";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { ActionButton, ActionLink, AppDialog, ManagedForm, Notice, confirmAction } from "./components/InteractionUI";
import { Paperclip } from "lucide-react";
import { procurementAttachments } from "./procurementAttachments";
import InvoiceReceiving from "./InvoiceReceiving";
import ReceiptFinance from "./ReceiptFinance";
import ExpiryHint from "./components/ExpiryHint";
import "./Procurement.css";

type Row = Record<string, any>;
const dateToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const euro = (c: number | undefined) => c === undefined ? "—" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(c / 100);
const day = (s: string) => s ? s.slice(0, 10).split("-").reverse().join("/") : "—";
const kinds: Row = { order: "Encomenda", receipt: "Receção", invoice: "Compra", return: "Devolução", credit: "Nota de crédito" };
const states: Row = { draft: "Rascunho", confirmed: "Confirmada", partial: "Parcialmente recebida", received: "Recebida", cancelled: "Cancelada" };
const movements: Row = { purchase: "Receção / compra", sale: "Venda", supplier_return: "Devolução ao fornecedor", customer_return: "Devolução de cliente", transfer_in: "Transferência — entrada", transfer_out: "Transferência — saída", loss: "Perda", expired: "Validade", adjustment: "Ajuste", opening: "Saldo inicial", valuation: "Ajuste de valorização" };
const sectionKind: Row = { "Encomendas a fornecedores": "order", "Receções de mercadoria": "receipt", "Compras": "invoice", "Devoluções a fornecedores": "return" };
const lineFields = ["article_id", "description", "nature", "supplier_reference", "unit", "quantity", "rejected", "price", "discount", "vat", "warehouse_id", "lot_id", "lot_number", "expiry", "source_id", "source_line", "category_id", "reason", "return_reason"];
const lineCents = (line: Row) => { const net = Math.round(Number(line.quantity || 0) * Number(line.price || 0) * (1 - Number(line.discount || 0) / 100) * 100); return net + Math.round(net * Number(line.vat || 0) / 100); };
const documentFields = ["kind", "store_id", "supplier_id", "document", "document_type", "document_date", "due_date", "expected_date", "contact", "payment_terms", "notes", "attachments", "purchase_id", "erp_id", "category_id", "archive_document_id", "archive_attachment_ids", "document_type_id", "payment_method", "credit_note_purchase_ids", "request_key", "revision"];
function editable(doc: Row): Row {
  return { ...Object.fromEntries(documentFields.filter(k => doc[k] !== undefined).map(k => [k, doc[k]])), lines: doc.lines.map((l: Row) => Object.fromEntries(lineFields.filter(k => l[k] !== undefined).map(k => [k, l[k]]))) };
}
const emptyLine = (warehouse = "") => ({ article_id: null, description: "", nature: "goods", supplier_reference: "", unit: "un", quantity: "1", rejected: "0", price: "0", discount: "0", vat: "23", warehouse_id: warehouse || null, lot_id: null, lot_number: "", expiry: null, source_id: null, source_line: 0, category_id: null, reason: "", return_reason: "outro" });

export default function Procurement({ section, store, stores, categories, navigate }: { section: string; store: string; stores: Row[]; categories: Row[]; navigate: (page: string) => void }) {
  const [data, setData] = useState<Row>({ documents: [], can_write: false });
  const [catalog, setCatalog] = useState<Row>({ articles: [], warehouses: [], suppliers: [] });
  const [suppliers, setSuppliers] = useState<Row[]>([]);
  const [purchases, setPurchases] = useState<Row[]>([]);
  const [erpDocs, setErpDocs] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Row | null>(null);
  const [draft, setDraft] = useState<Row | null>(null);
  const [editId, setEditId] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [overdelivery, setOverdelivery] = useState(false);
  const [differences, setDifferences] = useState(false);
  const [email, setEmail] = useState<Row | null>(null);
  const [preview, setPreview] = useState<Row | null>(null);
  const [sourceChoice, setSourceChoice] = useState("");
  const [ledger, setLedger] = useState<Row>({ rows: [], more: false });
  const [ledgerFilters, setLedgerFilters] = useState({ start: currentMonthRange().from, end: currentMonthRange().to, article_id: "", kind: "", page: 1 });
  const [loading, setLoading] = useState(true);
  const [fromInvoice, setFromInvoice] = useState(false);
  // Fatura do arquivo a ler logo ao abrir a receção a partir de fatura («Receber mercadoria»).
  const [invoiceStart, setInvoiceStart] = useState("");
  // Edição de receções confirmadas: a de fatura reabre a janela de fatura; as manuais, o formulário.
  const [editReceipt, setEditReceipt] = useState("");
  const [editConfirmed, setEditConfirmed] = useState<Row | null>(null);
  const [attachmentDocument, setAttachmentDocument] = useState<Row | null>(null);
  // Receção confirmada antes dos tipos de documento: escolher o tipo gera só o efeito financeiro.
  const [typing, setTyping] = useState<{ doc: Row; values: Row } | null>(null);
  // Lotes a acabar a validade de fornecedores que aceitam devolução (aviso da ficha do fornecedor).
  const [expiryReturns, setExpiryReturns] = useState<Row[]>([]);
  const docs = data.documents.filter((d: Row) => !store || d.store_id === store);
  const whs = catalog.warehouses.filter((w: Row) => !draft?.store_id || w.store_id === draft.store_id);
  async function load() {
    const [next, cat, entities, pur, erp] = await Promise.all([api("/procurement"), api("/stock/catalog"), api("/entities"), api("/purchases"), api("/documents/erp-catalog")]);
    if (!Array.isArray(next?.documents)) throw new Error("A consulta de documentos de compra está indisponível. Atualize para tentar novamente.");
    // Catálogos auxiliares ausentes não impedem a consulta dos documentos.
    const list = (value: unknown) => Array.isArray(value) ? value : [];
    setData({ ...next, documents: list(next?.documents) }); setCatalog({ ...cat, articles: list(cat?.articles), warehouses: list(cat?.warehouses) });
    setSuppliers(list(entities).filter((e: Row) => e.is_supplier && !e.removed && e.active)); setPurchases(list(pur)); setErpDocs(list(erp));
    api("/procurement/expiry-returns").then(r => setExpiryReturns(Array.isArray(r?.items) ? r.items : [])).catch(() => setExpiryReturns([]));
    setSelected(prev => prev ? next.documents.find((d: Row) => d.id === prev.id) || null : null);
  }
  useEffect(() => { setLoading(true); load().catch(e => setError(e.message)).finally(() => setLoading(false)); }, []);
  useEffect(() => {
    if (!["Movimentos de stock", "Compras e Stock — Visão geral"].includes(section)) return;
    // Filtros vazios não são enviados: datas vazias seriam rejeitadas pelo servidor (422).
    const params = new URLSearchParams(Object.entries({ store_id: store, ...ledgerFilters }).filter(([, v]) => v !== "" && v != null).map(([k, v]) => [k, String(v)]));
    let active = true;
    api("/procurement-ledger?" + params).then(r => { if (active) setLedger(r); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [section, store, ledgerFilters, data]);
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function start(kind: string, original?: Row, duplicate = false) {
    setSelected(null); setEditId(""); setEmail(null); setPreview(null); setError(""); setOverdelivery(false); setDifferences(false); setSourceChoice("");
    const targetStore = original?.store_id || store || stores[0]?.id || "";
    const warehouse = catalog.warehouses.find((w: Row) => w.store_id === targetStore)?.id;
    const base: Row = { kind, store_id: targetStore, supplier_id: original?.supplier_id || "", document: "", document_type: kind === "credit" ? "NC" : "FT", document_date: dateToday(), due_date: dateToday(), expected_date: dateToday(), contact: original?.contact || "", payment_terms: original?.payment_terms || "", notes: "", attachments: [], purchase_id: null, category_id: original?.category_id || "", request_key: crypto.randomUUID(), revision: 0, lines: [emptyLine(warehouse)] };
    if (original) {
      base.lines = original.lines.map((l: Row, i: number) => {
        const quantity = duplicate ? l.quantity : kind === "receipt" ? Number(l.quantity) - Number(l.received || 0) : kind === "invoice" ? Number(l.quantity) - Number(l.rejected || 0) - Number(l.invoiced || 0) : kind === "return" ? Number(l.quantity) - Number(l.rejected || 0) - Number(l.returned || 0) : Number(l.quantity) - Number(l.credited || 0);
        return { ...emptyLine(warehouse), ...Object.fromEntries(lineFields.filter(k => l[k] !== undefined).map(k => [k, l[k]])), quantity: String(Math.max(0, quantity)), rejected: "0", source_id: duplicate ? null : kind === "return" && original.kind === "invoice" ? l.source_id : original.id, source_line: kind === "return" && original.kind === "invoice" ? l.source_line : i, lot_id: kind === "return" ? l.lot_id : null };
      }).filter((l: Row) => Number(l.quantity) > 0 && (!["receipt", "return"].includes(kind) || l.nature === "goods") && (kind !== "invoice" || original.kind !== "order" || l.nature !== "goods"));
      if (!base.lines.length) { setError("Este documento já não tem quantidades pendentes para esta operação."); return; }
    }
    setDraft(base);
  }
  // Conferência documental → «Receber mercadoria» (compra sem receção nem compra no circuito): com a
  // fatura no arquivo, abre a receção a partir de fatura já lida; sem ela, uma receção preenchida com o
  // cabeçalho da compra, que fica ligada a ela ao confirmar com o tipo de documento.
  useEffect(() => {
    const purchaseId = new URLSearchParams(window.location.search).get("rececao.compra");
    if (loading || section !== "Receções de mercadoria" || !purchaseId) return;
    updateLocationParam("rececao.compra", "", true);
    void run(async () => {
      const info: Row = await api(`/purchase-receiving/purchases/${encodeURIComponent(purchaseId)}`);
      const purchase = info.purchase;
      const linked = docs.find((d: Row) => info.related.some((r: Row) => r.id === d.id) || d.id === info.archive_receipt_id);
      if (info.related.length || info.archive_receipt_id) {
        if (linked) openDoc(linked);
        setNotice(`A compra ${purchase.document} já tem ${linked ? `${kinds[linked.kind].toLowerCase()} ${linked.document || ""}`.trim() : "documento"} no circuito de compras: a mercadoria já deu entrada.`);
        return;
      }
      if (info.archive_document_id) {
        setInvoiceStart(info.archive_document_id);
        setFromInvoice(true);
        return;
      }
      if (!info.supplier_id) throw new Error(`O fornecedor ${purchase.supplier_name} (NIF ${purchase.supplier_nif}) não tem ficha em Fornecedores: crie-a antes de receber a mercadoria.`);
      start("receipt");
      const warehouse = catalog.warehouses.find((w: Row) => w.store_id === purchase.store_id)?.id;
      setDraft(d => d && {
        ...d, store_id: purchase.store_id, supplier_id: info.supplier_id, document: purchase.document,
        document_type: ["FT", "FS", "FR", "ND"].includes(purchase.document_type) ? purchase.document_type : "FT",
        document_date: purchase.document_date, due_date: purchase.document_date, document_type_id: info.suggested_document_type_id || "",
        contact: suppliers.find(s => s.id === info.supplier_id)?.email || "", lines: [emptyLine(warehouse)],
      });
      setNotice(`Receção preenchida a partir da compra ${purchase.document} (${purchase.supplier_name}). Indique os artigos; ao confirmar com o tipo de documento e o mesmo total, a receção fica ligada a esta compra.`);
    });
  }, [loading]);
  function addSource() {
    const source = docs.find((d: Row) => d.id === sourceChoice);
    if (!draft || !source) return;
    const lines = source.lines.map((l: Row, i: number) => ({ ...emptyLine(), ...Object.fromEntries(lineFields.filter(k => l[k] !== undefined).map(k => [k, l[k]])), source_id: source.id, source_line: i,
      rejected: "0", quantity: String(Math.max(0, Number(l.quantity) - (draft.kind === "receipt" ? Number(l.received || 0) : draft.kind === "return" ? Number(l.rejected || 0) + Number(l.returned || 0) : draft.kind === "credit" ? Number(l.credited || 0) : Number(l.rejected || 0) + Number(l.invoiced || 0)))) })).filter((l: Row) => Number(l.quantity) > 0);
    setDraft({ ...draft, lines: [...draft.lines.filter((l: Row) => l.description || l.article_id), ...lines] }); setSourceChoice("");
  }
  async function attachPdf(file: File) {
    if (!draft) return;
    const body = new FormData();
    body.append("files", file);
    body.append("store_id", draft.store_id);
    body.append("description", `${kinds[draft.kind]} ${draft.document || ""}`.trim());
    const response = await fetch("/api/archive/upload", { method: "POST", body, credentials: "same-origin" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Não foi possível guardar o PDF no Arquivo digital.");
    const item = result.results?.[0];
    // Um ficheiro igual já arquivado é reutilizado (mesmo documento), não duplicado.
    if (!item?.id) throw new Error(item?.message || "O PDF não ficou disponível no Arquivo digital.");
    setDraft(d => d && { ...d, archive_attachment_ids: [...new Set([...(d.archive_attachment_ids || []), item.id])], attachment_names: { ...(d.attachment_names || {}), [item.id]: file.name } });
  }
  function updateLine(index: number, changes: Row) { setDraft(d => d ? { ...d, lines: d.lines.map((l: Row, i: number) => i === index ? { ...l, ...changes } : l) } : d); }
  function chooseArticle(index: number, query: string) {
    const article = catalog.articles.find((a: Row) => a.id === query || a.code === query || (a.barcodes || []).includes(query));
    if (article) updateLine(index, { article_id: article.id, description: article.name, unit: article.unit, vat: article.vat_rate, supplier_reference: article.supplier_reference || "", price: article.last_cost || "0" });
  }
  async function save() {
    if (!draft) return;
    const body = editable(draft);
    for (const l of body.lines) { if (!l.warehouse_id) l.warehouse_id = null; if (!l.expiry) l.expiry = null; }
    if (!body.category_id) body.category_id = null;
    // Tipo de documento: vazio = sem efeito financeiro (só nas receções antigas); o formulário exige-o.
    for (const key of ["document_type_id", "payment_method"]) if (!body[key]) body[key] = null;
    if (!body.credit_note_purchase_ids) body.credit_note_purchase_ids = [];
    if (!body.archive_attachment_ids) body.archive_attachment_ids = [];
    if (editConfirmed) {
      // Receção confirmada: grava a mesma receção e volta a gerar os movimentos de stock.
      const result = await api(`/procurement/${editConfirmed.id}/confirmed`, { ...body, revision: editConfirmed.revision }, { method: "PUT" });
      setDraft(null); setEditConfirmed(null); setSelected(result); await load(); setNotice("Receção atualizada: os movimentos de stock foram gerados de novo.");
      return;
    }
    const result = await api("/procurement" + (editId ? "/" + editId : ""), body, editId ? { method: "PUT" } : undefined);
    setDraft(null); setSelected(result); await load(); setNotice("Rascunho guardado. Confirme-o para aplicar a operação.");
  }
  async function confirm() {
    if (!selected || !await confirmAction({ title: `Confirmar ${kinds[selected.kind].toLowerCase()}?`, message: selected.kind === "order" ? "Confirma a encomenda. Não movimenta stock nem regista dívida e não envia email." : "Aplica os movimentos e ligações apresentados. As quantidades rejeitadas não entram em stock. Uma fatura ligada a uma receção não cria outra entrada.", confirmLabel: "Confirmar" })) return;
    const result = await api(`/procurement/${selected.id}/confirm`, { revision: selected.revision, allow_overdelivery: overdelivery, acknowledge_differences: differences });
    await load(); setSelected(result); setNotice(`${kinds[result.kind]} confirmada.${result.customer_reservations?.length ? ` ${result.customer_reservations.length} reserva(s) feita(s) para pedidos de clientes.` : ""}`);
  }
  const rows = docs.filter((d: Row) => (section === "Compras" ? ["invoice", "credit"].includes(d.kind) : d.kind === sectionKind[section]) && (!status || d.status === status) && `${d.document} ${d.supplier_name} ${d.supplier_nif}`.toLocaleLowerCase("pt-PT").includes(search.toLocaleLowerCase("pt-PT")));
  const openDoc = (d: Row) => { setSelected(d); setOverdelivery(false); setDifferences(false); setEmail(null); setPreview(null); setError(""); };
  /** Estado da conferência por tipo: a receção espera a fatura; a compra espera a conferência. */
  const conference = (d: Row) => {
    if (d.status === "cancelled") return <span>{d.annulled ? "Anulada" : d.correction ? "Corrigida" : "Cancelada"}<small>{d.annulled?.reason || d.correction?.reason || ""}</small></span>;
    if (d.status === "draft") return <span className="procurement-pending">Por confirmar</span>;
    if (d.kind === "receipt" && d.financial) return <span className="procurement-done">Documento {d.financial.document_type_code} registado<small>{d.financial.movement_id ? "Movimento real criado" : d.financial.planned_id ? "Dívida e previsto criados" : d.financial.treasury === "erp" ? "Tesouraria na Wintouch" : ""}</small></span>;
    if (d.kind === "receipt") return d.lines.some((l: Row) => Number(l.pending) > 0)
      ? <span className="procurement-pending">Fatura por associar</span>
      : <span className="procurement-done">Faturada</span>;
    if (["invoice", "credit"].includes(d.kind)) return d.check_status === "checked"
      ? <span className="procurement-done">Conferida</span>
      : <span className="procurement-pending">Por conferir <ActionButton className="procurement-inline-action" onClick={() => navigate("Conferência documental")}>Conferir</ActionButton></span>;

  return <span>{d.check_status === "checked" ? "Conferida" : "Por conferir"}</span>;
  };
  /** Anular receção confirmada: estorno do stock e dos movimentos financeiros (sem apagar), com motivo. */
  async function annulReceipt(d: Row) {
    const reason = window.prompt("Motivo da anulação (mínimo 3 caracteres).\nO stock e os movimentos financeiros desta receção são estornados; nada é apagado.");
    if (!reason || reason.trim().length < 3) return;
    await run(async () => {
      await api(`/procurement/${d.id}/annul`, { revision: d.revision, reason: reason.trim() });
      await load();
      setNotice("Receção anulada: stock e movimentos financeiros estornados.");
    });
  }
  const untyped = (d: Row) => d.kind === "receipt" && d.confirmed_at && d.status !== "cancelled" && !d.document_type_id && !d.purchase_id && !d.lines.some((l: Row) => Number(l.invoiced) > 0);
  async function assignType() {
    if (!typing) return;
    await run(async () => {
      const { doc, values } = typing;
      await api(`/procurement/${doc.id}/document-type`, { revision: doc.revision, document_type_id: values.document_type_id, payment_method: values.payment_method || null, due_date: values.due_date || null });
      setTyping(null);
      await load();
      setNotice("Tipo de documento registado: compra e movimento financeiro criados; o stock não mudou.");
    });
  }
  /** «Editar» numa receção confirmada: a mesma janela do lançamento, já preenchida. */
  function editReceiptDoc(d: Row) {
    setSelected(null); setEmail(null); setPreview(null); setError("");
    if (d.archive_document_id) { setEditReceipt(d.id); return; }
    setEditId(""); setEditConfirmed(d); setDraft(editable(d));
  }
  const attachmentActions = (document: Row) => {
    const attachments = procurementAttachments(document, purchases);
    if (attachments.length === 1) return <ActionLink className="download-link ui-action" href={attachments[0].url} target="_blank" rel="noopener noreferrer" title={attachments[0].name}><Paperclip size={16} />Consultar anexo</ActionLink>;
    return <ActionButton disabled={!attachments.length} title={attachments.length ? `${attachments.length} anexos disponíveis` : "Não existe anexo associado a este documento."} onClick={() => setAttachmentDocument(document)}><Paperclip size={16} />Consultar anexo{attachments.length > 1 ? ` (${attachments.length})` : ""}</ActionButton>;
  };
  /** Devolução já preenchida com o lote a acabar a validade. */
  function startExpiryReturn(item: Row) {
    start("return");
    const article = catalog.articles.find((a: Row) => a.id === item.article_id);
    setDraft(d => d && { ...d, store_id: item.store_id, supplier_id: item.supplier_id, contact: suppliers.find(x => x.id === item.supplier_id)?.email || "",
      lines: [{ ...emptyLine(item.warehouse_id), article_id: item.article_id, description: item.description, unit: item.unit, quantity: item.quantity,
        price: article?.last_cost || "0", vat: article?.vat_rate || "23", warehouse_id: item.warehouse_id, lot_id: item.lot_id, lot_number: item.lot_number,
        expiry: item.expiry, return_reason: "validade", reason: `Validade ${day(item.expiry)}: devolução aceite pelo fornecedor` }] });
  }
  const shownReturns = expiryReturns.filter((i: Row) => !store || i.store_id === store);
  const expiryPanel = <section className="panel procurement-to-receive" aria-labelledby="procurement-expiry-title">
    <h3 id="procurement-expiry-title">Produtos a devolver por validade ({shownReturns.length})</h3>
    <p className="footnote">Lotes em stock de fornecedores que aceitam devolver produtos a acabar a validade, a partir do aviso definido na ficha do fornecedor (separador Validades e devoluções).</p>
    {!shownReturns.length ? <p>Sem lotes dentro do prazo de aviso.</p> : <div className="table-wrap"><table aria-label="Produtos a devolver por validade"><thead><tr>{["Artigo", "Lote / validade", "Quantidade", "Armazém", "Fornecedor", "Ações"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>
      {shownReturns.map((i: Row) => <tr key={`${i.lot_id}-${i.warehouse_id}`}>
        <td><b>{i.code}</b> {i.description}</td>
        <td>{i.lot_number}<small>Validade {day(i.expiry)}</small><span className={`status-badge status-${i.days_left <= 30 ? "danger" : "warning"}`}>Faltam {i.days_left} dia(s)</span></td>
        <td><b>{Number(i.quantity).toLocaleString("pt-PT")}</b> {i.unit}</td>
        <td>{i.warehouse_name}</td>
        <td>{i.supplier_name}<small>Aviso {i.months} mês(es) antes · desde {day(i.warn_from)}</small></td>
        <td>{data.can_write && <ActionButton className="primary" onClick={() => startExpiryReturn(i)}>Preparar devolução</ActionButton>}</td>
      </tr>)}
    </tbody></table></div>}
  </section>;
  const pendingClaims = docs.filter((d: Row) => d.kind === "receipt" && d.status !== "cancelled" && d.expiry_claim?.status === "pendente");
  async function registerClaim(d: Row, status: "reclamada" | "sem_reclamacao") {
    const note = window.prompt(status === "reclamada" ? "Como e quando reclamou ao fornecedor? (mínimo 3 caracteres)" : "Porque não reclama? (mínimo 3 caracteres)");
    if (!note || note.trim().length < 3) return;
    await run(async () => {
      const result = await api(`/procurement/${d.id}/expiry-claim`, { revision: d.revision, status, note: note.trim() });
      await load(); if (selected?.id === d.id) setSelected(result);
      setNotice(status === "reclamada" ? "Reclamação registada." : "Decisão registada: sem reclamação.");
    });
  }
  const claimsPanel = pendingClaims.length > 0 && <section className="panel procurement-to-receive" aria-labelledby="procurement-claims-title">
    <h3 id="procurement-claims-title">Reclamações de validade por fazer ({pendingClaims.length})</h3>
    <p className="footnote">Receções com artigos de validade inferior a 1 ano à data do documento. Reclame ao fornecedor e registe-o aqui.</p>
    <div className="table-wrap"><table aria-label="Reclamações de validade por fazer"><thead><tr>{["Receção", "Fornecedor", "Artigos com validade curta", "Ações"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>
      {pendingClaims.map((d: Row) => <tr key={d.id}>
        <td>{d.document || d.id.slice(0, 8)}<small>{day(d.document_date)}</small></td>
        <td>{d.supplier_name}<small>{d.supplier_nif}</small></td>
        <td>{(d.expiry_alerts || []).filter((a: Row) => a.level === "reclamar").map((a: Row) => <span key={a.line} className="status-badge status-danger">{a.description}{a.lot ? ` · lote ${a.lot}` : ""} · validade {day(a.expiry)} ({a.days} dias)</span>)}</td>
        <td><div className="actions">{data.can_write && <><ActionButton className="primary" onClick={() => void registerClaim(d, "reclamada")}>Registar reclamação</ActionButton><ActionButton onClick={() => void registerClaim(d, "sem_reclamacao")}>Não reclamar</ActionButton></>}<ActionButton onClick={() => openDoc(d)}>Abrir</ActionButton></div></td>
      </tr>)}
    </tbody></table></div>
  </section>;
  const toReceive = docs.filter((d: Row) => d.kind === "order" && ["confirmed", "partial"].includes(d.status) && d.lines.some((l: Row) => l.nature === "goods" && Number(l.pending) > 0))
    .sort((a: Row, b: Row) => String(a.expected_date || "9999").localeCompare(String(b.expected_date || "9999")));
  const waitingCustomers = (d: Row) => (d.customer_orders || []).filter((c: Row) => Number(c.waiting) > 0);
  const pendingOrders = <section className="panel procurement-to-receive" aria-labelledby="procurement-to-receive-title">
    <h3 id="procurement-to-receive-title">Encomendas por receber ({toReceive.length})</h3>
    <p className="footnote">Ao confirmar a receção de uma encomenda feita para pedidos de clientes, as quantidades recebidas ficam logo reservadas a esses clientes; o excedente fica em stock geral.</p>
    {!toReceive.length ? <p>Não há encomendas confirmadas por receber.</p> : <div className="table-wrap"><table aria-label="Encomendas por receber"><thead><tr>{["Encomenda", "Fornecedor", "Previsão de entrega", "Por receber", "Clientes à espera", "Ações"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>
      {toReceive.map((d: Row) => {
        const late = d.expected_date && d.expected_date < dateToday();
        const pending = d.lines.filter((l: Row) => l.nature === "goods").reduce((n: number, l: Row) => n + Number(l.pending || 0), 0);
        const waiting = waitingCustomers(d);
        return <tr key={d.id}>
          <td>{d.document || d.id.slice(0, 8)}<small>{day(d.document_date)} · {states[d.status]}</small></td>
          <td>{d.supplier_name}<small>{d.supplier_nif}</small></td>
          <td>{day(d.expected_date)}{late && <span className="status-badge status-danger">Em atraso</span>}</td>
          <td><b>{pending.toLocaleString("pt-PT")}</b><small>{d.lines.filter((l: Row) => l.nature === "goods" && Number(l.pending) > 0).length} artigo(s)</small></td>
          <td>{waiting.length ? <div className="procurement-customers">{waiting.map((c: Row, i: number) => <span key={i} className={`status-badge status-${c.urgent ? "danger" : "info"}`} title={c.description}>{c.number} · {c.customer} · {Number(c.waiting).toLocaleString("pt-PT")}</span>)}</div> : <small>Reposição de stock</small>}</td>
          <td><div className="actions">{data.can_write && <ActionButton className="primary" onClick={() => start("receipt", d)}>Receber</ActionButton>}<ActionButton onClick={() => openDoc(d)}>Abrir</ActionButton></div></td>
        </tr>;
      })}
    </tbody></table></div>}
  </section>;
  const summary = [
    ["Encomendas pendentes", docs.filter((d: Row) => d.kind === "order" && ["confirmed", "partial"].includes(d.status)).length],
    ["Entregas em atraso", docs.filter((d: Row) => d.kind === "order" && ["confirmed", "partial"].includes(d.status) && d.expected_date && d.expected_date < dateToday()).length],
    ["Receções por faturar", docs.filter((d: Row) => d.kind === "receipt" && d.status === "confirmed" && d.lines.some((l: Row) => Number(l.pending) > 0)).length],
    ["Compras por conferir", docs.filter((d: Row) => d.kind === "invoice" && d.status === "confirmed" && d.check_status === "pending").length],
    ["Devoluções por validade", expiryReturns.filter((i: Row) => !store || i.store_id === store).length],
    ["Devoluções sem nota de crédito", docs.filter((d: Row) => d.kind === "return" && d.status === "confirmed" && d.lines.some((l: Row) => Number(l.pending) > 0)).length],
  ];
  const draftGeneral = draft && <>
      <div className="procurement-grid"><Field label="Estabelecimento"><select required value={draft.store_id} onChange={e => setDraft({ ...draft, store_id: e.target.value, lines: [emptyLine(catalog.warehouses.find((w: Row) => w.store_id === e.target.value)?.id)] })}>{stores.map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select></Field><Field label="Fornecedor"><select required value={draft.supplier_id} onChange={e => { const s = suppliers.find(r => r.id === e.target.value); setDraft({ ...draft, supplier_id: e.target.value, contact: s?.email || s?.phone || "", category_id: s?.category_id || "", lines: [emptyLine(whs[0]?.id)] }); }}><option value="">Selecionar fornecedor</option>{suppliers.map(s => <option key={s.id} value={s.id}>{s.name} · {s.tax_number}</option>)}</select></Field><Field label="Contacto"><input value={draft.contact} onChange={e => setDraft({ ...draft, contact: e.target.value })} /></Field><Field label="Documento do fornecedor"><input required={draft.kind !== "order"} value={draft.document} onChange={e => setDraft({ ...draft, document: e.target.value })} /></Field><Field label="Data do documento"><input required type="date" value={draft.document_date} onChange={e => setDraft({ ...draft, document_date: e.target.value })} /></Field>{draft.kind === "order" ? <Field label="Previsão de entrega"><input type="date" value={draft.expected_date || ""} onChange={e => setDraft({ ...draft, expected_date: e.target.value || null })} /></Field> : ["invoice", "credit"].includes(draft.kind) && <><Field label="Vencimento"><input required type="date" value={draft.due_date} onChange={e => setDraft({ ...draft, due_date: e.target.value })} /></Field><Field label="Rubrica por omissão das linhas"><select required value={draft.category_id || ""} onChange={e => setDraft({ ...draft, category_id: e.target.value })}><option value="">Selecionar rubrica</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field><Field label="Tipo de documento"><select value={draft.document_type} onChange={e => setDraft({ ...draft, document_type: e.target.value })}>{(draft.kind === "credit" ? ["NC"] : ["FT", "FS", "FR", "ND"]).map(v => <option key={v}>{v}</option>)}</select></Field><Field label="Documento já existente (ERP / e-Fatura / manual)"><select value={draft.purchase_id || ""} onChange={e => { const p = purchases.find(p => p.id === e.target.value); setDraft({ ...draft, purchase_id: e.target.value || null, document: p?.document || draft.document, document_date: p?.document_date || draft.document_date, document_type: p?.document_type || draft.document_type }); }}><option value="">Criar documento novo</option>{purchases.filter(p => p.store_id === draft.store_id && p.supplier_nif === suppliers.find(s => s.id === draft.supplier_id)?.tax_number).map(p => <option key={p.id} value={p.id}>{p.document} · {euro(Math.abs(p.amount_cents))} · {p.origin}</option>)}</select></Field><Field label="Associar documento ERP"><select value={draft.erp_id || ""} onChange={e => { const p = erpDocs.find(p => p.id === e.target.value); setDraft({ ...draft, erp_id: e.target.value || null, document: p?.document || draft.document, document_date: p?.document_date || draft.document_date, document_type: p?.document_type || draft.document_type }); }}><option value="">Sem documento ERP associado</option>{erpDocs.filter(p => p.store_id === draft.store_id && p.supplier_nif === suppliers.find(s => s.id === draft.supplier_id)?.tax_number && !p.cancelled).map(p => <option key={p.id} value={p.id}>{p.document} - {euro(Math.abs(p.amount_cents))}</option>)}</select></Field></>}{draft.kind === "receipt" && <ReceiptFinance supplierId={draft.supplier_id} documentDate={draft.document_date}
        value={{ document_type_id: draft.document_type_id || "", payment_method: draft.payment_method || "", due_date: draft.due_date || draft.document_date, credit_note_purchase_ids: draft.credit_note_purchase_ids || [] }}
        onChange={patch => setDraft(current => current && { ...current, ...patch })} />}</div>
      {["receipt", "invoice", "return", "credit"].includes(draft.kind) && <div className="procurement-source"><Field label="Adicionar linhas de documento de origem"><select value={sourceChoice} onChange={e => setSourceChoice(e.target.value)}><option value="">Selecionar origem do mesmo fornecedor</option>{docs.filter((d: Row) => d.supplier_id === draft.supplier_id && d.store_id === draft.store_id && d.status !== "cancelled" && d.confirmed_at && d.kind === ({ receipt: "order", invoice: "receipt", return: "receipt", credit: "return" } as Row)[draft.kind]).map((d: Row) => <option key={d.id} value={d.id}>{kinds[d.kind]} · {d.document || d.id.slice(0, 8)} · {day(d.document_date)}</option>)}</select></Field><ActionButton type="button" disabled={!sourceChoice} onClick={addSource}>Adicionar linhas da origem</ActionButton></div>}
      <datalist id="procurement-articles">{catalog.articles.filter((a: Row) => a.active).map((a: Row) => <option key={a.id} value={a.code}>{a.name}</option>)}</datalist>
</>;
  const draftLines = draft && <>      {draft.lines.map((line: Row, index: number) => <fieldset className="procurement-line" key={index}><legend>Linha {index + 1}{line.source_id ? " · Associada à origem" : " · Direta"}</legend><div className="procurement-grid">
        <Field label="Natureza"><select disabled={!!line.source_id || ["receipt", "return"].includes(draft.kind)} value={line.nature} onChange={e => updateLine(index, { nature: e.target.value, article_id: e.target.value === "goods" ? line.article_id : null })}><option value="goods">Mercadoria</option><option value="service">Serviço</option><option value="expense">Despesa</option></select></Field>
        {line.nature === "goods" && <><Field label="Artigo"><select required disabled={!!line.source_id} value={line.article_id || ""} onChange={e => chooseArticle(index, e.target.value)}><option value="">Selecionar artigo</option>{catalog.articles.filter((a: Row) => a.active).map((a: Row) => <option key={a.id} value={a.id}>{a.code} · {a.name}{a.origin === "ERP" ? " · ERP" : ""}</option>)}</select></Field><Field label="Código / código de barras"><input list="procurement-articles" disabled={!!line.source_id} placeholder="Ler ou escrever código" onChange={e => chooseArticle(index, e.target.value)} /></Field></>}
        <Field label="Descrição"><input required value={line.description} onChange={e => updateLine(index, { description: e.target.value })} /></Field><Field label="Referência do fornecedor"><input value={line.supplier_reference || ""} onChange={e => updateLine(index, { supplier_reference: e.target.value })} /></Field><Field label="Unidade base"><input required readOnly={line.nature === "goods"} value={line.unit} onChange={e => updateLine(index, { unit: e.target.value })} /></Field><Field label={draft.kind === "receipt" ? "Quantidade recebida" : "Quantidade"}><input required type="number" min="0.000001" step="any" value={line.quantity} onChange={e => updateLine(index, { quantity: e.target.value })} /></Field>{draft.kind === "receipt" && <Field label="Quantidade rejeitada"><input required type="number" min="0" step="any" max={line.quantity} value={line.rejected} onChange={e => updateLine(index, { rejected: e.target.value })} /></Field>}<Field label="Preço unitário sem IVA (€)"><input required type="number" min="0" step="any" value={line.price} onChange={e => updateLine(index, { price: e.target.value })} /></Field><Field label="Desconto (%)"><input required type="number" min="0" max="100" step="any" value={line.discount} onChange={e => updateLine(index, { discount: e.target.value })} /></Field><Field label="IVA (%)"><input required type="number" min="0" max="100" step="any" value={line.vat} onChange={e => updateLine(index, { vat: e.target.value })} /></Field>
        {line.nature === "goods" && <><Field label="Armazém"><select required value={line.warehouse_id || ""} onChange={e => updateLine(index, { warehouse_id: e.target.value })}><option value="">Selecionar armazém</option>{whs.map((w: Row) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>{draft.kind !== "order" && <><Field label="Lote"><input value={line.lot_number || ""} onChange={e => updateLine(index, { lot_number: e.target.value })} /></Field><Field label="Validade"><input type="date" value={line.expiry || ""} onChange={e => updateLine(index, { expiry: e.target.value || null })} /></Field>{draft.kind === "receipt" && <ExpiryHint expiry={line.expiry} documentDate={draft.document_date} required={!!catalog.articles.find((a: Row) => a.id === line.article_id)?.lots} />}</>}</>}
        {["invoice", "credit"].includes(draft.kind) && <Field label="Rubrica da linha"><select value={line.category_id || ""} onChange={e => updateLine(index, { category_id: e.target.value || null })}><option value="">Por omissão{draft.category_id ? ` (${categories.find(c => c.id === draft.category_id)?.name || "rubrica da compra"})` : ""}</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>}
        {draft.kind === "return" && <Field label="Tipo de motivo"><select value={line.return_reason} onChange={e => updateLine(index, { return_reason: e.target.value })}><option value="defeito">Defeito</option><option value="erro_entrega">Erro de entrega</option><option value="validade">Validade</option><option value="outro">Outro</option></select></Field>}<Field label="Divergências / motivo"><input required={draft.kind === "return"} minLength={draft.kind === "return" ? 3 : undefined} value={line.reason || ""} onChange={e => updateLine(index, { reason: e.target.value })} /></Field>
      </div><ActionButton type="button" disabled={draft.lines.length === 1} onClick={() => setDraft({ ...draft, lines: draft.lines.filter((_: Row, i: number) => i !== index) })}>Retirar linha do rascunho</ActionButton></fieldset>)}
      <ActionButton type="button" onClick={() => setDraft({ ...draft, lines: [...draft.lines, emptyLine(whs[0]?.id)] })}>Adicionar linha</ActionButton>
      {["invoice", "credit"].includes(draft.kind) && (() => { const split: Row = {}; for (const line of draft.lines) { const key = line.category_id || draft.category_id || ""; split[key] = (split[key] || 0) + lineCents(line); } const entries = Object.entries(split).filter(([, cents]) => cents); return entries.length > 1 || entries.some(([key]) => !key) ? <p className="procurement-split">Repartição por rubrica: {entries.map(([key, cents]) => `${categories.find(c => c.id === key)?.name || "Sem rubrica"} ${(Number(cents) / 100).toFixed(2).replace(".", ",")} €`).join(" · ")}. O pagamento continua a ser um só; a repartição serve a análise por rubrica.</p> : null; })()}
</>;
  const draftDocuments = draft && <>      <div className="procurement-grid"><Field label="Condições de pagamento"><input value={draft.payment_terms} onChange={e => setDraft({ ...draft, payment_terms: e.target.value })} /></Field><Field label="Anexar PDF (guarda no Arquivo digital)"><input type="file" accept="application/pdf,image/jpeg,image/png,image/webp" onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void run(() => attachPdf(file)); }} /></Field>{(draft.archive_attachment_ids || []).length > 0 && <div className="procurement-attached" aria-label="PDF anexados">{draft.archive_attachment_ids.map((id: string) => <span key={id} className="status-badge status-success"><Paperclip size={14} aria-hidden="true" />{draft.attachment_names?.[id] || "PDF no Arquivo digital"}<ActionButton type="button" className="procurement-inline-action" aria-label="Retirar PDF" onClick={() => setDraft({ ...draft, archive_attachment_ids: draft.archive_attachment_ids.filter((x: string) => x !== id) })}>×</ActionButton></span>)}</div>}<Field label="Anexos — ligações HTTPS, uma por linha"><textarea value={draft.attachments.join("\n")} onChange={e => setDraft({ ...draft, attachments: e.target.value.split("\n").filter(Boolean) })} /></Field><Field label="Observações"><textarea value={draft.notes} onChange={e => setDraft({ ...draft, notes: e.target.value })} /></Field></div><p>Os anexos permanecem no Drive ou no serviço indicado. {editConfirmed ? "Ao gravar, a receção é atualizada e os movimentos de stock são gerados de novo." : "Guardar rascunho não movimenta stock."}</p></>;
  return <section className="procurement-module">
    <div className="panel"><div className="panel-title"><div><h3>{section === "Compras e Stock — Visão geral" ? "Compras e Stock" : section}</h3><p>Encomendar → receber → conferir → acompanhar o pagamento.</p></div><ActionButton disabled={busy} onClick={() => run(load)}>Atualizar</ActionButton></div>
      <p className="footnote">{data.integration_note}</p><div className="actions"><ActionButton onClick={() => navigate("Fornecedores")}>Fornecedores</ActionButton><ActionButton onClick={() => navigate("Artigos e validades")}>Artigos, lotes e validades</ActionButton><ActionButton onClick={() => navigate("Conferência documental")}>Conferência documental</ActionButton><ActionButton onClick={() => navigate("Movimentos previstos")}>Plano de pagamentos</ActionButton></div>
    </div>
    <OperationsOverview source={data} title="Acompanhamento de compras" scope={`${store ? "Loja selecionada" : "Lojas autorizadas"} · todos os documentos carregados, antes da pesquisa da lista`} loaded={!loading} error={!!error && !data.documents.length}
      metrics={summary.map(([label,value])=>({label:String(label),value,description:"Documentos e estados registados neste âmbito",tone: String(label).includes("atraso") ? "danger" as const : "warning" as const}))}
      suggestion={toReceive.length ? {title:"Acompanhar a próxima receção",reason:"Consulte as quantidades em aberto e as reservas dos clientes antes de confirmar mercadoria.",
        evidence:[`${toReceive.length} encomendas confirmadas ou parciais com mercadoria por receber`,"Ordenadas pela previsão de entrega; a receção conserva a revisão de stock e finanças."],
        actionLabel:"Consultar encomenda por receber",onReview:()=>openDoc(toReceive[0])}:null}/>
    {fromInvoice && <InvoiceReceiving startDocument={invoiceStart || undefined} articles={catalog.articles} stores={stores} categories={categories} onClose={() => setFromInvoice(false)} onDone={message => { setFromInvoice(false); setNotice(message); void run(load); }} />}
    {editReceipt && <InvoiceReceiving editReceipt={editReceipt} articles={catalog.articles} stores={stores} categories={categories} onClose={() => setEditReceipt("")} onDone={message => { setEditReceipt(""); setNotice(message); void run(load); }} />}
    {loading && <p role="status">A carregar compras e stock…</p>}{error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success">{notice}</Notice>}
    {section === "Compras e Stock — Visão geral" ? <><div className="panel"><h3>Últimos documentos</h3>{docs.slice(0, 8).map((d: Row) => <div className="procurement-recent" key={d.id}><span>{kinds[d.kind]} · {d.document || d.id.slice(0, 8)} · {d.supplier_name}</span><span>{states[d.status]}</span><ActionButton onClick={() => openDoc(d)}>Abrir</ActionButton></div>)}<h3>Movimentos de stock recentes</h3>{ledger.rows.slice(0, 5).map((r: Row) => <div className="procurement-recent" key={r.id}><span>{day(r.occurred_on)} - {r.article_name}</span><span>{movements[r.kind] || r.kind} - {formatQuantity(r.quantity)} {r.unit}</span><span>{r.warehouse_name}</span></div>)}{!ledger.rows.length && <p>Sem movimentos de stock registados.</p>}<ActionButton onClick={() => navigate("Movimentos de stock")}>Consultar movimentos de stock</ActionButton></div></> : section === "Movimentos de stock" ? <div className="panel">
      <div className="procurement-grid filter-bar"><DatePresets from={ledgerFilters.start} to={ledgerFilters.end} onSelect={(start, end) => setLedgerFilters({ ...ledgerFilters, start, end, page: 1 })} /><Field label="Desde"><input type="date" value={ledgerFilters.start} onChange={e => setLedgerFilters({ ...ledgerFilters, start: e.target.value, page: 1 })} /></Field><Field label="Até"><input type="date" value={ledgerFilters.end} onChange={e => setLedgerFilters({ ...ledgerFilters, end: e.target.value, page: 1 })} /></Field><Field label="Artigo"><select value={ledgerFilters.article_id} onChange={e => setLedgerFilters({ ...ledgerFilters, article_id: e.target.value, page: 1 })}><option value="">Todos</option>{catalog.articles.map((a: Row) => <option value={a.id} key={a.id}>{a.code} · {a.name}</option>)}</select></Field><Field label="Tipo de movimento"><select value={ledgerFilters.kind} onChange={e => setLedgerFilters({ ...ledgerFilters, kind: e.target.value, page: 1 })}><option value="">Todos</option>{Object.entries(movements).map(([k, v]) => <option value={k} key={k}>{String(v)}</option>)}</select></Field></div>
      <div className="table-wrap"><table><thead><tr>{["Data / utilizador", "Artigo", "Movimento", "Quantidade / unidade", "Armazém", "Lote / validade", "Custo / valor", "Origem"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{ledger.rows.map((r: Row) => <tr key={r.id}><td>{day(r.occurred_on)}<small>{new Date(r.created_at).toLocaleTimeString("pt-PT")} · {r.user_name}</small></td><td>{r.article_code}<small>{r.article_name}</small></td><td>{movements[r.kind] || r.kind}<small>{r.reason}</small></td><td>{formatQuantity(r.quantity)} {r.unit}</td><td>{r.warehouse_name}</td><td>{r.lot_number || "—"}<small>{day(r.expiry)}</small></td><td>{r.unit_cost === null ? "Reservado / desconhecido" : euro(Number(r.unit_cost) * 100)}<small>{r.value_delta ? `Ajuste: ${euro(Number(r.value_delta)*100)}` : r.value === null ? "" : euro(Number(r.value)*100)}</small></td><td>{r.document}<small>{r.origin}</small>{r.procurement_id && <ActionButton onClick={() => { const doc = docs.find((d: Row) => d.id === r.procurement_id); if (doc) openDoc(doc); }}>Documento de origem</ActionButton>}</td></tr>)}</tbody></table></div>
      <p>Movimentos confirmados são preservados. Use devolução para corrigir uma receção ou os ajustes/transferências justificados no módulo de artigos.</p><div className="actions"><ActionButton disabled={ledgerFilters.page <= 1} onClick={() => setLedgerFilters({ ...ledgerFilters, page: ledgerFilters.page - 1 })}>Anterior</ActionButton><span>Página {ledgerFilters.page}</span><ActionButton disabled={!ledger.more} onClick={() => setLedgerFilters({ ...ledgerFilters, page: ledgerFilters.page + 1 })}>Seguinte</ActionButton></div>
    </div> : <div className="panel"><div className="actions">{data.can_write && <ActionButton className="primary" onClick={() => start(sectionKind[section] || "invoice")}>Nova {kinds[sectionKind[section] || "invoice"].toLowerCase()}</ActionButton>}{section === "Compras" && data.can_write && <ActionButton onClick={() => start("credit")}>Registar nota de crédito</ActionButton>}{section === "Receções de mercadoria" && data.can_write && <ActionButton onClick={() => { setInvoiceStart(""); setFromInvoice(true); }}>Receção a partir de fatura</ActionButton>}</div>{section === "Receções de mercadoria" && claimsPanel}{section === "Receções de mercadoria" && pendingOrders}{section === "Devoluções a fornecedores" && expiryPanel}<div className="procurement-grid filter-bar"><Field label="Fornecedor ou documento"><input value={search} onChange={e => setSearch(e.target.value)} /></Field><Field label="Estado"><select value={status} onChange={e => setStatus(e.target.value)}><option value="">Todos</option>{Object.entries(states).map(([k, v]) => <option key={k} value={k}>{String(v)}</option>)}</select></Field></div>
      <div className="table-wrap"><table><thead><tr>{["Data / documento", "Fornecedor", "Estado", "Total", "Conferência / anexo", "Pagamento / ERP", "Ações"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{rows.map((d: Row) => <tr key={d.id}><td>{day(d.document_date)}<small>{kinds[d.kind]} · {d.document || d.id.slice(0, 8)}</small></td><td>{d.supplier_name}<small>{d.supplier_nif}</small></td><td><span className={`status-badge status-${d.status === "cancelled" ? "danger" : ["received", "confirmed"].includes(d.status) ? "success" : "warning"}`}>{states[d.status]}</span>{d.kind === "order" && <small>{d.email_history?.some((e: Row) => ["accepted", "received"].includes(e.status)) ? "Envio aceite pelo SMTP" : "Sem envio aceite"}</small>}</td><td>{euro(d.total_cents)}</td><td className="procurement-check-cell">{conference(d)}{d.expiry_claim?.status === "pendente" && <span className="status-badge status-danger">Reclamar validade</span>}{procurementAttachments(d, purchases).length > 0 ? attachmentActions(d) : <small>Sem anexo</small>}</td><td>{d.purchase_id ? d.payment_status === "paid" ? "Pago" : d.payment_status === "partial" ? "Pagamento parcial" : "Por pagar" : "Sem dívida registada"}<small>{d.erp_status === "linked" ? "Associada ao ERP" : "Sem associação ERP"}</small></td><td><div className="actions"><ActionButton onClick={() => openDoc(d)}>Abrir</ActionButton>{data.can_write && d.status === "draft" && <ActionButton onClick={() => { setSelected(null); setEditId(d.id); setDraft(editable(d)); }}>Editar</ActionButton>}{data.can_write && d.kind === "receipt" && d.confirmed_at && d.status !== "cancelled" && !d.lines.some((l: Row) => Number(l.invoiced) > 0 || Number(l.returned) > 0) && <ActionButton title="Reabre a janela de lançamento; ao gravar, os movimentos de stock são gerados de novo" onClick={() => editReceiptDoc(d)}>Editar</ActionButton>}{data.can_write && d.kind === "receipt" && d.confirmed_at && d.status !== "cancelled" && !d.lines.some((l: Row) => Number(l.invoiced) > 0 || Number(l.returned) > 0) && <ActionButton className="danger" title="Estorna o stock e os movimentos financeiros, com registo" onClick={() => annulReceipt(d)}>Anular</ActionButton>}{data.can_write && untyped(d) && <ActionButton title="Receção confirmada sem tipo de documento: gera a compra e o movimento previsto ou real, sem mexer no stock" onClick={() => { setError(""); setTyping({ doc: d, values: { document_type_id: "", payment_method: "", due_date: d.document_date, credit_note_purchase_ids: [] } }); }}>Definir tipo</ActionButton>}</div></td></tr>)}</tbody></table></div>{!rows.length && <p>Sem documentos para estes filtros.</p>}</div>}
    {attachmentDocument && <AppDialog className="procurement-dialog" aria-label="Anexos do documento" onRequestClose={() => setAttachmentDocument(null)}>
      <h2>Anexos do documento</h2><p>{attachmentDocument.document} {attachmentDocument.supplier_name}</p>
      <div className="actions">{procurementAttachments(attachmentDocument, purchases).map(attachment => <ActionLink className="download-link ui-action" key={attachment.url} href={attachment.url} target="_blank" rel="noopener noreferrer"><Paperclip size={16} />{attachment.name}</ActionLink>)}</div>
    </AppDialog>}
    {draft && <AppDialog className={`procurement-dialog${draft.kind === "order" ? " business-record-dialog" : ""}`} aria-label={`${kinds[draft.kind]} — ${editConfirmed ? "edição" : "rascunho"}`} busy={busy} onRequestClose={() => { setDraft(null); setEditConfirmed(null); }}>
      {draft.kind !== "order" && <h2>{kinds[draft.kind]} — {editConfirmed ? "edição" : "rascunho"}</h2>}{error && <Notice tone="error">{error}</Notice>}
      <ManagedForm onSubmit={e => { e.preventDefault(); run(save); }}>
        {draft.kind === "order" ? <RecordWorkspace title="Encomenda a fornecedor" subtitle="Rascunho · confirmação posterior" icon={Truck}
          actions={<ActionButton type="submit" className="record-save" variant="primary" disabled={busy}><Save size={16} />Guardar rascunho</ActionButton>}
          indicators={<><RecordIndicator label="Linhas de compra" value={draft.lines.length} icon={Package} /><RecordIndicator label="Total previsto (IVA incl.)" value={euro(draft.lines.reduce((sum: number, l: Row) => sum + lineCents(l), 0))} note="Sem portes adicionais; confirmar custos antes de emitir" icon={Euro} /></>}
          sections={[
            { label: "Geral", icon: Truck, content: <RecordGroup title="Fornecedor e condições da encomenda" icon={Truck}>{draftGeneral}</RecordGroup> },
            { label: "Artigos", icon: Package, content: <RecordGroup title="Quantidades e custos" icon={Package}>{draftLines}</RecordGroup> },
            { label: "Documentos e notas", icon: FileText, content: <RecordGroup title="Documentos e observações" icon={FileText}>{draftDocuments}</RecordGroup> },
          ]} /> : <>{draftGeneral}{draftLines}{draftDocuments}<ActionButton type="submit" className="primary" disabled={busy}>{editConfirmed ? "Guardar alterações" : "Guardar rascunho"}</ActionButton></>}
      </ManagedForm>
    </AppDialog>}
    {typing && <AppDialog className="procurement-dialog" aria-label="Tipo de documento da receção" onRequestClose={() => setTyping(null)}><h2>Tipo de documento · {typing.doc.document}</h2>{error && <Notice tone="error">{error}</Notice>}<p>{typing.doc.supplier_name} · {day(typing.doc.document_date)} · {euro(typing.doc.total_cents)}</p><p>Esta receção foi confirmada sem tipo de documento. Ao gravar, cria-se a compra e o movimento previsto (a crédito) ou real (a dinheiro).</p>
      <ManagedForm onSubmit={e => { e.preventDefault(); assignType(); }}><div className="procurement-grid"><ReceiptFinance confirmedReceipt supplierId={typing.doc.supplier_id} documentDate={typing.doc.document_date} value={typing.values as any} onChange={patch => setTyping(t => t && { ...t, values: { ...t.values, ...patch } })} /></div><div className="actions"><ActionButton type="submit" className="primary" disabled={busy || !typing.values.document_type_id}>Gravar tipo de documento</ActionButton></div></ManagedForm></AppDialog>}
    {selected && !draft && <AppDialog className={`procurement-dialog${selected.kind === "order" ? " business-record-dialog" : ""}`} aria-label="Documento de compras" busy={busy} onRequestClose={() => setSelected(null)}>
      {selected.kind === "order" ? <RecordWorkspace key={selected.id} title={`Encomenda ${selected.document || selected.id.slice(0, 8)}`} subtitle={selected.supplier_name} icon={Truck}
        indicators={<>
          <RecordIndicator label="Total da encomenda" value={euro(selected.total_cents)} icon={Euro} />
          <RecordIndicator label="Estado" value={states[selected.status] || selected.status} tone={selected.status === "received" ? "success" : selected.status === "partial" ? "warning" : "info"} icon={ShoppingCart} />
          <RecordIndicator label="Linhas por receber" value={selected.lines.filter((l: Row) => l.nature === "goods" && Number(l.quantity) > Number(l.received || 0)).length} icon={Package} tone="warning" />
          <RecordIndicator label="Pedidos de clientes" value={new Set(waitingCustomers(selected).map((c: Row) => c.order_id)).size} icon={User} />
        </>}
        sections={[
          { label: "Geral e artigos", icon: Package, content: <RecordGroup title="Encomenda e quantidades" icon={Package}>{error && <Notice tone="error">{error}</Notice>}<p>{selected.supplier_name} · NIF {selected.supplier_nif} · {day(selected.document_date)} · {states[selected.status]}</p><p>Total: <b>{euro(selected.total_cents)}</b>{selected.purchase_id && <> · Por pagar: <b>{euro(selected.remaining_cents)}</b> · Vencimento: {day(selected.due_date)}</>}</p>
      <div className="table-wrap"><table><thead><tr>{["Artigo / descrição", "Quantidade", "Rejeitada", "Aceite", "Recebida", "Faturada", "Devolvida", "Pendente", "Preço / IVA"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{selected.lines.map((l: Row, i: number) => <tr key={i}><td>{l.description}<small>{l.supplier_reference} · {l.unit}</small></td><td>{formatQuantity(l.quantity)}</td><td>{formatQuantity(l.rejected || 0)}</td><td>{formatQuantity(l.accepted ?? Number(l.quantity) - Number(l.rejected || 0))}</td><td>{formatQuantity(l.received || 0)}</td><td>{formatQuantity(l.invoiced || 0)}</td><td>{formatQuantity(l.returned || 0)}</td><td>{formatQuantity(l.pending || 0)}</td><td>{l.price === undefined ? "—" : euro(Number(l.price) * 100)} · {l.vat}%{l.valuation && <small>Ajuste de custo: {euro(Number(l.valuation.value_delta) * 100)}</small>}</td></tr>)}</tbody></table></div>
</RecordGroup> },
          { label: "Clientes e receções", icon: User, content: <RecordGroup title="Clientes e receções parciais" icon={Truck}>      {selected.kind === "receipt" && selected.status === "draft" && (() => { const origin = docs.find((d: Row) => selected.lines.some((l: Row) => l.source_id === d.id) && d.kind === "order"); const waiting = origin ? waitingCustomers(origin) : []; return waiting.length ? <Notice tone="info">Ao confirmar, as quantidades recebidas ficam reservadas para: {waiting.map((c: Row) => `${c.number} · ${c.customer} (${Number(c.waiting).toLocaleString("pt-PT")} ${c.description})`).join("; ")}. O excedente fica em stock geral.</Notice> : null; })()}
      {selected.kind === "order" && waitingCustomers(selected).length > 0 && <Notice tone="info">Clientes à espera desta encomenda: {waitingCustomers(selected).map((c: Row) => `${c.number} · ${c.customer} (${Number(c.waiting).toLocaleString("pt-PT")} ${c.description})`).join("; ")}.</Notice>}
      {!!selected.customer_reservations?.length && <Notice tone="success">Reservado aos clientes: {selected.customer_reservations.map((c: Row) => `${c.number} · ${c.customer} (${Number(c.quantity).toLocaleString("pt-PT")} ${c.description})`).join("; ")}. Contacte-os nas Encomendas de clientes → Prontos a levantar.</Notice>}
      {!!selected.customer_warnings?.length && <Notice tone="warning">Não foi possível reservar: {selected.customer_warnings.join("; ")}. Reserve no pedido do cliente.</Notice>}
      {selected.kind === "receipt" && selected.financial?.planned_id && <Notice tone="info">Dívida registada com movimento previsto{selected.due_date ? ` para ${day(selected.due_date)}` : ""}: entra na previsão de liquidez. <ActionButton className="procurement-inline-action" onClick={() => navigate("Previsão de liquidez")}>Abrir previsão de liquidez</ActionButton></Notice>}
      {selected.kind === "receipt" && selected.financial?.movement_id && <Notice tone="success">Pagamento a dinheiro registado como movimento real.</Notice>}
      {selected.kind === "receipt" && selected.status === "draft" && selected.lines.some((l: Row) => l.nature === "goods" && !l.expiry && catalog.articles.find((a: Row) => a.id === l.article_id)?.lots) && <Notice tone="error">Há artigos com lote sem validade: a receção não pode ser lançada. Edite o rascunho e indique a validade.</Notice>}
      {!!selected.expiry_alerts?.length && <Notice tone={selected.expiry_alerts.some((a: Row) => a.level === "reclamar") ? "error" : "warning"}>Validades curtas: {selected.expiry_alerts.map((a: Row) => `${a.description}${a.lot ? ` (lote ${a.lot})` : ""}: ${day(a.expiry)}, ${a.days} dias${a.level === "reclamar" ? " — reclamar" : ""}`).join("; ")}.
        {selected.expiry_claim?.status === "pendente" ? <> Reclamação ao fornecedor por fazer. {data.can_write && <><ActionButton className="procurement-inline-action" onClick={() => void registerClaim(selected, "reclamada")}>Registar reclamação</ActionButton><ActionButton className="procurement-inline-action" onClick={() => void registerClaim(selected, "sem_reclamacao")}>Não reclamar</ActionButton></>}</>
          : selected.expiry_claim ? ` ${selected.expiry_claim.status === "reclamada" ? "Reclamação registada" : "Sem reclamação"}: ${selected.expiry_claim.note}.` : ""}</Notice>}
{docs.filter((d: Row) => d.kind === "receipt" && d.lines.some((l: Row) => l.source_id === selected.id)).map((d: Row) => <p key={d.id}>{d.document || d.id.slice(0, 8)} · {day(d.document_date)} · {states[d.status] || d.status}</p>)}</RecordGroup> },
          { label: "Operações", icon: ShoppingCart, content: <RecordGroup title="Confirmar e acompanhar" icon={ShoppingCart}>      {data.can_write && selected.status === "draft" && <><label className="procurement-check"><input type="checkbox" checked={overdelivery} onChange={e => setOverdelivery(e.target.checked)} />Autorizo entrega superior à quantidade encomendada</label><label className="procurement-check"><input type="checkbox" checked={differences} onChange={e => setDifferences(e.target.checked)} />Revi e aceito as divergências de preço ou mercadoria</label><div className="actions"><ActionButton className="primary" disabled={busy} onClick={() => run(confirm)}>Confirmar {kinds[selected.kind].toLowerCase()}</ActionButton><ActionButton disabled={busy} onClick={() => { setEditId(selected.id); setDraft(editable(selected)); }}>Editar rascunho</ActionButton></div></>}
      <div className="actions">{data.can_write && <ActionButton onClick={() => start(selected.kind, selected, true)}>Duplicar como rascunho</ActionButton>}{data.can_write && selected.confirmed_at && selected.status !== "cancelled" && <>{selected.kind === "order" && <ActionButton onClick={() => start("receipt", selected)}>Receber mercadoria</ActionButton>}{selected.kind === "order" && selected.lines.some((l: Row) => l.nature !== "goods") && <ActionButton onClick={() => start("invoice", selected)}>Associar fatura de serviços / despesas</ActionButton>}{selected.kind === "receipt" && <><ActionButton onClick={() => start("invoice", selected)}>Associar fatura</ActionButton><ActionButton onClick={() => start("return", selected)}>Devolver mercadoria</ActionButton></>}{selected.kind === "invoice" && selected.lines.some((l: Row) => l.nature === "goods") && <ActionButton onClick={() => start("return", selected)}>Devolver mercadoria</ActionButton>}{selected.kind === "return" && <ActionButton onClick={() => start("credit", selected)}>Associar nota de crédito</ActionButton>}{selected.kind === "order" && <ActionButton onClick={() => { setEmail({ to: suppliers.find(s => s.id === selected.supplier_id)?.email || "", subject: `Encomenda ${selected.document || selected.id.slice(0, 8)}`, message: `Exmos. Senhores,\n\nSegue em anexo a nossa encomenda. Agradecemos confirmação e previsão de entrega.\n\nCom os melhores cumprimentos.` }); setPreview(null); }}>Preparar email ao fornecedor</ActionButton>}</>}{selected.purchase_id && <ActionButton onClick={() => navigate("Conferência documental")}>Conferir, associar pagamento e preparar dossiê</ActionButton>}{data.can_write && selected.status !== "cancelled" && (!selected.confirmed_at || selected.kind === "order") && <ActionButton onClick={() => run(async () => { const reason = window.prompt("Motivo do cancelamento (mínimo 3 caracteres):"); if (!reason) return; await api(`/procurement/${selected.id}/cancel`, { revision: selected.revision, reason }); await load(); })}>Cancelar com motivo</ActionButton>}</div>
      {email && <ManagedForm onSubmit={e => { e.preventDefault(); run(async () => { const next = await api(`/procurement/${selected.id}/email-preview`, { revision: selected.revision, ...email }); setPreview(next); await load(); }); }}><h3>Pré-visualização do email</h3><Field label="Destinatário"><input type="email" required value={email.to} onChange={e => { setEmail({ ...email, to: e.target.value }); setPreview(null); }} /></Field><Field label="Assunto"><input required value={email.subject} onChange={e => { setEmail({ ...email, subject: e.target.value }); setPreview(null); }} /></Field><Field label="Mensagem"><textarea required value={email.message} onChange={e => { setEmail({ ...email, message: e.target.value }); setPreview(null); }} /></Field><p>Será anexado o PDF desta encomenda. Preparar a pré-visualização não envia email.</p><ActionButton type="submit" disabled={busy}>Preparar pré-visualização</ActionButton>{preview && <><p>PDF preparado · {preview.manifest.plan.messages} mensagem · {preview.manifest.plan.total_bytes} bytes</p><ActionButton type="button" className="primary" disabled={busy || preview.status !== "prepared"} onClick={() => run(async () => { if (!await confirmAction({ title: "Enviar encomenda ao fornecedor?", message: `Destinatário: ${email.to}. O envio será efetuado através do SMTP configurado.`, confirmLabel: "Enviar email" })) return; const result = await api(`/procurement/${selected.id}/email-send/${preview.id}`, { revision: preview.revision, request_key: crypto.randomUUID(), confirmed: true }); setPreview(result); await load(); setNotice(result.status === "accepted" ? "Encomenda aceite pelo servidor SMTP." : "Consulte o resultado no histórico de envio."); })}>Confirmar envio por email</ActionButton></>}</ManagedForm>}
</RecordGroup> },
          { label: "Documentos", icon: FileText, content: <RecordGroup title="Documentos e observações" icon={FileText}>      {selected.notes && <p>{selected.notes}</p>}{selected.differences?.map((s: string) => <Notice tone="warning" key={s}>{s}</Notice>)}<div className="actions">{procurementAttachments(selected, purchases).map(attachment => <ActionLink href={attachment.url} target="_blank" rel="noopener noreferrer" key={attachment.url}><Paperclip size={16} />{attachment.name}</ActionLink>)}<a href={`/api/procurement/${selected.id}/pdf`} target="_blank" rel="noreferrer">Imprimir / PDF</a></div>
</RecordGroup> },
          { label: "Histórico", icon: History, content: <RecordGroup title="Histórico de envio" icon={History}>      {!!selected.email_history?.length && <><h3>Histórico de envio</h3>{selected.email_history.map((e: Row) => <p key={e.id}>{day(e.started_at || e.created_at)} · {({ prepared: "Preparado, não enviado", sending: "Envio em curso", accepted: "Aceite pelo SMTP", failed: "Falhou", received: "Receção confirmada" } as Row)[e.status] || e.status}{e.uncertain ? " · Resultado incerto — verificar entrega antes de repetir" : ""}{e.error ? ` · ${e.error}` : ""}</p>)}</>}
{!selected.email_history?.length && <p>Sem envios registados.</p>}</RecordGroup> },
        ]} /> : <><h2>{kinds[selected.kind]} · {selected.document || selected.id.slice(0, 8)}</h2>{error && <Notice tone="error">{error}</Notice>}<p>{selected.supplier_name} · NIF {selected.supplier_nif} · {day(selected.document_date)} · {states[selected.status]}</p><p>Total: <b>{euro(selected.total_cents)}</b>{selected.purchase_id && <> · Por pagar: <b>{euro(selected.remaining_cents)}</b> · Vencimento: {day(selected.due_date)}</>}</p>
      <div className="table-wrap"><table><thead><tr>{["Artigo / descrição", "Quantidade", "Rejeitada", "Aceite", "Recebida", "Faturada", "Devolvida", "Pendente", "Preço / IVA"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{selected.lines.map((l: Row, i: number) => <tr key={i}><td>{l.description}<small>{l.supplier_reference} · {l.unit}</small></td><td>{formatQuantity(l.quantity)}</td><td>{formatQuantity(l.rejected || 0)}</td><td>{formatQuantity(l.accepted ?? Number(l.quantity) - Number(l.rejected || 0))}</td><td>{formatQuantity(l.received || 0)}</td><td>{formatQuantity(l.invoiced || 0)}</td><td>{formatQuantity(l.returned || 0)}</td><td>{formatQuantity(l.pending || 0)}</td><td>{l.price === undefined ? "—" : euro(Number(l.price) * 100)} · {l.vat}%{l.valuation && <small>Ajuste de custo: {euro(Number(l.valuation.value_delta) * 100)}</small>}</td></tr>)}</tbody></table></div>
      {selected.kind === "receipt" && selected.status === "draft" && (() => { const origin = docs.find((d: Row) => selected.lines.some((l: Row) => l.source_id === d.id) && d.kind === "order"); const waiting = origin ? waitingCustomers(origin) : []; return waiting.length ? <Notice tone="info">Ao confirmar, as quantidades recebidas ficam reservadas para: {waiting.map((c: Row) => `${c.number} · ${c.customer} (${Number(c.waiting).toLocaleString("pt-PT")} ${c.description})`).join("; ")}. O excedente fica em stock geral.</Notice> : null; })()}
      {selected.kind === "order" && waitingCustomers(selected).length > 0 && <Notice tone="info">Clientes à espera desta encomenda: {waitingCustomers(selected).map((c: Row) => `${c.number} · ${c.customer} (${Number(c.waiting).toLocaleString("pt-PT")} ${c.description})`).join("; ")}.</Notice>}
      {!!selected.customer_reservations?.length && <Notice tone="success">Reservado aos clientes: {selected.customer_reservations.map((c: Row) => `${c.number} · ${c.customer} (${Number(c.quantity).toLocaleString("pt-PT")} ${c.description})`).join("; ")}. Contacte-os nas Encomendas de clientes → Prontos a levantar.</Notice>}
      {!!selected.customer_warnings?.length && <Notice tone="warning">Não foi possível reservar: {selected.customer_warnings.join("; ")}. Reserve no pedido do cliente.</Notice>}
      {selected.kind === "receipt" && selected.financial?.planned_id && <Notice tone="info">Dívida registada com movimento previsto{selected.due_date ? ` para ${day(selected.due_date)}` : ""}: entra na previsão de liquidez. <ActionButton className="procurement-inline-action" onClick={() => navigate("Previsão de liquidez")}>Abrir previsão de liquidez</ActionButton></Notice>}
      {selected.kind === "receipt" && selected.financial?.movement_id && <Notice tone="success">Pagamento a dinheiro registado como movimento real.</Notice>}
      {selected.kind === "receipt" && selected.status === "draft" && selected.lines.some((l: Row) => l.nature === "goods" && !l.expiry && catalog.articles.find((a: Row) => a.id === l.article_id)?.lots) && <Notice tone="error">Há artigos com lote sem validade: a receção não pode ser lançada. Edite o rascunho e indique a validade.</Notice>}
      {!!selected.expiry_alerts?.length && <Notice tone={selected.expiry_alerts.some((a: Row) => a.level === "reclamar") ? "error" : "warning"}>Validades curtas: {selected.expiry_alerts.map((a: Row) => `${a.description}${a.lot ? ` (lote ${a.lot})` : ""}: ${day(a.expiry)}, ${a.days} dias${a.level === "reclamar" ? " — reclamar" : ""}`).join("; ")}.
        {selected.expiry_claim?.status === "pendente" ? <> Reclamação ao fornecedor por fazer. {data.can_write && <><ActionButton className="procurement-inline-action" onClick={() => void registerClaim(selected, "reclamada")}>Registar reclamação</ActionButton><ActionButton className="procurement-inline-action" onClick={() => void registerClaim(selected, "sem_reclamacao")}>Não reclamar</ActionButton></>}</>
          : selected.expiry_claim ? ` ${selected.expiry_claim.status === "reclamada" ? "Reclamação registada" : "Sem reclamação"}: ${selected.expiry_claim.note}.` : ""}</Notice>}
      {selected.notes && <p>{selected.notes}</p>}{selected.differences?.map((s: string) => <Notice tone="warning" key={s}>{s}</Notice>)}<div className="actions">{procurementAttachments(selected, purchases).map(attachment => <ActionLink href={attachment.url} target="_blank" rel="noopener noreferrer" key={attachment.url}><Paperclip size={16} />{attachment.name}</ActionLink>)}<a href={`/api/procurement/${selected.id}/pdf`} target="_blank" rel="noreferrer">Imprimir / PDF</a></div>
      {data.can_write && selected.status === "draft" && <><label className="procurement-check"><input type="checkbox" checked={overdelivery} onChange={e => setOverdelivery(e.target.checked)} />Autorizo entrega superior à quantidade encomendada</label><label className="procurement-check"><input type="checkbox" checked={differences} onChange={e => setDifferences(e.target.checked)} />Revi e aceito as divergências de preço ou mercadoria</label><div className="actions"><ActionButton className="primary" disabled={busy} onClick={() => run(confirm)}>Confirmar {kinds[selected.kind].toLowerCase()}</ActionButton><ActionButton disabled={busy} onClick={() => { setEditId(selected.id); setDraft(editable(selected)); }}>Editar rascunho</ActionButton></div></>}
      <div className="actions">{data.can_write && <ActionButton onClick={() => start(selected.kind, selected, true)}>Duplicar como rascunho</ActionButton>}{data.can_write && selected.confirmed_at && selected.status !== "cancelled" && <>{selected.kind === "order" && <ActionButton onClick={() => start("receipt", selected)}>Receber mercadoria</ActionButton>}{selected.kind === "order" && selected.lines.some((l: Row) => l.nature !== "goods") && <ActionButton onClick={() => start("invoice", selected)}>Associar fatura de serviços / despesas</ActionButton>}{selected.kind === "receipt" && <><ActionButton onClick={() => start("invoice", selected)}>Associar fatura</ActionButton><ActionButton onClick={() => start("return", selected)}>Devolver mercadoria</ActionButton></>}{selected.kind === "invoice" && selected.lines.some((l: Row) => l.nature === "goods") && <ActionButton onClick={() => start("return", selected)}>Devolver mercadoria</ActionButton>}{selected.kind === "return" && <ActionButton onClick={() => start("credit", selected)}>Associar nota de crédito</ActionButton>}{selected.kind === "order" && <ActionButton onClick={() => { setEmail({ to: suppliers.find(s => s.id === selected.supplier_id)?.email || "", subject: `Encomenda ${selected.document || selected.id.slice(0, 8)}`, message: `Exmos. Senhores,\n\nSegue em anexo a nossa encomenda. Agradecemos confirmação e previsão de entrega.\n\nCom os melhores cumprimentos.` }); setPreview(null); }}>Preparar email ao fornecedor</ActionButton>}</>}{selected.purchase_id && <ActionButton onClick={() => navigate("Conferência documental")}>Conferir, associar pagamento e preparar dossiê</ActionButton>}{data.can_write && selected.status !== "cancelled" && (!selected.confirmed_at || selected.kind === "order") && <ActionButton onClick={() => run(async () => { const reason = window.prompt("Motivo do cancelamento (mínimo 3 caracteres):"); if (!reason) return; await api(`/procurement/${selected.id}/cancel`, { revision: selected.revision, reason }); await load(); })}>Cancelar com motivo</ActionButton>}</div>
      {email && <ManagedForm onSubmit={e => { e.preventDefault(); run(async () => { const next = await api(`/procurement/${selected.id}/email-preview`, { revision: selected.revision, ...email }); setPreview(next); await load(); }); }}><h3>Pré-visualização do email</h3><Field label="Destinatário"><input type="email" required value={email.to} onChange={e => { setEmail({ ...email, to: e.target.value }); setPreview(null); }} /></Field><Field label="Assunto"><input required value={email.subject} onChange={e => { setEmail({ ...email, subject: e.target.value }); setPreview(null); }} /></Field><Field label="Mensagem"><textarea required value={email.message} onChange={e => { setEmail({ ...email, message: e.target.value }); setPreview(null); }} /></Field><p>Será anexado o PDF desta encomenda. Preparar a pré-visualização não envia email.</p><ActionButton type="submit" disabled={busy}>Preparar pré-visualização</ActionButton>{preview && <><p>PDF preparado · {preview.manifest.plan.messages} mensagem · {preview.manifest.plan.total_bytes} bytes</p><ActionButton type="button" className="primary" disabled={busy || preview.status !== "prepared"} onClick={() => run(async () => { if (!await confirmAction({ title: "Enviar encomenda ao fornecedor?", message: `Destinatário: ${email.to}. O envio será efetuado através do SMTP configurado.`, confirmLabel: "Enviar email" })) return; const result = await api(`/procurement/${selected.id}/email-send/${preview.id}`, { revision: preview.revision, request_key: crypto.randomUUID(), confirmed: true }); setPreview(result); await load(); setNotice(result.status === "accepted" ? "Encomenda aceite pelo servidor SMTP." : "Consulte o resultado no histórico de envio."); })}>Confirmar envio por email</ActionButton></>}</ManagedForm>}
      {!!selected.email_history?.length && <><h3>Histórico de envio</h3>{selected.email_history.map((e: Row) => <p key={e.id}>{day(e.started_at || e.created_at)} · {({ prepared: "Preparado, não enviado", sending: "Envio em curso", accepted: "Aceite pelo SMTP", failed: "Falhou", received: "Receção confirmada" } as Row)[e.status] || e.status}{e.uncertain ? " · Resultado incerto — verificar entrega antes de repetir" : ""}{e.error ? ` · ${e.error}` : ""}</p>)}</>}
</>}
    </AppDialog>}
  </section>;
}
