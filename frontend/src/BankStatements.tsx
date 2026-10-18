import { ActionButton, Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./App";
import "./RecordList.css";

type Row = Record<string, any>;
const money = (value: number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(value / 100);
const day = (value: string) => value.split("-").reverse().join("/");

export default function BankStatements({ accounts, manager }: { accounts: Row[]; manager: boolean }) {
  const [account, setAccount] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Row | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState(1);
  const selected = accounts.some(a => a.id === account) ? account : accounts[0]?.id || "";
  useEffect(() => {
    let active = true;
    setPreview(null); setFile(null); setRows([]); setPage(1); setMessage(""); setError("");
    if (selected) api(`/accounts/${selected}/transactions`).then(data => { if (active) setRows(data); }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [selected]);
  async function upload(confirm: boolean) {
    if (!file || !selected) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const body = new FormData(); body.append("file", file);
      const response = await fetch(`/api/accounts/${selected}/import-statement?confirm=${confirm}`, { method: "POST", body, credentials: "same-origin" });
      if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Não foi possível importar o extrato.");
      if (confirm) {
        setPreview(null); setFile(null); setPage(1);
        setMessage(`${result.new_count} movimentos importados. ${result.duplicate_count} duplicados ignorados.`);
        try { setRows(await api(`/accounts/${selected}/transactions`)); }
        catch { setError("A importação foi guardada, mas não foi possível atualizar a lista. Volte a abrir esta página para consultar os movimentos."); }
      } else { setPreview(result); setPage(1); }
    } catch (err) { setError(err instanceof Error ? err.message : "Falha na importação."); }
    finally { setBusy(false); }
  }
  const displayed: Row[] = preview ? preview.rows : rows;
  const totalPages = Math.max(1, Math.ceil(displayed.length / 20));
  const latest = rows.reduce<Row | null>((last, row) => !last || row.movement_date > last.movement_date || (row.movement_date === last.movement_date && (row.exported_at > last.exported_at || (row.exported_at === last.exported_at && row.source_position < last.source_position))) ? row : last, null);
  return <section className="panel">
    <h2>Extratos bancários</h2>
    {!accounts.length ? <p>Crie uma conta bancária para importar o extrato.</p> : <>
      <label>Conta bancária<select value={selected} disabled={busy} onChange={event => setAccount(event.target.value)}>{accounts.map(a => <option key={a.id} value={a.id}>{a.name} · {a.bank} · {a.iban_last4}</option>)}</select></label>
      {latest && <p>Saldo do último extrato: <strong>{money(latest.balance_cents)}</strong> · {day(latest.movement_date)}</p>}
      {manager && <div style={{ margin: "16px 0" }}>
        <label>Extrato bancário (.xlsx ou .xls)<input key={selected} type="file" accept=".xlsx,.xls" disabled={busy} onChange={event => { setFile(event.target.files?.[0] || null); setPreview(null); setError(""); setMessage(""); }} /></label>
        <p><small>Formatos suportados: BPI Net Empresas (.xlsx) e Viva Wallet (.xls original). Não é necessário converter o ficheiro.</small></p>
        <ActionButton disabled={!file || busy} onClick={() => upload(false)}>{busy ? "A processar…" : "Pré-visualizar extrato"}</ActionButton>
      </div>}
      {error && <Notice tone="error">{error}</Notice>}
      {message && <Notice onDismiss={() => setMessage("")} tone="success">{message}</Notice>}
      {preview && <div style={{ marginBottom: 16 }}><p><strong>{preview.format}</strong> · <strong>{preview.new_count} movimentos novos</strong> · {preview.duplicate_count} duplicados. Confirme que o extrato pertence à conta selecionada.</p>{preview.warnings?.map((warning: string) => <Notice key={warning} tone="warning">{warning}</Notice>)}<ActionButton className="primary" disabled={busy || !preview.new_count} onClick={() => upload(true)}>{busy ? "A importar…" : "Confirmar importação"}</ActionButton> <ActionButton disabled={busy} onClick={() => { setPreview(null); setPage(1); }}>Cancelar</ActionButton></div>}
      <p><small>Importar regista os movimentos bancários para consulta, incluindo comissões e transferências. A reconciliação com pagamentos é feita separadamente.</small></p>
      <div style={{ overflowX: "auto" }}><table><thead><tr><th>Data</th><th>Data valor</th><th>Descrição</th><th className="numeric">Valor</th><th className="numeric">Saldo</th>{preview && <th>Estado</th>}</tr></thead><tbody>{displayed.slice((page - 1) * 20, page * 20).map((row, i) => <tr key={row.id || `${row.fingerprint}-${i}`}><td>{day(row.movement_date)}</td><td>{day(row.value_date)}</td><td>{row.description}</td><td className="numeric">{money(row.amount_cents)}</td><td className="numeric">{money(row.balance_cents)}</td>{preview && <td>{row.duplicate ? "Já importado" : "Novo"}</td>}</tr>)}</tbody></table></div>
      {!displayed.length && <p>Ainda não existem movimentos importados nesta conta.</p>}
      {totalPages > 1 && <div><ActionButton disabled={page <= 1} onClick={() => setPage(page - 1)}>Anterior</ActionButton> <span>{page} / {totalPages}</span> <ActionButton disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Seguinte</ActionButton></div>}
    </>}
  </section>;
}
