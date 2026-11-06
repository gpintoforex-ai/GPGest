import { useEffect, useState, type ReactNode } from "react";
import { Building2, User, Phone, CreditCard, ShoppingCart, Truck, FileText, History, Package, Euro, CircleAlert, CalendarClock, Info } from "lucide-react";
import { api } from "../lib/api";
import { ActionLink, Notice } from "../components/InteractionUI";
import { RecordGroup, RecordHistory, RecordIndicator } from "../components/RecordSheet";
import { RecordWorkspace } from "../components/RecordWorkspace";
import type { FieldSpec, RecordDetails, TableInfo } from "./types";

type Row = Record<string, any>;
type Remote = { rows: Row[]; error?: string; loading: boolean };
const empty: Remote = { rows: [], loading: true };
const money = (c: number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(c / 100);
const date = (s: string) => s ? new Date(s.slice(0, 10) + "T12:00:00").toLocaleDateString("pt-PT") : "—";
const contacts = new Set(["phone", "email", "address", "postal_code", "locality"]);
const commercial = new Set(["category_id", "payment_term_id", "payment_method_id", "order_minimum", "shipping", "free_shipping", "lead_days", "accepts_expiry_returns", "expiry_return_months", "withholding", "withholding_rate"]);
export const entitySection = (field: FieldSpec) => contacts.has(field.name) ? 1 : commercial.has(field.name) ? 2 : 0;

function Related({ data, render, emptyText }: { data: Remote; render: (row: Row) => ReactNode; emptyText: string }) {
  if (data.loading) return <p role="status">A consultar registos associados…</p>;
  if (data.error) return <Notice tone="warning">{data.error}</Notice>;
  return data.rows.length ? <ul className="record-related-list">{data.rows.map(row => <li key={row.id}>{render(row)}</li>)}</ul> : <p className="record-sheet-empty">{emptyText}</p>;
}

/** Only presentation and scoped read APIs. Existing RecordDialog owns fields, validation and writes. */
export default function EntityRecordSheet({ table, details, values, title, actions, active, onSelect, renderFields, metadata }: {
  table: TableInfo; details: RecordDetails | null; values: Row; title: string; actions: ReactNode;
  active: number; onSelect: (index: number) => void; renderFields: (fields: FieldSpec[]) => ReactNode; metadata: ReactNode;
}) {
  const supplier = table.key === "suppliers";
  const [orders, setOrders] = useState<Remote>(empty);
  const [purchases, setPurchases] = useState<Remote>(empty);
  const [articles, setArticles] = useState<Remote>(empty);
  const [history, setHistory] = useState<Remote>(empty);
  const [documents, setDocuments] = useState<Remote>(empty);
  useEffect(() => {
    if (supplier || active !== 6) return;
    let current = true;
    const normalize = (value: string) => value.trim().toUpperCase().replace(/[\s.-]/g, "").replace(/^PT/, "");
    const nif = normalize(String(details?.values.tax_number || ""));
    if (!nif) { setDocuments({ rows: [], loading: false, error: "Indique e guarde o NIF para consultar os documentos da entidade." }); return; }
    setDocuments(empty);
    api(`/archive/documents?q=${encodeURIComponent(nif)}&limit=200`).then(data => {
      if (!Array.isArray(data.documents)) throw new Error("unavailable");
      if (current) setDocuments({ loading: false, rows: data.documents.filter((r: Row) => normalize(String(r.entity_nif || "")) === nif) });
    }).catch(() => { if (current) setDocuments({ rows: [], loading: false, error: "Não foi possível consultar o arquivo ou não tem permissão." }); });
    return () => { current = false; };
  }, [details, active, supplier]);
  useEffect(() => {
    let current = true;
    const entityId = details?.related_entity_id ?? (details?.id.startsWith("local:") ? details.id.slice(6) : null);
    const requests = new Map<string, Promise<any>>();
    const load = (path: string, select: (data: any) => Row[], update: (data: Remote) => void) => {
      update(empty);
      if (!requests.has(path)) requests.set(path, api(path));
      requests.get(path)!.then(data => { if (current) update({ rows: select(data), loading: false }); })
        .catch(() => { if (current) update({ rows: [], loading: false, error: "Consulta indisponível ou sem permissão. Não foi possível apurar este indicador." }); });
    };
    if (!details) {
      for (const update of [setOrders, setPurchases, setArticles, setHistory]) update({ rows: [], loading: false, error: "Guarde a ficha para consultar os registos associados." });
    } else if (!entityId) {
      for (const update of [setOrders, setPurchases, setArticles, setHistory]) update({ rows: [], loading: false, error: "Esta entidade ainda não tem uma referência local associada. A consulta não cria registos nem importa dados do ERP." });
    } else {
      if (supplier) {
        load("/procurement", data => {
          if (!Array.isArray(data.documents)) throw new Error("unavailable");
          return data.documents.filter((r: Row) => r.supplier_id === entityId && r.kind === "order");
        }, setOrders);
        load("/procurement", data => {
          if (!Array.isArray(data.documents)) throw new Error("unavailable");
          return data.documents.filter((r: Row) => r.supplier_id === entityId && ["invoice", "credit"].includes(r.kind));
        }, setPurchases);
        load("/stock/catalog", data => {
          if (!Array.isArray(data.articles)) throw new Error("unavailable");
          return data.articles.filter((r: Row) => r.supplier_id === entityId);
        }, setArticles);
      } else {
        load("/customer-orders", data => {
          if (!Array.isArray(data.orders)) throw new Error("unavailable");
          return data.orders.filter((r: Row) => r.customer_id === entityId);
        }, setOrders);
        load("/customer-orders/history", data => {
          if (!Array.isArray(data)) throw new Error("unavailable");
          // History only joins through scoped order IDs; names are never used as identity.
          return data;
        }, setHistory);
      }
    }
    return () => { current = false; };
  }, [details, supplier]);
  const unavailable = <Notice tone="info">Esta consulta ainda requer uma ligação financeira consolidada à entidade, incluindo os documentos do ERP. Não é apresentado um saldo calculado apenas pelo nome.</Notice>;
  const sectionFields = (index: number) => renderFields(table.fields.filter(f => !f.hidden && entitySection(f) === index));
  const pending = orders.loading || orders.error ? "—" : orders.rows.filter(r => supplier ? !["received", "cancelled"].includes(r.status) : !r.closed).length;
  const orderRows = <Related data={orders} emptyText="Sem encomendas associadas nas lojas autorizadas." render={r => <><strong>{r.number || r.document || r.id.slice(0, 8)}</strong><small>{date(r.requested_at || r.document_date)} · {supplier ? ({ draft: "Rascunho", confirmed: "Confirmada", partial: "Receção parcial", received: "Recebida", cancelled: "Cancelada" } as Row)[r.status] || r.status : r.closed ? "Concluída" : r.fully_ready ? "Pronta para levantamento" : r.ready ? "Disponível parcialmente" : "Em curso"}</small></>} />;
  const historyRows = history.rows.filter(r => orders.rows.some(o => o.id === r.order_id));
  const recentStart = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const today = new Date().toISOString().slice(0, 10);
  const recentPurchases = purchases.rows.filter(r => r.kind === "invoice" && !["draft", "cancelled"].includes(r.status) && r.document_date >= recentStart && r.document_date <= today);
  const supplierHistory = <RecordGroup title="Encomendas e compras registadas" icon={History}>{orders.loading || purchases.loading ? <p>A consultar histórico…</p> : orders.error || purchases.error ? <Notice tone="warning">Não foi possível consultar o histórico completo de encomendas e compras.</Notice> : <RecordHistory rows={[...orders.rows, ...purchases.rows].sort((a, b) => String(b.created_at || b.document_date).localeCompare(String(a.created_at || a.document_date))).map(r => ({ id: r.id, title: `${r.kind === "order" ? "Encomenda" : r.kind === "credit" ? "Nota de crédito" : "Compra"} · ${r.document || r.id.slice(0, 8)}`, detail: `Registada em ${date(r.created_at || r.document_date)}${r.confirmed_at ? ` · Confirmada em ${date(r.confirmed_at)}` : ""}` }))} />}</RecordGroup>;
  return <RecordWorkspace title={title} subtitle={details?.description || "Novo registo"} icon={supplier ? Building2 : User} actions={actions} active={active} onSelect={onSelect} indicators={<>
    <RecordIndicator label={supplier ? "Mínimo sem portes" : "Faturação"} value={supplier && values.free_shipping !== "" && values.free_shipping != null ? money(Number(values.free_shipping) * 100) : "—"} note={supplier ? "Condições da ficha" : "Consolidação financeira necessária"} icon={Euro} />
    <RecordIndicator label={supplier ? "Prazo de entrega" : "Saldo"} value={supplier && values.lead_days !== "" && values.lead_days != null ? `${values.lead_days} dias` : "—"} note={supplier ? "Prazo configurado" : "Não apurado"} icon={supplier ? CalendarClock : Euro} />
    <RecordIndicator label="Encomendas pendentes" value={pending} note={orders.loading ? "A consultar…" : orders.error ? "Consulta indisponível" : "Registos nas lojas autorizadas"} tone="warning" icon={ShoppingCart} onOpen={() => onSelect(4)} />
    <RecordIndicator label={supplier ? "Compras recentes" : "Dívida vencida"} value={supplier && !purchases.loading && !purchases.error ? recentPurchases.length : "—"} note={supplier ? "Últimos 30 dias · módulo Compras" : "Não apurada"} icon={supplier ? Truck : CircleAlert} onOpen={() => onSelect(5)} />
  </>} sections={[
    { label: "Geral", icon: supplier ? Building2 : User, content: <RecordGroup title="Identificação" icon={User}>{sectionFields(0)}</RecordGroup> },
    { label: "Contactos", icon: Phone, content: <RecordGroup title="Contactos e localização" icon={Phone}>{sectionFields(1)}</RecordGroup> },
    { label: supplier ? "Condições de Compra" : "Condições Comerciais", icon: CreditCard, content: <RecordGroup title={supplier ? "Condições de compra e devoluções" : "Condições comerciais"} icon={CreditCard}>{sectionFields(2)}</RecordGroup> },
    { label: supplier ? "Artigos" : "Vendas", icon: Package, content: <RecordGroup title={supplier ? "Artigos do fornecedor principal" : "Vendas e faturação"} icon={Package}>{supplier ? <Related data={articles} emptyText="Sem artigos com este fornecedor principal nas lojas autorizadas." render={r => <>{r.code} · {r.name}<small>{r.unit}</small></>} /> : unavailable}</RecordGroup> },
    { label: "Encomendas", icon: ShoppingCart, content: <RecordGroup title="Encomendas associadas" icon={ShoppingCart}>{orderRows}</RecordGroup> },
    ...(supplier ? [{ label: "Compras", icon: Truck, content: <RecordGroup title="Documentos de compra registados" icon={Truck}><Related data={purchases} emptyText="Sem compras associadas neste módulo." render={r => <>{r.document || r.id.slice(0, 8)}<small>{date(r.document_date)} · {r.kind === "credit" ? "Nota de crédito" : "Compra"} · {r.total_cents == null ? "Custo sem permissão de consulta" : money(r.total_cents)}</small></>} /></RecordGroup> }] : []),
    { label: "Conta Corrente", icon: Euro, content: <RecordGroup title="Conta corrente" icon={Euro}>{unavailable}</RecordGroup> },
    ...(!supplier ? [{ label: "Documentos", icon: FileText, content: <RecordGroup title="Documentos com o mesmo NIF" icon={FileText}><p className="footnote">Consulta dos primeiros 200 resultados do arquivo, com correspondência exata do NIF e respeitando as permissões de acesso.</p><Related data={documents} emptyText="Sem documentos com este NIF nos resultados consultados." render={r => <><ActionLink href={`/api/archive/documents/${encodeURIComponent(r.id)}/file`} target="_blank" rel="noopener noreferrer">{r.document_number || r.original_name}</ActionLink><small>{date(r.issue_date || r.received_at)} · {r.type_label}</small></>} /></RecordGroup> }] : []),
    { label: "Histórico", icon: History, content: <><RecordGroup title="Integração e utilização" icon={Info}>{metadata}</RecordGroup>{supplier ? supplierHistory : <RecordGroup title="Histórico das encomendas associadas" icon={History}>{history.loading || history.error ? <Related data={history} emptyText="" render={() => null} /> : <RecordHistory rows={historyRows.map(r => ({ id: r.id, title: `${date(r.at)} · ${r.action}`, detail: r.user }))} />}</RecordGroup>}</> },
  ]} />;
}
