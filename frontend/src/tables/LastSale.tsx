import { useEffect, useState } from "react";
import "./LastSale.css";
import { Settings2 } from "lucide-react";
import { StatusBadge, type BusinessTone } from "../components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "../components/InteractionUI";
import { api } from "../lib/api";

/**
 * «Última venda» na tabela de Clientes: data do documento de venda válido mais recente (todo o histórico disponível),
 * indicador de atividade com texto e dica, filtros rápidos e limites configuráveis. A data abre o documento.
 */

type Row = Record<string, any>;
const TONES: Record<string, BusinessTone> = { ativo: "success", acompanhar: "info", risco: "warning", inativo: "danger", sem_historico: "neutral" };
export const ACTIVITY_FILTERS: [string, string][] = [["", "Todos os clientes"], ["recent30", "Com vendas nos últimos 30 dias"], ["over90", "Sem vendas há mais de 90 dias"],
  ["over180", "Sem vendas há mais de 180 dias"], ["over365", "Sem vendas há mais de 365 dias"], ["never", "Nunca compraram"]];
const day = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "";

export function LastSaleCell({ sale, name, onOpen }: { sale?: Row; name: string; onOpen: (sale: Row) => void }) {
  if (!sale) return <>—</>;
  return <span className="last-sale">
    {sale.has_sale ? <button type="button" className="last-sale-date" title={`Abrir ${sale.type} ${sale.document}`} aria-label={`Última venda a ${name}: ${day(sale.date)}, ${sale.type} ${sale.document}. Abrir o documento`}
      onClick={() => onOpen(sale)}>{day(sale.date)}</button> : <span className="last-sale-none">Sem vendas</span>}
    <StatusBadge tone={TONES[sale.state] || "neutral"} compact title={sale.hint}>{sale.label}</StatusBadge>
  </span>;
}

export function LastSaleFilters({ activity, from, to, onChange, onSettings }: {
  activity: string; from: string; to: string; onChange: (values: { activity?: string; from?: string; to?: string }) => void; onSettings: () => void;
}) {
  return <div className="last-sale-filters" role="group" aria-label="Filtros de atividade comercial">
    <div className="last-sale-chips">{ACTIVITY_FILTERS.map(([value, label]) =>
      <button key={value || "all"} type="button" aria-pressed={activity === value} onClick={() => onChange({ activity: value })}>{label}</button>)}</div>
    <label>Última venda desde<input type="date" value={from} max={to || undefined} onChange={e => onChange({ from: e.target.value })} /></label>
    <label>até<input type="date" value={to} min={from || undefined} onChange={e => onChange({ to: e.target.value })} /></label>
    <ActionButton type="button" onClick={onSettings}><Settings2 size={15} aria-hidden="true" />Limites de atividade</ActionButton>
  </div>;
}

export function LastSaleNotice({ meta }: { meta?: Row | null }) {
  if (!meta) return null;
  if (meta.mode === "none" || !meta.data_from) return <Notice tone="info">Ainda não há vendas na IAZELA (SAF-T de vendas ou vendas sincronizadas do ERP): «Última venda» mostra «Sem vendas» para todos os clientes.</Notice>;
  const until = new Date(meta.data_until + "T00:00:00");
  const stale = Date.now() - until.getTime() > 8 * 86_400_000;
  return <p className={`last-sale-coverage${stale ? " is-stale" : ""}`}>
    «Última venda» usa as vendas {meta.mode === "erp" ? "sincronizadas do ERP" : "do SAF-T importado"} de {day(meta.data_from)} a {day(meta.data_until)}
    {stale ? ": há vendas por importar ou sincronizar; a atividade dos clientes pode estar desatualizada." : "."} Estados: até {meta.days?.[0]} dias ativo,
    até {meta.days?.[1]} a acompanhar, até {meta.days?.[2]} em risco; acima, inativo.
  </p>;
}

export function ActivitySettings({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [value, setValue] = useState<Row | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { api("/customers/activity-settings").then(setValue).catch(e => setError((e as Error).message)); }, []);
  const labels = ["Ativo até (dias)", "A acompanhar até (dias)", "Em risco até (dias)"];
  async function save() {
    setBusy(true); setError("");
    try { await api("/customers/activity-settings", { days: value!.days.map(Number), revision: value!.revision }, { method: "PUT" }); onSaved(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <AppDialog className="last-sale-settings" aria-label="Limites de atividade" guardChanges={false} onRequestClose={onClose}>
    <header className="drilldown-header"><h2>Limites de atividade</h2><p>Dias desde a última venda. Acima do último limite, o cliente fica «Inativo».</p></header>
    {error && <Notice tone="error">{error}</Notice>}
    {value && <form onSubmit={e => { e.preventDefault(); void save(); }} className="last-sale-form">
      {labels.map((label, index) => <label key={label}>{label}<input type="number" min={1} max={3650} value={value.days[index]} disabled={!value.can_edit}
        onChange={e => setValue({ ...value, days: value.days.map((d: number, i: number) => i === index ? e.target.value : d) })} /></label>)}
      {value.can_edit ? <div className="last-sale-actions"><ActionButton type="submit" className="primary" disabled={busy}>Guardar limites</ActionButton>
        <ActionButton type="button" onClick={onClose}>Cancelar</ActionButton></div> : <p>Só o administrador altera os limites.</p>}
    </form>}
  </AppDialog>;
}
