import { DocumentPreview, DrilldownPanel, DrilldownTable, DrilldownTrigger, type DrilldownContext, type DrilldownStep } from "./Drilldown";
import { ActionButton, Notice } from "./components/InteractionUI";
import { formatEuro } from "./utils/format";

type Allocation = { label: string; base_cents: number; tax_cents: number; amount_cents: number };
type Row = { id: string; label: string; amount_cents: number; date?: string; entity?: string; source?: string; synced_at?: string };
type Result = {
  vat_basis: string;
  rows?: Row[];
  count?: number;
  total_cents?: number;
  document?: Row & { allocations: Allocation[] };
  lines?: { description: string; quantity: string; price: string | null; discount: string | null; vat: string; base_cents: number | null }[];
  originals?: string[];
  related?: string[];
};
const day = (date?: string) => date?.slice(0, 10).split("-").reverse().join("/") ?? "—";

export default function ExpenseDrilldown({ context, initial, onClose }: { context: DrilldownContext; initial: DrilldownStep[]; onClose: () => void }) {
  return <DrilldownPanel<Result> context={context} endpoint="/dashboard/expenses/drilldown" initial={initial} onClose={onClose}>
    {(result, { step, open, paginate }) => {
      const doc = result.document;
      const amount = doc?.amount_cents ?? result.total_cents ?? 0;
      const offset = step.offset ?? 0;
      const rows = result.rows ?? [];
      const count = result.count ?? 0;
      const byDocument = step.params.category !== undefined;
      return <>
        <p><strong>{formatEuro(amount)}</strong> · {result.vat_basis} {doc ? "· parcela neste indicador" : "· total de todos os registos deste nível"}</p>
        {step.expected !== undefined && step.expected !== amount && <Notice tone="warning">O total atual difere dos {formatEuro(step.expected)} mostrados no nível anterior. Atualize o Dashboard para consultar os valores mais recentes.</Notice>}
        {doc ? <>
          <p>{day(doc.date)} · {doc.entity} · {doc.source}{doc.synced_at && ` · Sincronizado ${day(doc.synced_at)}`}</p>
          <h3>Valores que entram no indicador</h3>
          <DrilldownTable label="Imputações no indicador" columns={["Rubrica", "Base", "IVA", "Valor no indicador"]} rows={doc.allocations.map((item, index) => ({
            id: String(index), cells: [item.label, formatEuro(item.base_cents), formatEuro(item.tax_cents), formatEuro(item.amount_cents)],
          }))} />
          <h3>Linhas do lançamento</h3>
          {result.lines?.length ? <><p>Linhas do documento completo. A imputação da rubrica selecionada está na tabela acima.</p>
            <DrilldownTable label="Linhas do documento" columns={["Descrição", "Quantidade", "Preço unitário (€)", "Desconto (%)", "IVA (%)", "Base da linha"]}
              rows={result.lines.map((line, index) => ({ id: String(index), cells: [line.description, line.quantity, line.price ?? "—", line.discount ?? "—", line.vat, line.base_cents === null ? "—" : formatEuro(line.base_cents)] }))} /></>
            : <p>A origem disponibiliza o valor do documento ou movimento sem linhas de artigo.</p>}
          <DocumentPreview identifiers={result.originals ?? []} />
          {!!result.related?.length && <p>{result.related.length} documento(s) de origem referenciado(s) no lançamento.</p>}
        </> : <>
          <DrilldownTable label={byDocument ? "Documentos e movimentos da rubrica" : "Distribuição por rubrica"}
            columns={byDocument ? ["Data", "Documento ou movimento", "Entidade", "Origem", "Valor imputado"] : ["Rubrica", "Valor imputado"]}
            rows={rows.map(row => {
              const trigger = <DrilldownTrigger onOpen={() => open({ label: row.label, expected: row.amount_cents,
                params: byDocument ? { ...step.params, document_id: row.id } : { category: row.id } })}>{row.label}</DrilldownTrigger>;
              return { id: row.id, cells: byDocument ? [day(row.date), trigger, row.entity, row.source, formatEuro(row.amount_cents)] : [trigger, formatEuro(row.amount_cents)] };
            })} />
          {count > 25 && <div className="drilldown-pagination" aria-label="Paginação do detalhe">
            <ActionButton type="button" disabled={offset === 0} onClick={() => paginate(Math.max(0, offset - 25))}>Anterior</ActionButton>
            <span>{offset + 1}–{Math.min(offset + rows.length, count)} de {count}</span>
            <ActionButton type="button" disabled={offset + 25 >= count} onClick={() => paginate(offset + 25)}>Seguinte</ActionButton>
          </div>}
        </>}
      </>;
    }}
  </DrilldownPanel>;
}
