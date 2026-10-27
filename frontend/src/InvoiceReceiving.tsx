import { useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, ExternalLink, Eye, EyeOff, FileText, PackagePlus, Plus, Search, Trash2 } from "lucide-react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import Processing from "./tables/Processing";
import RecordDialog from "./tables/RecordDialog";
import SupplierPicker from "./components/SupplierPicker";
import ReceiptFinance from "./ReceiptFinance";
import type { TableInfo } from "./tables/types";
import "./tables/TablePage.css";

type Row = Record<string, any>;
const euro = (cents: number | null | undefined) =>
  cents == null ? "—" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(cents / 100);
const money = (value: string | number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR", maximumFractionDigits: 4 }).format(Number(value || 0));
const day = (value: string | null) => (value ? value.slice(0, 10).split("-").reverse().join("/") : "—");
const METHODS: Row = {
  aprendido: "Correspondência aprendida",
  codigo_barras: "Código de barras",
  referencia_fornecedor: "Referência do fornecedor",
  codigo: "Código do artigo",
  semelhante: "Nome semelhante",
  lancado: "Lançado",
};
const NEW = "__novo__";
// Acerto máximo de arredondamento por componente (base e IVA), em cêntimos (como no servidor).
const ROUNDING_LIMIT = 10;
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Vencimento pelo prazo da ficha do fornecedor (mesma regra do servidor, payment_terms.due_date). */
function dueDate(documentDate: string, term: Row | null | undefined) {
  if (!term || !documentDate) return "";
  const [year, month, day] = documentDate.split("-").map(Number);
  if (term.term_type === "apos" && term.days != null) {
    const due = new Date(Date.UTC(year, month - 1, day + Number(term.days)));
    return due.toISOString().slice(0, 10);
  }
  if (term.term_type === "fim_mes" || term.term_type === "fim_mes_seguinte") {
    const last = new Date(Date.UTC(year, month + (term.term_type === "fim_mes_seguinte" ? 1 : 0), 0));
    return last.toISOString().slice(0, 10);
  }
  return "";
}

/** Linha que ainda precisa de artigo: criar um novo ou escolher outro. */
const needsArticle = (line: Row) => line.include && (line.choice === NEW || line.choice === OTHER);
const OTHER = "__outro__";

const round = (value: number, places = 4) => String(Number(value.toFixed(places)));

/**
 * Desconto global do documento: só quando todas as linhas o partilham (ex.: 20 % comercial).
 * Se apenas algumas linhas o tiverem (OCR imperfeito), fica no desconto dessas linhas.
 */
function documentDiscount(lines: Row[]) {
  const globals = new Set(lines.filter(line => !line.service).map(line => Number(line.global_discount || 0)));
  const [only] = [...globals];
  return globals.size === 1 && only > 0 ? only : 0;
}

/** Linha em revisão: artigo escolhido (existente ou novo), quantidades, preço e descontos. */
function initialLine(line: Row, global: number) {
  const own = Number(line.discount || 0);
  const extra = global ? 0 : Number(line.global_discount || 0);
  return {
    ...line,
    include: !line.service,
    choice: line.selected?.article_id || NEW,
    search: "",
    qty: line.quantity,
    bonusQty: line.bonus || "0",
    priceValue: line.price,
    discountValue: round(100 - (100 - own) * (1 - extra / 100)),
    vatValue: line.vat || line.proposal?.vat_rate || "23",
    lotValue: line.lot || "",
    expiryValue: line.expiry || "",
    newArticle: { ...line.proposal },
  };
}

/** Linha acrescentada à mão (fatura sem linhas legíveis ou linhas em falta): artigo novo por omissão. */
function blankLine(global: number) {
  return {
    ...initialLine({
      reference: "", barcode: "", lot: "", expiry: "", description: "", quantity: "1", bonus: "0", price: "0",
      discount: "0", vat: "23", total: "0", unit_cost: "0", global_discount: "0", service: false, selected: null,
      suggestions: [], evidence: "",
      proposal: { code: "", name: "", unit: "un", precision: 0, vat_rate: "23", supplier_reference: "", barcodes: [], lots: false, expiry: false },
    }, global),
    manual: true,
  };
}

/** Valor líquido e custo por unidade recebida (faturadas + bónus), como no servidor. */
function lineValues(line: Row, global: number) {
  const quantity = Number(line.qty || 0), bonus = Number(line.bonusQty || 0);
  // O desconto global (comercial) não se aplica a portes/serviços.
  const net = quantity * Number(line.priceValue || 0) * (1 - Number(line.discountValue || 0) / 100) * (1 - (line.service ? 0 : global) / 100);
  const units = quantity + bonus;
  return { net, units, cost: units ? net / units : 0 };
}

/**
 * Receção a partir da fatura do fornecedor (PDF no Arquivo digital): lê as linhas (texto ou OCR),
 * relaciona-as com os artigos e propõe fichas novas. Nada entra em stock antes de confirmar.
 */
export default function InvoiceReceiving({ articles, stores, categories, onClose, onDone, editReceipt }: {
  // Receção já lançada a partir de fatura: reabre a mesma janela com o lançamento gravado.
  editReceipt?: string;
  articles: Row[];
  stores: Row[];
  categories: Row[];
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [supplierTable, setSupplierTable] = useState<TableInfo | null>(null);
  const [supplierName, setSupplierName] = useState("");
  const [documents, setDocuments] = useState<Row[] | null>(null);
  const [filter, setFilter] = useState("");
  const [reading, setReading] = useState<Row | null>(null);
  const [result, setResult] = useState<Row | null>(null);
  const [header, setHeader] = useState<Row>({});
  const [lines, setLines] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [requestKey] = useState(() => crypto.randomUUID());
  // Original da fatura ao lado da conferência (descarregado para um URL local, como no Arquivo).
  const [pdf, setPdf] = useState<{ url: string; mime: string } | null>(null);
  const [pdfError, setPdfError] = useState("");
  const [showPdf, setShowPdf] = useState(() => window.matchMedia("(min-width: 1100px)").matches);
  const pdfUrl = useRef("");
  const active = useMemo(() => articles.filter(a => a.active !== false), [articles]);
  const byLabel = useMemo(() => new Map(active.map(a => [`${a.code} · ${a.name}`, a])), [active]);

  useEffect(() => {
    if (editReceipt) {
      setBusy(true);
      api(`/purchase-receiving/receipts/${editReceipt}`).then((data: Row) => {
        const global = Number(data.receipt.global_discount || 0);
        setReading({ id: data.document.id, file: "", document: data.document.number });
        setResult(data);
        setHeader({
          supplier_id: data.document.supplier_id || "",
          document: data.document.number || "",
          document_type: data.document.document_type,
          document_date: data.document.issue_date,
          warehouse_id: data.receipt.warehouse_id || data.warehouses[0]?.id || "",
          document_type_id: data.receipt.document_type_id || "",
          payment_method: data.receipt.payment_method || "",
          due_date: data.receipt.due_date || data.document.issue_date,
          credit_note_purchase_ids: data.receipt.credit_note_purchase_ids || [],
          global_discount: String(global),
          match_document_totals: !!data.receipt.match_document_totals,
        });
        setLines(data.lines.map((line: Row) => initialLine(line, global)));
      }).catch(e => setError(e.message)).finally(() => setBusy(false));
      return;
    }
    api("/purchase-receiving/documents").then(r => setDocuments(r.rows)).catch(e => setError(e.message));
  }, [editReceipt]);

  useEffect(() => {
    setPdf(null); setPdfError("");
    if (!reading) return;
    let alive = true;
    fetch(`/api/archive/documents/${reading.id}/file`, { credentials: "same-origin" }).then(async response => {
      if (!response.ok) { const info = await response.json().catch(() => ({})); throw new Error(info.detail || "Original indisponível."); }
      const blob = await response.blob();
      if (!alive) return;
      if (pdfUrl.current) URL.revokeObjectURL(pdfUrl.current);
      pdfUrl.current = URL.createObjectURL(blob);
      setPdf({ url: pdfUrl.current, mime: blob.type });
    }).catch(e => { if (alive) setPdfError((e as Error).message); });
    return () => { alive = false; };
  }, [reading?.id]);
  useEffect(() => () => { if (pdfUrl.current) URL.revokeObjectURL(pdfUrl.current); }, []);

  async function read(doc: Row) {
    setReading(doc); setResult(null); setError(""); setBusy(true);
    try {
      const data: Row = await api(`/purchase-receiving/${doc.id}/read`, {});
      setResult(data);
      setHeader({
        supplier_id: data.document.supplier_id || "",
        document: data.document.number || "",
        document_type: data.document.document_type,
        document_date: data.document.issue_date,
        warehouse_id: data.warehouses.find((w: Row) => !data.document.store_id || w.store_id === data.document.store_id)?.id || data.warehouses[0]?.id || "",
        document_type_id: "", payment_method: "", due_date: data.document.issue_date, credit_note_purchase_ids: [],
        match_document_totals: false,
      });
      const global = documentDiscount(data.lines);
      setHeader(current => ({ ...current, global_discount: String(global) }));
      setLines(data.lines.map((line: Row) => initialLine(line, global)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const change = (index: number, patch: Row) => setLines(current => current.map((line, i) => (i === index ? { ...line, ...patch } : line)));
  const included = lines.filter(line => line.include);
  const globalDiscount = Number(header.global_discount || 0);
  const total = included.reduce((sum, line) => sum + lineValues(line, globalDiscount).net, 0);
  // Resumo do IVA de todas as linhas lidas (o documento inclui portes/serviços, mesmo sem entrada em stock).
  const summary = useMemo(() => {
    const rates = new Map<number, number>();
    for (const line of lines) {
      const rate = Number(line.vatValue || 0);
      rates.set(rate, (rates.get(rate) || 0) + lineValues(line, globalDiscount).net);
    }
    const rows = [...rates.entries()].sort(([a], [b]) => a - b).map(([rate, base]) => {
      const baseCents = Math.round(base * 100);
      return { rate, base: baseCents, vat: Math.round(baseCents * rate / 100) };
    });
    const base = rows.reduce((sum, row) => sum + row.base, 0);
    const vat = rows.reduce((sum, row) => sum + row.vat, 0);
    return { rows, base, vat, total: base + vat };
  }, [lines, globalDiscount]);
  const unresolved = included.filter(line => line.choice === OTHER);
  // Diferença para a fatura do arquivo, por componente; o acerto iguala-a (só arredondamentos).
  const gap = useMemo(() => {
    const doc = result?.document;
    if (!doc || doc.total_cents == null) return null;
    return doc.base_cents != null
      ? { base: doc.base_cents - summary.base, vat: doc.total_cents - doc.base_cents - summary.vat }
      : { base: 0, vat: doc.total_cents - summary.total };
  }, [result, summary]);
  const canAdjust = !!gap && (gap.base !== 0 || gap.vat !== 0) && Math.abs(gap.base) <= ROUNDING_LIMIT && Math.abs(gap.vat) <= ROUNDING_LIMIT;
  const adjusting = canAdjust && !!header.match_document_totals;
  const adjustment = adjusting && gap ? { base: gap.base, vat: gap.vat, total: gap.base + gap.vat } : { base: 0, vat: 0, total: 0 };

  async function confirm() {
    const missing = included.findIndex(line => !line.description.trim() || Number(line.qty) <= 0
      || (line.choice === NEW && (!String(line.newArticle.code || "").trim() || !String(line.newArticle.name || "").trim())));
    if (missing >= 0) {
      const line = included[missing];
      setError(`Linha ${lines.indexOf(line) + 1}: ${!line.description.trim() ? "indique a descrição" : Number(line.qty) <= 0 ? "indique a quantidade" : "indique o código e a designação do artigo novo"}.`);
      return;
    }
    setBusy(true); setError("");
    try {
      const body = {
        ...header,
        global_discount: String(globalDiscount || 0),
        document_type_id: header.document_type_id || null,
        payment_method: header.payment_method || null,
        due_date: header.due_date || null,
        credit_note_purchase_ids: header.credit_note_purchase_ids || [],
        match_document_totals: adjusting,
        request_key: requestKey,
        lines: included.map(line => ({
          ...(line.choice === NEW ? { new_article: { ...line.newArticle, vat_rate: String(line.newArticle.vat_rate) } } : { article_id: line.choice }),
          description: line.description,
          reference: line.reference,
          barcode: line.barcode,
          quantity: String(line.qty),
          bonus: String(line.bonusQty || 0),
          price: String(line.priceValue),
          discount: String(line.discountValue || 0),
          vat: String(line.vatValue || 0),
          lot: line.lotValue,
          expiry: line.expiryValue || null,
        })),
      };
      if (editReceipt) {
        const saved: Row = await api(`/purchase-receiving/receipts/${editReceipt}?revision=${result!.receipt.revision}`, body, { method: "PUT" });
        onDone(`Receção atualizada: ${saved.lines} linhas; os movimentos de stock foram gerados de novo.`);
        return;
      }
      const done: Row = await api(`/purchase-receiving/${reading!.id}/confirm`, body);
      onDone(`Receção registada: ${done.lines} linhas em stock${done.created_articles ? `, ${done.created_articles} artigos novos criados` : ""}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const shown = (documents || []).filter(d => `${d.supplier_name} ${d.supplier_nif} ${d.document} ${d.file}`.toLocaleLowerCase("pt-PT").includes(filter.toLocaleLowerCase("pt-PT")));

  const split = reading && showPdf;
  return <AppDialog autoOpen busy={busy} className={`procurement-dialog invoice-receiving${split ? " has-pdf" : ""}`} onRequestClose={onClose} aria-labelledby="invoice-receiving-title">
    <div className="panel-title invoice-receiving-title">
      <h2 id="invoice-receiving-title">{editReceipt ? "Editar receção a partir de fatura" : "Receção a partir de fatura"}</h2>
      {reading && <div className="invoice-receiving-pdf-actions">
        <ActionButton type="button" aria-pressed={showPdf} onClick={() => setShowPdf(!showPdf)}>
          {showPdf ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}{showPdf ? "Ocultar fatura" : "Mostrar fatura"}
        </ActionButton>
        {pdf && <a className="ui-action" href={pdf.url} target="_blank" rel="noreferrer"><ExternalLink size={16} aria-hidden="true" />Abrir noutra janela</a>}
      </div>}
    </div>
    <div className={split ? "invoice-receiving-split" : undefined}>
    {split && <aside className="invoice-receiving-pdf" aria-label="Original da fatura">
      {pdf ? (pdf.mime === "application/pdf"
        ? <iframe title={`Original de ${reading.file || "fatura"}`} src={pdf.url} />
        : <img src={pdf.url} alt={`Original de ${reading.file || "fatura"}`} />)
        : pdfError ? <Notice tone="warning">{pdfError}</Notice> : <Processing label="A abrir o original" />}
    </aside>}
    <div className="invoice-receiving-form">
    {error && <Notice tone="error">{error}</Notice>}
    {!reading && <>
      <p className="footnote">Escolha a fatura do fornecedor guardada no Arquivo digital. As linhas são lidas do PDF (ou por OCR, nas digitalizações) e relacionadas com os artigos; nada entra em stock antes de confirmar.</p>
      <Field label="Procurar fatura"><input type="search" value={filter} placeholder="Fornecedor, NIF ou número" onChange={e => setFilter(e.target.value)} /></Field>
      {!documents && !error && <Processing label="A carregar faturas do arquivo" />}
      {documents && <div className="table-wrap"><table className="invoice-receiving-docs">
        <thead><tr><th>Data</th><th>Fornecedor</th><th>Documento</th><th>Total</th><th>Estado</th><th aria-label="Ações"></th></tr></thead>
        <tbody>{shown.map(d => <tr key={d.id}>
          <td>{day(d.issue_date)}</td>
          <td>{d.supplier_name || "Fornecedor por identificar"}<small>{d.supplier_nif || d.file}</small></td>
          <td>{d.document || "—"}</td>
          <td>{euro(d.total_cents)}</td>
          <td>{d.receipt_id ? <span className="status-badge status-success"><CheckCircle2 size={14} aria-hidden="true" />Recebida</span> : <span className="status-badge status-warning">Por receber</span>}</td>
          <td><ActionButton disabled={!!d.receipt_id || busy} onClick={() => read(d)}><FileText size={16} aria-hidden="true" />Ler fatura</ActionButton></td>
        </tr>)}</tbody>
      </table>{!shown.length && <p>Sem faturas no arquivo para esta pesquisa.</p>}</div>}
    </>}
    {reading && !result && busy && <Processing label="A ler a fatura (nas digitalizações o OCR pode demorar alguns segundos)" />}
    {reading && result && <>
      <div className="procurement-grid">
        <SupplierPicker
          items={result.suppliers}
          selectedName={result.suppliers.find((s: Row) => s.id === header.supplier_id)?.name || ""}
          loading={false}
          canCreate
          onSelect={supplier => setHeader(current => ({ ...current, supplier_id: supplier?.id || "" }))}
          onRequestCreate={name => { setSupplierName(name); api("/tables/suppliers").then(setSupplierTable).catch(e => setError(e.message)); }}
        />
        <Field label="Documento do fornecedor"><input required value={header.document} onChange={e => setHeader({ ...header, document: e.target.value })} /></Field>
        <Field label="Data do documento"><input required type="date" value={header.document_date} onChange={e => setHeader({ ...header, document_date: e.target.value })} /></Field>
        <Field label="Armazém de entrada"><select required value={header.warehouse_id} onChange={e => setHeader({ ...header, warehouse_id: e.target.value })}>
          {result.warehouses.map((w: Row) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select></Field>
        <Field label="Desconto global (%)"><input type="number" min="0" max="100" step="any" value={header.global_discount}
          title="Desconto comercial do documento, aplicado a todas as linhas (além do desconto de cada linha)."
          onChange={e => setHeader({ ...header, global_discount: e.target.value })} /></Field>
        {(() => {
          const term = result.suppliers.find((s: Row) => s.id === header.supplier_id)?.payment_term;
          const due = dueDate(header.document_date, term);
          return <>
            <Field label="Prazo de pagamento"><input readOnly value={header.supplier_id ? term?.name || "Sem prazo na ficha do fornecedor" : "—"} title="Definido na ficha do fornecedor (Tabelas → Fornecedores)." /></Field>
            <ReceiptFinance supplierId={header.supplier_id} documentDate={header.document_date} defaultDueDate={due || undefined}
              suggestedTypeId={editReceipt ? null : result.document.suggested_document_type_id}
              value={{ document_type_id: header.document_type_id || "", payment_method: header.payment_method || "", due_date: header.due_date || header.document_date, credit_note_purchase_ids: header.credit_note_purchase_ids || [] }}
              onChange={patch => setHeader(current => ({ ...current, ...patch }))} />
          </>;
        })()}
      </div>
      {!header.supplier_id && <Notice tone="warning">
        {result.document.supplier_nif ? `O NIF ${result.document.supplier_nif} não corresponde a nenhum fornecedor.` : "Não foi possível identificar o fornecedor."} Escolha-o na lista ou crie-o agora.
        <ActionButton type="button" disabled={busy} onClick={() => { setSupplierName(""); api("/tables/suppliers").then(setSupplierTable).catch(e => setError(e.message)); }}>
          <PackagePlus size={16} aria-hidden="true" />Criar fornecedor
        </ActionButton>
      </Notice>}
      {supplierTable && <RecordDialog
        table={supplierTable}
        context={{ stores: stores.map(s => ({ id: s.id, name: s.name })), categories: categories.map(c => ({ id: c.id, name: c.name, active: c.active })) }}
        mode="create"
        initialValues={{ tax_number: result.document.supplier_nif || "", name: supplierName || result.document.supplier_name || "" }}
        onClose={() => setSupplierTable(null)}
        onCreated={(id, values) => {
          // A ficha devolve o identificador da tabela («local:…»); a receção usa o da entidade.
          const supplierId = id.replace(/^local:/, "");
          setResult(current => current && { ...current, suppliers: [...current.suppliers, { id: supplierId, name: values.name, tax_number: values.tax_number, payment_term: null }] });
          setHeader(current => ({ ...current, supplier_id: supplierId }));
          // O prazo escolhido na ficha nova passa logo para o cabeçalho (vencimento).
          if (values.payment_term_id) api("/payment-terms").then((terms: Row[]) => {
            const term = terms.find(t => t.id === values.payment_term_id);
            if (term) setResult(current => current && { ...current, suppliers: current.suppliers.map((s: Row) => s.id === supplierId ? { ...s, payment_term: { name: term.name, term_type: term.term_type, days: term.days } } : s) });
          }).catch(() => undefined);
        }}
        onSaved={() => setSupplierTable(null)}
      />}
      <ul className="table-sync-summary" aria-label="Leitura da fatura">
        <li className="has-items"><FileText size={16} aria-hidden="true" />{result.source === "rececao" ? "Lançamento gravado" : result.source === "ocr" ? "Lida por OCR" : "Lida do texto do PDF"}</li>
        <li className="has-items">{plural(lines.length, "linha", "linhas")}</li>
        <li className={lines.filter(l => l.selected).length ? "has-items" : ""}><CheckCircle2 size={16} aria-hidden="true" />{plural(lines.filter(l => l.selected).length, "relacionada", "relacionadas")}</li>
        <li className={lines.filter(l => !l.selected).length ? "has-items" : ""}><PackagePlus size={16} aria-hidden="true" />{plural(lines.filter(l => !l.selected).length, "artigo novo proposto", "artigos novos propostos")}</li>
        {result.check.matches === true && <li className="has-items invoice-receiving-ok"><CheckCircle2 size={16} aria-hidden="true" />Soma das linhas = base do documento ({euro(result.check.document_base_cents)})</li>}
      </ul>
      {result.warnings.map((w: string) => <Notice key={w} tone="warning">{w}</Notice>)}
      <datalist id="invoice-receiving-articles">{active.map(a => <option key={a.id} value={`${a.code} · ${a.name}`} />)}</datalist>
      {lines.some(needsArticle) && <p className="invoice-receiving-pending" role="status">
        <CircleAlert size={16} aria-hidden="true" /> {plural(lines.filter(needsArticle).length, "linha precisa", "linhas precisam")} de artigo (criar ou associar): assinaladas a vermelho.
      </p>}
      {lines.map((line, index) => <fieldset key={index} className={`invoice-receiving-line${line.include ? "" : " is-excluded"}${needsArticle(line) ? " needs-article" : ""}`}>
        <legend>
          <label className="invoice-receiving-include"><input type="checkbox" aria-label={`Incluir ${line.description || `linha ${index + 1}`}`} checked={line.include} onChange={e => change(index, { include: e.target.checked })} />
            {line.manual ? <span>Linha {index + 1} (acrescentada)</span> : <b>{line.description}</b>}</label>
          {line.manual && <ActionButton type="button" className="danger" aria-label={`Remover linha ${index + 1}`} onClick={() => setLines(current => current.filter((_, i) => i !== index))}><Trash2 size={16} aria-hidden="true" />Remover</ActionButton>}
          {needsArticle(line) && <span className="invoice-receiving-badge"><CircleAlert size={14} aria-hidden="true" />{line.choice === NEW ? "Artigo por criar" : "Artigo por associar"}</span>}
          {!line.manual && <small>{[line.reference && `Ref. ${line.reference}`, line.barcode && line.barcode !== line.reference && `EAN ${line.barcode}`, `${line.quantity}${Number(line.bonus) ? ` + ${line.bonus} bónus` : ""} × ${money(line.price)}${Number(line.discount) ? ` − ${line.discount} %` : ""}${Number(line.global_discount) ? ` − ${line.global_discount} % global` : ""} = ${money(line.total)}`].filter(Boolean).join(" · ")}</small>}
          {line.service && <small className="invoice-receiving-hint">Parece portes/serviço: não entra em stock salvo se o incluir.</small>}
          {line.include && ((line.choice === NEW && line.newArticle.service) || (line.choice === line.selected?.article_id && line.selected?.service)) && <small className="invoice-receiving-hint">Serviço: conta no valor e no IVA da fatura, mas não entra em stock.</small>}
        </legend>
        {line.manual && <Field label="Descrição"><input required value={line.description} placeholder="Como aparece na fatura"
          onChange={e => {
            const description = e.target.value;
            // A designação do artigo novo acompanha a descrição enquanto não for alterada à mão.
            const follows = !line.newArticle.name || line.newArticle.name === line.description;
            change(index, { description, ...(follows ? { newArticle: { ...line.newArticle, name: description } } : {}) });
          }} /></Field>}
        <div className="invoice-receiving-grid">
          <Field label="Artigo">
            <select aria-label={`Artigo de ${line.description}`} value={line.choice} disabled={!line.include} onChange={e => change(index, { choice: e.target.value })}>
              {line.selected && <option value={line.selected.article_id}>{line.selected.code} · {line.selected.name} ({METHODS[line.selected.method]})</option>}
              {line.suggestions.filter((s: Row) => s.article_id !== line.selected?.article_id).map((s: Row) => <option key={s.article_id} value={s.article_id}>{s.code} · {s.name} (semelhante {Math.round(s.score * 100)} %)</option>)}
              {line.choice !== NEW && line.choice !== OTHER && !line.selected && !line.suggestions.some((s: Row) => s.article_id === line.choice) && <option value={line.choice}>{active.find(a => a.id === line.choice)?.name || "Artigo escolhido"}</option>}
              <option value={NEW}>Criar artigo novo</option>
              <option value={OTHER}>Procurar outro artigo…</option>
            </select>
          </Field>
          {line.choice === OTHER && <Field label="Procurar artigo"><input list="invoice-receiving-articles" value={line.search} placeholder="Código ou nome"
            onChange={e => { const found = byLabel.get(e.target.value); change(index, found ? { choice: found.id, search: "" } : { search: e.target.value }); }} /></Field>}
          <Field label="Quantidade"><input required type="number" min="0.000001" step="any" disabled={!line.include} value={line.qty} onChange={e => change(index, { qty: e.target.value })} /></Field>
          <Field label="Bónus"><input type="number" min="0" step="any" disabled={!line.include} value={line.bonusQty} onChange={e => change(index, { bonusQty: e.target.value })} /></Field>
          <Field label="Preço unitário (€)"><input required type="number" min="0" step="any" disabled={!line.include} value={line.priceValue} onChange={e => change(index, { priceValue: e.target.value })} /></Field>
          <Field label="Desconto (%)"><input type="number" min="0" max="100" step="any" disabled={!line.include} value={line.discountValue} onChange={e => change(index, { discountValue: e.target.value })} /></Field>
          <Field label="IVA (%)"><select value={String(Number(line.vatValue))} onChange={e => change(index, { vatValue: e.target.value })}>
            {[...new Set(["0", "6", "13", "23", String(Number(line.vatValue))])].map(v => <option key={v} value={v}>{v}</option>)}
          </select></Field>
          {(() => { const v = lineValues(line, globalDiscount); return <div className="invoice-receiving-cost" aria-label={`Valores de ${line.description}`}>
            <span>Valor líquido <b>{money(v.net)}</b></span>
            <span>Custo unitário <b>{money(v.cost)}</b>{v.units !== Number(line.qty) ? ` · ${round(v.units, 6)} un. em stock` : ""}</span>
          </div>; })()}
          <Field label="Lote"><input disabled={!line.include} value={line.lotValue} onChange={e => change(index, { lotValue: e.target.value })} /></Field>
          <Field label="Validade"><input type="date" disabled={!line.include} value={line.expiryValue} onChange={e => change(index, { expiryValue: e.target.value })} /></Field>
        </div>
        {line.include && line.choice === NEW && <div className="invoice-receiving-new">
          <span className="invoice-receiving-new-title"><PackagePlus size={16} aria-hidden="true" />Artigo novo</span>
          {line.suggestions.length > 0 && <small className="invoice-receiving-hint">Há {plural(line.suggestions.length, "artigo", "artigos")} com nome semelhante na lista «Artigo»: confirme que não é {line.suggestions.length === 1 ? "esse" : "um deles"} antes de criar.</small>}
          <Field label="Código"><input required value={line.newArticle.code} onChange={e => change(index, { newArticle: { ...line.newArticle, code: e.target.value } })} /></Field>
          <Field label="Designação"><input required value={line.newArticle.name} onChange={e => change(index, { newArticle: { ...line.newArticle, name: e.target.value } })} /></Field>
          <Field label="IVA (%)"><select value={String(line.newArticle.vat_rate)} onChange={e => change(index, { newArticle: { ...line.newArticle, vat_rate: e.target.value } })}>{["0", "6", "13", "23"].map(v => <option key={v}>{v}</option>)}</select></Field>
          <label className="stock-check"><input type="checkbox" checked={!!line.newArticle.service} onChange={e => change(index, { newArticle: { ...line.newArticle, service: e.target.checked, ...(e.target.checked ? { lots: false, expiry: false } : {}) } })} />Serviço (não movimenta stock)</label>
          <label className="stock-check"><input type="checkbox" checked={line.newArticle.lots} disabled={!!line.newArticle.service} onChange={e => change(index, { newArticle: { ...line.newArticle, lots: e.target.checked, expiry: e.target.checked && line.newArticle.expiry } })} />Controlar lotes</label>
          <label className="stock-check"><input type="checkbox" checked={line.newArticle.expiry} onChange={e => change(index, { newArticle: { ...line.newArticle, expiry: e.target.checked, lots: e.target.checked || line.newArticle.lots } })} />Controlar validades</label>
        </div>}
      </fieldset>)}
      {!lines.length && <p>Não há linhas lidas: acrescente-as a partir do original (ao lado).</p>}
      <div className="actions">
        <ActionButton type="button" onClick={() => setLines(current => [...current, blankLine(globalDiscount)])}><Plus size={16} aria-hidden="true" />Adicionar linha</ActionButton>
      </div>
      {lines.length > 0 && <section className="invoice-receiving-summary" aria-label="Resumo do documento">
        <h3>Resumo do IVA e total do documento</h3>
        <div className="table-wrap"><table>
          <thead><tr><th>Taxa de IVA</th><th className="numeric">Incidência</th><th className="numeric">IVA</th><th className="numeric">Total</th></tr></thead>
          <tbody>{summary.rows.map(row => <tr key={row.rate}><td>{row.rate} %</td><td className="numeric">{euro(row.base)}</td><td className="numeric">{euro(row.vat)}</td><td className="numeric">{euro(row.base + row.vat)}</td></tr>)}</tbody>
          <tfoot>
            {(() => {
              const doc = result.document;
              const known = doc.total_cents != null;
              // Calculado vs. fatura, coluna a coluna: igual, arredondamento (≤ 2 cêntimos) ou diferente.
              const columns = [
                { calc: summary.base + adjustment.base, doc: doc.base_cents },
                { calc: summary.vat + adjustment.vat, doc: doc.base_cents != null && known ? doc.total_cents - doc.base_cents : null },
                { calc: summary.total + adjustment.total, doc: known ? doc.total_cents : null },
              ].map(c => {
                const diff = c.doc == null ? null : c.calc - c.doc;
                const state = diff == null ? "" : diff === 0 ? "is-equal" : Math.abs(diff) <= 2 ? "is-rounding" : "is-different";
                return { ...c, diff, state };
              });
              const label = (state: string) => state === "is-equal" ? "igual" : state === "is-rounding" ? "arredondamento" : state === "is-different" ? "diferente" : "";
              return <>
                {adjusting && <tr className="invoice-receiving-adjustment"><th>Acerto de arredondamento</th>{[adjustment.base, adjustment.vat, adjustment.total].map((value, i) => <td key={i} className="numeric">{value > 0 ? "+" : ""}{euro(value)}</td>)}</tr>}
                <tr className="invoice-receiving-calculated"><th>{adjusting ? "Total com acerto" : "Total calculado"}</th>{columns.map((c, i) => <td key={i} className={`numeric ${c.state}`}>{i === 2 ? <b>{euro(c.calc)}</b> : euro(c.calc)}</td>)}</tr>
                {known && <tr className="invoice-receiving-document"><th>Fatura (arquivo)</th>{columns.map((c, i) => <td key={i} className={`numeric ${c.state}`}>{c.doc == null ? "—" : i === 2 ? <b>{euro(c.doc)}</b> : euro(c.doc)}</td>)}</tr>}
                {known && <tr className="invoice-receiving-difference"><th>Diferença</th>{columns.map((c, i) => <td key={i} className={`numeric ${c.state}`}>
                  {c.diff == null ? "—" : <><span className="invoice-receiving-diff-value">{c.diff > 0 ? "+" : ""}{euro(c.diff)}</span><small>{label(c.state)}</small></>}
                </td>)}</tr>}
              </>;
            })()}
          </tfoot>
        </table></div>
        {result.document.total_cents != null && (() => {
          const diff = summary.total - result.document.total_cents;
          const toggle = <ActionButton type="button" disabled={busy} onClick={() => setHeader(current => ({ ...current, match_document_totals: !adjusting }))}>
            {adjusting ? "Retirar acerto" : "Acertar ao total da fatura"}
          </ActionButton>;
          if (adjusting) return <div className="invoice-receiving-result is-equal"><p><CheckCircle2 size={16} aria-hidden="true" /> Com o acerto de arredondamento de {euro(adjustment.total)}, a base e o IVA ficam iguais aos da fatura. O acerto entra no documento financeiro; o custo dos artigos em stock não muda.</p>{toggle}</div>;
          if (diff === 0) return <p className="invoice-receiving-result is-equal"><CheckCircle2 size={16} aria-hidden="true" /> O total calculado coincide com o total da fatura.</p>;
          if (Math.abs(diff) <= 2) return <div className="invoice-receiving-result is-rounding"><p><CircleAlert size={16} aria-hidden="true" /> Diferença de arredondamento de {euro(Math.abs(diff))} no total: o IVA é arredondado por taxa e pode diferir do fornecedor. Pode confirmar assim ou acertar ao total da fatura.</p>{canAdjust && toggle}</div>;
          return <div className="invoice-receiving-result is-different"><p><CircleAlert size={16} aria-hidden="true" /> Diferença de {euro(diff)} para o total da fatura: confira quantidades, descontos, taxas de IVA, portes ou linhas em falta.{canAdjust ? " Se for só arredondamento, pode acertar ao total da fatura." : ""}</p>{canAdjust && toggle}</div>;
        })()}
        {lines.some(line => !line.include) && <p className="footnote">Inclui as linhas desmarcadas (por exemplo, portes): fazem parte da fatura, mas não entram em stock.</p>}
      </section>}
      <div className="ui-dialog-actions">
        <span className="invoice-receiving-total">{plural(included.length, "linha", "linhas")} · {money(total)} sem IVA</span>
        {!editReceipt && <ActionButton type="button" disabled={busy} onClick={() => { setReading(null); setResult(null); }}><Search size={16} aria-hidden="true" />Outra fatura</ActionButton>}
        <ActionButton type="button" className="primary" disabled={busy || !included.length || !header.supplier_id || !header.warehouse_id || !header.document_type_id || unresolved.length > 0 || (!!editReceipt && !result.receipt?.editable)} onClick={confirm}>
          {busy ? "A registar…" : editReceipt ? `Guardar alterações (${included.length})` : `Confirmar receção (${included.length})`}
        </ActionButton>
      </div>
      {unresolved.length > 0 && <p className="footnote"><CircleAlert size={14} aria-hidden="true" /> Escolha o artigo nas linhas em «Procurar outro artigo…».</p>}
    </>}
    </div>
    </div>
  </AppDialog>;
}
