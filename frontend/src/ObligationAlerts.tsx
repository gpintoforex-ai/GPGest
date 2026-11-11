import { useEffect, useState } from "react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";

/**
 * Alertas das Obrigações: avisos internos (barra e dashboard) e lembretes por email com dupla adesão (empresa,
 * pelo administrador, e cada utilizador). Um email por dia; nunca repete um alerta enviado nem um envio incerto.
 */

type Row = Record<string, any>;
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const STATUS: Record<string, [string, "success" | "danger" | "warning"]> = { enviado: ["Enviado", "success"], falhou: ["Falhou (repete)", "danger"], incerto: ["Incerto (não repete)", "warning"] };

export default function ObligationAlerts({ admin, onClose }: { admin: boolean; onClose: () => void }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { api("/obligations/notifications").then(setData).catch(e => setError((e as Error).message)); }, []);
  async function run(action: () => Promise<Row>, message: string) {
    setBusy(true); setError(""); setNotice("");
    try { const result = await action(); if (result.company_enabled !== undefined) setData(result); else setData(await api("/obligations/notifications")); setNotice(message); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <AppDialog className="obl-alerts" aria-label="Alertas e notificações" busy={busy} onRequestClose={onClose}>
    <h2>Alertas e notificações</h2>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!data ? !error && <p role="status">A carregar…</p> : <>
      <ul className="obl-notes">{data.rules.map((r: string) => <li key={r}>{r}</li>)}</ul>
      <section aria-label="Os meus lembretes"><h3>Os meus lembretes por email</h3>
        <label className="checkbox"><input type="checkbox" checked={data.my_preference} disabled={busy || !data.my_email}
          onChange={e => void run(() => api("/obligations/notifications/me", { obligations_email: e.target.checked }, { method: "PUT" }), e.target.checked ? "Vai receber os lembretes por email." : "Lembretes por email desativados.")} />
          Receber por email os alertas dos meus prazos{data.my_email ? ` (${data.my_email})` : " (o seu utilizador não tem email)"}</label>
        {!data.company_enabled && <Notice tone="info">Os lembretes por email estão desativados para a empresa{admin ? "" : ": peça ao administrador para os ativar"}.</Notice>}
        {data.my_pending.length > 0 && <p>Hoje: {data.my_pending.map((p: Row) => `${p.title} (${p.alert === "vencido" ? "vencido" : p.alert === "0" ? "no dia" : `${p.alert} dias antes`})`).join(" · ")}</p>}
      </section>
      {admin && <section aria-label="Empresa"><h3>Empresa</h3>
        <label className="checkbox"><input type="checkbox" checked={data.company_enabled} disabled={busy}
          onChange={e => void run(() => api("/obligations/notifications/company", { enabled: e.target.checked }, { method: "PUT" }), e.target.checked ? "Lembretes por email ativados para a empresa." : "Lembretes por email desativados para a empresa.")} />
          Permitir lembretes por email (cada utilizador decide se os recebe)</label>
        {!data.smtp_enabled && <Notice tone="warning">O SMTP não está ativo: configure-o em Configurações → Email.</Notice>}
        <p>{data.pending_alerts} alerta(s) por enviar hoje a {data.pending_users} utilizador(es). {data.last_run ? `Última execução: ${fullDate(data.last_run)}${data.last_result ? ` · ${data.last_result.emails} email(s)` : ""}.` : "Ainda sem execuções."} Execução automática diária a partir das 8 h.</p>
        <ActionButton type="button" disabled={busy || !data.company_enabled || !data.smtp_enabled || !data.pending_alerts}
          onClick={() => void run(() => api("/obligations/notifications/send", {}), "Lembretes enviados.")}>Enviar agora</ActionButton>
        {data.history.length > 0 && <div className="table-wrap"><table className="obl-grid" aria-label="Histórico de envios">
          <thead><tr><th>Data</th><th>Prazo</th><th>Utilizador</th><th>Alerta</th><th>Estado</th></tr></thead>
          <tbody>{data.history.map((h: Row, i: number) => <tr key={i}><td>{fullDate(h.created_at)}</td><td>{h.title}</td><td>{h.user}</td>
            <td>{h.alert === "vencido" ? "Vencido" : h.alert === "0" ? "No dia" : `${h.alert} dias`}</td>
            <td><StatusBadge tone={STATUS[h.status]?.[1] || "neutral"} compact>{STATUS[h.status]?.[0] || h.status}</StatusBadge>{h.error && <small>{h.error}</small>}</td></tr>)}</tbody></table></div>}
      </section>}
    </>}
    <div className="ui-dialog-actions"><ActionButton type="button" className="primary" onClick={onClose}>Fechar</ActionButton></div>
  </AppDialog>;
}
