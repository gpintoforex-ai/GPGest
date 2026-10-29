import { useEffect, useState, type KeyboardEvent } from "react";
import { ExternalLink } from "lucide-react";
import { Field } from "./components/Form";
import { ActionButton, Notice, confirmAction } from "./components/InteractionUI";
import { api } from "./lib/api";
import "./components/DataTable.css";

type Row = Record<string, any>;
const euro = (cents: number | null | undefined) => cents === null || cents === undefined ? "Sem informação" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(cents / 100);
const percent = (v: number) => `${new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 1 }).format(v)} %`.replace("-", "−");
const day = (v: string) => v.split("-").reverse().join("/");
const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon" }).format(new Date());
const blank = () => ({ source: "kuantokusta", store_name: "", price: "", observed_on: today(), url: "", notes: "" });
// Resposta sem a forma esperada não derruba a ficha do artigo: fica uma lista vazia, sem comparação.
const normalize = (value: Row): Row => ({
  prices: Array.isArray(value?.prices) ? value.prices : [],
  summary: { stores: 0, own_cents: null, lowest_cents: null, highest_cents: null, average_cents: null, cheaper_stores: null, lowest_store: null, ...(value?.summary || {}) },
  search_url: typeof value?.search_url === "string" && value.search_url.startsWith("https://") ? value.search_url : "https://www.kuantokusta.pt/",
  can_edit: !!value?.can_edit,
});

/**
 * Preços de mercado do artigo (KuantoKusta ou outras lojas). O KuantoKusta não tem API pública de
 * consulta: o botão abre a pesquisa no site e o utilizador regista o preço que vê. Compara com o
 * preço de venda com IVA e, com permissão de custos, mostra a margem que teria a esse preço.
 */
export default function MarketPrices({ articleId, vatRate, cost, readOnly }: {
  articleId: string;
  vatRate: number;
  // Custo médio por unidade (null sem permissão ou sem informação).
  cost: number | null;
  readOnly: boolean;
}) {
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState(""), [formError, setFormError] = useState("");
  const [value, setValue] = useState(blank), [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    setError("");
    api(`/stock/articles/${articleId}/market-prices`).then(result => { if (alive) setData(normalize(result)); })
      .catch(e => { if (alive) setError((e as Error).message); });
    return () => { alive = false; };
  }, [articleId, attempt]);

  async function add() {
    if (busy) return;
    const price = value.price.replace(",", ".");
    if (!value.store_name.trim() || !(Number(price) > 0) || !value.observed_on) {
      setFormError("Indique a loja, o preço com IVA e a data.");
      return;
    }
    setBusy(true); setFormError("");
    try {
      setData(normalize(await api(`/stock/articles/${articleId}/market-prices`, { ...value, price })));
      setValue(old => ({ ...blank(), source: old.source }));
    } catch (e) { setFormError((e as Error).message); } finally { setBusy(false); }
  }
  async function remove(row: Row) {
    if (!await confirmAction({ title: "Remover preço de mercado?", message: `${row.store_name} · ${euro(row.price_cents)} · ${day(row.observed_on)}`, confirmLabel: "Remover", tone: "danger" })) return;
    setBusy(true); setFormError("");
    try { setData(normalize(await api(`/stock/articles/${articleId}/market-prices/${row.id}`, {}, { method: "DELETE" }))); }
    catch (e) { setFormError((e as Error).message); } finally { setBusy(false); }
  }
  // O registo está dentro do formulário do artigo: Enter regista o preço em vez de guardar a ficha.
  const enter = (e: KeyboardEvent) => { if (e.key === "Enter") { e.preventDefault(); add(); } };
  const change = (key: string, next: string) => setValue(old => ({ ...old, [key]: next }));

  if (error) return <Notice tone="error">{error}<ActionButton type="button" onClick={() => setAttempt(v => v + 1)}>Voltar a tentar</ActionButton></Notice>;
  if (!data) return <p role="status">A carregar preços de mercado…</p>;
  const s = data.summary, own: number | null = s.own_cents;
  const difference = (cents: number) => {
    if (own === null) return "Não calculável";
    const delta = cents - own;
    const sign = delta > 0 ? "+" : "−";
    return delta === 0 ? "Igual ao seu" : `${sign}${euro(Math.abs(delta))} (${sign}${percent(Math.abs(delta / own * 100))})`;
  };
  const margin = (cents: number) => {
    if (cost === null) return "Sem informação";
    const net = cents / 100 / (1 + vatRate / 100);
    return `${euro(Math.round((net - cost) * 100))}${net > 0 ? ` · ${percent((net - cost) / net * 100)}` : ""}`;
  };
  const fact = (label: string, v: string) => <div className="article-fact"><span>{label}</span><strong>{v}</strong></div>;
  const editable = data.can_edit && !readOnly;
  return <div className="market-prices" data-ignore-dirty>
    <p>Quanto custa este artigo noutras lojas. O KuantoKusta não disponibiliza os preços para consulta automática: pesquise no site e registe aqui o preço com IVA de cada loja. A comparação usa o preço mais recente de cada loja.</p>
    <div className="stock-actions">
      <a className="ui-action" href={data.search_url} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} aria-hidden="true" />Pesquisar no KuantoKusta</a>
    </div>
    <div className="article-facts" aria-label="Comparação com o mercado">
      {fact("O seu preço com IVA", own === null ? "Sem preço local" : euro(own))}
      {fact("Mais barato", s.stores ? `${euro(s.lowest_cents)} · ${s.lowest_store}` : "Sem preços registados")}
      {fact("Preço médio", euro(s.average_cents))}
      {fact("Mais caro", euro(s.highest_cents))}
      {fact("Posição", !s.stores || own === null ? "Não calculável" : s.cheaper_stores === 0 ? `Mais barato ou igual em ${s.stores} loja(s)` : `${s.cheaper_stores} de ${s.stores} loja(s) mais baratas`)}
      {s.stores > 0 && own !== null && fact("Diferença para o mais barato", difference(s.lowest_cents))}
    </div>
    {own !== null && s.stores > 0 && s.cheaper_stores === s.stores && <Notice tone="warning">Todas as lojas registadas vendem abaixo do seu preço.</Notice>}
    {editable && <fieldset className="market-prices-form" disabled={busy}>
      <legend>Registar preço</legend>
      <div className="form-grid">
        <Field label="Origem"><select value={value.source} onChange={e => change("source", e.target.value)}><option value="kuantokusta">KuantoKusta</option><option value="outro">Outra loja ou site</option></select></Field>
        <Field label="Loja"><input value={value.store_name} maxLength={120} required placeholder="Ex.: loja mostrada no KuantoKusta" onKeyDown={enter} onChange={e => change("store_name", e.target.value)} /></Field>
        <Field label="Preço com IVA (€)"><input value={value.price} type="number" inputMode="decimal" min="0.01" step="0.01" required onKeyDown={enter} onChange={e => change("price", e.target.value)} /></Field>
        <Field label="Data da consulta"><input value={value.observed_on} type="date" max={today()} required onChange={e => change("observed_on", e.target.value)} /></Field>
        <Field label="Ligação (HTTPS, opcional)"><input value={value.url} type="url" maxLength={2000} onKeyDown={enter} onChange={e => change("url", e.target.value)} /></Field>
        <Field label="Observações"><input value={value.notes} maxLength={300} onKeyDown={enter} onChange={e => change("notes", e.target.value)} /></Field>
      </div>
      <ActionButton type="button" className="primary" onClick={add}>{busy ? "A registar…" : "Registar preço"}</ActionButton>
    </fieldset>}
    {formError && <Notice tone="error" role="alert">{formError}</Notice>}
    {!data.can_edit && !readOnly && <p className="footnote">O seu perfil não permite registar preços de mercado.</p>}
    {data.prices.length ? <div className="table-wrap"><table className="responsive-data-table" aria-label="Preços de mercado">
      <thead><tr><th>Loja</th><th>Preço com IVA</th><th>Data</th><th>Diferença para o seu</th>{cost !== null && <th>A sua margem a este preço</th>}<th>Registo</th>{editable && <th>Ações</th>}</tr></thead>
      <tbody>{data.prices.map((row: Row) => <tr key={row.id}>
        <td data-label="Loja"><div>{row.url ? <a href={row.url} target="_blank" rel="noopener noreferrer">{row.store_name}</a> : row.store_name}<small> · {row.source === "kuantokusta" ? "KuantoKusta" : "Outra origem"}{row.current ? "" : " · histórico"}</small>{row.notes && <small> · {row.notes}</small>}</div></td>
        <td data-label="Preço com IVA"><div>{euro(row.price_cents)}</div></td>
        <td data-label="Data"><div>{day(row.observed_on)}</div></td>
        <td data-label="Diferença para o seu"><div>{difference(row.price_cents)}</div></td>
        {cost !== null && <td data-label="A sua margem a este preço"><div>{margin(row.price_cents)}</div></td>}
        <td data-label="Registo"><div>{row.user_name || "Sem informação"}</div></td>
        {editable && <td data-label="Ações"><div><ActionButton type="button" disabled={busy} onClick={() => remove(row)}>Remover</ActionButton></div></td>}
      </tr>)}</tbody>
    </table></div> : <p>Ainda não há preços de mercado registados para este artigo.</p>}
    <p className="footnote">Diferença positiva: a loja vende mais caro do que o seu preço. A margem a este preço usa o preço sem IVA ({vatRate} %) menos o custo médio.</p>
  </div>;
}
