import { useState } from "react";
import { api } from "./App";
type Row = Record<string, any>;
export default function DriveAutoMatch({ documents, onUpdated }: { documents: Row[]; onUpdated: () => Promise<void> }) {
  const [rows, setRows] = useState<Row[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [next, setNext] = useState("");
  async function find(page = "") {
    setBusy(true); setError("");
    try {
      const result = await api("/documents/assistant/drive-match", { ids: documents.slice(0, 200).map(d => d.id), page_token: page });
      setRows(result.results); setNext(result.next_page);
      setSelected(result.results.filter((r: Row) => r.status === "sugerido").slice(0, 20).map((r: Row) => r.drive_id));
      setNotice(!documents.length ? "PDFs consultados. Não existem documentos no período selecionado para comparar; importe o ficheiro e-Fatura ou altere as datas." : "Consulta concluída. Confirme as correspondências propostas para guardar as referências.");
    } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel">
    <h3>Associação automática dos PDFs Drive</h3>
    <p>O sistema cruza todos os documentos do período, sem obrigar a escolher uma fatura. A pesquisa inclui a pasta configurada e todas as subpastas. Os ficheiros permanecem no Drive.</p>
    <button type="button" className="primary" disabled={busy} onClick={() => find()}>Procurar correspondências automaticamente</button>
    {!documents.length && <p role="status">Não existem documentos para comparar neste período. Pode consultar os PDFs do Drive; para os associar, importe o ficheiro e-Fatura ou altere as datas.</p>}
    {documents.length > 200 && <p>A análise usa os primeiros 200 documentos. Reduza o período para conferir os restantes.</p>}
    {busy && <p role="status">A analisar PDFs…</p>}{error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="table-wrap"><table><thead><tr><th>Confirmar</th><th>PDF</th><th>Documento identificado</th><th>Confiança e motivo</th></tr></thead><tbody>{rows.map(r => <tr key={r.drive_id}>
      <td><input type="checkbox" aria-label={"Associar " + r.name} disabled={busy || r.status !== "sugerido"} checked={selected.includes(r.drive_id)} onChange={e => { const checked = e.target.checked; setSelected(ids => checked ? [...ids, r.drive_id].slice(0, 20) : ids.filter(id => id !== r.drive_id)); }} /></td>
      <td><a href={"https://drive.google.com/file/d/" + r.drive_id + "/view"} target="_blank" rel="noreferrer">{r.name}</a></td>
      <td>{r.match ? `${r.match.supplier} · ${r.match.document}` : r.status === "associado" ? "Já associado" : "Por rever"}</td>
      <td>{r.match ? `${r.match.confidence}/100 · ${r.match.reasons.join("; ")}` : r.error || "Sem correspondência inequívoca; conferir individualmente."}</td>
    </tr>)}</tbody></table></div>
    {!!rows.length && <button type="button" disabled={busy || !selected.length} onClick={async () => {
      setBusy(true); setError("");
      try {
        const pairs = rows.filter(r => selected.includes(r.drive_id)).map(r => ({ purchase_id: r.match.purchase_id, drive_id: r.drive_id }));
        const result = await api("/documents/assistant/drive-match-confirm", { pairs, confirmed: true });
        const failures = result.results.filter((r: Row) => r.status === "falhou");
        setNotice(`${result.results.length - failures.length} associações confirmadas.`);
        if (failures.length) setError(failures.map((r: Row) => r.error).join(" "));
        setRows(current => current.map(r => result.results.some((x: Row) => x.drive_id === r.drive_id && x.status === "associado") ? { ...r, status: "associado", match: null } : r));
        setSelected(failures.map((r: Row) => r.drive_id)); await onUpdated();
      } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
    }}>Confirmar associações propostas ({selected.length})</button>}
    {next && <button type="button" disabled={busy} onClick={() => find(next)}>Analisar mais PDFs</button>}
  </section>;
}
