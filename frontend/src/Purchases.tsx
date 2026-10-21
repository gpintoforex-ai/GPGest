import DatePresets from "./components/DatePresets";
import { ActionLink } from "./components/InteractionUI";
import { confirmAction } from "./components/InteractionUI";
import { ActionButton, AppDialog, ManagedForm, Notice } from "./components/InteractionUI";
import { ScanButton } from "./DocumentScanner";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "./lib/api";
import { uploadPurchaseFile } from "./utils/purchaseImport";
import { currentMonth } from "./RecordList";
import "./Purchases.css";

type Row = Record<string, any>;
const money = (n: number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(n / 100);
const day = (value: string) => value.split("-").reverse().join("/");
const status: Record<string, string> = { por_reconciliar: "Por pagar", parcial: "Pago parcialmente", reconciliado: "Pago" };

export default function Purchases({ stores, store, manager, admin, embedded = false, onOpenMovements, onOpenHistory }: { stores: Row[]; store: string; manager: boolean; admin: boolean; embedded?: boolean; onOpenMovements?: () => void; onOpenHistory?: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [banks, setBanks] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const actionPending = useRef(false);
  const [importing, setImporting] = useState("");
  const [refreshingImport, setRefreshingImport] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [range, setRange] = useState(currentMonth);
  const [search, setSearch] = useState("");
  const [payment, setPayment] = useState("");
  const [documents, setDocuments] = useState("");
  const [view, setView] = useState("compras");
  const [page, setPage] = useState(1);
  const [modal, setModal] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [connection, setConnection] = useState<Row | null>(null);
  const [transactionId, setTransactionId] = useState("");
  const [amount, setAmount] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<Row | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const selectedStore = store || stores[0]?.id || "";
  useEffect(() => { setModal(""); setImportFile(null); setImportPreview(null); }, [selectedStore]);
  const selected = rows.find(r => r.id === selectedId);
  async function load() {
    const [purchases, transactions] = await Promise.all([api("/purchases", undefined, { timeoutMs: 45_000 }), api("/purchases/bank-transactions", undefined, { timeoutMs: 45_000 })]);
    setRows(purchases); setBanks(transactions); setLoaded(true);
  }
  useEffect(() => { let active = true; Promise.all([api("/purchases"), api("/purchases/bank-transactions")]).then(([purchases, transactions]) => { if (active) { setRows(purchases); setBanks(transactions); setLoaded(true); } }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, []);
  useEffect(() => { if (modal && !dialog.current?.open) dialog.current?.showModal(); }, [modal]);
  useEffect(() => { setPage(1); }, [store, range, search, payment, documents, view]);
  useEffect(() => {
    let active = true;
    setConnection(null);
    if (manager && selectedStore) api(`/purchases/efatura/${selectedStore}`).then(r => { if (active) setConnection(r); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [selectedStore, manager]);
  async function act(task: () => Promise<void>) {
    if (actionPending.current) return;
    actionPending.current = true;
    setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError(e instanceof Error ? e.message : "Não foi possível concluir a operação."); }
    finally { actionPending.current = false; setBusy(false); }
  }
  async function uploadPurchases(confirm: boolean) {
    if (!importFile) return;
    await act(async () => {
      setImporting(confirm ? "A importar compras…" : "A pré-visualizar ficheiro…");
      let result;
      try { result = await uploadPurchaseFile(selectedStore, importFile, confirm); }
      finally { setImporting(""); }
      if (confirm) {
        const dates = result.rows.map((r: Row) => r.document_date as string).sort();
        if (dates.length) setRange({ from: dates[0], to: dates[dates.length - 1] });
        setSearch(""); setPayment(""); setDocuments(""); setView("compras");
        setModal(""); setImportPreview(null); setImportFile(null);
        setNotice(`${result.new_count} compras importadas; ${result.duplicate_count} duplicados ignorados.`);
        setRefreshingImport(true);
        void load().catch(() => setError("A importação ficou guardada, mas não foi possível atualizar a lista. Atualize a página para consultar as compras; não é necessário voltar a importar."))
          .finally(() => setRefreshingImport(false));
      }
      else setImportPreview(result);
    });
  }
  function close() { if (!busy) { setModal(""); setError(""); } }
  function openDetail(row: Row) { setSelectedId(row.id); setTransactionId(""); setAmount(""); setError(""); setModal("detail"); }
  const visible = rows.filter(r => !store || r.store_id === store);
  const filtered = visible.filter(r => (!range.from || r.document_date >= range.from) && (!range.to || r.document_date <= range.to) && (!payment || r.payment_status === payment) && (!documents || (documents === "missing" ? !r.attachments.length : !!r.attachments.length)) && `${r.supplier_name} ${r.supplier_nif} ${r.document}`.toLocaleLowerCase("pt-PT").includes(search.toLocaleLowerCase("pt-PT")));
  const bankMissing = banks.filter(r => (!store || r.store_id === store) && r.amount_cents < 0 && r.available_cents > 0 && (!range.from || r.movement_date >= range.from) && (!range.to || r.movement_date <= range.to) && `${r.description} ${r.account_name}`.toLocaleLowerCase("pt-PT").includes(search.toLocaleLowerCase("pt-PT")));
  const pageRows = view === "compras" ? filtered : bankMissing;
  const pages = Math.max(1, Math.ceil(pageRows.length / 20));
  const currentPage = Math.min(page, pages);
  const candidates = selected ? banks.filter(b => b.store_id === selected.store_id && b.amount_cents * selected.amount_cents < 0 && b.available_cents > 0 && !selected.allocations.some((a: Row) => a.transaction_id === b.id)).sort((a, b) => Number(b.available_cents === selected.remaining_cents) - Number(a.available_cents === selected.remaining_cents) || b.movement_date.localeCompare(a.movement_date)) : [];
  return <>
    <div className="page-intro"><div><h1>{embedded ? "Faturas e documentos" : "Compras"}</h1><p>Pagamento e documento são acompanhados separadamente.</p></div>{manager && <div className="actions"><ActionButton disabled={busy || !selectedStore} onClick={() => { setImportFile(null); setImportPreview(null); setError(""); setModal("import"); }}>Importar ficheiro</ActionButton><ActionButton disabled={busy || !selectedStore} onClick={() => { setError(""); setModal("config"); }}>Configurar e-Fatura</ActionButton><ActionButton disabled={busy || !connection?.has_password || !connection?.certificate_ready} onClick={() => setModal("sync")}>Sincronizar e-Fatura</ActionButton><ActionButton className="primary" disabled={busy || !selectedStore} onClick={() => { setError(""); setModal("new"); }}>Registar compra</ActionButton></div>}</div>
    {error && !modal && <Notice className="error" role="alert">{error}</Notice>}{notice && <Notice className="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    {refreshingImport && <p role="status">Importação guardada. A atualizar a lista de compras…</p>}
    {manager && connection && !connection.certificate_ready && <section className="panel"><p><strong>e-Fatura: certificado por configurar.</strong> A sincronização ficará disponível depois de configurar os acessos e o certificado da AT no servidor. Já pode registar compras, anexar documentos e reconciliar pagamentos.</p></section>}
    <section className="panel">
      <div className="actions"><ActionButton onClick={() => setView("compras")} aria-pressed={view === "compras"}>{embedded ? "Faturas" : "Compras"}</ActionButton><ActionButton onClick={() => setView("bancos")} aria-pressed={view === "bancos"}>Pagamentos sem associação completa</ActionButton></div>
      <div className="purchase-filters filter-bar"><DatePresets from={range.from} to={range.to} onSelect={(from, to) => setRange({ ...range, from, to })} /><label>Desde<input type="date" value={range.from} onChange={e => setRange({ ...range, from: e.target.value })} /></label><label>Até<input type="date" value={range.to} onChange={e => setRange({ ...range, to: e.target.value })} /></label><label>Pesquisar<input value={search} onChange={e => setSearch(e.target.value)} placeholder="Fornecedor, NIF ou documento" /></label>{view === "compras" && <><label>Pagamento<select value={payment} onChange={e => setPayment(e.target.value)}><option value="">Todos</option>{Object.entries(status).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label>Documento<select value={documents} onChange={e => setDocuments(e.target.value)}><option value="">Todos</option><option value="missing">Por anexar</option><option value="attached">Anexado</option></select></label></>}</div>
      {range.from && range.to && range.from > range.to ? <p role="alert">A data inicial deve ser anterior à data final.</p> : view === "compras" ? <>
        <p>{filtered.length} compras · Total: <strong>{money(filtered.reduce((sum, r) => sum + r.amount_cents, 0))}</strong> · Documentos por anexar: <strong>{filtered.filter(r => !r.attachments.length).length}</strong></p>
        <div className="purchase-table"><table><thead><tr><th>Data</th><th>Fornecedor</th><th>Documento</th><th className="numeric">Valor</th><th>Pagamento</th><th>Documento</th><th>Validação</th><th /></tr></thead><tbody>{filtered.slice((currentPage - 1) * 20, currentPage * 20).map(r => <tr key={r.id}><td>{day(r.document_date)}</td><td>{r.supplier_name}<br /><small>{r.supplier_nif}</small></td><td>{r.document}<br /><small>{r.origin === "efatura_testes" ? "e-Fatura (teste)" : r.origin === "efatura" ? "e-Fatura" : r.origin === "ficheiro" ? "Ficheiro" : "Registo manual"}</small></td><td className="numeric">{money(r.amount_cents)}</td><td className="numeric">{status[r.payment_status]}<br /><small>{money(r.paid_cents)} associados</small></td><td><span className={`pill ${r.attachments.length ? "neutral" : ""}`}>{r.attachments.length ? "Anexado" : "Por anexar"}</span></td><td>{r.validated ? "Validada" : "Por validar"}</td><td><ActionButton onClick={() => openDetail(r)}>Abrir</ActionButton></td></tr>)}</tbody></table></div>
      </> : <><p>Movimentos de saída com valor ainda sem compra associada. A falta de associação não prova que falta uma fatura.</p><div className="purchase-table"><table><thead><tr><th>Data</th><th>Conta</th><th>Descrição</th><th className="numeric">Valor</th><th>Por associar</th></tr></thead><tbody>{bankMissing.slice((currentPage - 1) * 20, currentPage * 20).map(r => <tr key={r.id}><td>{day(r.movement_date)}</td><td>{r.account_name}</td><td>{r.description}</td><td className="numeric">{money(r.amount_cents)}</td><td className="numeric">{money(r.available_cents)}</td></tr>)}</tbody></table></div></>}
      {!pageRows.length && <p>{loaded ? "Sem registos para os filtros selecionados." : "A carregar…"}</p>}
      {pages > 1 && <div className="actions"><ActionButton disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Anterior</ActionButton><span>{currentPage} / {pages}</span><ActionButton disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)}>Seguinte</ActionButton></div>}
    </section>
    {modal && <AppDialog busy={busy} ref={dialog} className="purchase-dialog" aria-labelledby="purchase-dialog-title" onRequestClose={() => {  close(); }}>
      <div className="panel-title"><h2 id="purchase-dialog-title">{modal === "import" ? "Importar compras" : modal === "new" ? "Registar compra" : modal === "config" ? "Configurar e-Fatura" : modal === "sync" ? "Sincronizar e-Fatura" : selected?.document}</h2></div>
      {error && <Notice className="error" role="alert">{error}</Notice>}
      {importing && <p role="status" aria-live="polite">{importing}</p>}
      {modal === "import" && <div className="purchase-form">
        <p>Loja: <strong>{stores.find(s => s.id === selectedStore)?.name}</strong>. Importe uma lista CSV ou XLSX com NIF do fornecedor, documento, data e total. O comprovativo será anexado separadamente.</p>
        <ActionLink href="/api/purchases/import-template">Descarregar modelo CSV</ActionLink>
        <small>O modelo contém uma linha fictícia de exemplo; substitua-a pelas suas compras.</small>
        <label>Ficheiro de compras<input type="file" accept=".csv,.xlsx" disabled={busy} onChange={e => { setImportFile(e.target.files?.[0] || null); setImportPreview(null); setError(""); }} /></label>
        <ActionButton disabled={busy || !importFile} onClick={() => uploadPurchases(false)}>Pré-visualizar</ActionButton>
        {!importPreview && <small>Depois da pré-visualização, confirme a importação para guardar as compras.</small>}
        {importPreview && <>
          <p><strong>{importPreview.new_count} compras novas</strong> · {importPreview.duplicate_count} duplicados · {importPreview.conflict_count} conflitos.</p>
          {!!importPreview.conflict_count && <p role="alert">Há documentos repetidos com dados diferentes. Corrija o ficheiro antes de importar.</p>}
          {!importPreview.new_count && !importPreview.conflict_count && <p>Todas as compras deste ficheiro já existem. Não há compras novas para importar.</p>}
          <ActionButton className="primary" disabled={busy || !importPreview.new_count || !!importPreview.conflict_count} onClick={() => uploadPurchases(true)}>{busy ? "A processar…" : `Confirmar importação de ${importPreview.new_count} compras`}</ActionButton>
          <div className="purchase-table"><table><thead><tr><th>Linha</th><th>Fornecedor</th><th>Documento</th><th>Data</th><th className="numeric">Total</th><th>Estado</th></tr></thead><tbody>{importPreview.rows.slice(0, 100).map((r: Row) => <tr key={r.line}><td>{r.line}</td><td>{r.supplier_name}<br /><small>{r.supplier_nif}</small></td><td>{r.document}</td><td>{day(r.document_date)}</td><td className="numeric">{money(r.amount_cents)}</td><td>{r.status === "new" ? "Nova" : r.status === "duplicate" ? "Já existe" : "Conflito"}</td></tr>)}</tbody></table></div>
          {importPreview.rows.length > 100 && <p>Mostradas as primeiras 100 linhas. A confirmação importa todas as compras novas do ficheiro.</p>}
          <ActionButton className="primary" disabled={busy || !importPreview.new_count || !!importPreview.conflict_count} onClick={() => uploadPurchases(true)}>Confirmar importação</ActionButton>
        </>}
      </div>}
      {modal === "new" && <ManagedForm className="purchase-form" onSubmit={(e: FormEvent<HTMLFormElement>) => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.currentTarget)); act(async () => { await api("/purchases", data); await load(); setModal(""); setNotice("Compra registada."); }); }}>
        <label>Loja<select name="store_id" defaultValue={selectedStore} required>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><label>NIF do fornecedor<input name="supplier_nif" pattern="[0-9]{9}" maxLength={9} required /></label><label>Fornecedor<input name="supplier_name" maxLength={300} required /></label><label>Tipo<select name="document_type"><option>FT</option><option>FS</option><option>FR</option><option>ND</option><option>NC</option></select></label><label>Número do documento<input name="document" maxLength={100} required /></label><label>Data<input name="document_date" type="date" required /></label><label>Valor total (€)<input name="amount" inputMode="decimal" required placeholder="100,00" /></label><label>Observações<textarea name="notes" maxLength={5000} /></label><ActionButton className="primary" disabled={busy}>Guardar compra</ActionButton>
      </ManagedForm>}
      {modal === "config" && <ManagedForm className="purchase-form" onSubmit={e => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.currentTarget)); act(async () => { setConnection(await api(`/purchases/efatura/${selectedStore}`, data)); setModal(""); setNotice("Acesso ao e-Fatura guardado."); }); }}>
        <p>Loja: {stores.find(s => s.id === selectedStore)?.name}. A senha é guardada cifrada e não é devolvida ao navegador.</p><label>NIF da empresa<input name="nif" defaultValue={connection?.nif} pattern="[0-9]{9}" required disabled={!admin} /></label><label>Subutilizador AT<input name="username" defaultValue={connection?.username} placeholder="NIF/Utilizador" required disabled={!admin} /></label><label>Senha<input name="password" type="password" autoComplete="new-password" placeholder={connection?.has_password ? "Deixar vazio para manter" : "Senha do subutilizador"} disabled={!admin} /></label><label>Ambiente<select name="environment" defaultValue={connection?.environment || "testes"} disabled={!admin}><option value="testes">Testes</option><option value="producao">Produção</option></select></label><p>Certificado do ambiente selecionado: {connection?.certificate_ready ? "configurado" : "por configurar no servidor"}. Os dados do ambiente de testes são dados de teste.</p>{admin ? <ActionButton className="primary" disabled={busy}>Guardar acesso</ActionButton> : <p>A configuração do acesso é feita pelo administrador.</p>}
      </ManagedForm>}
      {modal === "sync" && <ManagedForm className="purchase-form" onSubmit={e => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.currentTarget)); act(async () => { const result = await api(`/purchases/efatura/${selectedStore}/sync`, data); await load(); setModal(""); setNotice(`${result.imported} compras importadas; ${result.updated} atualizadas; ${result.total} consultadas.`); }); }}><p>Consulta de compras no ambiente <strong>{connection?.environment === "producao" ? "Produção" : "Testes"}</strong>. Selecione um intervalo dentro do mesmo mês.</p><label>Desde<input name="start" type="date" defaultValue={currentMonth().from} required /></label><label>Até<input name="end" type="date" defaultValue={currentMonth().to} required /></label><ActionButton className="primary" disabled={busy}>Confirmar sincronização</ActionButton></ManagedForm>}
      {modal === "detail" && selected && <>
        <p><strong>{selected.supplier_name}</strong> · NIF {selected.supplier_nif} · {day(selected.document_date)} · {money(selected.amount_cents)}</p><p>{status[selected.payment_status]} · {selected.attachments.length ? "Documento anexado" : "Documento por anexar"} · {selected.validated ? "Validada" : "Por validar"}</p>{selected.notes && <p>{selected.notes}</p>}
        {manager && <ActionButton disabled={busy} onClick={() => act(async () => { await api(`/purchases/${selected.id}/validate`, { validated: !selected.validated }); await load(); })}>{selected.validated ? "Retirar validação" : "Validar compra"}</ActionButton>}
        <h3>Documentos</h3>{manager && <ScanButton initialDocument={selected} onSaved={load} />}<ul>{selected.attachments.map((a: Row) => <li key={a.id}><ActionLink href={a.view_url || `/api/purchases/attachments/${a.id}`}>{a.name}</ActionLink> {manager && !a.view_url && <ActionButton disabled={busy} onClick={async () => { if (!(await confirmAction({ title: "Eliminar anexo?", message: `O anexo «${a.name}» da compra «${selected.document}» será eliminado da IAZELA. Esta ação não pode ser anulada. A compra e os movimentos associados serão preservados.`, confirmLabel: "Eliminar anexo", tone: "danger" }))) return; act(async () => { await api(`/purchases/${selected.id}/attachments/${a.id}/remove`, {}); setNotice("Anexo eliminado com sucesso."); await load(); }); }}>Remover</ActionButton>}{a.validation_status === "por_validar" && <small> · Anexo por validar</small>}</li>)}</ul>
        {manager && <label>Anexar fatura (PDF, JPG ou PNG, até 5 MB)<input type="file" accept=".pdf,.jpg,.jpeg,.png" disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) act(async () => { const body = new FormData(); body.append("file", file); const response = await fetch(`/api/purchases/${selected.id}/attachments`, { method: "POST", body, credentials: "same-origin" }); if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired")); const result = await response.json(); if (!response.ok) throw new Error(result.detail || "Falha ao anexar documento."); await load(); }); }} /></label>}
        <h3>Pagamentos associados</h3>{!selected.allocations.length && !selected.reconciliations?.length && <p>Ainda sem pagamentos associados.</p>}{selected.allocations.map((a: Row) => { const bank = banks.find(b => b.id === a.transaction_id); return <p key={a.id}>{bank ? `${day(bank.movement_date)} · ${bank.account_name} · ${bank.description}` : a.transaction_id} · <strong>{money(a.amount_cents)}</strong> {manager && <ActionButton disabled={busy} onClick={() => act(async () => { if (!(await confirmAction({ title: "Desfazer associação ao pagamento?", message: `A associação à compra «${selected.document}» será retirada. O movimento bancário e o histórico serão preservados.`, confirmLabel: "Desfazer associação", tone: "danger" }))) return; await api(`/purchases/${selected.id}/allocations/${a.id}/remove`, {}); await load(); })}>Desfazer associação</ActionButton>}</p>; })}
        {(selected.reconciliations || []).map((g: Row) => <p key={g.id}>Reconciliação confirmada · <strong>{money(g.amount_cents)}</strong> · {g.reason} {onOpenHistory && <ActionButton onClick={() => { close(); onOpenHistory(); }}>Ver no histórico</ActionButton>}</p>)}
        {embedded && manager && selected.origin !== "efatura_testes" && selected.remaining_cents > 0 && <ActionButton onClick={() => { close(); onOpenMovements?.(); }}>Reconciliar pagamento nos movimentos</ActionButton>}
        {!embedded && manager && selected.origin !== "efatura_testes" && selected.remaining_cents > 0 && <ManagedForm className="purchase-form" onSubmit={e => { e.preventDefault(); act(async () => { await api(`/purchases/${selected.id}/allocate`, { transaction_id: transactionId, amount }); await load(); setTransactionId(""); setAmount(""); }); }}><h3>Associar movimento bancário</h3><p>Por reconciliar: <strong>{money(selected.remaining_cents)}</strong>. Valores iguais são sugestões; confirme a descrição e a data.</p><label>Movimento<select value={transactionId} required disabled={busy} onChange={e => { setTransactionId(e.target.value); const bank = candidates.find(b => b.id === e.target.value); setAmount(bank ? (Math.min(bank.available_cents, selected.remaining_cents) / 100).toFixed(2).replace(".", ",") : ""); }}><option value="">Selecionar movimento</option>{candidates.map(b => <option key={b.id} value={b.id}>{day(b.movement_date)} · {b.account_name} · {b.description} · {money(b.available_cents)} disponíveis{b.available_cents === selected.remaining_cents ? " · Valor igual" : ""}</option>)}</select></label><label>Valor a associar (€)<input inputMode="decimal" required value={amount} disabled={busy} onChange={e => setAmount(e.target.value)} /></label><ActionButton className="primary" disabled={busy || !transactionId}>Confirmar associação</ActionButton>{!candidates.length && <p>Não há movimentos disponíveis. Importe o extrato em Bancos.</p>}</ManagedForm>}
      </>}
    </AppDialog>}
  </>;
}
