import { useState } from "react";
import { FileUp } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice, confirmAction } from "./components/InteractionUI";
import { Field } from "./components/Form";

type Row = Record<string, any>;
const FIELDS: [string, string, boolean][] = [
  ["code", "Código do artigo", true], ["quantity", "Quantidade", true], ["unit_cost", "Custo unitário sem IVA", true],
  ["warehouse", "Armazém (nome; opcional)", false], ["lot_number", "Lote (opcional)", false], ["expiry", "Validade (opcional)", false],
];
const GUESS: Record<string, RegExp> = { code: /c[oó]d|ref/i, quantity: /quant|qtd|stock|exist/i, unit_cost: /custo|cost|pre[cç]o.*compra|pmp/i, warehouse: /armaz/i, lot_number: /lote/i, expiry: /valid/i };
const money = (value: string) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(value));

/** Inventário inicial valorizado: dá custo conhecido aos artigos (margem bruta) e o stock de abertura. */
export default function OpeningStockImport({ warehouses, rights, run, saved }: {
  warehouses: Row[]; rights: Row; run: (task: () => Promise<void>) => Promise<void>; saved: (message: string) => Promise<void>;
}) {
  const [content, setContent] = useState(""), [delimiter, setDelimiter] = useState(";"), [mapping, setMapping] = useState<Row>({});
  const [warehouse, setWarehouse] = useState(warehouses[0]?.id || ""), [occurredOn, setOccurredOn] = useState(""), [reason, setReason] = useState("Inventário inicial valorizado");
  const [preview, setPreview] = useState<Row | null>(null);
  const headers = content.split(/\r?\n/, 1)[0]?.split(delimiter).map(v => v.replace(/^"|"$/g, "").trim()).filter(Boolean) || [];
  if (!rights.adjust_stock || !rights.view_cost) return <section className="panel"><h2><FileUp size={19} /> Inventário inicial valorizado</h2><Notice tone="warning">Precisa de permissão para ajustar stock e ver custos.</Notice></section>;
  return <section className="panel">
    <h2><FileUp size={19} /> Inventário inicial valorizado</h2>
    <p>Regista o stock de abertura com o custo unitário <b>sem IVA</b> de cada artigo, numa data de referência (por exemplo, 01/01/2026). Dá custo conhecido aos artigos e permite calcular a margem bruta. Não altera vendas, compras nem pagamentos. Cada artigo só pode ter um stock inicial por armazém; correções posteriores fazem-se por ajuste justificado.</p>
    <Field label="Ficheiro CSV (código; quantidade; custo unitário…)">
      <input type="file" accept=".csv,text/csv" onChange={e => {
        const file = e.target.files?.[0];
        if (!file) return;
        void run(async () => {
          if (file.size > 4_000_000) throw new Error("Limite de 4 MB. Divida o ficheiro.");
          const bytes = await file.arrayBuffer();
          let text = new TextDecoder("utf-8").decode(bytes);
          if (text.includes("�")) text = new TextDecoder("windows-1252").decode(bytes);
          text = text.replace(/^﻿/, "");
          const first = text.split(/\r?\n/, 1)[0] || "";
          const sep = first.includes(";") ? ";" : first.includes("\t") ? "\t" : ",";
          const cols = first.split(sep).map(v => v.replace(/^"|"$/g, "").trim());
          setDelimiter(sep); setContent(text); setPreview(null);
          setMapping(Object.fromEntries(Object.entries(GUESS).map(([key, re]) => [key, cols.find(c => re.test(c)) || ""]).filter(([, v]) => v)));
        });
      }} />
    </Field>
    {!!content && <>
      <div className="form-grid">
        {FIELDS.map(([key, label, required]) => <Field key={key} label={label + (required ? "" : "")}><select value={mapping[key] || ""} onChange={e => { setMapping({ ...mapping, [key]: e.target.value }); setPreview(null); }}>
          <option value="">{required ? "Escolher coluna" : "Não usar"}</option>{headers.map(h => <option key={h} value={h}>{h}</option>)}</select></Field>)}
        <Field label="Armazém (quando o ficheiro não indica)"><select value={warehouse} onChange={e => { setWarehouse(e.target.value); setPreview(null); }}>{warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="Data do inventário"><input type="date" required value={occurredOn} onChange={e => { setOccurredOn(e.target.value); setPreview(null); }} /></Field>
        <Field label="Motivo"><input value={reason} minLength={3} onChange={e => { setReason(e.target.value); setPreview(null); }} /></Field>
      </div>
      <ActionButton type="button" disabled={!mapping.code || !mapping.quantity || !mapping.unit_cost || !occurredOn || reason.trim().length < 3} onClick={() => void run(async () => {
        setPreview(await api("/stock/opening/preview", { content, delimiter, warehouse_id: warehouse, occurred_on: occurredOn, reason,
          mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)) }, { timeoutMs: 120_000 }));
      })}>Pré-visualizar</ActionButton>
    </>}
    {preview && <>
      <p><b>{preview.rows.length}</b> linha(s) válidas · {preview.articles} artigo(s) · valor total <b>{money(preview.total_value)}</b> (sem IVA){preview.errors.length ? <> · <b>{preview.errors.length}</b> erro(s)</> : ""}</p>
      {!!preview.errors.length && <Notice tone="error">Corrija o ficheiro: nada é importado enquanto houver erros.<ul>{preview.errors.slice(0, 30).map((e: Row) => <li key={e.line}>Linha {e.line}: {e.message}</li>)}</ul>{preview.errors.length > 30 && <p>… e mais {preview.errors.length - 30}.</p>}</Notice>}
      {!!preview.warnings.length && <Notice tone="warning"><ul>{preview.warnings.slice(0, 15).map((w: Row) => <li key={w.line}>Linha {w.line}: {w.message}</li>)}</ul></Notice>}
      {!!preview.rows.length && <div className="table-wrap"><table><thead><tr><th>Artigo</th><th>Armazém</th><th className="numeric">Quantidade</th><th className="numeric">Custo unitário</th><th className="numeric">Valor</th></tr></thead><tbody>
        {preview.rows.slice(0, 50).map((r: Row) => <tr key={r.line}><td>{r.code} · {r.name}{r.lot_number && <small> · lote {r.lot_number}</small>}</td><td>{r.warehouse}</td><td className="numeric">{r.quantity}</td><td className="numeric">{money(r.unit_cost)}</td><td className="numeric">{money(r.value)}</td></tr>)}
      </tbody></table></div>}
      <ActionButton type="button" className="primary" disabled={!preview.can_confirm} onClick={() => void run(async () => {
        if (!(await confirmAction({ title: "Registar o inventário inicial?", message: `Serão criados ${preview.rows.length} movimentos de stock inicial (${money(preview.total_value)}), com data ${occurredOn.split("-").reverse().join("/")}. Não há anulação automática: correções fazem-se por ajuste justificado.`, confirmLabel: "Registar inventário" }))) return;
        const result = await api(`/stock/opening/${preview.id}/confirm`, { confirmed: true }, { timeoutMs: 300_000 });
        setPreview(null); setContent("");
        await saved(`${result.imported} linha(s) de inventário inicial registadas.`);
      })}>Registar inventário inicial</ActionButton>
    </>}
  </section>;
}
