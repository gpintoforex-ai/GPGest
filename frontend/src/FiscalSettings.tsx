import { ActionButton, ManagedForm, Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import { api } from "./lib/api";
export default function FiscalSettings() {
  const [period, setPeriod] = useState(""), [busy, setBusy] = useState(true), [error, setError] = useState(""), [notice, setNotice] = useState("");
  useEffect(() => { api("/settings/fiscal").then(r => setPeriod(r.vat_period)).catch(e => setError(e.message)).finally(() => setBusy(false)); }, []);
  return <section className="panel"><h3>Regime de IVA da empresa</h3><p>Defina a periodicidade da declaração de IVA desta empresa.</p>
    <ManagedForm className="doc-form" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); setNotice(""); try { await api("/settings/fiscal", { vat_period: period }); setNotice("Regime de IVA guardado."); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }}>
      <label>Periodicidade do IVA<select name="vat_period" value={period} disabled={busy} onChange={e => { setPeriod(e.target.value); setNotice(""); }}><option value="">Por configurar</option><option value="mensal">Mensal</option><option value="trimestral">Trimestral</option></select></label>
      <ActionButton className="primary" disabled={busy}>Guardar regime de IVA</ActionButton>
    </ManagedForm>{error && <Notice className="error" role="alert">{error}</Notice>}{notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
  </section>;
}
