import { useEffect, useState } from "react";
import { ListFilter, Pencil, Plus, Trash2 } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, AppDialog, Notice, confirmAction } from "./components/InteractionUI";
import { Field } from "./components/Form";

type Row = Record<string, any>;
const kinds: Row = { payment: "Pagamento", receipt: "Recebimento", fee: "Comissão bancária", interest: "Juros", transfer: "Transferência entre contas próprias" };
const directions: Row = { "-1": "Saídas", "1": "Entradas", "0": "Entradas e saídas" };
const blank = { name: "", pattern: "", account_id: null, direction: -1, kind: "payment", category_id: null, employee_id: null,
  target_account_id: null, description: "", counts_as_cost: true, active: true };

/** Regras por descrição do extrato: só propõem o tratamento; o registo exige sempre confirmação. */
export default function BankRules({ accounts, categories, onClose, onChanged }: {
  accounts: Row[]; categories: Row[]; onClose: () => void; onChanged: () => void;
}) {
  const [rules, setRules] = useState<Row[] | null>(null), [employees, setEmployees] = useState<Row[]>([]);
  const [draft, setDraft] = useState<Row | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function load() { setRules(await api("/bank-rules")); }
  useEffect(() => {
    load().catch(e => setError(e.message));
    api("/employees").then(setEmployees).catch(() => setEmployees([]));
  }, []);
  async function run(task: () => Promise<void>) {
    setBusy(true); setError("");
    try { await task(); } catch (e) { setError(e instanceof Error ? e.message : "Não foi possível concluir a operação."); } finally { setBusy(false); }
  }
  const change = (values: Row) => setDraft(current => {
    const next = { ...current, ...values };
    if (next.kind === "transfer") Object.assign(next, { category_id: null, employee_id: null, counts_as_cost: false });
    else next.target_account_id = null;
    if (!["payment", "fee"].includes(next.kind) || !next.category_id) next.counts_as_cost = false;
    return next;
  });
  const costable = draft && ["payment", "fee"].includes(draft.kind);
  function save() {
    run(async () => {
      const values = Object.fromEntries(Object.keys(blank).map(name => [name, draft![name]]));
      await api(draft!.id ? `/bank-rules/${draft!.id}` : "/bank-rules", { ...values, direction: Number(values.direction) });
      setDraft(null); await load(); onChanged();
    });
  }
  function remove(rule: Row) {
    run(async () => {
      if (!await confirmAction({ title: "Remover a regra?", message: `A regra «${rule.name}» deixa de propor tratamentos. Os registos já criados mantêm-se.`, confirmLabel: "Remover regra" })) return;
      await api(`/bank-rules/${rule.id}/remove`, {}); await load(); onChanged();
    });
  }
  return <AppDialog autoOpen className="bank-rules-dialog" busy={busy} aria-labelledby="bank-rules-title" onRequestClose={onClose}>
    <h2 id="bank-rules-title"><ListFilter size={20} />Regras por descrição</h2>
    <p>Para movimentos sem documento (impostos, Segurança Social, vencimentos, encargos, transferências). Quando a descrição do extrato contém o texto da regra, a ZELA propõe o tratamento. O registo só é criado depois de o aceitar, com pré-visualização, procura de duplicações e auditoria.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {!draft && <ActionButton variant="suggestion" disabled={busy} onClick={() => setDraft({ ...blank })}><Plus size={16} />Nova regra</ActionButton>}
    {draft && <section className="bank-rule-form" aria-label={draft.id ? "Editar regra" : "Nova regra"}>
      <div className="bank-rule-grid">
        <Field label="Nome"><input value={draft.name} maxLength={100} onChange={e => change({ name: e.target.value })} /></Field>
        <Field label="Texto na descrição do extrato"><input value={draft.pattern} maxLength={200} placeholder="Ex.: SEG SOCIAL" onChange={e => change({ pattern: e.target.value })} /></Field>
        <Field label="Conta"><select value={draft.account_id || ""} onChange={e => change({ account_id: e.target.value || null })}><option value="">Todas as contas</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.bank} · {a.name}</option>)}</select></Field>
        <Field label="Sentido"><select value={String(draft.direction)} onChange={e => change({ direction: Number(e.target.value) })}>{Object.entries(directions).map(([v, l]) => <option key={v} value={v}>{l as string}</option>)}</select></Field>
        <Field label="Tratamento"><select value={draft.kind} onChange={e => change({ kind: e.target.value })}>{Object.entries(kinds).map(([v, l]) => <option key={v} value={v}>{l as string}</option>)}</select></Field>
        {draft.kind === "transfer"
          ? <Field label="Outra conta própria"><select value={draft.target_account_id || ""} onChange={e => change({ target_account_id: e.target.value || null })}><option value="">Escolher conta</option>{accounts.filter(a => a.id !== draft.account_id).map(a => <option key={a.id} value={a.id}>{a.bank} · {a.name}</option>)}</select></Field>
          : <Field label="Rubrica"><select value={draft.category_id || ""} onChange={e => change({ category_id: e.target.value || null, counts_as_cost: costable && !!e.target.value })}><option value="">Sem rubrica</option>{categories.filter(c => c.active !== false || c.id === draft.category_id).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>}
        {draft.kind === "payment" && <Field label="Funcionário (opcional)"><select value={draft.employee_id || ""} onChange={e => change({ employee_id: e.target.value || null })}><option value="">Sem funcionário</option>{employees.filter(m => m.active !== false || m.id === draft.employee_id).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></Field>}
        <Field label="Descrição do registo"><input value={draft.description} maxLength={300} placeholder="Vazio: o nome da regra" onChange={e => change({ description: e.target.value })} /></Field>
      </div>
      {costable && <label className="bank-resolution-check"><input type="checkbox" checked={draft.counts_as_cost} disabled={!draft.category_id} onChange={e => change({ counts_as_cost: e.target.checked })} />Conta como custo nos mapas de gestão e no Dashboard</label>}
      <label className="bank-resolution-check"><input type="checkbox" checked={draft.active} onChange={e => change({ active: e.target.checked })} />Regra ativa</label>
      <div className="actions"><ActionButton className="primary" disabled={busy || draft.name.trim().length < 3 || draft.pattern.trim().length < 3} onClick={save}>{draft.id ? "Guardar regra" : "Criar regra"}</ActionButton><ActionButton disabled={busy} onClick={() => setDraft(null)}>Cancelar</ActionButton></div>
    </section>}
    {rules === null ? <p role="status">A carregar regras…</p> : !rules.length ? <p className="reconciliation-empty">Ainda não há regras. Crie uma aqui ou escolha «Guardar como regra» ao resolver um movimento.</p> :
      <div className="table-wrap"><table><caption className="sr-only">Regras por descrição</caption><thead><tr><th>Regra</th><th>Texto</th><th>Conta</th><th>Tratamento</th><th>Estado</th><th><span className="sr-only">Ações</span></th></tr></thead><tbody>
        {rules.map(rule => <tr key={rule.id}>
          <td><b>{rule.name}</b>{rule.description && rule.description !== rule.name && <small>{rule.description}</small>}</td>
          <td><code>{rule.pattern}</code><small>{directions[String(rule.direction)]}</small></td>
          <td>{rule.account_name}</td>
          <td>{rule.kind_label}<small>{[rule.category_name && `Rubrica ${rule.category_name}`, rule.employee_name, rule.target_account_name && `Para ${rule.target_account_name}`, rule.counts_as_cost ? "conta como custo" : ""].filter(Boolean).join(" · ")}</small></td>
          <td>{rule.active ? "Ativa" : "Inativa"}</td>
          <td><div className="actions"><ActionButton variant="quiet" disabled={busy} aria-label={`Editar ${rule.name}`} onClick={() => setDraft({ ...blank, ...rule })}><Pencil size={14} /></ActionButton><ActionButton variant="quiet" disabled={busy} aria-label={`Remover ${rule.name}`} onClick={() => remove(rule)}><Trash2 size={14} /></ActionButton></div></td>
        </tr>)}
      </tbody></table></div>}
  </AppDialog>;
}
