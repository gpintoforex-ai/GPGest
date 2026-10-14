import { ScanButton } from "./DocumentScanner";
import { useEffect, useRef, useState } from "react";
import { api } from "./App";
import { StatusBadge } from "./components/BusinessUI";
import AssistantIdentity from "./components/AssistantIdentity";

type Period = { vat_period: string; year: number; number: number; start: string; end: string; documents: number; without_attachment: number; calendar_ended: boolean; closed_at: string | null; reason: string };
const day = (value: string) => value.slice(0, 10).split("-").reverse().join("/");
const label = (period: Period) => period.vat_period === "trimestral" ? `${period.number}.º trimestre` : new Intl.DateTimeFormat("pt-PT", { month: "long", timeZone: "UTC" }).format(new Date(`${period.start}T12:00:00Z`));

export default function AccountingDelivery({ admin, onSettings }: { admin: boolean; onSettings: () => void }) {
  const [year, setYear] = useState(Number(new Intl.DateTimeFormat("en", { year: "numeric", timeZone: "Europe/Lisbon" }).format(new Date())));
  const [periods, setPeriods] = useState<Period[]>([]);
  const [mode, setMode] = useState("");
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [action, setAction] = useState<{ period: Period; reopen: boolean } | null>(null);
  const [reason, setReason] = useState(""), [confirmed, setConfirmed] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  async function load() {
    const result = await api(`/accounting/delivery-periods?year=${year}`);
    setMode(result.vat_period); setPeriods(result.periods);
  }
  useEffect(() => {
    if (!admin) { setLoading(false); return; }
    let active = true;
    setLoading(true); setError(""); setPeriods([]);
    api(`/accounting/delivery-periods?year=${year}`).then(result => { if (active) { setMode(result.vat_period); setPeriods(result.periods); } }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [year, admin]);
  useEffect(() => { if (action && !dialog.current?.open) dialog.current?.showModal(); }, [action]);
  async function exportDossier(period: Period) {
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/accounting/delivery-periods/export", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vat_period: period.vat_period, year: period.year, number: period.number }) });
      if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
      if (!response.ok) { const result = await response.json(); throw new Error(result.detail || "Não foi possível exportar o dossiê."); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `dossie-${period.vat_period}-${period.year}-${String(period.number).padStart(2, "0")}.zip`; link.click(); URL.revokeObjectURL(url);
      setNotice("Dossiê exportado. A exportação não envia ficheiros nem marca a entrega como efetuada.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <>
    <div className="page-intro"><div><h1>Entrega à contabilidade</h1><p>Encerre os períodos da empresa e prepare o dossiê de documentos e anexos.</p></div>{admin && <div className="actions"><ScanButton onSaved={load} /><button onClick={onSettings}>Configurar regime de IVA</button></div>}</div>
    <AssistantIdentity assistant="clara" />
    {!admin ? <section className="panel"><p>O fecho dos períodos e a exportação do dossiê completo da empresa estão reservados ao administrador.</p></section> : <section className="panel">
      <div className="actions"><label>Ano <input type="number" min="2000" max="2100" value={year} disabled={busy} onChange={e => { const value = Number(e.target.value); if (value >= 2000 && value <= 2100) setYear(value); }} /></label><StatusBadge>{mode === "mensal" ? "IVA mensal" : mode === "trimestral" ? "IVA trimestral" : "IVA por configurar"}</StatusBadge><span>Dossiê da empresa · Todas as lojas</span></div>
      {loading && <p role="status">A carregar períodos…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {error && !action && <button disabled={busy} onClick={() => load().then(() => setError("")).catch(e => setError(e.message))}>Tentar novamente</button>}
      {notice && <p className="success" role="status">{notice}</p>}
      {!loading && !mode && !error && <div className="empty"><h3>Configure o regime de IVA da empresa</h3><p>Escolha mensal ou trimestral em Configurações para organizar os períodos.</p><button onClick={onSettings}>Abrir configurações</button></div>}
      {!!mode && <><p>Confirme o fecho depois de conferir o período. O ZIP reúne os documentos e anexos disponíveis, a listagem ERP e as pendências. O fecho desta entrega não bloqueia alterações documentais.</p>
        <div className="purchase-table"><table><thead><tr><th>Período</th><th>Datas</th><th>Documentos</th><th>Sem anexo</th><th>Estado</th><th>Ações</th></tr></thead><tbody>{periods.map(period => <tr key={`${period.vat_period}-${period.number}`}>
          <td><strong>{label(period)} {period.year}</strong>{period.vat_period !== mode && <small> · Regime anterior</small>}</td><td>{day(period.start)} — {day(period.end)}</td><td>{period.documents}</td><td>{period.without_attachment}</td>
          <td><StatusBadge tone={period.closed_at ? "success" : "neutral"}>{period.closed_at ? "Encerrado" : period.calendar_ended ? "Por encerrar" : "Em curso / futuro"}</StatusBadge>{period.closed_at && <small> · {day(period.closed_at)}</small>}</td>
          <td><div className="actions">{period.closed_at ? <><button className="primary" disabled={busy} onClick={() => exportDossier(period)}>Exportar dossiê</button><button className="danger" disabled={busy} onClick={() => { setAction({ period, reopen: true }); setReason(""); setConfirmed(false); }}>Reabrir</button></> : <button disabled={busy || !period.calendar_ended || period.vat_period !== mode} onClick={() => { setAction({ period, reopen: false }); setReason(""); setConfirmed(false); }}>Encerrar período</button>}</div></td>
        </tr>)}</tbody></table></div></>}
    </section>}
    {action && <dialog ref={dialog} className="doc-dialog" aria-label="Confirmar estado do período" onCancel={e => { e.preventDefault(); if (!busy) setAction(null); }}>
      <div className="panel-title"><h2>{action.reopen ? "Reabrir" : "Encerrar"} {label(action.period)} {action.period.year}</h2><button disabled={busy} onClick={() => setAction(null)} aria-label="Fechar">×</button></div>
      <p>{day(action.period.start)} — {day(action.period.end)} · {action.period.documents} documentos · {action.period.without_attachment} sem anexo.</p>
      {!!action.period.without_attachment && <p className="error">Existem documentos sem cópia digital. O dossiê incluirá essa pendência; não substitui os originais em falta.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <form className="doc-form" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); try { await api(`/accounting/delivery-periods/${action.reopen ? "reopen" : "close"}`, { vat_period: action.period.vat_period, year: action.period.year, number: action.period.number, reason }); await load(); setNotice(action.reopen ? "Período reaberto. A exportação fica indisponível até novo fecho." : "Período encerrado. Pode exportar o dossiê."); setAction(null); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }}>
        <label>Justificação<textarea required minLength={3} maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <label className="doc-check"><input type="checkbox" required checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />Confirmo esta ação para toda a empresa e revi as pendências do período.</label>
        <button className={action.reopen ? "danger" : "primary"} disabled={busy || !confirmed || reason.trim().length < 3}>Confirmar {action.reopen ? "reabertura" : "fecho"}</button>
      </form>
    </dialog>}
  </>;
}
