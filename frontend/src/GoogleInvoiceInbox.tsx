import { useEffect, useState } from "react";
import { api, Field, Form } from "./App";

type Row = Record<string, any>;
const base = "/integrations/attachments/google";
const euro = (value: number) => (value / 100).toLocaleString("pt-PT", { style: "currency", currency: "EUR" });
export default function GoogleInvoiceInbox() {
  const [files, setFiles] = useState<Row[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [status, setStatus] = useState<Row | null>(null), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [nextPage, setNextPage] = useState(""), [period, setPeriod] = useState<Row | null>(null);
  const [editing, setEditing] = useState<Row | null>(null), [matches, setMatches] = useState<Row | null>(null);
  const [search, setSearch] = useState("");
  async function refresh() { setFiles(await api(base + "/files")); }
  useEffect(() => {
    api(base + "/status").then(setStatus).catch(e => setError(e.message));
    refresh().catch(e => setError(e.message));
  }, []);
  async function scan(values: Row, page = "") {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api(base + "/scan", { ...values, page_token: page });
      setNextPage(result.next_page); setPeriod(values);
      setNotice(`${result.files.length} anexos encontrados nesta página; ${result.skipped} ficheiros ignorados por formato ou tamanho.`);
      await refresh();
      setFiles(current => [...result.files, ...current.filter(f => !result.files.some((r: Row) => r.id === f.id))]);
    } catch(e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function openMatches(file: Row) {
    setBusy(true); setError("");
    try {
      setMatches(await api(base + "/files/" + file.id + "/suggestions"));
      setSearch(""); setEditing(file);
    } catch(e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="panel" style={{ marginTop: 24 }}>
    <h3>Anexos Gmail → Drive → Faturas</h3>
    <p>Consulte os emails, selecione os anexos e confirme a associação. Não cria faturas nem altera pagamentos, ERP ou entrega à contabilidade.</p>
    {status && !status.connected && <p>Ligue a conta Google nas Configurações antes de consultar emails.</p>}
    {status?.connected && <>
      <p>Conta: <b>{status.email}</b>. PDF, JPG e PNG até 5 MB; selecione até 30 anexos por importação. Cada consulta lê até 30 mensagens; use «Consultar mais emails» para continuar.</p>
      <Form submitLabel="Consultar emails" onSubmit={v => scan(v)}>
        <fieldset disabled={busy} className="form-grid">
          <Field label="Emails desde"><input name="start_date" type="date" required /></Field>
          <Field label="Emails até"><input name="end_date" type="date" required /></Field>
        </fieldset>
      </Form>
      {nextPage && period && <button disabled={busy} onClick={() => scan(period, nextPage)}>Consultar mais emails</button>}
      <button className="primary" disabled={busy || !selected.length} onClick={async () => {
        setBusy(true); setError(""); setNotice("");
        try {
          const result = await api(base + "/import", { ids: selected });
          const failed = result.results.filter((r: Row) => r.status === "falhou");
          setNotice(`${result.results.length - failed.length} anexos disponíveis no Drive. ${failed.length} falhas.`);
          if (failed.length) setError(failed.map((r: Row) => `${files.find(f => f.id === r.id)?.name || "Anexo"}: ${r.error}`).join(" "));
          setSelected(failed.map((r: Row) => r.id));
          await refresh();
        } catch(e) { setError((e as Error).message); }
        finally { setBusy(false); }
      }}>Guardar selecionados no Drive ({selected.length})</button>
    </>}
    {busy && <p role="status">A processar…</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="success" role="status">{notice}</p>}
    <div className="table-wrap"><table><thead><tr><th>Selecionar</th><th>Anexo</th><th>Origem</th><th>Estado</th><th>Ações</th></tr></thead><tbody>
      {files.map(file => <tr key={file.id}>
        <td><input aria-label={"Selecionar " + file.name} type="checkbox" disabled={busy || file.uploaded} checked={selected.includes(file.id)} onChange={e => {
          const checked = e.target.checked;
          setSelected(values => checked ? [...values, file.id].slice(0, 30) : values.filter(id => id !== file.id));
        }} /></td>
        <td><b>{file.name}</b><br />{Math.ceil(file.size / 1024)} KB</td>
        <td>{file.sender}<br />{file.subject}<br />{file.received_at.slice(0, 10)}</td>
        <td>{file.purchase_id ? "Associado à fatura" : file.uploaded ? "No Drive · por associar" : "Por importar"}</td>
        <td>{file.uploaded && <>
          <a href={file.drive_url} target="_blank" rel="noreferrer">Abrir Drive</a>{" "}
          <a href={"/api" + base + "/files/" + file.id + "/preview"} target="_blank" rel="noreferrer">Ver anexo</a>{" "}
          {!file.purchase_id && <button disabled={busy} onClick={() => openMatches(file)}>Associar à fatura</button>}
        </>}</td>
      </tr>)}
    </tbody></table></div>
    {!files.length && <p>Ainda não existem anexos consultados nesta conta.</p>}
    <p>As sugestões usam o nome do ficheiro, assunto e texto extraível do PDF. Imagens e PDFs digitalizados não têm OCR nesta versão. A confiança é uma pontuação indicativa, não uma garantia.</p>
    {editing && matches && <div className="overlay"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="gmail-associate-title">
      <div className="panel-title"><h2 id="gmail-associate-title">Associar {editing.name}</h2><button disabled={busy} onClick={() => setEditing(null)}>Fechar</button></div>
      {matches.suggestions.length ? matches.suggestions.map((r: Row) => <p key={r.id}><b>{r.supplier} · {r.document} · {euro(r.amount_cents)}</b><br />Confiança: {r.confidence}/100 · {r.reasons.join("; ")}</p>) : <p>Sem sugestões suficientes. Pode procurar e escolher a fatura manualmente.</p>}
      <Field label="Procurar fornecedor, NIF ou documento"><input value={search} onChange={e => setSearch(e.target.value)} /></Field>
      <button disabled={busy} onClick={async () => {
        setBusy(true);
        try { setMatches(await api(base + "/files/" + editing.id + "/suggestions?search=" + encodeURIComponent(search))); }
        catch(e) { setError((e as Error).message); }
        finally { setBusy(false); }
      }}>Pesquisar faturas</button>
      {matches.limited && <p>Lista limitada a 1000 faturas. Refine a pesquisa para encontrar documentos mais antigos.</p>}
      <Form submitLabel="Confirmar associação" onSubmit={async v => {
        await api(base + "/files/" + editing.id + "/associate", { purchase_id: v.purchase_id, confirmed: v.confirmed === "on" });
        setEditing(null); setNotice("Anexo associado. O pagamento e os restantes estados da fatura mantêm-se."); await refresh();
      }}>
        <Field label="Fatura a associar"><select name="purchase_id" required defaultValue="">
          <option value="" disabled>Escolha a fatura</option>
          {matches.documents.map((r: Row) => <option key={r.id} value={r.id}>{r.supplier} · {r.document} · {r.date} · {euro(r.amount_cents)}</option>)}
        </select></Field>
        <label className="checkbox"><input name="confirmed" type="checkbox" required /> Confirmei que este anexo pertence à fatura selecionada.</label>
      </Form>
    </section></div>}
  </section>;
}
