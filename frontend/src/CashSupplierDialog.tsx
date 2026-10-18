import { ActionButton, AppDialog, ManagedForm, Notice } from "./components/InteractionUI";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { api } from "./App";
import SupplierPicker from "./components/SupplierPicker";
import EntityCreateDialog, { type CreatedEntity } from "./EntityCreateDialog";

export type CashSupplier = { id: string; name: string; tax_number?: string };
export type CashSupplierAllocation = CashSupplier & { amount_cents: number; payment?: string };
type AllocationDraft = { key: number; supplier: CashSupplier | null; amount: string; payment: string };
type DebitPayment = { id: string; name: string; amount_cents: number };

function euros(value: number) {
  return (value / 100).toFixed(2).replace(".", ",");
}

function cents(value: string): number | null {
  const text = value.trim().replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

export default function CashSupplierDialog({
  total,
  context,
  initialId,
  payments,
  canCreate,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  total: number;
  context?: string;
  initialId?: string;
  payments?: DebitPayment[];
  canCreate: boolean;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: (allocations: CashSupplierAllocation[]) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const helpId = useId();
  const [items, setItems] = useState<CashSupplier[]>([]);
  const [allocations, setAllocations] = useState<AllocationDraft[]>([
    { key: 0, supplier: null, amount: euros(total), payment: payments?.length === 1 ? payments[0].id : "" },
  ]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [createSupplier, setCreateSupplier] = useState<{ allocationKey: number; name: string } | null>(null);
  const initialAllocations = useRef<string | null>(null);
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => {
      element.close();
      previous?.focus();
    };
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError("");
    api("/entities")
      .then((entities) => {
        if (!active) return;
        const suppliers = entities.filter(
          (entity: Record<string, any>) => entity.is_supplier && entity.active,
        );
        setItems(suppliers);
        const initialSupplier =
          suppliers.find((item: CashSupplier) => item.id === initialId) || null;
        const next = [{ key: 0, supplier: initialSupplier, amount: euros(total), payment: payments?.length === 1 ? payments[0].id : "" }];
        initialAllocations.current = JSON.stringify(next);
        setAllocations(next);
      })
      .catch((e) => {
        if (active) setLoadError((e as Error).message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [initialId, attempt]);
  const allocatedCents = allocations.reduce(
    (sum, allocation) => sum + (cents(allocation.amount) ?? 0),
    0,
  );
  const remainingCents = total - allocatedCents;
  const allocationError = allocations.some(
    (allocation) => !allocation.supplier || cents(allocation.amount) === null || cents(allocation.amount)! <= 0 || (payments && !allocation.payment),
  )
    ? payments && payments.length > 1 ? "Selecione um fornecedor, o meio de pagamento e um valor válido em cada linha." : "Selecione um fornecedor e indique um valor válido em cada linha."
    : new Set(allocations.map((allocation) => `${allocation.supplier!.id}:${allocation.payment}`)).size !== allocations.length
      ? "Cada fornecedor só pode aparecer uma vez por meio de pagamento."
      : allocatedCents > total
        ? "O valor atribuído não pode ultrapassar o total dos débitos."
        : payments?.some(payment => allocations.filter(a => a.payment === payment.id).reduce((sum, a) => sum + (cents(a.amount) || 0), 0) > payment.amount_cents)
          ? "O valor atribuído aos fornecedores ultrapassa os débitos do meio de pagamento selecionado."
          : "";
  return <>
    {createPortal(
    <AppDialog busy={busy} initialFocus="cancel" dirty={initialAllocations.current !== null && initialAllocations.current !== JSON.stringify(allocations)}
      ref={dialog}
      className="doc-dialog"
      style={{ maxWidth: 560 }}
      aria-labelledby={titleId}
      aria-describedby={helpId}
      onRequestClose={() => {

        if (!busy) onCancel();
      }}
    >
      <div className="panel-title">
        <h2 id={titleId}>Identificar fornecedor</h2>

      </div>
      {context && <p><strong>Fecho de caixa: {context}</strong></p>}
      <p id={helpId}>Existem <strong>{new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(total / 100)}</strong> em débitos nesta folha. Indique o fornecedor e o montante a atribuir a cada um.</p>
      <ManagedForm
        className="doc-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && !loading && !loadError && !allocationError) {
            onConfirm(allocations.map((allocation) => ({
              ...allocation.supplier!,
              amount_cents: cents(allocation.amount)!,
              ...(allocation.payment ? { payment: allocation.payment } : {}),
            })));
          }
        }}
      >
        <fieldset
          disabled={busy}
          style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
        >
          {allocations.map((allocation, index) => (
            <div className="cash-supplier-allocation" key={allocation.key}>
              <SupplierPicker
                key={loading ? `loading-${allocation.key}` : `loaded-${allocation.key}-${attempt}`}
                items={items}
                selectedName={allocation.supplier?.name || ""}
                loading={loading || !!loadError}
                canCreate={canCreate}
                onRequestCreate={(name) => setCreateSupplier({ allocationKey: allocation.key, name })}
                onSelect={(supplier) => setAllocations((previous) => previous.map((item) =>
                  item.key === allocation.key ? { ...item, supplier } : item,
                ))}
              />
              {payments && payments.length > 1 && (
                <label>
                  Meio de pagamento
                  <select aria-label={`Meio de pagamento do fornecedor ${index + 1}`} value={allocation.payment}
                    onChange={event => setAllocations(previous => previous.map(item => item.key === allocation.key ? { ...item, payment: event.target.value } : item))}>
                    <option value="">Escolher meio de pagamento</option>
                    {payments.map(payment => <option key={payment.id} value={payment.id}>{payment.name}</option>)}
                  </select>
                </label>
              )}
              <label>
                Valor a atribuir (€)
                <input
                  aria-label={`Valor a atribuir ao fornecedor ${index + 1} (€)`}
                  inputMode="decimal"
                  value={allocation.amount}
                  onChange={(event) => setAllocations((previous) => previous.map((item) =>
                    item.key === allocation.key ? { ...item, amount: event.target.value } : item,
                  ))}
                  placeholder="0,00"
                />
              </label>
              {allocations.length > 1 && (
                <ActionButton
                  type="button"
                  disabled={busy}
                  aria-label={`Remover fornecedor ${index + 1}`}
                  onClick={() => setAllocations((previous) => previous.filter((item) => item.key !== allocation.key))}
                >
                  Remover
                </ActionButton>
              )}
            </div>
          ))}
          <ActionButton
            type="button"
            disabled={busy || allocations.length >= 50}
            onClick={() => setAllocations((previous) => [
              ...previous,
              { key: Math.max(...previous.map((item) => item.key)) + 1, supplier: null, amount: remainingCents > 0 ? euros(remainingCents) : "", payment: payments?.length === 1 ? payments[0].id : "" },
            ])}
          >
            Adicionar fornecedor
          </ActionButton>
        </fieldset>
        <p className="footnote">Montante atribuído: {new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(allocatedCents / 100)} · Por atribuir: {new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Math.max(remainingCents, 0) / 100)}. {payments ? "O fecho cria os pagamentos em falta numa linha por fornecedor e meio de pagamento. Os valores já registados são descontados." : "A repartição fica registada no fecho; não associa valores a movimentos individuais."}</p>
        {allocationError && <Notice className="error" role="alert">{allocationError}</Notice>}
        {loadError && (
          <div>
            <Notice className="error" role="alert">
              {loadError}
            </Notice>
            <ActionButton
              type="button"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Tentar novamente
            </ActionButton>
          </div>
        )}
        {error && (
          <Notice className="error" role="alert">
            {error}
          </Notice>
        )}
        <div className="actions">
          <ActionButton type="button" disabled={busy} onClick={onCancel}>
            Cancelar
          </ActionButton>
          <ActionButton
            type="submit"
            className="primary"
            disabled={busy || loading || !!loadError || !!allocationError}
          >
            {busy ? "A fechar…" : "Confirmar e fechar caixa"}
          </ActionButton>
        </div>
      </ManagedForm>
    </AppDialog>,
    document.body,
  )}
    {createSupplier && <EntityCreateDialog
      kind="Fornecedores"
      initialName={createSupplier.name}
      onCancel={() => setCreateSupplier(null)}
      onCreated={(entity: CreatedEntity) => {
        const supplier = { id: entity.id, name: entity.name, tax_number: entity.tax_number };
        setItems((previous) => [...previous, supplier]);
        setAllocations((previous) => previous.map((allocation) =>
          allocation.key === createSupplier.allocationKey
            ? { ...allocation, supplier }
            : allocation,
        ));
        setCreateSupplier(null);
      }}
    />}
  </>;
}
