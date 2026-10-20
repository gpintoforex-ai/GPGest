import { useEffect, useState } from "react";
import { PackagePlus, RefreshCw } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice, confirmAction } from "./components/InteractionUI";
import { StatusBadge } from "./components/BusinessUI";

type Row = Record<string, any>;
const price = (value: string) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR", maximumFractionDigits: 4 }).format(Number(value));

/** Propõe criar (ou associar) artigos a partir dos produtos do SAF-T; nada é criado sem confirmação. */
export default function SaftArticleProposals({ refresh }: { refresh: number }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [choices, setChoices] = useState<Record<string, Row>>({}), [busy, setBusy] = useState(false), [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true; setError("");
    api("/stock/saft-articles").then(result => {
      if (!active) return;
      setData(result);
      setChoices(Object.fromEntries((result?.proposals || []).map((p: Row) => [p.code, { ...p }])));
    }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [refresh, version]);
  if (error) return <section className="panel"><h2>Artigos do SAF-T</h2><Notice tone="warning">{error}</Notice></section>;
  if (!data?.proposals?.length) return null;
  const selected = Object.values(choices).filter(c => c.selected);
  const update = (code: string, change: Row) => setChoices(v => ({ ...v, [code]: { ...v[code], ...change } }));
  async function apply() {
    const creating = selected.filter(c => c.action === "create").length, linking = selected.length - creating;
    const ok = await confirmAction({ title: "Criar e associar artigos", tone: "primary", confirmLabel: "Confirmar artigos",
      message: <>Cria {creating} artigo(s) e associa {linking} código(s) SAF-T a artigos existentes. <strong>O stock não é movimentado.</strong>{!data!.can_set_price && " O seu perfil não altera preços: os artigos ficam sem preço de venda."}</> });
    if (!ok) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api("/stock/saft-articles", { confirmed: true, items: selected.map(c => ({ code: c.code, action: c.action, article_id: c.article_id, name: c.name, unit: c.unit, vat_rate: c.vat_rate, sale_net: c.sale_net, barcode: c.barcode })) });
      setNotice(`${result.created} artigo(s) criado(s) e ${result.linked} associado(s). O stock não foi movimentado.`);
      setVersion(v => v + 1);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel">
    <h2>Artigos do SAF-T por criar ou associar</h2>
    <p>{data.proposals.length} código(s) de artigo do SAF-T sem correspondência no catálogo. {data.note}</p>
    {notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    {!data.can_create && <Notice tone="warning">O seu perfil não permite criar artigos. Peça a um administrador.</Notice>}
    <div className="table-wrap"><table><thead><tr><th>Incluir</th><th>Código</th><th>Nome do artigo</th><th>Tipo</th><th>Proposta</th><th>Unid. · IVA</th><th className="numeric">Último preço s/ IVA</th><th className="numeric">Linhas</th></tr></thead><tbody>
      {Object.values(choices).map(c => <tr key={c.code}>
        <td><input type="checkbox" aria-label={`Incluir ${c.code}`} checked={!!c.selected} onChange={e => update(c.code, { selected: e.target.checked })} /></td>
        <td>{c.code}{c.barcode && <small>EAN {c.barcode}</small>}</td>
        <td>{c.action === "create" ? <input type="text" aria-label={`Nome de ${c.code}`} value={c.name} maxLength={300} onChange={e => update(c.code, { name: e.target.value })} /> : <>{c.article_name}<small>{c.reason}</small></>}{c.warnings.map((w: string) => <small key={w}>{w}</small>)}</td>
        <td>{c.type_name}</td>
        <td><StatusBadge tone={c.action === "link" ? "success" : "neutral"}>{c.action === "link" ? "Associar existente" : "Criar novo"}</StatusBadge></td>
        <td>{c.unit} · {c.vat_rate}%</td><td className="numeric">{price(c.sale_net)}</td><td className="numeric">{c.lines}</td>
      </tr>)}
    </tbody></table></div>
    <ActionButton className="primary" disabled={busy || !selected.length || !data.can_create} onClick={apply}><PackagePlus size={16} />Criar / associar {selected.length} artigo(s)</ActionButton>{" "}
    <ActionButton disabled={busy} onClick={() => setVersion(v => v + 1)}><RefreshCw size={16} />Atualizar propostas</ActionButton>
  </section>;
}
