import { useEffect, useState, type KeyboardEvent } from "react";
import { Field } from "./components/Form";
import { ActionButton, Notice, confirmAction } from "./components/InteractionUI";
import { api } from "./lib/api";

type Row = Record<string, any>;
const money = (v: any) => v === null || v === undefined || v === "" ? "Sem informação" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR", maximumFractionDigits: 4 }).format(Number(v));
const quantity = (v: any) => new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 6 }).format(Number(v));
const day = (v: string) => v.split("-").reverse().join("/");
const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon" }).format(new Date());
const blank = { supplier_id: "", revision: 0, supplier_reference: "", agreed_cost: "", valid_until: "", lead_days: "", min_quantity: "", pack_quantity: "", notes: "" };
const normalize = (value: Row): Row => ({
  terms: Array.isArray(value?.terms) ? value.terms : [],
  can_edit: !!value?.can_edit,
  cost_visible: !!value?.cost_visible,
});

/**
 * Condições negociadas com cada fornecedor do artigo (referência, preço acordado, prazo, quantidades).
 * Grava pela sua própria API, sem passar pelo «Guardar» da ficha; compara o preço acordado com o
 * último custo real das compras desse fornecedor.
 */
export default function ArticleSupplierTerms({ articleId, suppliers, purchases, unit, readOnly }: {
  articleId: string;
  suppliers: Row[];
  // Compras por fornecedor (detalhes do artigo), para comparar com o preço acordado.
  purchases: Row[];
  unit: string;
  readOnly: boolean;
}) {
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState(""), [formError, setFormError] = useState("");
  const [value, setValue] = useState<Row>(blank), [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    setError("");
    api(`/stock/articles/${articleId}/supplier-terms`).then(result => { if (alive) setData(normalize(result)); })
      .catch(e => { if (alive) setError((e as Error).message); });
    return () => { alive = false; };
  }, [articleId, attempt]);

  const editing = data?.terms.find((t: Row) => t.supplier_id === value.supplier_id);
  function choose(supplierId: string) {
    const existing = data?.terms.find((t: Row) => t.supplier_id === supplierId);
    // Fornecedor com condições já registadas: passa a editá-las (com a revisão certa).
    setValue(existing ? {
      ...blank, ...Object.fromEntries(Object.entries(existing).map(([k, v]) => [k, v ?? ""])),
      lead_days: existing.lead_days ?? "",
    } : { ...blank, supplier_id: supplierId });
    setFormError("");
  }
  async function save() {
    if (busy) return;
    if (!value.supplier_id) { setFormError("Escolha o fornecedor."); return; }
    const decimal = (v: string) => v === "" ? null : String(v).replace(",", ".");
    setBusy(true); setFormError("");
    try {
      setData(normalize(await api(`/stock/articles/${articleId}/supplier-terms/${value.supplier_id}`, {
        revision: editing?.revision || 0,
        supplier_reference: value.supplier_reference,
        agreed_cost: data?.cost_visible ? decimal(value.agreed_cost) : null,
        valid_until: value.valid_until || null,
        lead_days: value.lead_days === "" ? null : Number(value.lead_days),
        min_quantity: decimal(value.min_quantity),
        pack_quantity: decimal(value.pack_quantity),
        notes: value.notes,
      }, { method: "PUT" })));
      setValue(blank);
    } catch (e) { setFormError((e as Error).message); } finally { setBusy(false); }
  }
  async function remove(row: Row) {
    if (!await confirmAction({ title: "Remover condições do fornecedor?", message: `${row.supplier_name}: a referência, o preço acordado e os prazos deste artigo deixam de estar registados. As compras já feitas não mudam.`, confirmLabel: "Remover", tone: "danger" })) return;
    setBusy(true); setFormError("");
    try {
      setData(normalize(await api(`/stock/articles/${articleId}/supplier-terms/${row.supplier_id}`, {}, { method: "DELETE" })));
      if (value.supplier_id === row.supplier_id) setValue(blank);
    } catch (e) { setFormError((e as Error).message); } finally { setBusy(false); }
  }
  // Está dentro do formulário do artigo: Enter grava as condições em vez da ficha.
  const enter = (e: KeyboardEvent) => { if (e.key === "Enter") { e.preventDefault(); save(); } };
  const change = (key: string, next: string) => setValue(old => ({ ...old, [key]: next }));

  if (error) return <Notice tone="error">{error}<ActionButton type="button" onClick={() => setAttempt(v => v + 1)}>Voltar a tentar</ActionButton></Notice>;
  if (!data) return <p role="status">A carregar condições por fornecedor…</p>;
  const editable = data.can_edit && !readOnly;
  const real = (supplierId: string) => purchases.find(p => p.supplier_id === supplierId);
  const compare = (t: Row) => {
    const last = real(t.supplier_id)?.last_cost;
    if (t.agreed_cost === null || last === null || last === undefined) return null;
    const agreed = Number(t.agreed_cost), paid = Number(last);
    if (!agreed) return null;
    const pct = (paid - agreed) / agreed * 100;
    return Math.abs(pct) < 0.05 ? "igual ao acordado" : `${pct > 0 ? "+" : "−"}${new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 1 }).format(Math.abs(pct))} % face ao acordado`;
  };
  const input = (key: string, label: string, props: Row = {}) => <Field label={label}>
    <input value={value[key]} onKeyDown={enter} onChange={e => change(key, e.target.value)} {...props} />
  </Field>;
  return <div className="article-supplier-terms" data-ignore-dirty>
    {data.terms.length ? <div className="table-wrap"><table className="responsive-data-table article-compact-table" aria-label="Condições por fornecedor">
      <thead><tr><th>Fornecedor</th><th>Referência</th>{data.cost_visible && <><th>Preço acordado</th><th>Último custo real</th></>}<th>Prazo</th><th>Mínimo / múltiplo</th>{editable && <th>Ações</th>}</tr></thead>
      <tbody>{data.terms.map((t: Row) => {
        const expired = t.valid_until && t.valid_until < today();
        const paid = real(t.supplier_id);
        return <tr key={t.supplier_id}>
          <td data-label="Fornecedor"><div>{t.supplier_name}{t.main && <small> · principal</small>}{t.notes && <small> · {t.notes}</small>}</div></td>
          <td data-label="Referência"><div>{t.supplier_reference || "Sem referência"}</div></td>
          {data.cost_visible && <>
            <td data-label="Preço acordado"><div>{money(t.agreed_cost)}{t.valid_until && <small className={expired ? "article-expired" : undefined}> · {expired ? "expirou a" : "até"} {day(t.valid_until)}</small>}</div></td>
            <td data-label="Último custo real"><div>{paid ? money(paid.last_cost) : "Sem compras"}{compare(t) && <small> · {compare(t)}</small>}</div></td>
          </>}
          <td data-label="Prazo"><div>{t.lead_days === null ? "Sem prazo" : `${t.lead_days} dia(s)`}</div></td>
          <td data-label="Mínimo / múltiplo"><div>{t.min_quantity ? `${quantity(t.min_quantity)} ${unit}` : "—"} / {t.pack_quantity ? `${quantity(t.pack_quantity)} ${unit}` : "—"}</div></td>
          {editable && <td data-label="Ações"><div className="article-terms-actions">
            <ActionButton type="button" disabled={busy} aria-label={`Editar condições de ${t.supplier_name}`} onClick={() => choose(t.supplier_id)}>Editar</ActionButton>
            <ActionButton type="button" disabled={busy} aria-label={`Remover condições de ${t.supplier_name}`} onClick={() => remove(t)}>Remover</ActionButton>
          </div></td>}
        </tr>;
      })}</tbody>
    </table></div> : <p>Ainda não há condições registadas por fornecedor.</p>}
    {editable && <fieldset className="market-prices-form" disabled={busy}>
      <legend>{editing ? `Alterar condições de ${editing.supplier_name}` : "Acrescentar fornecedor"}</legend>
      <div className="form-grid">
        <Field label="Fornecedor">
          <select value={value.supplier_id} disabled={!!editing} onChange={e => choose(e.target.value)}>
            <option value="">Selecionar fornecedor</option>
            {suppliers.map((s: Row) => <option key={s.id} value={s.id}>{s.name}{data.terms.some((t: Row) => t.supplier_id === s.id) ? " (já registado)" : ""}</option>)}
          </select>
        </Field>
        {input("supplier_reference", "Referência neste fornecedor", { maxLength: 100 })}
        {data.cost_visible && input("agreed_cost", "Preço acordado sem IVA (€ / un.)", { type: "number", inputMode: "decimal", min: "0", step: "any" })}
        {data.cost_visible && input("valid_until", "Preço válido até", { type: "date" })}
        {input("lead_days", "Prazo de entrega (dias)", { type: "number", inputMode: "numeric", min: "0", max: "3650", step: "1" })}
        {input("min_quantity", `Quantidade mínima (${unit})`, { type: "number", inputMode: "decimal", min: "0", step: "any" })}
        {input("pack_quantity", `Múltiplo de encomenda (${unit})`, { type: "number", inputMode: "decimal", min: "0", step: "any" })}
        {input("notes", "Observações", { maxLength: 300 })}
      </div>
      <div className="stock-actions">
        <ActionButton type="button" className="primary" onClick={save}>{busy ? "A gravar…" : "Gravar condições"}</ActionButton>
        {value.supplier_id && <ActionButton type="button" dismiss={false} onClick={() => { setValue(blank); setFormError(""); }}>Limpar</ActionButton>}
      </div>
    </fieldset>}
    {formError && <Notice tone="error" role="alert">{formError}</Notice>}
    {!data.can_edit && !readOnly && <p className="footnote">O seu perfil não permite alterar as condições por fornecedor.</p>}
  </div>;
}
