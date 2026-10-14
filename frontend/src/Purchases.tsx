import { ScanButton } from "./DocumentScanner";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "./App";
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
  const [removeDocument, setRemoveDocument] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<Row | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const selectedStore = store || stores[0]?.id || "";
  useEffect(() => { setModal(""); setImportFile(null); setImportPreview(null); }, [selectedStore]);
  const selected = rows.find(r => r.id === selectedId);
  async function load() {
    const [purchases, transactions] = await Promise.all([api("/purchases"), api("/purchases/bank-transactions")]);
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
    setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError(e instanceof Error ? e.message : "Não foi possível concluir a operação."); }
    finally { setBusy(false); }
  }
  async function uploadPurchases(confirm: boolean) {
    if (!importFile) return;
    await act(async () => {
      const body = new FormData(); body.append("file", importFile);
      const response = await fetch(`/api/purchases/import/${selectedStore}?confirm=${confirm}`, { method: "POST", body, credentials: "same-origin" });
      if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Falha na importacao.");
      if (confirm) {
        await load();
        const dates = result.rows.map((r: Row) => r.document_date as string).sort();
        if (dates.length) setRange({ from: dates[0], to: dates[dates.length - 1] });
        setSearch(""); setPayment(""); setDocuments(""); setView("compras");
        setModal(""); setImportPreview(null); setImportFile(null);
        setNotice(`${result.new_count} compras importadas; ${result.duplicate_count} duplicados ignorados.`);
      }
      else setImportPreview(result);
    });
  }
  function close() { if (!busy) { setModal(""); setRemoveDocument(""); setError(""); } }
  function openDetail(row: Row) { setSelectedId(row.id); setTransactionId(""); setAmount(""); setRemoveDocument(""); setError(""); setModal("detail"); }
  const visible = rows.filter(r => !store || r.store_id === store);
  const filtered = visible.filter(r => (!range.from || r.document_date >= range.from) && (!range.to || r.document_date <= range.to) && (!payment || r.payment_status === payment) && (!documents || (documents === "missing" ? !r.attachments.length : !!r.attachments.length)) && `${r.supplier_name} ${r.supplier_nif} ${r.document}`.toLocaleLowerCase("pt-PT").includes(search.toLocaleLowerCase("pt-PT")));
  const bankMissing = banks.filter(r => (!store || r.store_id === store) && r.amount_cents < 0 && r.available_cents > 0 && (!range.from || r.movement_date >= range.from) && (!range.to || r.movement_date <= range.to) && `${r.description} ${r.account_name}`.toLocaleLowerCase("pt-PT").includes(search.toLocaleLowerCase("pt-PT")));
  const pageRows = view === "compras" ? filtered : bankMissing;
  const pages = Math.max(1, Math.ceil(pageRows.length / 20));
  const currentPage = Math.min(page, pages);
  const candidates = selected ? banks.filter(b => b.store_id === selected.store_id && b.amount_cents * selected.amount_cents < 0 && b.available_cents > 0 && !selected.allocations.some((a: Row) => a.transaction_id === b.id)).sort((a, b) => Number(b.available_cents === selected.remaining_cents) - Number(a.available_cents === selected.remaining_cents) || b.movement_date.localeCompare(a.movement_date)) : [];
  return <>
    <div className="page-intro"><div><h1>{embedded ? "Faturas e documentos" : "Compras"}</h1><p>Pagamento e documento são acompanhados separadamente.</p></div>{manager && <div className="actions"><button disabled={busy || !selectedStore} onClick={() => { setImportFile(null); setImportPreview(null); setError(""); setModal("import"); }}>Importar ficheiro</button><button disabled={busy || !selectedStore} onClick={() => { setError(""); setModal("config"); }}>Configurar e-Fatura</button><button disabled={busy || !connection?.has_password || !connection?.certificate_ready} onClick={() => setModal("sync")}>Sincronizar e-Fatura</button><button className="primary" disabled={busy || !selectedStore} onClick={() => { setError(""); setModal("new"); }}>Registar compra</button></div>}</div>
    {error && !modal && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {manager && connection && !connection.certificate_ready && <section className="panel"><p><strong>e-Fatura: certificado por configurar.</strong> A sincronização ficará disponível depois de configurar os acessos e o certificado da AT no servidor. Já pode registar compras, anexar documentos e reconciliar pagamentos.</p></section>}
    <section className="panel">
      <div className="actions"><button onClick={() => setView("compras")} aria-pressed={view === "compras"}>{embedded ? "Faturas" : "Compras"}</button><button onClick={() => setView("bancos")} aria-pressed={view === "bancos"}>Pagamentos sem associação completa</button></div>
      <div className="purchase-filters"><label>Desde<input type="date" value={range.from} onChange={e => setRange({ ...range, from: e.target.value })} /></label><label>Até<input type="date" value={range.to} onChange={e => setRange({ ...range, to: e.target.value })} /></label><label>Pesquisar<input value={search} onChange={e => setSearch(e.target.value)} placeholder="Fornecedor, NIF ou documento" /></label>{view === "compras" && <><label>Pagamento<select value={payment} onChange={e => setPayment(e.target.value)}><option value="">Todos</option>{Object.entries(status).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label>Documento<select value={documents} onChange={e => setDocuments(e.target.value)}><option value="">Todos</option><option value="missing">Por anexar</option><option value="attached">Anexado</option></select></label></>}</div>
      {range.from && range.to && range.from > range.to ? <p role="alert">A data inicial deve ser anterior à data final.</p> : view === "compras" ? <>
        <p>{filtered.length} compras · Total: <strong>{money(filtered.reduce((sum, r) => sum + r.amount_cents, 0))}</strong> · Documentos por anexar: <strong>{filtered.filter(r => !r.attachments.length).length}</strong></p>
        <div className="purchase-table"><table><thead><tr><th>Data</th><th>Fornecedor</th><th>Documento</th><th>Valor</th><th>Pagamento</th><th>Documento</th><th>Validação</th><th /></tr></thead><tbody>{filtered.slice((currentPage - 1) * 20, currentPage * 20).map(r => <tr key={r.id}><td>{day(r.document_date)}</td><td>{r.supplier_name}<br /><small>{r.supplier_nif}</small></td><td>{r.document}<br /><small>{r.origin === "efatura_testes" ? "e-Fatura (teste)" : r.origin === "efatura" ? "e-Fatura" : r.origin === "ficheiro" ? "Ficheiro" : "Registo manual"}</small></td><td>{money(r.amount_cents)}</td><td>{status[r.payment_status]}<br /><small>{money(r.paid_cents)} associados</small></td><td><span className={`pill ${r.attachments.length ? "neutral" : ""}`}>{r.attachments.length ? "Anexado" : "Por anexar"}</span></td><td>{r.validated ? "Validada" : "Por validar"}</td><td><button onClick={() => openDetail(r)}>Abrir</button></td></tr>)}</tbody></table></div>
      </> : <><p>Movimentos de saída com valor ainda sem compra associada. A falta de associação não prova que falta uma fatura.</p><div className="purchase-table"><table><thead><tr><th>Data</th><th>Conta</th><th>Descrição</th><th>Valor</th><th>Por associar</th></tr></thead><tbody>{bankMissing.slice((currentPage - 1) * 20, currentPage * 20).map(r => <tr key={r.id}><td>{day(r.movement_date)}</td><td>{r.account_name}</td><td>{r.description}</td><td>{money(r.amount_cents)}</td><td>{money(r.available_cents)}</td></tr>)}</tbody></table></div></>}
      {!pageRows.length && <p>{loaded ? "Sem registos para os filtros selecionados." : "A carregar…"}</p>}
      {pages > 1 && <div className="actions"><button disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Anterior</button><span>{currentPage} / {pages}</span><button disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)}>Seguinte</button></div>}
    </section>
    {modal && <dialog ref={dialog} className="purchase-dialog" aria-labelledby="purchase-dialog-title" onCancel={e => { e.preventDefault(); close(); }}>
      <div className="panel-title"><h2 id="purchase-dialog-title">{modal === "import" ? "Importar compras" : modal === "new" ? "Registar compra" : modal === "config" ? "Configurar e-Fatura" : modal === "sync" ? "Sincronizar e-Fatura" : selected?.document}</h2><button disabled={busy} onClick={close} aria-label="Fechar">×</button></div>
      {error && <p className="error" role="alert">{error}</p>}
      {modal === "import" && <div className="purchase-form">
        <p>Loja: <strong>{stores.find(s => s.id === selectedStore)?.name}</strong>. Importe uma lista CSV ou XLSX com NIF do fornecedor, documento, data e total. O comprovativo será anexado separadamente.</p>
        <a href="/api/purchases/import-template">Descarregar modelo CSV</a>
        <small>O modelo contém uma linha fictícia de exemplo; substitua-a pelas suas compras.</small>
        <label>Ficheiro de compras<input type="file" accept=".csv,.xlsx" disabled={busy} onChange={e => { setImportFile(e.target.files?.[0] || null); setImportPreview(null); setError(""); }} /></label>
        <button disabled={busy || !importFile} onClick={() => uploadPurchases(false)}>Pré-visualizar</button>
        {!importPreview && <small>Depois da pré-visualização, confirme a importação para guardar as compras.</small>}
        {importPreview && <>
          <p><strong>{importPreview.new_count} compras novas</strong> · {importPreview.duplicate_count} duplicados · {importPreview.conflict_count} conflitos.</p>
          {!!importPreview.conflict_count && <p role="alert">Há documentos repetidos com dados diferentes. Corrija o ficheiro antes de importar.</p>}
          {!importPreview.new_count && !importPreview.conflict_count && <p>Todas as compras deste ficheiro já existem. Não há compras novas para importar.</p>}
          <button className="primary" disabled={busy || !importPreview.new_count || !!importPreview.conflict_count} onClick={() => uploadPurchases(true)}>{busy ? "A processar…" : `Confirmar importação de ${importPreview.new_count} compras`}</button>
          <div className="purchase-table"><table><thead><tr><th>Linha</th><th>Fornecedor</th><th>Documento</th><th>Data</th><th>Total</th><th>Estado</th></tr></thead><tbody>{importPreview.rows.slice(0, 100).map((r: Row) => <tr key={r.line}><td>{r.line}</td><td>{r.supplier_name}<br /><small>{r.supplier_nif}</small></td><td>{r.document}</td><td>{day(r.document_date)}</td><td>{money(r.amount_cents)}</td><td>{r.status === "new" ? "Nova" : r.status === "duplicate" ? "Já existe" : "Conflito"}</td></tr>)}</tbody></table></div>
          {importPreview.rows.length > 100 && <p>Mostradas as primeiras 100 linhas. A confirmação importa todas as compras novas do ficheiro.</p>}
          <button className="primary" disabled={busy || !importPreview.new_count || !!importPreview.conflict_count} onClick={() => uploadPurchases(true)}>Confirmar importação</button>
        </>}
      </div>}
      {modal === "new" && <form className="purchase-form" onSubmit={(e: FormEvent<HTMLFormElement>) => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.currentTarget)); act(async () => { await api("/purchases", data); await load(); setModal(""); setNotice("Compra registada."); }); }}>
        <label>Loja<select name="store_id" defaultValue={selectedStore} required>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><label>NIF do fornecedor<input name="supplier_nif" pattern="[0-9]{9}" maxLength={9} required /></label><label>Fornecedor<input name="supplier_name" maxLength={300} required /></label><label>Tipo<select name="document_type"><option>FT</option><option>FS</option><option>FR</option><option>ND</option><option>NC</option></select></label><label>Número do documento<input name="document" maxLength={100} required /></label><label>Data<input name="document_date" type="date" required /></label><label>Valor total (€)<input name="amount" inputMode="decimal" required placeholder="100,00" /></label><label>Observações<textarea name="notes" maxLength={5000} /></label><button className="primary" disabled={busy}>Guardar compra</button>
      </form>}
      {modal === "config" && <form className="purchase-form" onSubmit={e => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.currentTarget)); act(async () => { setConnection(await api(`/purchases/efatura/${selectedStore}`, data)); setModal(""); setNotice("Acesso ao e-Fatura guardado."); }); }}>
        <p>Loja: {stores.find(s => s.id === selectedStore)?.name}. A senha é guardada cifrada e não é devolvida ao navegador.</p><label>NIF da empresa<input name="nif" defaultValue={connection?.nif} pattern="[0-9]{9}" required disabled={!admin} /></label><label>Subutilizador AT<input name="username" defaultValue={connection?.username} placeholder="NIF/Utilizador" required disabled={!admin} /></label><label>Senha<input name="password" type="password" autoComplete="new-password" placeholder={connection?.has_password ? "Deixar vazio para manter" : "Senha do subutilizador"} disabled={!admin} /></label><label>Ambiente<select name="environment" defaultValue={connection?.environment || "testes"} disabled={!admin}><option value="testes">Testes</option><option value="producao">Produção</option></select></label><p>Certificado do ambiente selecionado: {connection?.certificate_ready ? "configurado" : "por configurar no servidor"}. Os dados do ambiente de testes são dados de teste.</p>{admin ? <button className="primary" disabled={busy}>Guardar acesso</button> : <p>A configuração do acesso é feita pelo administrador.</p>}
      </form>}
      {modal === "sync" && <form className="purchase-form" onSubmit={e => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.currentTarget)); act(async () => { const result = await api(`/purchases/efatura/${selectedStore}/sync`, data); await load(); setModal(""); setNotice(`${result.imported} compras importadas; ${result.updated} atualizadas; ${result.total} consultadas.`); }); }}><p>Consulta de compras no ambiente <strong>{connection?.environment === "producao" ? "Produção" : "Testes"}</strong>. Selecione um intervalo dentro do mesmo mês.</p><label>Desde<input name="start" type="date" defaultValue={currentMonth().from} required /></label><label>Até<input name="end" type="date" defaultValue={currentMonth().to} required /></label><button className="primary" disabled={busy}>Confirmar sincronização</button></form>}
      {modal === "detail" && selected && <>
        <p><strong>{selected.supplier_name}</strong> · NIF {selected.supplier_nif} · {day(selected.document_date)} · {money(selected.amount_cents)}</p><p>{status[selected.payment_status]} · {selected.attachments.length ? "Documento anexado" : "Documento por anexar"} · {selected.validated ? "Validada" : "Por validar"}</p>{selected.notes && <p>{selected.notes}</p>}
        {manager && <button disabled={busy} onClick={() => act(async () => { await api(`/purchases/${selected.id}/validate`, { validated: !selected.validated }); await load(); })}>{selected.validated ? "Retirar validação" : "Validar compra"}</button>}
        <h3>Documentos</h3>{manager && <ScanButton initialDocument={selected} onSaved={load} />}<ul>{selected.attachments.map((a: Row) => <li key={a.id}><a href={a.view_url || `/api/purchases/attachments/${a.id}`}>{a.name}</a> {manager && !a.view_url && <button disabled={busy} onClick={() => setRemoveDocument(a.id)}>Remover</button>}{a.validation_status === "por_validar" && <small> · Anexo por validar</small>}</li>)}</ul>
        {removeDocument && <div><p>Confirmar a remoção deste documento anexado?</p><button disabled={busy} onClick={() => act(async () => { await api(`/purchases/${selected.id}/attachments/${removeDocument}/remove`, {}); setRemoveDocument(""); await load(); })}>Confirmar remoção</button> <button disabled={busy} onClick={() => setRemoveDocument("")}>Cancelar</button></div>}
        {manager && <label>Anexar fatura (PDF, JPG ou PNG, até 5 MB)<input type="file" accept=".pdf,.jpg,.jpeg,.png" disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) act(async () => { const body = new FormData(); body.append("file", file); const response = await fetch(`/api/purchases/${selected.id}/attachments`, { method: "POST", body, credentials: "same-origin" }); if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired")); const result = await response.json(); if (!response.ok) throw new Error(result.detail || "Falha ao anexar documento."); await load(); }); }} /></label>}
        <h3>Pagamentos associados</h3>{!selected.allocations.length && !selected.reconciliations?.length && <p>Ainda sem pagamentos associados.</p>}{selected.allocations.map((a: Row) => { const bank = banks.find(b => b.id === a.transaction_id); return <p key={a.id}>{bank ? `${day(bank.movement_date)} · ${bank.account_name} · ${bank.description}` : a.transaction_id} · <strong>{money(a.amount_cents)}</strong> {manager && <button disabled={busy} onClick={() => act(async () => { await api(`/purchases/${selected.id}/allocations/${a.id}/remove`, {}); await load(); })}>Desfazer associação</button>}</p>; })}
        {(selected.reconciliations || []).map((g: Row) => <p key={g.id}>Reconciliação confirmada · <strong>{money(g.amount_cents)}</strong> · {g.reason} {onOpenHistory && <button onClick={() => { close(); onOpenHistory(); }}>Ver no histórico</button>}</p>)}
        {embedded && manager && selected.origin !== "efatura_testes" && selected.remaining_cents > 0 && <button onClick={() => { close(); onOpenMovements?.(); }}>Reconciliar pagamento nos movimentos</button>}
        {!embedded && manager && selected.origin !== "efatura_testes" && selected.remaining_cents > 0 && <form className="purchase-form" onSubmit={e => { e.preventDefault(); act(async () => { await api(`/purchases/${selected.id}/allocate`, { transaction_id: transactionId, amount }); await load(); setTransactionId(""); setAmount(""); }); }}><h3>Associar movimento bancário</h3><p>Por reconciliar: <strong>{money(selected.remaining_cents)}</strong>. Valores iguais são sugestões; confirme a descrição e a data.</p><label>Movimento<select value={transactionId} required disabled={busy} onChange={e => { setTransactionId(e.target.value); const bank = candidates.find(b => b.id === e.target.value); setAmount(bank ? (Math.min(bank.available_cents, selected.remaining_cents) / 100).toFixed(2).replace(".", ",") : ""); }}><option value="">Selecionar movimento</option>{candidates.map(b => <option key={b.id} value={b.id}>{day(b.movement_date)} · {b.account_name} · {b.description} · {money(b.available_cents)} disponíveis{b.available_cents === selected.remaining_cents ? " · Valor igual" : ""}</option>)}</select></label><label>Valor a associar (€)<input inputMode="decimal" required value={amount} disabled={busy} onChange={e => setAmount(e.target.value)} /></label><button className="primary" disabled={busy || !transactionId}>Confirmar associação</button>{!candidates.length && <p>Não há movimentos disponíveis. Importe o extrato em Bancos.</p>}</form>}
      </>}
    </dialog>}
  </>;
}
