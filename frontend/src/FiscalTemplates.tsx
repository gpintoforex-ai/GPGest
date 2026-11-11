import { useEffect, useState } from "react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";

/**
 * Modelos de obrigações fiscais (IVA, IRC, IES, DMR, Segurança Social…), propostos pelo regime da empresa e
 * criados só por escolha do utilizador, sempre «a validar» com o calendário oficial e o contabilista.
 */

type Row = Record<string, any>;
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const REGIME: Record<string, string> = { normal: "Regime normal", isento_53: "Isento (art.º 53.º)", pequenos_retalhistas: "Pequenos retalhistas", "": "Regime de IVA por configurar" };

export default function FiscalTemplates({ users, onClose, onCreated }: { users: Row[]; onClose: () => void; onCreated: (message: string) => void }) {
  const [data, setData] = useState<Row | null>(null), [chosen, setChosen] = useState<Set<string>>(new Set()), [responsible, setResponsible] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => {
    api("/obligations/fiscal-templates").then(r => { setData(r); setChosen(new Set(r.templates.filter((t: Row) => t.selected).map((t: Row) => t.key))); })
      .catch(e => setError((e as Error).message));
  }, []);
  async function create() {
    setBusy(true); setError("");
    try {
      const result = await api("/obligations/fiscal-templates", { keys: [...chosen], responsible_id: responsible || null });
      onCreated(`${result.created.length} obrigação(ões) fiscal(is) criada(s), a validar.${result.skipped.length ? ` ${result.skipped.length} já existia(m).` : ""}`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const toggle = (key: string, on: boolean) => { const next = new Set(chosen); if (on) next.add(key); else next.delete(key); setChosen(next); };
  return <AppDialog className="obl-templates" aria-label="Modelos fiscais" busy={busy} onRequestClose={onClose}>
    <h2>Modelos de obrigações fiscais</h2>
    {error && <Notice tone="error">{error}</Notice>}
    {!data ? !error && <p role="status">A preparar as propostas…</p> : <>
      <p>{REGIME[data.regime] ?? data.regime}{data.period ? ` · IVA ${data.period}` : ""} · {data.employees} funcionário(s) ativo(s). Pré-selecionadas as que se aplicam; reveja antes de criar.</p>
      <Notice tone="warning">As datas seguem a regra habitual e ficam «a validar»: confirme com o calendário fiscal oficial e o contabilista. Prorrogações e dias não úteis corrigem-se reagendando a ocorrência.</Notice>
      <div className="table-wrap"><table className="obl-grid" aria-label="Modelos fiscais disponíveis">
        <thead><tr><th><span className="obl-sr">Escolher</span></th><th>Obrigação</th><th>Regra habitual</th><th>Próxima data</th><th>Aplicação</th></tr></thead>
        <tbody>{data.templates.map((t: Row) => <tr key={t.key}>
          <td><input type="checkbox" aria-label={`Criar ${t.title}`} disabled={t.created} checked={chosen.has(t.key)} onChange={e => toggle(t.key, e.target.checked)} /></td>
          <td><strong>{t.title}</strong><small>{t.periodicity}</small></td>
          <td>{t.rule}<small>Fonte: {t.source}</small></td>
          <td>{fullDate(t.first_due)}</td>
          <td>{t.created ? <StatusBadge tone="success" compact>Já criada</StatusBadge> : <StatusBadge tone={t.applicable ? "info" : "neutral"} compact>{t.applicable ? "Aplicável" : "Não se aplica"}</StatusBadge>}
            <small>{t.reason}</small></td></tr>)}</tbody></table></div>
      <label>Responsável (opcional)<select value={responsible} onChange={e => setResponsible(e.target.value)}><option value="">Sem responsável</option>
        {users.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
      <ul className="obl-notes">{data.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
    </>}
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Cancelar</ActionButton>
      <ActionButton type="button" className="primary" disabled={busy || !chosen.size} onClick={() => void create()}>Criar {chosen.size} selecionada(s)</ActionButton></div>
  </AppDialog>;
}
