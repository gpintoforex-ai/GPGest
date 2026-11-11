import { useEffect, useState } from "react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";

/**
 * Retenções na fonte registadas nas reconciliações: por entregar ao Estado (pagamentos a fornecedores com
 * retenção, até ao dia 20 do mês seguinte) e retidas por clientes. O total a entregar pode passar a valor previsto
 * da ocorrência «Retenções na fonte» em Obrigações e Prazos; nada é pago automaticamente.
 */

type Row = Record<string, any>;
const day = (iso?: string) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const monthLabel = (month: string) => new Date(`${month}-15T12:00:00Z`).toLocaleDateString("pt-PT", { month: "long", year: "numeric", timeZone: "Europe/Lisbon" });

export default function Withholdings({ revision }: { revision?: unknown }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false), [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    api("/withholdings").then(r => { if (active) setData(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [version, revision]);
  async function forecast(month: Row) {
    setBusy(true); setError(""); setNotice("");
    try {
      await api("/withholdings/forecast", { month: month.month, occurrence_id: month.occurrence.id, revision: month.occurrence.revision });
      setNotice(`Valor previsto da obrigação de ${day(month.due_date)} atualizado para ${formatEuro(month.total_cents)}.`);
      setVersion(v => v + 1);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  if (!data) return error ? <Notice tone="error">{error}</Notice> : <p role="status">A carregar as retenções na fonte…</p>;
  const months: Row[] = data.months;
  return <div className="withholdings">
    <p className="footnote">Diferenças registadas ao confirmar reconciliações (natureza da diferença: retenção na fonte ou desconto financeiro). As retidas a fornecedores são entregues ao Estado
      até ao dia 20 do mês seguinte e não são um custo novo: já fazem parte do valor das faturas.</p>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!months.length ? <p>Ainda não há reconciliações com retenção na fonte nem desconto financeiro.</p> : <div className="table-wrap"><table aria-label="Retenções na fonte por mês">
      <thead><tr><th>Mês</th><th>Tipo</th><th>Documentos</th><th>Total</th><th>Prazo e obrigação</th></tr></thead>
      <tbody>{months.map(m => <tr key={`${m.kind}:${m.month}`}>
        <td>{monthLabel(m.month)}</td>
        <td>{m.kind === "a_entregar" ? <StatusBadge tone="warning" compact>A entregar ao Estado</StatusBadge>
          : m.kind === "retida_por_clientes" ? <StatusBadge tone="info" compact>Retida por clientes</StatusBadge>
          : m.kind === "desconto_obtido" ? <StatusBadge tone="success" compact>Desconto financeiro obtido</StatusBadge>
          : <StatusBadge tone="neutral" compact>Desconto financeiro concedido</StatusBadge>}</td>
        <td><ul className="withholdings-items">{m.items.map((i: Row) => <li key={i.reconciliation_id}>{day(i.date)} · {i.documents.map((d: Row) => `${d.document} (${d.entity})`).join(", ") || i.bank} · {formatEuro(i.withholding_cents)}</li>)}</ul></td>
        <td className="numeric"><strong>{formatEuro(m.total_cents)}</strong></td>
        <td>{m.kind === "retida_por_clientes" ? "A deduzir no IRS/IRC" : m.kind === "desconto_obtido" ? "Ganho financeiro (a fatura ficou paga na totalidade)"
          : m.kind === "desconto_concedido" ? "Perda financeira (o recebimento ficou completo)" : <>
          Até {day(m.due_date)}
          {m.occurrence ? <small>{m.occurrence.title}: {m.occurrence.status === "pendente" ? "por pagar" : m.occurrence.status} · previsto {m.occurrence.expected_cents == null ? "sem valor" : formatEuro(m.occurrence.expected_cents)}</small>
            : <small>Sem obrigação «Retenções na fonte» neste prazo (Obrigações e Prazos → modelos fiscais).</small>}
          {m.occurrence && m.occurrence.status === "pendente" && m.occurrence.expected_cents !== m.total_cents &&
            <ActionButton type="button" disabled={busy} onClick={() => void forecast(m)}>Usar {formatEuro(m.total_cents)} como valor previsto</ActionButton>}
        </>}</td>
      </tr>)}</tbody></table></div>}
  </div>;
}
