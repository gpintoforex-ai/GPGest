import { ActionButton, ActionLink, Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import "./RecordList.css";
import BankOverview from "./BankOverview";
import DriveImportFiles, { DriveSearchNotice, useDriveFiles } from "./DriveImportFiles";

type Row = Record<string, any>;
const money = (value: number | null | undefined, currency = "EUR") => value == null ? "Não fornecido" : new Intl.NumberFormat("pt-PT", { style: "currency", currency }).format(value / 100);
const day = (value: string) => value.split("-").reverse().join("/");

export default function BankStatements({ accounts, manager }: { accounts: Row[]; manager: boolean }) {
  const [account, setAccount] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [driveFile, setDriveFile] = useState<{ id: string; name: string } | null>(null);
  const [preview, setPreview] = useState<Row | null>(null);
  const [mapping, setMapping] = useState<Row | null>(null), [columns, setColumns] = useState<string[]>([]), [original, setOriginal] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState(1);
  const selected = accounts.some(a => a.id === account) ? account : accounts[0]?.id || "";
  const driveSearch = useDriveFiles(manager && selected ? `/accounts/${selected}/drive-statements` : null);
  useEffect(() => {
    let active = true;
    setPreview(null); setFile(null); setDriveFile(null); setRows([]); setPage(1); setMessage(""); setError("");
    setMapping(null); setColumns([]); setOriginal("");
    if (selected) api(`/accounts/${selected}/transactions`).then(data => { if (active) setRows(data); }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [selected]);
  async function upload(confirm: boolean, drive = driveFile, mappingInput = mapping) {
    if ((!file && !drive) || !selected) return;
    setBusy(true); setError(""); setMessage("");
    try {
      let result;
      if (drive) result = await api(`/accounts/${selected}/import-statement/drive?confirm=${confirm}`, { drive_id: drive.id, mapping: mappingInput ? JSON.stringify(mappingInput) : "" }, { timeoutMs: 120000 });
      else {
        const body = new FormData(); body.append("file", file as File);
        if (mappingInput) body.append("mapping", JSON.stringify(mappingInput));
        const response = await fetch(`/api/accounts/${selected}/import-statement?confirm=${confirm}`, { method: "POST", body, credentials: "same-origin" });
        if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
        result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Não foi possível importar o extrato.");
      }
      if (confirm) {
        window.dispatchEvent(new Event("iazela-bank-changed"));
        setPreview(null); setFile(null); setDriveFile(null); driveSearch.refresh(); setPage(1);
        setMessage(`${result.new_count} movimentos importados. ${result.duplicate_count} duplicados ignorados.`);
        setOriginal(result.original_url || ""); setMapping(null); setColumns([]);
        try { setRows(await api(`/accounts/${selected}/transactions`)); }
        catch { setError("A importação foi guardada, mas não foi possível atualizar a lista. Volte a abrir esta página para consultar os movimentos."); }
      } else { setPreview(result); if (result.columns) setColumns(result.columns); if (result.mapping) setMapping(result.mapping); setPage(1); }
    } catch (err) { setError(err instanceof Error ? err.message : "Falha na importação."); }
    finally { setBusy(false); }
  }
  const displayed: Row[] = preview ? preview.rows : rows;
  const totalPages = Math.max(1, Math.ceil(displayed.length / 20));
  const latest = rows.reduce<Row | null>((last, row) => !last || row.movement_date > last.movement_date || (row.movement_date === last.movement_date && (row.exported_at > last.exported_at || (row.exported_at === last.exported_at && row.source_position < last.source_position))) ? row : last, null);
  return <section className="panel">
    <h2>Extratos bancários</h2>
    <DriveSearchNotice search={driveSearch} what="extratos bancários" />
    {!accounts.length ? <p>Crie uma conta bancária para importar o extrato.</p> : <>
      <label>Conta bancária<select value={selected} disabled={busy} onChange={event => setAccount(event.target.value)}>{accounts.map(a => <option key={a.id} value={a.id}>{a.name} · {a.bank} · {a.iban_last4}</option>)}</select></label>
      {selected && <BankOverview accounts={accounts} fixedAccount={selected} canWrite={manager} />}
      {latest && <p>Saldo bancário do último extrato: <strong>{money(latest.balance_cents, accounts.find(a => a.id === selected)?.currency)}</strong> · Referência: {day(latest.movement_date)}. Pode não corresponder ao saldo atual.</p>}
      {manager && <div style={{ margin: "16px 0" }}>
        <label>Extrato bancário (.xlsx, .xls, .csv ou .xml)<input key={selected} type="file" accept=".xlsx,.xls,.csv,.txt,.xml" disabled={busy} onChange={event => { setFile(event.target.files?.[0] || null); setDriveFile(null); setPreview(null); setMapping(null); setColumns([]); setError(""); setMessage(""); }} /></label>
        <p><small>BPI Net Empresas e Viva Wallet nos formatos originais; CSV/Excel com mapeamento de colunas; XML CAMT.053 com conta e moeda identificadas. A pré-visualização não importa movimentos.</small></p>
        <ActionButton disabled={!file || busy} onClick={() => upload(false, null)}>{busy ? "A processar…" : "Pré-visualizar extrato"}</ActionButton>
        {selected && <DriveImportFiles search={driveSearch} title="Extratos no Google Drive" actionLabel="Pré-visualizar" disabled={busy}
          onPick={async drive => { setDriveFile(drive); setPreview(null); setMapping(null); setColumns([]); setMessage(""); await upload(false, drive, null); }} />}
      </div>}
      {error && <Notice tone="error">{error}</Notice>}
      {message && <Notice onDismiss={() => setMessage("")} tone="success">{message}</Notice>}
      {original && <ActionLink href={original}>Descarregar ficheiro original preservado</ActionLink>}
      {manager && columns.length > 0 && mapping && <details open={preview?.mapping_required || undefined}><summary>Mapeamento das colunas do extrato</summary><p>Escolha valor com sinal ou o par débito/crédito. As colunas de conta e moeda, quando presentes, têm de corresponder à conta selecionada.</p><div className="form-grid">{[
        ["date", "Data do movimento"], ["description", "Descrição"], ["amount", "Valor com sinal"], ["debit", "Débito"], ["credit", "Crédito"], ["balance", "Saldo (opcional)"], ["reference", "Referência (opcional)"], ["value_date", "Data valor (opcional)"], ["account", "Conta / IBAN (opcional)"], ["currency", "Moeda (opcional)"],
      ].map(([id, label]) => <label key={id}>{label}<select value={mapping[id] || ""} disabled={busy} onChange={e => { setMapping({ ...mapping, [id]: e.target.value }); setPreview(p => p && { ...p, mapping_dirty: true }); }}><option value="">Não mapear</option>{columns.map(c => <option key={c} value={c}>{c}</option>)}</select></label>)}
      <label>Separador decimal<select value={mapping.decimal_separator} disabled={busy} onChange={e => { setMapping({ ...mapping, decimal_separator: e.target.value }); setPreview(p => p && { ...p, mapping_dirty: true }); }}><option value=",">Vírgula (1.234,56)</option><option value=".">Ponto (1,234.56)</option></select></label><label>Ordem das linhas<select value={mapping.order} disabled={busy} onChange={e => { setMapping({ ...mapping, order: e.target.value }); setPreview(p => p && { ...p, mapping_dirty: true }); }}><option value="ascending">Mais antigas primeiro</option><option value="descending">Mais recentes primeiro</option></select></label></div>{preview?.mapping_dirty && <Notice>O mapeamento foi alterado. Pré-visualize novamente antes de importar.</Notice>}</details>}
      {preview && <div style={{ marginBottom: 16 }}><p><strong>{preview.format}</strong>{driveFile && <> · Google Drive: {driveFile.name}</>} · <strong>{preview.new_count} movimentos novos</strong> · {preview.duplicate_count} duplicados. Confirme que o extrato pertence à conta selecionada.</p>{preview.warnings?.map((warning: string) => <Notice key={warning} tone="warning">{warning}</Notice>)}<ActionButton className="primary" disabled={busy || !preview.new_count || preview.mapping_required || preview.mapping_dirty} onClick={() => upload(true)}>{busy ? "A importar…" : "Confirmar importação"}</ActionButton> <ActionButton disabled={busy} onClick={() => { setPreview(null); setPage(1); }}>Cancelar</ActionButton></div>}
      <p><small>Importar regista os movimentos bancários para consulta, incluindo comissões e transferências. A reconciliação com pagamentos é feita separadamente.</small></p>
      <div style={{ overflowX: "auto" }}><table><thead><tr><th>Data</th><th>Data valor</th><th>Descrição</th><th className="numeric">Valor</th><th className="numeric">Saldo</th>{preview && <th>Estado</th>}</tr></thead><tbody>{displayed.slice((page - 1) * 20, page * 20).map((row, i) => <tr key={row.id || `${row.fingerprint}-${i}`}><td>{day(row.movement_date)}</td><td>{day(row.value_date)}</td><td>{row.description}</td><td className="numeric">{money(row.amount_cents, accounts.find(a => a.id === selected)?.currency)}{row.original_available && row.import_id && <small><ActionLink href={`/api/reconciliation/accounts/${selected}/imports/${row.import_id}/original`}>Original</ActionLink></small>}</td><td className="numeric">{money(row.balance_cents, accounts.find(a => a.id === selected)?.currency)}</td>{preview && <td>{row.duplicate ? "Já importado" : "Novo"}</td>}</tr>)}</tbody></table></div>
      {!displayed.length && <p>Ainda não existem movimentos importados nesta conta.</p>}
      {totalPages > 1 && <div><ActionButton disabled={page <= 1} onClick={() => setPage(page - 1)}>Anterior</ActionButton> <span>{page} / {totalPages}</span> <ActionButton disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Seguinte</ActionButton></div>}
    </>}
  </section>;
}
