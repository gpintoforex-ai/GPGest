import { ActionButton, ManagedForm, Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Field } from "./components/Form";

export const VAT_REGIMES: [string, string][] = [
  ["normal", "Regime normal"],
  ["isento_53", "Isenção do artigo 53.º do CIVA"],
  ["pequenos_retalhistas", "Pequenos retalhistas (art. 60.º do CIVA)"],
];

export function vatLabel(regime: string, period: string) {
  const name = VAT_REGIMES.find(([value]) => value === regime)?.[1];
  if (!name) return "Regime de IVA por configurar";
  if (regime === "isento_53") return `${name} · entrega ${period || "por definir"} à contabilidade`;
  return `${name} · declaração ${period || "por definir"}`;
}

/** Regime e periodicidade do IVA; a periodicidade depende do regime escolhido. */
export function VatRegimeFields({ regime, period, disabled, onChange }: {
  regime: string;
  period: string;
  disabled?: boolean;
  onChange: (regime: string, period: string) => void;
}) {
  const fixed = regime === "pequenos_retalhistas";
  return <>
    <Field label="Regime de IVA"><select name="vat_regime" value={regime} disabled={disabled}
      onChange={e => { const next = e.target.value; onChange(next, next === "pequenos_retalhistas" ? "trimestral" : next ? period || "trimestral" : ""); }}>
      <option value="">Por configurar</option>
      {VAT_REGIMES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></Field>
    {!!regime && <Field label={regime === "isento_53" ? "Periodicidade da entrega à contabilidade" : "Periodicidade da declaração de IVA"}><select name="vat_period" value={fixed ? "trimestral" : period} disabled={disabled || fixed} onChange={e => onChange(regime, e.target.value)}>
      {!fixed && <option value="mensal">Mensal</option>}<option value="trimestral">Trimestral</option>
    </select></Field>}
    {regime === "isento_53" && <p className="footnote">Na isenção do artigo 53.º não há declaração periódica de IVA. A periodicidade serve apenas para organizar os períodos de entrega dos documentos à contabilidade.</p>}
    {fixed && <p className="footnote">No regime dos pequenos retalhistas a declaração periódica é trimestral.</p>}
    {regime === "normal" && <p className="footnote">A periodicidade mensal é obrigatória com volume de negócios anual igual ou superior a 650 000 €; abaixo desse valor é, em regra, trimestral. Confirme com o contabilista.</p>}
  </>;
}

export default function FiscalSettings() {
  const [regime, setRegime] = useState(""), [period, setPeriod] = useState("");
  const [busy, setBusy] = useState(true), [error, setError] = useState(""), [notice, setNotice] = useState("");
  useEffect(() => { api("/settings/fiscal").then(r => { setRegime(r.vat_regime); setPeriod(r.vat_period); }).catch(e => setError(e.message)).finally(() => setBusy(false)); }, []);
  return <section className="panel"><h3>Regime de IVA da empresa</h3><p>Defina o regime de IVA e a periodicidade desta empresa. Os períodos da entrega à contabilidade seguem esta periodicidade.</p>
    <ManagedForm className="doc-form" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); setNotice(""); try { const saved = await api("/settings/fiscal", { vat_regime: regime, vat_period: period }); setRegime(saved.vat_regime); setPeriod(saved.vat_period); setNotice("Regime de IVA guardado."); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }}>
      <VatRegimeFields regime={regime} period={period} disabled={busy} onChange={(nextRegime, nextPeriod) => { setRegime(nextRegime); setPeriod(nextPeriod); setNotice(""); }} />
      <ActionButton className="primary" disabled={busy}>Guardar regime de IVA</ActionButton>
    </ManagedForm>{error && <Notice className="error" role="alert">{error}</Notice>}{notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
  </section>;
}
