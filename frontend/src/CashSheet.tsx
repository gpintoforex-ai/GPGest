import { ActionLink } from "./components/InteractionUI";
import { ActionButton, AppDialog, Notice, confirmAction } from "./components/InteractionUI";
import { useEffect, useRef, useState } from "react";
import { ArrowLeftRight, Banknote, CreditCard, FileText, LockKeyhole, LockKeyholeOpen, Smartphone, Wallet } from "lucide-react";
import { paymentTone } from "./lib/paymentTone";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import CashCountDialog from "./CashCountDialog";
import SettlementConfirmation from "./SettlementConfirmation";
import { useCashPreferences } from "./CashPreferences";
import CashReconciliation from "./CashReconciliation";
import CashSupplierDialog, { type CashSupplierAllocation } from "./CashSupplierDialog";
import "./CashSheet.css";

type Row = Record<string, any>;
const money = (value: number) =>
  new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(
    value / 100,
  );
const euros = (value: number) => (value / 100).toFixed(2).replace(".", ",");
function readAmount(value: string): number | null {
  const text = value.trim().replace(",", ".");
  if (!/^-?\d+(\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.replace("-", "").split(".");
  return (
    (Number(whole) * 100 + Number(fraction.padEnd(2, "0"))) *
    (text.startsWith("-") ? -1 : 1)
  );
}

export default function CashSheet({
  session: s,
  register,
  store,
  operator,
  writable,
  manager,
  canImportErp = false,
  demo = false,
  reload,
  onSaved,
  onAction,
}: {
  session: Row;
  register: string;
  store: string;
  operator: string;
  writable: boolean;
  manager: boolean;
  canImportErp?: boolean;
  demo?: boolean;
  reload: () => Promise<void>;
  onSaved?: (saved: Row) => void;
  onAction: (kind: string, row: Row) => void;
}) {
  const stored = s.count_data || {};
  const [closeNotice, setCloseNotice] = useState("");
  const { preferences } = useCashPreferences();
  const [importPrompt, setImportPrompt] = useState(false), [confirmDemo, setConfirmDemo] = useState(false);
  const importDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (importPrompt && !importDialog.current?.open) importDialog.current?.showModal(); }, [importPrompt]);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      s.sheet.rows.map((row: Row) => [
        row.key,
        stored.values?.[row.key] != null
          ? euros(stored.values[row.key])
          : row.expected_cents === 0
            ? "0,00"
            : "",
      ]),
    ),
  );
  const [counts, setCounts] = useState<Record<string, number>>(
    stored.counts || {},
  );
  const [cashMode, setCashMode] = useState(stored.cash_mode || "total");
  const [countDialog, setCountDialog] = useState(false);
  const [reason, setReason] = useState(stored.reason || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sourceDay, setSourceDay] = useState(s.sheet.day);
  const [supplierPrompt, setSupplierPrompt] = useState(false);
  const editable = writable && !s.closed_at;
  const countedCash = Object.entries(counts).reduce(
    (sum, [key, count]) => sum + Number(key) * count,
    0,
  );
  const amountFor = (key: string) =>
    s.closed_at || !editable
      ? (stored.values?.[key] ?? null)
      : key === "numerario" && cashMode === "denominations"
        ? countedCash
        : readAmount(values[key] || "");
  const rows: Row[] = s.sheet.rows;
  const cashRow = rows.find((row) => row.key === "numerario")!;
  const cashCount = amountFor("numerario");
  async function updateSources() {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await api(`/sessions/${s.id}/sources`, {
        sheet_day: sourceDay,
      });
      await reload();
      setNotice(
        "Dia atualizado. Confira a contagem com os novos valores esperados.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const difference = rows.reduce(
    (sum, row) =>
      sum +
      (amountFor(row.key) === null
        ? 0
        : amountFor(row.key)! - row.expected_cents),
    0,
  );
  async function save(closing: boolean, supplierAllocations?: CashSupplierAllocation[]) {
    setError("");
    setNotice("");
    if (closing && (s.sheet.preferences?.count_required ?? true) && rows.some((row) => amountFor(row.key) === null)) {
      setError(
        "Introduza a contagem de todos os meios de pagamento antes de fechar.",
      );
      return;
    }
    if (closing && rows.some(row => row.debits_cents > 0) && !supplierAllocations) {
      setSupplierPrompt(true);
      return;
    }
    if (closing && !supplierAllocations && !(await confirmAction({ title: "Fechar caixa?", message: `Pretende fechar «${register} · ${store}» do dia ${s.sheet.day.split("-").reverse().join("/")}? A contagem e as diferenças serão guardadas no fecho. Para alterar depois, terá de reabrir a caixa.`, confirmLabel: "Confirmar e fechar caixa" }))) return;
    const payments: Record<string, string> = {};
    for (const row of rows)
      if (row.key !== "numerario" && values[row.key]?.trim())
        payments[row.key] = values[row.key];
    setBusy(true);
    try {
      const saved = await api(`/sessions/${s.id}/${closing ? "close" : "count"}`, {
        counts: cashMode === "denominations" ? counts : {},
        cash_mode: cashMode,
        payments,
        cash_total:
          cashMode === "total" && values.numerario?.trim()
            ? values.numerario
            : null,
        reason,
        ...(closing && supplierAllocations ? {
          supplier_answer: "indicado",
          supplier_allocations: supplierAllocations.map(({ id, amount_cents }) => ({ supplier_id: id, amount_cents })),
        } : {}),
      });
      if (!closing && onSaved) onSaved(saved);
      else await reload();
      setSupplierPrompt(false);
      if (closing) {
        setCloseNotice(`${register} · ${store} · ${s.sheet.day.split("-").reverse().join("/")}. Os valores do fecho foram guardados.`);
        window.dispatchEvent(new CustomEvent("gpgest-sales-updated", { detail: { appRefreshed: true } }));
      } else {
        setNotice("Contagem guardada. Pode continuar a registar movimentos.");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="panel cash-sheet"
      aria-label={`Folha de caixa ${register}`}
    >
      <div className="panel-title">
        <div>
          <h3>
            {register} · {store}
          </h3>
          <p>
            {s.sheet.day.split("-").reverse().join("/")} · {operator} · {s.closed_at ? "Fechada" : "Em aberto"}
          </p>
        </div>
        <span className={`cash-sheet-status ${s.closed_at ? "closed" : "open"}`}>
          {s.closed_at ? <LockKeyhole size={16} aria-hidden="true" /> : <LockKeyholeOpen size={16} aria-hidden="true" />}
          {s.closed_at ? "Fechada" : "Aberta"}
        </span>
      </div>
      <CashReconciliation opening={s.retained_fund_cents ?? s.opening_cents} expected={cashRow.expected_cents} counted={cashCount} />
      <details className="cash-sheet-details">
        <summary>{s.closed_at ? "Ver detalhes do fecho" : "Conferência e fecho"}</summary>
        {canImportErp && <ActionButton disabled={busy || !!s.closed_at} title={s.closed_at ? "Reabra a folha para atualizar a conferência." : undefined} onClick={() => { setConfirmDemo(false); setImportPrompt(true); }}>Importar movimentos do ERP</ActionButton>}
      <p className="footnote">
        Origem dos valores: Manual (IAZELA) e ERP (Wintouch).
      </p>
      <p className="footnote">
        {s.sheet.scope === "store_day"
          ? "Totais de todos os movimentos reais da loja neste dia: créditos e débitos, manuais e Wintouch, de todas as sessões de caixa. Os documentos a crédito ficam nos movimentos previstos."
          : "Fecho anterior: valores guardados dos movimentos desta sessão de caixa."}
      </p>
      {s.sheet.include_wintouch && (
        <p className="footnote">
          Importe os movimentos do ERP para atualizar a conferência. Evite
          registar novamente na IAZELA os mesmos recebimentos.
        </p>
      )}
      {importPrompt && <AppDialog busy={busy} className="doc-dialog" ref={importDialog} aria-label="Importar movimentos do ERP" onRequestClose={() => {  if (!busy) setImportPrompt(false); }}><div className="panel-title"><h2>Importar movimentos do ERP</h2></div><p>Importar os movimentos do estabelecimento para {s.sheet.day.split("-").reverse().join("/")}, usando o ERP configurado na empresa.</p>{error && <Notice className="error" role="alert">{error}</Notice>}{demo && <label className="doc-check"><input type="checkbox" checked={confirmDemo} onChange={e => setConfirmDemo(e.target.checked)} disabled={busy} />Confirmo a importação de dados reais do ERP para esta empresa de demonstração.</label>}<ActionButton className="primary" disabled={busy || (demo && !confirmDemo)} onClick={async () => { setBusy(true); setError(""); setNotice(""); try { const result = await api(`/sessions/${s.id}/import-erp`, { confirm_demo: confirmDemo }, { timeoutMs: 120000 }); await reload(); window.dispatchEvent(new Event("gpgest-sales-updated")); setImportPrompt(false); setNotice(result.closed ? "Movimentos importados. A folha foi fechada entretanto; os valores do fecho foram preservados." : `Movimentos importados com sucesso: ${result.import.read_count} registos, ${result.import.inserted_count} novos e ${result.import.updated_count} atualizados.`); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>{busy ? "A importar…" : "Importar movimentos"}</ActionButton></AppDialog>}
      {editable && (
        <div className="cash-sheet-sources">
          <strong>Dia dos movimentos reais: {String(s.sheet.day).split("-").reverse().join("/")}</strong>
          <p className="footnote">A conferência inclui apenas os movimentos desta data. Se uma entrada ou saída estiver registada noutro dia no ERP, selecione esse dia e clique em Atualizar dia.</p>
          <Field label="Dia da folha de caixa">
            <input
              type="date"
              value={sourceDay}
              onChange={(e) => setSourceDay(e.target.value)}
            />
          </Field>
          <ActionButton disabled={busy || !sourceDay} onClick={updateSources}>
            Atualizar dia
          </ActionButton>
        </div>
      )}
      <div className="table-wrap">
        <table className="cash-sheet-table">
          <thead>
            <tr>
              <th>Meio de pagamento</th>
              <th>IAZELA (saldo)</th>
              <th>Wintouch (saldo)</th>
              <th>Créditos</th>
              <th>Débitos</th>
              <th>Esperado</th>
              <th>Contado / conferido</th>
              <th>Diferença</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const counted = amountFor(row.key);
              const PaymentIcon = row.key === "numerario" ? Banknote
                : row.key === "cartao" ? CreditCard
                : row.key === "mbway" ? Smartphone
                : row.key === "transferencia" ? ArrowLeftRight
                : String(row.key).startsWith("cheque") ? FileText : Wallet;
              return (
                <tr key={row.key} className={paymentTone(String(row.key))}>
                  <td>
                    <strong className="cash-payment-label">
                      <PaymentIcon size={18} aria-hidden="true" />
                      <span>{row.name}</span>
                    </strong>
                    {row.key === "numerario" && (
                      <small>Inclui o fundo de maneio no esperado</small>
                    )}
                  </td>
                  <td>{money(row.local_cents)}</td>
                  <td>{money(row.wintouch_cents)}</td>
                  <td className="positive">{money(row.credits_cents)}</td>
                  <td className="negative">{money(row.debits_cents)}</td>
                  <td>
                    <b>{money(row.expected_cents)}</b>
                  </td>
                  <td>
                    {editable && row.key === "numerario" ? (
                      <ActionButton
                        className="cash-count-trigger"
                        aria-label="Contar numerário"
                        aria-haspopup="dialog"
                        disabled={busy}
                        onClick={() => setCountDialog(true)}
                      >
                        <span>
                          {counted === null ? "Por contar" : money(counted)}
                        </span>
                        <small>Contar notas e moedas</small>
                      </ActionButton>
                    ) : editable ? (
                      <input
                        aria-label={`Contagem ${row.name} (€)`}
                        inputMode="decimal"
                        value={values[row.key] || ""}
                        onChange={(e) =>
                          setValues({ ...values, [row.key]: e.target.value })
                        }
                        placeholder="0,00"
                      />
                    ) : counted === null ? (
                      "Por contar"
                    ) : (
                      money(counted)
                    )}
                  </td>
                  <td
                    className={
                      counted === null
                        ? ""
                        : counted === row.expected_cents
                          ? "positive"
                          : "negative"
                    }
                  >
                    {counted === null
                      ? "Por contar"
                      : money(counted - row.expected_cents)}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th>Total</th>
              <td>{money(rows.reduce((sum, r) => sum + r.local_cents, 0))}</td>
              <td>
                {money(rows.reduce((sum, r) => sum + r.wintouch_cents, 0))}
              </td>
              <td>
                {money(rows.reduce((sum, r) => sum + r.credits_cents, 0))}
              </td>
              <td>{money(rows.reduce((sum, r) => sum + r.debits_cents, 0))}</td>
              <td>
                {money(rows.reduce((sum, r) => sum + r.expected_cents, 0))}
              </td>
              <td>
                {rows.some((r) => amountFor(r.key) === null)
                  ? "Contagem incompleta"
                  : money(rows.reduce((sum, r) => sum + amountFor(r.key)!, 0))}
              </td>
              <td>
                {rows.some((r) => amountFor(r.key) === null)
                  ? "Contagem incompleta"
                  : money(difference)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      {editable && countDialog && (
        <CashCountDialog
          defaultMode={preferences.countMode}
          initial={{
            mode: cashMode === "denominations" ? "denominations" : "total",
            counts,
            total: values.numerario || "",
          }}
          expected={rows.find((row) => row.key === "numerario")!.expected_cents}
          onCancel={() => setCountDialog(false)}
          onApply={(draft) => {
            setCashMode(draft.mode);
            setCounts(draft.counts);
            setValues((previous) => ({ ...previous, numerario: draft.total }));
            setCountDialog(false);
          }}
        />
      )}
      <p className="footnote">
        Numerário: dinheiro físico. Nos restantes meios, introduza o valor
        líquido conferido no terminal, extrato ou comprovativos. Diferença =
        contado − esperado.
      </p>
      {editable && (
        <Field label="Observações / justificação das diferenças">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={2000}
          />
        </Field>
      )}
      {s.closed_at && s.close_reason && <p>Justificação: {s.close_reason}</p>}
      {s.closed_at && stored.debit_supplier && <div className="cash-supplier-summary"><strong>Fornecedores dos débitos:</strong>{stored.debit_supplier.allocations?.length ? stored.debit_supplier.allocations.map((allocation: Row) => <p key={allocation.id}>{allocation.name} · {money(allocation.amount_cents)}</p>) : <p>{stored.debit_supplier.answer === "indicado" ? `${stored.debit_supplier.name} · ${money(stored.debit_supplier.debits_cents)}` : stored.debit_supplier.answer === "desconhecido" ? "Não identificado" : "Não aplicável"}</p>}{stored.debit_supplier.unassigned_cents > 0 && <p>Por atribuir: {money(stored.debit_supplier.unassigned_cents)}</p>}</div>}
      {error && (
        <Notice className="error" role="alert">
          {error}
        </Notice>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="actions">
        {editable && (
          <>
            <ActionButton disabled={busy} onClick={() => save(false)}>
              Guardar contagem
            </ActionButton>
            <ActionButton
              className="primary"
              disabled={busy}
              onClick={() => save(true)}
            >
              Fechar folha de caixa
            </ActionButton>
            <ActionButton disabled={busy} onClick={() => onAction("handover", s)}>
              Passar caixa
            </ActionButton>
          </>
        )}
        {s.closed_at && (
          <ActionLink className="download-link" href={`/api/sessions/${s.id}/report`}>
            Relatório PDF
          </ActionLink>
        )}
        {s.closed_at && manager && (
          <ActionButton onClick={() => onAction("reopen", s)}>Reabrir</ActionButton>
        )}
      </div>

      </details>
      {supplierPrompt && <CashSupplierDialog context={`${register} · ${store} · ${s.sheet.day.split("-").reverse().join("/")}`} total={rows.reduce((sum, row) => sum + row.debits_cents, 0)} initialId={stored.debit_supplier?.id} canCreate={manager} busy={busy} error={error} onCancel={() => setSupplierPrompt(false)} onConfirm={allocations => save(true, allocations)} />}
      {closeNotice && <SettlementConfirmation title="Caixa fechada" message={closeNotice} onClose={() => setCloseNotice("")} />}
    </section>
  );
}
