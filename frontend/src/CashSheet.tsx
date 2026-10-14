import { useEffect, useRef, useState } from "react";
import { ArrowLeftRight, Banknote, CreditCard, FileText, LockKeyhole, LockKeyholeOpen, Wallet } from "lucide-react";
import { api, Field } from "./App";
import CashCountDialog from "./CashCountDialog";
import SupplierPicker from "./components/SupplierPicker";
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
  operator,
  writable,
  manager,
  canImportErp = false,
  demo = false,
  reload,
  onAction,
}: {
  session: Row;
  register: string;
  operator: string;
  writable: boolean;
  manager: boolean;
  canImportErp?: boolean;
  demo?: boolean;
  reload: () => Promise<void>;
  onAction: (kind: string, row: Row) => void;
}) {
  const stored = s.count_data || {};
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
  const [supplierAnswer, setSupplierAnswer] = useState(stored.debit_supplier?.answer || "");
  const [supplierName, setSupplierName] = useState(stored.debit_supplier?.name || "");
  const [supplierId, setSupplierId] = useState(stored.debit_supplier?.id || "");
  const [suppliers, setSuppliers] = useState<Row[]>([]);
  const [supplierLoading, setSupplierLoading] = useState(false);
  useEffect(() => {
    if (!supplierPrompt) return;
    let active = true; setSupplierLoading(true);
    api("/entities").then(result => { if (active) setSuppliers(result.filter((entity: Row) => entity.is_supplier && entity.active)); }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setSupplierLoading(false); });
    return () => { active = false; };
  }, [supplierPrompt]);
  const supplierDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (supplierPrompt && !supplierDialog.current?.open) supplierDialog.current?.showModal(); }, [supplierPrompt]);
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
  const cashDifference =
    cashCount === null ? null : cashCount - cashRow.expected_cents;
  const cashToWithdraw =
    cashCount === null ? null : cashCount - s.opening_cents;
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
  async function save(closing: boolean, supplier?: { answer: string; name: string; id?: string }) {
    setError("");
    setNotice("");
    if (closing && rows.some((row) => amountFor(row.key) === null)) {
      setError(
        "Introduza a contagem de todos os meios de pagamento antes de fechar.",
      );
      return;
    }
    if (closing && rows.some(row => row.debits_cents > 0) && !supplier) {
      setSupplierPrompt(true);
      return;
    }
    const payments: Record<string, string> = {};
    for (const row of rows)
      if (row.key !== "numerario" && values[row.key]?.trim())
        payments[row.key] = values[row.key];
    setBusy(true);
    try {
      await api(`/sessions/${s.id}/${closing ? "close" : "count"}`, {
        counts: cashMode === "denominations" ? counts : {},
        cash_mode: cashMode,
        payments,
        cash_total:
          cashMode === "total" && values.numerario?.trim()
            ? values.numerario
            : null,
        reason,
        ...(closing && supplier ? { supplier_answer: supplier.answer, supplier_name: supplier.name, supplier_id: supplier.id || null } : {}),
      });
      await reload();
      setSupplierPrompt(false);
      if (closing) window.dispatchEvent(new Event("gpgest-sales-updated"));
      setNotice(
        closing
          ? "Folha de caixa fechada."
          : "Contagem guardada. Pode continuar a registar movimentos.",
      );
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
            {register} · {s.sheet.day.split("-").reverse().join("/")}
          </h3>
          <p>
            {operator} · {s.closed_at ? "Fechada" : "Em aberto"}
          </p>
        </div>
        <div className="actions">{canImportErp && <button disabled={busy || !!s.closed_at} title={s.closed_at ? "Reabra a folha para atualizar a conferência." : undefined} onClick={() => { setConfirmDemo(false); setImportPrompt(true); }}>Importar movimentos do ERP</button>}
        <span className={`cash-sheet-status ${s.closed_at ? "closed" : "open"}`}>
          {s.closed_at ? <LockKeyhole size={16} aria-hidden="true" /> : <LockKeyholeOpen size={16} aria-hidden="true" />}
          {s.closed_at ? "Fechada" : "Aberta"}
        </span>
        </div>
      </div>
      <div
        className={`cash-reconciliation ${cashDifference === null ? "pending" : cashDifference === 0 ? "balanced" : "unbalanced"}`}
        role="status"
        aria-label="Apuramento de numerário"
      >
        <div className="cash-reconciliation-heading">
          <b>Apuramento de numerário</b>
          <span>
            {cashDifference === null
              ? "Por contar"
              : cashDifference === 0
                ? "Caixa correta"
                : "Caixa com diferença"}
          </span>
        </div>
        <div className="cash-reconciliation-values">
          <div>
            <small>Fundo de maneio a manter</small>
            <strong>{money(s.opening_cents)}</strong>
          </div>
          <div>
            <small>Movimentos de numerário (entradas − saídas)</small>
            <strong>{money(cashRow.expected_cents - s.opening_cents)}</strong>
          </div>
          <div>
            <small>Total esperado (fundo + movimentos)</small>
            <strong>{money(cashRow.expected_cents)}</strong>
          </div>
          <div>
            <small>Diferença de caixa (contado − esperado)</small>
            <strong>{cashDifference === null ? "Por contar" : money(cashDifference)}</strong>
          </div>
          <div className={`cash-withdrawal-highlight ${cashToWithdraw === null ? "pending" : cashToWithdraw < 0 ? "replenish" : ""}`}>
            <small>{cashToWithdraw !== null && cashToWithdraw < 0 ? "Valor a repor no fundo" : "Valor a retirar"}</small>
            <strong>{cashToWithdraw === null ? "Por contar" : money(Math.abs(cashToWithdraw))}</strong>
          </div>
        </div>
        <p>Valor a retirar = numerário contado − fundo de maneio. Se faltar dinheiro para o fundo, é apresentado o valor a repor.</p>
      </div>
      <p className="footnote">
        Origem dos valores: Manual (GPGest) e ERP (Wintouch).
      </p>
      <p className="footnote">
        {s.sheet.scope === "store_day"
          ? "Totais de todos os movimentos reais da loja neste dia: créditos e débitos, manuais e Wintouch, de todas as sessões de caixa. Os documentos a crédito ficam nos movimentos previstos."
          : "Fecho anterior: valores guardados dos movimentos desta sessão de caixa."}
      </p>
      {s.sheet.include_wintouch && (
        <p className="footnote">
          Importe os movimentos do ERP para atualizar a conferência. Evite
          registar novamente no GPGest os mesmos recebimentos.
        </p>
      )}
      {importPrompt && <dialog className="doc-dialog" ref={importDialog} aria-label="Importar movimentos do ERP" onCancel={e => { e.preventDefault(); if (!busy) setImportPrompt(false); }}><div className="panel-title"><h2>Importar movimentos do ERP</h2><button disabled={busy} aria-label="Fechar" onClick={() => setImportPrompt(false)}>×</button></div><p>Importar os movimentos do estabelecimento para {s.sheet.day.split("-").reverse().join("/")}, usando o ERP configurado na empresa.</p>{error && <p className="error" role="alert">{error}</p>}{demo && <label className="doc-check"><input type="checkbox" checked={confirmDemo} onChange={e => setConfirmDemo(e.target.checked)} disabled={busy} />Confirmo a importação de dados reais do ERP para esta empresa de demonstração.</label>}<button className="primary" disabled={busy || (demo && !confirmDemo)} onClick={async () => { setBusy(true); setError(""); setNotice(""); try { const result = await api(`/sessions/${s.id}/import-erp`, { confirm_demo: confirmDemo }, { timeoutMs: 120000 }); await reload(); window.dispatchEvent(new Event("gpgest-sales-updated")); setImportPrompt(false); setNotice(result.closed ? "Movimentos importados. A folha foi fechada entretanto; os valores do fecho foram preservados." : `Movimentos importados com sucesso: ${result.import.read_count} registos, ${result.import.inserted_count} novos e ${result.import.updated_count} atualizados.`); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>{busy ? "A importar…" : "Importar movimentos"}</button></dialog>}
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
          <button disabled={busy || !sourceDay} onClick={updateSources}>
            Atualizar dia
          </button>
        </div>
      )}
      <div className="table-wrap">
        <table className="cash-sheet-table">
          <thead>
            <tr>
              <th>Meio de pagamento</th>
              <th>GPGest (saldo)</th>
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
                : row.key === "transferencia" ? ArrowLeftRight
                : String(row.key).startsWith("cheque") ? FileText : Wallet;
              return (
                <tr key={row.key}>
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
                      <button
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
                      </button>
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
          initial={{
            mode:
              cashMode === "denominations" || amountFor("numerario") === null
                ? "denominations"
                : "total",
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
      {s.closed_at && stored.debit_supplier && <p>Fornecedor dos débitos: <strong>{stored.debit_supplier.answer === "indicado" ? stored.debit_supplier.name : stored.debit_supplier.answer === "desconhecido" ? "Não identificado" : "Não aplicável"}</strong></p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="actions">
        {editable && (
          <>
            <button disabled={busy} onClick={() => save(false)}>
              Guardar contagem
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => save(true)}
            >
              Fechar folha de caixa
            </button>
            <button disabled={busy} onClick={() => onAction("handover", s)}>
              Passar caixa
            </button>
          </>
        )}
        {s.closed_at && (
          <a className="download-link" href={`/api/sessions/${s.id}/report`}>
            Relatório PDF
          </a>
        )}
        {s.closed_at && manager && (
          <button onClick={() => onAction("reopen", s)}>Reabrir</button>
        )}
      </div>
      {supplierPrompt && <dialog ref={supplierDialog} className="doc-dialog" aria-label="Fornecedor dos débitos" style={{ maxWidth: 560 }} onCancel={e => { e.preventDefault(); if (!busy) setSupplierPrompt(false); }}>
        <div className="panel-title"><h2>Identificar fornecedor</h2><button disabled={busy} aria-label="Fechar pergunta do fornecedor" onClick={() => setSupplierPrompt(false)}>×</button></div>
        <p>Existem {money(rows.reduce((sum, row) => sum + row.debits_cents, 0))} em débitos nesta folha.</p>
        <form className="doc-form" onSubmit={e => { e.preventDefault(); save(true, { answer: supplierAnswer, name: supplierName, id: supplierAnswer === "indicado" ? supplierId : undefined }); }}>
          <fieldset><legend>Sabe indicar o fornecedor dos débitos?</legend>
            {[["indicado", "Sim, indicar fornecedor"], ["desconhecido", "Não sei indicar"], ["nao_aplicavel", "Não se aplica a um fornecedor"]].map(([value, label]) => <label className="doc-check" key={value}><input type="radio" name="supplier_answer" value={value} checked={supplierAnswer === value} onChange={() => setSupplierAnswer(value)} />{label}</label>)}
          </fieldset>
          {supplierAnswer === "indicado" && <>
            <SupplierPicker items={suppliers as { id: string; name: string; tax_number?: string }[]} selectedName={supplierName} loading={supplierLoading} canCreate={manager} onSelect={entity => { setSupplierId(entity?.id || ""); setSupplierName(entity?.name || ""); }} onCreated={entity => setSuppliers(previous => [...previous, entity])} />
          </>}
          <p className="footnote">A indicação fica guardada no fecho. Os movimentos e o registo na Wintouch mantêm os seus dados.</p>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="actions"><button type="button" disabled={busy} onClick={() => setSupplierPrompt(false)}>Cancelar</button><button className="primary" disabled={busy || !supplierAnswer || (supplierAnswer === "indicado" && !supplierId)}>Confirmar e fechar caixa</button></div>
        </form>
      </dialog>}
    </section>
  );
}
