import { ActionButton, Notice } from "./components/InteractionUI";
import { useState } from "react";
import { api } from "./lib/api";
type Row = Record<string, any>;
export default function ErpAssistantStep({ documents, admin, start, end, onUpdated, onReview }: { documents: Row[]; admin: boolean; start: string; end: string; onUpdated: () => Promise<void>; onReview: (doc: Row) => void }) {
  const [rows, setRows] = useState<Row[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState(""), [checked, setChecked] = useState(false), [confirmed, setConfirmed] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  function proposal(doc: Row) {
    if (doc.erp_link_id || doc.origin === "efatura_testes") return null;
    const candidates = [...(doc.erp_candidates || [])].filter(c => !c.cancelled && !c.unavailable && !c.linked).sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
    const best = candidates[0];
    return best && !best.differences?.length && best.confidence >= 70 && (candidates.length < 2 || best.confidence - candidates[1].confidence >= 20) ? best : null;
  }
  async function run(sync: boolean) {
    setBusy(true); setError(""); setNotice(""); setChecked(false);
    try {
      if (sync) for (const store_id of [...new Set(documents.map(d => d.store_id))]) await api("/documents/erp-sync", { store_id, start, end, confirm_demo: confirmed });
      const details = [];
      for (const doc of documents) details.push(await api(`/documents/${doc.id}/detail`));
      setRows(details); setSelected(details.filter(d => proposal(d)).slice(0, 20).map(d => d.id)); setChecked(sync); await onUpdated();
    } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel">
    <h3>3.º passo — Correspondência com o ERP</h3>
    <p>Compare os documentos do período com as compras lançadas na Wintouch e confirme as correspondências.</p>
    {!documents.length && <p>Não existem documentos no período. Volte aos parâmetros para importar o e-Fatura ou alterar as datas.</p>}
    <ActionButton type="button" disabled={busy || !documents.length} onClick={() => run(false)}>Procurar correspondências no catálogo ERP</ActionButton>
    {admin && <><label className="checkbox"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> Confirmo a consulta/importação ERP, incluindo dados reais se a empresa estiver em demonstração.</label><ActionButton type="button" disabled={busy || !documents.length || !confirmed} onClick={() => run(true)}>Atualizar consulta à Wintouch</ActionButton></>}
    {busy && <p role="status">A verificar correspondências ERP…</p>}{error && <Notice role="alert" className="error">{error}</Notice>}
    <p>{checked ? "Consulta à Wintouch concluída para o período." : "O catálogo local pode estar desatualizado. A ausência de correspondência não confirma que falta o lançamento no ERP."}</p>
    <p>As propostas comparam NIF, número, tipo, data e total. Divergências, documentos anulados, já associados ou candidatos ambíguos ficam para revisão.</p>
    {!!rows.length && <ActionButton type="button" className="erp-confirm-button" disabled={busy || !selected.length} onClick={async () => {
      setBusy(true); setError(""); setNotice(""); let count = 0; const failures: string[] = []; const updated = [...rows];
      try {
        for (const id of selected) {
          const doc = rows.find(r => r.id === id); const candidate = doc && proposal(doc);
          if (!candidate) continue;
          try { await api(`/documents/${id}/erp-link`, { erp_id: candidate.id, reason: "Correspondência revista e confirmada em conjunto no assistente e-Fatura" }); count++; updated[updated.findIndex(r => r.id === id)] = await api(`/documents/${id}/detail`); }
          catch(e) { failures.push((e as Error).message); }
        }
        setRows(updated); setSelected(updated.filter(d => selected.includes(d.id) && proposal(d)).map(d => d.id)); setError(failures.join(" ")); await onUpdated();
      } catch(e) { setError((e as Error).message); } finally { setNotice(`${count} ${count === 1 ? "correspondência ERP confirmada" : "correspondências ERP confirmadas"}.${failures.length ? ` ${failures.length} operações precisam de revisão.` : ""}`); setBusy(false); }
    }}>Confirmar correspondências ERP propostas ({selected.length})</ActionButton>}
    {notice && <Notice onDismiss={() => setNotice("")} tone="success" className="erp-confirmation-notice" role="status">{notice}</Notice>}
    {rows.map(doc => <section className={`panel ${proposal(doc) || doc.erp_status === "confirmada" ? "erp-match-confident" : "erp-match-review"}`} key={doc.id}><h4>{doc.supplier_name} · {doc.document}</h4>
      {proposal(doc) && <label className="checkbox"><input type="checkbox" disabled={busy} checked={selected.includes(doc.id)} onChange={e => setSelected(current => e.target.checked ? [...current, doc.id].slice(0, 20) : current.filter(id => id !== doc.id))} /> Incluir na confirmação conjunta</label>}
      {doc.erp_status === "confirmada" ? <p>Correspondência ERP confirmada.</p> : <>
        {(doc.erp_candidates || []).map((c: Row) => <div key={c.id}>
          <p><strong>Documento ERP:</strong> {c.internal_document || "—"}<br /><strong>Doc. de referência:</strong> {c.document || "—"}</p>
          <p><span className={proposal(doc)?.id === c.id ? "erp-status erp-status-confirmada" : ""}>Confiança: {c.confidence || 0}/100</span> · {(c.reasons || []).join("; ")} · {c.unavailable ? "Já associado a outro documento" : c.differences?.length ? "Divergências: " + c.differences.join("; ") : proposal(doc)?.id === c.id ? "Correspondência proposta — por confirmar" : "Correspondência ambígua; rever individualmente"}</p><ActionButton type="button" className="erp-confirm-button" disabled={busy || c.linked || proposal(doc)?.id !== c.id} onClick={async () => {
          setBusy(true); setError("");
          try { await api(`/documents/${doc.id}/erp-link`, { erp_id: c.id, reason: "Correspondência confirmada no assistente e-Fatura" }); const updated = await api(`/documents/${doc.id}/detail`); setRows(current => current.map(r => r.id === doc.id ? updated : r)); await onUpdated(); }
          catch(e) { setError((e as Error).message); } finally { setBusy(false); }
        }}>Confirmar correspondência ERP</ActionButton></div>)}
        {!doc.erp_candidates?.length && <p>Sem correspondência encontrada. Reveja o documento e, se necessário, prepare uma proposta de lançamento.</p>}
        <ActionButton type="button" className="erp-review-button" disabled={busy} onClick={() => onReview(doc)}>Rever documento e proposta de lançamento</ActionButton>
      </>}
    </section>)}
  </section>;
}
