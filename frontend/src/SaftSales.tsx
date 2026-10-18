import { useEffect, useRef, useState } from "react";
import { FileUp, Eye, RefreshCw, Check, Save } from "lucide-react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { ActionButton, AppDialog, ManagedForm, Notice, markDialogSaved } from "./components/InteractionUI";
import { StatusBadge } from "./components/BusinessUI";

type Row = Record<string, any>;
const money = (value: number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format((value || 0) / 100);
const day = (value: string) => value?.slice(0, 10).split("-").reverse().join("/") || "—";
const states: Row = { validating: "A validar XML…", ready: "Pronto para confirmar", importing: "A importar…", completed: "Importado", failed: "Falhado" };
const decisions: Row = { new: "Novo", existing: "Já existente", conflict: "Conflito" };
const kinds: Row = { invoice: "Fatura / nota", receipt: "Recibo", customer: "Cliente", product: "Artigo / serviço" };

export function SaftReference({ sessionId, selectedDay }: { sessionId: string; selectedDay: string }) {
  const [opened, setOpened] = useState(false), [data, setData] = useState<Row | null>(null), [error, setError] = useState("");
  useEffect(() => { if (!opened) return; let active = true; setData(null); setError("");
    api(`/sessions/${sessionId}/saft-reference`).then(value => { if (active) setData(value); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [opened, sessionId, selectedDay]);
  return <details className="panel" onToggle={e => setOpened(e.currentTarget.open)}><summary>Referência SAF-T do estabelecimento · {day(selectedDay)}</summary>
    {error && <Notice tone="error">{error}</Notice>}{opened && !data && !error && <p role="status">A consultar referência SAF-T…</p>}
    {data && <ReferenceTables data={data}/>}
  </details>;
}

function ReferenceTables({ data }: { data: Row }) {
  return <><p className="footnote">{data.limitations}</p>{data.warnings.map((message: string) => <Notice key={message} tone="warning">{message}</Notice>)}
    <h3>Faturação emitida · {money(data.billing_total_cents)}</h3>
    <div className="table-wrap"><table><thead><tr><th>Data</th><th>Tipo</th><th>Documentos</th><th className="numeric">Líquido</th><th className="numeric">Imposto</th><th className="numeric">Total</th></tr></thead><tbody>{data.billing.map((row: Row) => <tr key={`${row.date}-${row.store_id}-${row.type}`}><td>{day(row.date)}</td><td>{row.type}</td><td>{row.documents}</td><td className="numeric">{money(row.net_cents)}</td><td className="numeric">{money(row.tax_cents)}</td><td className="numeric">{money(row.gross_cents)}</td></tr>)}</tbody></table></div>
    {!data.billing.length && <p>Sem documentos de faturação importados para este período.</p>}
    <h3>Recebimentos identificados · {money(data.receipts_total_cents)}</h3>
    <div className="table-wrap"><table><thead><tr><th>Data do recebimento</th><th>Código SAF-T</th><th>Meio associado</th><th className="numeric">Montante</th></tr></thead><tbody>{data.receipts.map((row: Row, index: number) => <tr key={index}><td>{day(row.date)}</td><td>{row.code || "Não indicado"}</td><td>{row.name}</td><td className="numeric">{money(row.amount_cents)}</td></tr>)}</tbody></table></div>
    {!data.receipts.length && <p>Informação de recebimento indisponível, ou sem pagamentos identificáveis neste período.</p>}
    {!!data.unavailable.length && <details><summary>Informação indisponível e duplicações excluídas ({data.unavailable.length})</summary><ul>{data.unavailable.map((row: Row, index: number) => <li key={index}><strong>{row.document}</strong>: {row.reason}</li>)}</ul></details>}
    {!!data.payments.length && <details><summary>Origem dos recebimentos</summary><div className="table-wrap"><table><thead><tr><th>Documento</th><th>Data</th><th>Código</th><th className="numeric">Valor</th><th>Importação</th></tr></thead><tbody>{data.payments.map((row: Row) => <tr key={`${row.record_id}-${row.payment_index}`}><td>{row.document}</td><td>{day(row.date)}</td><td>{row.code || "Não indicado"}</td><td className="numeric">{money(row.amount_cents)}</td><td>{row.import_id}</td></tr>)}</tbody></table></div></details>}
  </>;
}

export default function SaftSales({ stores, store, admin, local }: { stores: Row[]; store: string; admin: boolean; local: boolean }) {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const [history, setHistory] = useState<Row | null>(null), [opened, setOpened] = useState<Row | null>(null);
  const [target, setTarget] = useState(store || stores[0]?.id || ""), [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [mapping, setMapping] = useState<Row | null>(null), [start, setStart] = useState(today.slice(0, 8) + "01"), [end, setEnd] = useState(today);
  const [analysis, setAnalysis] = useState<Row | null>(null);
  const running = useRef(false);
  async function load() { const result = await api("/saft/imports"); setHistory(result); }
  useEffect(() => { if (!local || !admin) return; let active = true; Promise.all([api("/saft/imports"), api("/saft/payment-mapping")]).then(([h, m]) => { if (active) { setHistory(h); setMapping(m); } }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [local, admin]);
  async function run(task: () => Promise<void>) { if (running.current) return; running.current = true; setBusy(true); setError(""); setNotice(""); try { await task(); } catch (e) { setError((e as Error).message); } finally { running.current = false; setBusy(false); } }
  if (!local) return <Notice tone="warning">A importação SAF-T de vendas destina-se ao modo local autónomo.</Notice>;
  if (!admin) return <Notice tone="warning">A importação de ficheiros fiscais está reservada ao administrador. A referência SAF-T continua disponível nas folhas de caixa autorizadas.</Notice>;
  return <><div className="page-intro"><div><h1>Importar SAF-T de vendas</h1><p>Importe a faturação e consulte os recebimentos identificados, sem ligação a um ERP.</p></div></div>
    {error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success">{notice}</Notice>}
    <section className="panel"><ManagedForm onSubmit={e => { e.preventDefault(); if (!file) return; const element = e.currentTarget; void run(async () => {
      const body = new FormData(); body.append("store_id", target); body.append("file", file);
      const response = await fetch("/api/saft/imports", { method: "POST", credentials: "same-origin", body });
      if (!response.ok) { const result = await response.json(); throw new Error(typeof result.detail === "string" ? result.detail : "Não foi possível carregar o SAF-T. Reveja o ficheiro e o estabelecimento."); }
      setOpened(await response.json()); markDialogSaved(element); await load();
    }); }}><fieldset disabled={busy} className="ui-form-fields"><div className="form-grid">
      <Field label="Estabelecimento de destino"><select required value={target} onChange={e => setTarget(e.target.value)}>{stores.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
      <Field label="Ficheiro XML SAF-T (PT)"><input type="file" accept=".xml,application/xml,text/xml" required onChange={e => setFile(e.target.files?.[0] || null)}/></Field>
    </div><p>Versão suportada: {history?.versions.join(", ") || "1.04_01"}. Limite: {Math.round((history?.max_bytes || 100000000) / 1000000)} MB. Validação XSD oficial no servidor; processamento em segundo plano.</p>
      <p className="footnote">Confirme o estabelecimento: o ficheiro não identifica necessariamente a loja ou gaveta da IAZELA. Nenhum documento é aplicado antes da confirmação. Transportes e encomendas não são faturação. Os artigos importados não alteram stock.</p>
      <ActionButton className="primary" disabled={busy || !file || (!!history && file.size > history.max_bytes)}><FileUp size={17}/>{busy ? "A carregar…" : "Validar e pré-visualizar"}</ActionButton>
      {!!file && !!history && file.size > history.max_bytes && <Notice tone="error">O ficheiro excede o limite. Exporte um período menor.</Notice>}
    </fieldset></ManagedForm></section>
    <section className="panel"><h2>Histórico de importações</h2><ActionButton disabled={busy} onClick={() => run(load)}><RefreshCw size={16}/>Atualizar histórico</ActionButton>
      {!history && !error && <p role="status">A carregar histórico…</p>}
      <div className="table-wrap"><table><thead><tr><th>Ficheiro / data</th><th>Empresa / período</th><th>Estado</th><th>Resultado</th><th>Ações</th></tr></thead><tbody>{history?.imports.map((row: Row) => <tr key={row.id}><td>{row.filename}<br/><small>{day(row.created_at)} · {row.user_id}</small></td><td>{row.header.CompanyName || "—"}<br/>{day(row.header.StartDate)} — {day(row.header.EndDate)}</td><td><StatusBadge tone={row.status === "failed" ? "danger" : row.status === "completed" ? "success" : "warning"}>{states[row.status]}</StatusBadge></td><td>{row.status === "failed" ? `0 aplicados / ${row.summary.result?.rejected ? `${row.summary.result.rejected} rejeitados` : "ficheiro rejeitado"}` : row.summary.result ? `${row.summary.result.imported || 0} novos · ${row.summary.result.updated || 0} atualizados · ${row.summary.result.ignored || 0} ignorados` : "Por confirmar"}</td><td><ActionButton onClick={() => setOpened(row)}><Eye size={16}/>Consultar importação</ActionButton></td></tr>)}</tbody></table></div>
      {history && !history.imports.length && <p>Ainda não existem importações SAF-T nesta empresa.</p>}
    </section>
    {mapping && <section className="panel"><details><summary>Associar códigos SAF-T aos meios de pagamento</summary><ManagedForm onSubmit={e => { e.preventDefault(); void run(async () => { setMapping(await api("/saft/payment-mapping", { mapping: mapping.mapping })); setNotice("Associações guardadas. Apenas a referência SAF-T será recalculada."); }); }}><fieldset disabled={busy} className="ui-form-fields"><p>Não é feita associação automática. MB significa referência Multibanco e DE dinheiro eletrónico; não identificam, por si só, MB WAY ou TPA. Associe de acordo com o sistema de origem.</p><div className="form-grid">{mapping.codes.map((code: string) => <Field key={code} label={`Código SAF-T ${code}`}><select value={mapping.mapping[code] || ""} onChange={e => { const next = { ...mapping.mapping }; if (e.target.value) next[code] = e.target.value; else delete next[code]; setMapping({ ...mapping, mapping: next }); }}><option value="">Por classificar</option>{mapping.methods.map((method: Row) => <option key={method.id} value={method.id}>{method.name}</option>)}</select></Field>)}</div><ActionButton className="primary" disabled={busy}><Save size={16}/>Guardar associações</ActionButton></fieldset></ManagedForm></details></section>}
    <section className="panel"><h2>Analisar faturação e recebimentos</h2><ManagedForm onSubmit={e => { e.preventDefault(); void run(async () => setAnalysis(await api(`/saft/analysis?store_id=${encodeURIComponent(target)}&start=${start}&end=${end}`))); }}><div className="form-grid"><Field label="Desde"><input type="date" required value={start} onChange={e => setStart(e.target.value)}/></Field><Field label="Até"><input type="date" required min={start} value={end} onChange={e => setEnd(e.target.value)}/></Field></div><p>Estabelecimento: {stores.find(row => row.id === target)?.name}</p><ActionButton disabled={busy}><Eye size={16}/>Consultar período</ActionButton></ManagedForm>{analysis && <ReferenceTables data={analysis}/>}</section>
    {opened && <ImportDialog key={opened.id} initial={opened} onClose={() => { setOpened(null); void load(); }} onUpdated={() => { void load(); }}/>}
  </>;
}

function ImportDialog({ initial, onClose, onUpdated }: { initial: Row; onClose: () => void; onUpdated: () => void }) {
  const [row, setRow] = useState(initial), [data, setData] = useState<Row | null>(null), [page, setPage] = useState(1), [kind, setKind] = useState(""), [conflictsOnly, setConflictsOnly] = useState(false);
  const [choices, setChoices] = useState<Record<string, string>>({}), [approved, setApproved] = useState(false), [detail, setDetail] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const processing = ["validating", "importing"].includes(row.status), running = useRef(false);
  useEffect(() => { let active = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() { try { const value = await api(`/saft/imports/${row.id}`); if (active) { setRow(value); if (["validating", "importing"].includes(value.status)) timer = setTimeout(poll, 2000); else onUpdated(); } } catch (e) { if (active) setError((e as Error).message); } }
    void poll(); return () => { active = false; clearTimeout(timer); };
  }, [row.id, row.status]);
  useEffect(() => { let active = true; if (processing) return; setData(null); api(`/saft/imports/${row.id}/items?page=${page}&kind=${kind}&conflicts=${conflictsOnly}`).then(value => { if (active) setData(value); }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [row.id, row.revision, row.status, page, kind, conflictsOnly]);
  async function run(task: () => Promise<void>) { if (running.current) return; running.current = true; setBusy(true); setError(""); try { await task(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); running.current = false; } }
  const counts = row.summary.counts || {}, totals = row.summary.totals || {};
  return <AppDialog className="doc-dialog" aria-label="Importação SAF-T de vendas" busy={busy} guardChanges={row.status === "ready"} onRequestClose={onClose}>
    <h2>Importação SAF-T de vendas</h2><p>{row.filename} · <StatusBadge tone={row.status === "failed" ? "danger" : row.status === "completed" ? "success" : "warning"}>{states[row.status]}</StatusBadge></p>
    <ActionButton disabled={busy} onClick={() => run(async () => setRow(await api(`/saft/imports/${row.id}`)))}><RefreshCw size={16}/>Consultar estado</ActionButton>
    {error && <Notice tone="error">{error}</Notice>}{row.error && <Notice tone="error">{row.error}</Notice>}
    {processing && <Notice tone="info">O processamento continua no servidor. Pode fechar esta janela e consultar o histórico. Não repita a confirmação.</Notice>}
    {row.header.CompanyName && <><p><strong>{row.header.CompanyName}</strong> · NIF {row.header.TaxRegistrationNumber} · {row.header.AuditFileVersion} · {row.header.CurrencyCode}</p><p>{day(row.header.StartDate)} — {day(row.header.EndDate)} · Aplicação de origem: {row.header.ProductID}</p></>}
    {row.status === "completed" && <Notice tone="success">Importação concluída: {row.summary.result?.imported || 0} registos novos, {row.summary.result?.updated || 0} atualizados e {row.summary.result?.ignored || 0} ignorados. Os movimentos e o stock não foram alterados.</Notice>}
    {!!Object.keys(counts).length && <><p><strong>{counts.new || 0}</strong> novos · <strong>{counts.existing || 0}</strong> já existentes · <strong>{counts.conflict || 0}</strong> conflitos · <strong>{counts.cancelled || 0}</strong> anulados</p>
      <p>Faturação após notas de crédito: <strong>{money(totals.gross_cents)}</strong> · Base: {money(totals.net_cents)} · Impostos: {money(totals.tax_cents)} · Notas de crédito: {money(totals.credit_notes_cents)}</p>
      <p>Clientes: {counts.customer || 0} · Artigos / serviços: {counts.product || 0} · Sem pagamentos identificados: {counts.without_payments || 0}</p>
      <p>Códigos de pagamento presentes: {Object.keys(row.summary.payment_codes || {}).join(", ") || "Nenhum — informação de recebimento indisponível"}. As associações aos meios da IAZELA são manuais.</p>
      <p>{Object.entries(row.summary.types || {}).map(([key, value]) => { const [section, type] = key.split(":"); return `${section === "invoice" ? "Vendas" : "Recibos"} ${type}: ${value}`; }).join(" · ")}</p>
      <details><summary>Avisos e limitações da informação importada</summary>{row.summary.warnings?.map((message: string) => <p key={message}>{message}</p>)}</details>
    </>}
    {!processing && <><div className="form-grid"><Field label="Registos a consultar"><select value={kind} onChange={e => { setKind(e.target.value); setPage(1); }}><option value="">Todos</option>{Object.entries(kinds).map(([key, label]) => <option key={key} value={key}>{String(label)}</option>)}</select></Field><label className="doc-check"><input type="checkbox" checked={conflictsOnly} onChange={e => { setConflictsOnly(e.target.checked); setPage(1); }}/>Apenas conflitos</label></div>
      <p className="footnote">Amostra paginada de 30 registos. As versões anteriores permanecem nas importações de origem. Substituir um conflito altera apenas a referência SAF-T, mediante esta decisão explícita.</p>
      <div className="table-wrap"><table><thead><tr><th>Tipo</th><th>Documento / código</th><th>Data / estado</th><th className="numeric">Total</th><th>Comparação / decisão</th><th>Detalhe</th></tr></thead><tbody>{data?.items.map((item: Row) => <tr key={item.id}><td>{kinds[item.kind]}</td><td>{item.data.document || item.data.code}<br/><small>{item.data.raw.CompanyName || item.data.raw.ProductDescription || item.data.type}</small></td><td>{day(item.data.date)} · {item.data.status}</td><td className="numeric">{item.data.gross_cents !== undefined ? money(item.data.gross_cents) : "—"}</td><td>{decisions[item.decision]}{item.decision === "conflict" && row.status === "ready" && <select aria-label={`Decisão ${item.data.document || item.data.code}`} value={choices[item.id] || ""} onChange={e => { setChoices({ ...choices, [item.id]: e.target.value }); setApproved(false); }}><option value="">Rever conflito</option><option value="keep">Manter versão existente</option><option value="replace">Aplicar esta versão</option></select>}</td><td><ActionButton disabled={busy} onClick={() => run(async () => setDetail(await api(`/saft/imports/${row.id}/items/${item.id}`)))}><Eye size={15}/>Consultar</ActionButton></td></tr>)}</tbody></table></div>
      {data && !data.items.length && <p>Sem registos neste filtro.</p>}
      <div className="actions"><ActionButton disabled={page <= 1 || busy} onClick={() => setPage(page - 1)}>Anterior</ActionButton><span>Página {page} · {data?.total || 0} registos</span><ActionButton disabled={!data || page * 30 >= data.total || busy} onClick={() => setPage(page + 1)}>Seguinte</ActionButton></div>
    </>}
    {["ready", "failed"].includes(row.status) && !!Object.keys(counts).length && <ActionButton disabled={busy} onClick={() => run(async () => { setRow(await api(`/saft/imports/${row.id}/review`, {})); setChoices({}); setApproved(false); })}><RefreshCw size={16}/>Atualizar pré-visualização</ActionButton>}
    {row.status === "ready" && <><p>Decisões de conflitos: {Object.keys(choices).filter(key => choices[key]).length} de {counts.conflict || 0}. Consulte todas as páginas antes de confirmar.</p><label className="doc-check"><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)}/>Confirmo a empresa, estabelecimento, período, registos e decisões de substituição. A importação não cria movimentos de caixa.</label><ActionButton className="primary" disabled={busy || !approved || Object.keys(choices).filter(key => choices[key]).length !== (counts.conflict || 0)} onClick={() => run(async () => { setRow(await api(`/saft/imports/${row.id}/confirm`, { confirmed: true, revision: row.revision, conflicts: choices })); setApproved(false); onUpdated(); })}><Check size={16}/>Confirmar importação</ActionButton></>}
    {processing && <ActionButton disabled={busy} onClick={() => run(async () => setRow(await api(`/saft/imports/${row.id}/recover`, { confirmed: true, revision: row.revision, conflicts: {} })))}>Recuperar processamento interrompido há mais de 30 minutos</ActionButton>}
    <ActionButton onClick={onClose}>Fechar</ActionButton>
    {detail && <AppDialog className="doc-dialog" aria-label="Detalhe do registo SAF-T" onRequestClose={() => setDetail(null)}><h2>{detail.data.document || detail.data.code}</h2><p>Origem SAF-T · {kinds[detail.kind]} · SHA-256: {detail.fingerprint}</p>
      <DocumentDetails data={detail.data}/>{detail.previous && detail.decision === "conflict" && <details open><summary>Versão atualmente guardada</summary><DocumentDetails data={detail.previous}/></details>}
      <details><summary>Todos os campos originais do registo</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(detail.data.raw, null, 2)}</pre></details>
    </AppDialog>}
  </AppDialog>;
}

function DocumentDetails({ data }: { data: Row }) {
  const lines: Row[] = !data.raw.Line ? [] : Array.isArray(data.raw.Line) ? data.raw.Line : [data.raw.Line];
  return <><p>{data.type} · {day(data.date)} · Estado {data.status || "—"} · Cliente {data.customer_id || data.raw.CompanyName || "—"}</p>
    {data.gross_cents !== undefined && <p>Líquido: {money(data.net_cents)} · Impostos: {money(data.tax_cents)} · Total original: {money(data.gross_cents)} · Sentido na análise: {data.direction > 0 ? "Positivo" : data.direction < 0 ? "Negativo" : "Por rever"}</p>}
    {!!lines.length && <div className="table-wrap"><table><thead><tr><th>Linha / artigo</th><th>Descrição</th><th className="numeric">Quantidade</th><th className="numeric">Preço</th><th className="numeric">Desconto</th><th>Débito / crédito</th><th>Imposto</th></tr></thead><tbody>{lines.map((line, index) => <tr key={index}><td>{line.LineNumber} · {line.ProductCode}</td><td>{line.ProductDescription || line.Description}</td><td className="numeric">{line.Quantity || "—"}</td><td className="numeric">{line.UnitPrice || "—"}</td><td className="numeric">{line.SettlementAmount || "—"}</td><td>{line.DebitAmount ? `Débito ${line.DebitAmount}` : `Crédito ${line.CreditAmount || "—"}`}</td><td>{line.Tax?.TaxCode} {line.Tax?.TaxPercentage !== undefined ? `${line.Tax.TaxPercentage}%` : line.Tax?.TaxAmount || ""}</td></tr>)}</tbody></table></div>}
    {!!data.references?.length && <p>Documentos de origem: {data.references.join(", ")}</p>}
    {data.payments && <p>{data.payments.length ? data.payments.map((payment: Row) => `${day(payment.date)} · ${payment.code || "Por classificar"} · ${money(payment.amount_cents)}`).join("; ") : "Informação de recebimento indisponível"}</p>}
  </>;
}
