import { useRef, useState } from "react";
import { api } from "./lib/api";
import { Field, Form } from "./components/Form";
import { ActionButton, Notice, confirmFormExit } from "./components/InteractionUI";

type Scope = "articles" | "customers" | "suppliers";
type Plan = { scope: Scope; label: string; total: number; removable: number; protected: number; shared: number; token: string; protected_examples: { id: string; name: string }[] };
const tables: [Scope, string][] = [["articles", "Artigos"], ["customers", "Clientes"], ["suppliers", "Fornecedores"]];

export default function PlatformCatalogReset({ company, onDone }: {
  company: { id: string; name: string; nif: string | null };
  onDone: (message: string) => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null), [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const root = useRef<HTMLDivElement>(null), working = useRef(false);
  async function preview(next: Scope) {
    if (working.current || !await confirmFormExit(root.current)) return;
    working.current = true; setBusy(true); setError(""); setScope(next); setPlan(null);
    try { setPlan(await api(`/platform/companies/${company.id}/reset-catalog/${next}`)); }
    catch (e) { setError((e as Error).message); }
    finally { working.current = false; setBusy(false); }
  }
  return <div className="company-removal" ref={root}>
    <h3>Reinicializar uma tabela</h3>
    <p>Escolha a tabela desta empresa. A pré-visualização identifica as fichas sem histórico que podem ser retiradas.</p>
    <div className="actions">{tables.map(([key, label]) => <ActionButton key={key} type="button" disabled={busy} aria-pressed={scope === key} onClick={() => void preview(key)}>Reinicializar {label.toLocaleLowerCase("pt-PT")}</ActionButton>)}</div>
    {busy && <p role="status">A verificar fichas e associações…</p>}
    {error && <Notice tone="error">{error}{scope && <ActionButton type="button" onClick={() => void preview(scope)}>Voltar a tentar</ActionButton>}</Notice>}
    {plan && <>
      <h4>{plan.label} · {company.name}</h4>
      <p><strong>{plan.total}</strong> fichas na tabela · <strong>{plan.removable}</strong> a retirar · <strong>{plan.protected}</strong> preservadas por histórico ou associações.</p>
      {!!plan.shared && <p>{plan.shared} entidades também têm a outra função (cliente/fornecedor). Será retirada apenas a função selecionada; a outra ficha mantém-se.</p>}
      {!!plan.protected && <Notice tone="info">As fichas utilizadas em movimentos, documentos ou outras associações mantêm-se. Exemplos: {plan.protected_examples.map(r => r.name).join("; ")}.</Notice>}
      <p>As outras tabelas, os movimentos e os documentos são preservados. Não altera a Wintouch nem ficheiros externos. Uma importação posterior poderá voltar a criar fichas do ERP.</p>
      {plan.removable ? <Form key={plan.token} destructive submitLabel={`Confirmar reinicialização de ${plan.label.toLocaleLowerCase("pt-PT")}`} onSubmit={async values => {
        const result = await api(`/platform/companies/${company.id}/reset-catalog/${plan.scope}`, {
          confirmation: values.confirmation, acknowledged: values.acknowledged === "on", preview_token: plan.token,
        });
        setPlan(null); setScope(null);
        onDone(`${plan.label}: ${result.removed} fichas retiradas da tabela; ${result.protected} preservadas. A cópia anterior ficou arquivada e as sessões da empresa terminaram.`);
      }}>
        <p>A confirmação termina as sessões desta empresa. Os dados anteriores ficam numa cópia interna, sem recuperação automática nesta janela.</p>
        <Field label={`Escreva ${company.nif || company.name} para confirmar esta tabela`}><input name="confirmation" required autoComplete="off" /></Field>
        <label className="checkbox"><input name="acknowledged" type="checkbox" required />Confirmo a reinicialização individual de {plan.label.toLocaleLowerCase("pt-PT")} desta empresa, preservando as fichas com histórico e terminando as sessões atuais.</label>
      </Form> : <Notice tone="info">Não há fichas sem histórico ou associações que possam ser retiradas desta tabela.</Notice>}
    </>}
  </div>;
}
