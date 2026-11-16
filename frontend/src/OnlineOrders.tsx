import { useCallback, useEffect, useState } from "react";
import { History, RefreshCw } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, ManagedForm, Notice, confirmAction } from "./components/InteractionUI";
import { MetricCard, StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { DataTable } from "./components/DataTable";

type Line = { article_id: string; description: string; quantity: string; reserved: string; delivered: string; invoiced: string; cancelled: string };
type Order = {
  id: string;
  connection_id: string;
  connection_name: string;
  external_id: string;
  shop_order: string;
  number: string;
  created_at: string;
  shop_status: string;
  order_state: string;
  customer: { name: string; email: string; phone: string; shipping_address: string };
  total_cents: number;
  payment_method: string;
  payment_kind_label: string;
  paid_cents: number;
  paid_at: string | null;
  payment_state: string;
  reservation_state: string;
  reservation_expires_at: string | null;
  review: string;
  ready_for_preparation: boolean;
  lines: Line[];
  history?: { action: string; at: string; user: string; data: Record<string, unknown> }[];
  payments?: { at: string; provider: string; method: string; status: string; amount_cents: number; transaction_id: string; verification: string; effect: string }[];
};
type Connection = { id: string; name: string; deadlines: Record<string, number> };
type Listing = { connections: Connection[]; items: Order[]; counts: Record<string, number> };

const euro = (cents: number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(cents / 100);
const when = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString("pt-PT", { timeZone: "Europe/Lisbon", dateStyle: "short", timeStyle: "short" }) : "—";
const qty = (value: string) => Number(value).toLocaleString("pt-PT");

const PAYMENT: Record<string, [string, BusinessTone]> = {
  pending: ["Por pagar", "warning"],
  paid: ["Pago", "success"],
  failed: ["Pagamento falhado", "danger"],
  refunded: ["Reembolsado", "neutral"],
  legacy: ["Anterior ao registo", "neutral"],
};
const RESERVATION: Record<string, [string, BusinessTone]> = {
  temporary: ["Temporária", "info"],
  confirmed: ["Confirmada", "success"],
  expired: ["Expirada", "neutral"],
  released: ["Libertada", "neutral"],
  review: ["Por rever", "danger"],
  legacy: ["Sem prazo (anterior)", "neutral"],
};
const ORDER_STATE: Record<string, string> = { registered: "Registada", partial: "Entrega parcial", delivered: "Entregue", cancelled: "Cancelada" };
const ACTIONS: Record<string, string> = {
  received: "Encomenda recebida e reservada",
  status: "Estado na loja alterado",
  payment_method_changed: "Meio de pagamento alterado",
  cancelled: "Cancelada na loja",
  reservation_expired: "Reserva expirada (prazo de pagamento)",
  reservation_released: "Reserva libertada",
  release_blocked: "Libertação bloqueada",
  reservation_confirmed_after_expiry: "Reservada de novo após pagamento tardio",
  payment_confirmed: "Pagamento confirmado: reserva definitiva",
  payment_late_reserved: "Pagamento tardio com stock: reservada",
  payment_late_no_stock: "Pagamento tardio sem stock: por rever",
  payment_duplicate_payment: "Segundo pagamento recebido",
  payment_amount_mismatch: "Montante do pagamento divergente",
  payment_failed: "Pagamento falhado",
  payment_refunded: "Reembolso registado",
  payment_recorded: "Pagamento registado",
  retry_reservation: "Nova tentativa de reserva",
  extend: "Prazo prolongado",
  release: "Reserva libertada pelo operador",
  resolve: "Revisão resolvida",
};
const KINDS: [string, string][] = [
  ["mbway", "MB WAY"],
  ["card", "Cartão"],
  ["multibanco", "Multibanco"],
  ["payshop", "Payshop"],
  ["transfer", "Transferência"],
  ["other", "Outro meio"],
];
const VERIFICATION: Record<string, string> = {
  provider_callback: "Confirmação autenticada do prestador",
  admin_confirmation: "Confirmada por administrador da loja",
};

function Badge({ map, value }: { map: Record<string, [string, BusinessTone]>; value: string }) {
  const [label, tone] = map[value] || [value, "neutral"];
  return <StatusBadge tone={tone}>{label}</StatusBadge>;
}

export default function OnlineOrders() {
  const [connection, setConnection] = useState("");
  const [payment, setPayment] = useState("");
  const [reservation, setReservation] = useState("");
  const [attention, setAttention] = useState(false);
  const [data, setData] = useState<Listing | null>(null);
  const [active, setActive] = useState<Order | null>(null);
  const [deadlines, setDeadlines] = useState<Record<string, string> | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (connection) params.set("connection_id", connection);
    if (payment) params.set("payment", payment);
    if (reservation) params.set("reservation", reservation);
    if (attention) params.set("attention", "true");
    setData(await api(`/webshop/online-orders?${params}`));
  }, [connection, payment, reservation, attention]);

  useEffect(() => {
    load().catch((e) => setError((e as Error).message));
  }, [load]);

  async function open(row: Order) {
    try {
      setActive(await api(`/webshop/online-orders/${row.id}`));
      setNote("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function act(action: "retry_reservation" | "extend" | "release" | "resolve") {
    if (!active) return;
    const labels = {
      retry_reservation: "Tentar reservar de novo",
      extend: "Prolongar o prazo da reserva",
      release: "Libertar a reserva",
      resolve: "Dar a revisão por resolvida",
    };
    if (action !== "retry_reservation" && note.trim().length < 3) {
      setError(`${labels[action]}: indique o motivo no campo «Motivo» (fica no histórico).`);
      return;
    }
    if (action === "release") {
      const proceed = await confirmAction({
        title: "Libertar a reserva?",
        message: "As unidades voltam ao stock livre e podem ser vendidas a outro cliente. A encomenda na loja não é cancelada.",
        confirmLabel: "Libertar reserva",
        tone: "danger",
      });
      if (!proceed) return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      setActive(await api(`/webshop/online-orders/${active.id}/${action}`, { note: note.trim() }));
      setNote("");
      setMessage(`${labels[action]}: feito.`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const selected = data?.connections.find((c) => c.id === (connection || data?.connections[0]?.id));

  async function saveDeadlines() {
    if (!selected || !deadlines) return;
    setBusy(true);
    setError("");
    try {
      const values = Object.fromEntries(Object.entries(deadlines).map(([k, v]) => [k, Math.round(Number(v.replace(",", ".")) * 60)]));
      await api(`/webshop/connections/${selected.id}/order-settings`, { deadlines: values }, { method: "PUT" });
      setDeadlines(null);
      setMessage("Prazos de pagamento guardados. Aplicam-se às novas encomendas e às mudanças de meio de pagamento.");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (data && !data.connections.length)
    return (
      <section className="panel">
        <h3>Encomendas online</h3>
        <p>Crie primeiro uma ligação Raizpura em Conectores.</p>
      </section>
    );

  const counts = data?.counts ?? {};
  return (
    <section className="panel online-orders">
      <h3>Encomendas online</h3>
      <p>
        Encomendas da loja online com os estados separados de encomenda, pagamento e reserva. A reserva é temporária até ao
        prazo do meio de pagamento e só fica confirmada com a confirmação autenticada do prestador (nunca pelo regresso do
        navegador). Uma reserva vencida é libertada automaticamente; um pagamento que chegue depois volta a reservar ou fica
        por rever.
      </p>
      {error && <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice>}
      {message && <Notice tone="success" onDismiss={() => setMessage("")}>{message}</Notice>}
      <div className="cash-grid">
        <MetricCard label="Por pagar (reserva temporária)" value={counts["reservation_state:temporary"] ?? 0} description="Aguardam o pagamento dentro do prazo" tone="info" />
        <MetricCard label="Prontas para preparação" value={counts["reservation_state:confirmed"] ?? 0} description="Pagas, com reserva confirmada" tone="success" />
        <MetricCard label="Expiradas" value={counts["reservation_state:expired"] ?? 0} description="Prazo terminado; stock libertado" tone="neutral" />
        <MetricCard label="Por rever" value={counts.attention ?? 0} description="Pagamento tardio sem stock, montante divergente, duplicado ou paga cancelada" tone="danger" />
      </div>

      <div className="toolbar">
        {(data?.connections.length ?? 0) > 1 && (
          <label>
            Ligação
            <select value={connection} onChange={(e) => setConnection(e.target.value)}>
              <option value="">Todas</option>
              {data?.connections.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </label>
        )}
        <label>
          Pagamento
          <select value={payment} onChange={(e) => setPayment(e.target.value)}>
            <option value="">Todos</option>
            {Object.entries(PAYMENT).map(([k, [label]]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Reserva
          <select value={reservation} onChange={(e) => setReservation(e.target.value)}>
            <option value="">Todas</option>
            {Object.entries(RESERVATION).map(([k, [label]]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
        </label>
        <label className="doc-check">
          <input type="checkbox" checked={attention} onChange={(e) => setAttention(e.target.checked)} /> Só as que pedem decisão
        </label>
        <ActionButton onClick={() => load().catch((e) => setError((e as Error).message))}>
          <RefreshCw size={16} aria-hidden="true" /> Atualizar
        </ActionButton>
        {selected && (
          <ActionButton onClick={() => setDeadlines(Object.fromEntries(KINDS.map(([k]) => [k, String((selected.deadlines[k] ?? 0) / 60)])))}>
            Prazos de pagamento
          </ActionButton>
        )}
      </div>

      {deadlines && selected && (
        <ManagedForm
          className="panel"
          onSubmit={(e) => {
            e.preventDefault();
            saveDeadlines();
          }}
        >
          <h4>Prazos de pagamento · {selected.name}</h4>
          <p>Tempo (em horas) que a reserva fica temporária à espera do pagamento, por meio. Mínimo 5 minutos, máximo 30 dias.</p>
          {KINDS.map(([key, label]) => (
            <label key={key}>
              {label} (horas)
              <input required inputMode="decimal" value={deadlines[key]} onChange={(e) => setDeadlines({ ...deadlines, [key]: e.target.value })} />
            </label>
          ))}
          <div className="actions">
            <ActionButton type="submit" className="primary" disabled={busy}>Guardar prazos</ActionButton>
            <ActionButton onClick={() => setDeadlines(null)}>Cancelar</ActionButton>
          </div>
        </ManagedForm>
      )}

      <DataTable<Order>
        label="encomendas online"
        rows={data?.items ?? []}
        loading={!data}
        rowKey={(r) => r.id}
        searchText={(r) => `${r.number} ${r.shop_order} ${r.customer.name} ${r.customer.email} ${r.lines.map((l) => l.description).join(" ")}`}
        exportName="encomendas-online"
        emptyText="Sem encomendas online com estes filtros."
        rowClassName={(r) => (r.review || r.reservation_state === "review" ? "row-danger" : undefined)}
        columns={[
          {
            id: "order",
            label: "Encomenda",
            sortValue: (r) => r.created_at,
            exportValue: (r) => `${r.number} / ${r.shop_order}`,
            render: (r) => (
              <>
                <ActionButton className="quiet" onClick={() => open(r)}>{r.number}</ActionButton>
                <small>Loja #{r.shop_order} · {when(r.created_at)}</small>
              </>
            ),
          },
          {
            id: "customer",
            label: "Cliente",
            sortValue: (r) => r.customer.name,
            exportValue: (r) => r.customer.name,
            render: (r) => (<>{r.customer.name}<small>{r.customer.email || r.customer.phone || "—"}</small></>),
          },
          {
            id: "lines",
            label: "Artigos",
            exportValue: (r) => r.lines.map((l) => `${qty(l.quantity)} × ${l.description}`).join("; "),
            render: (r) => (
              <ul className="compact-list">
                {r.lines.map((l) => (
                  <li key={l.article_id}>{qty(l.quantity)} × {l.description}</li>
                ))}
              </ul>
            ),
          },
          { id: "total", label: "Total", numeric: true, sortValue: (r) => r.total_cents, exportValue: (r) => r.total_cents / 100, render: (r) => euro(r.total_cents) },
          {
            id: "payment",
            label: "Pagamento",
            sortValue: (r) => r.payment_state,
            exportValue: (r) => `${r.payment_kind_label} · ${PAYMENT[r.payment_state]?.[0] ?? r.payment_state}`,
            render: (r) => (
              <>
                <Badge map={PAYMENT} value={r.payment_state} />
                <small>{r.payment_kind_label}{r.paid_at ? ` · ${when(r.paid_at)}` : ""}</small>
              </>
            ),
          },
          {
            id: "reservation",
            label: "Reserva",
            sortValue: (r) => r.reservation_state,
            exportValue: (r) => RESERVATION[r.reservation_state]?.[0] ?? r.reservation_state,
            render: (r) => (
              <>
                <Badge map={RESERVATION} value={r.reservation_state} />
                {r.reservation_expires_at && <small>Expira {when(r.reservation_expires_at)}</small>}
              </>
            ),
          },
          {
            id: "state",
            label: "Estado",
            sortValue: (r) => r.order_state,
            exportValue: (r) => ORDER_STATE[r.order_state] ?? r.order_state,
            render: (r) => (
              <>
                {ORDER_STATE[r.order_state] ?? r.order_state}
                <small>{r.ready_for_preparation ? "Pronta para preparação e faturação" : `Loja: ${r.shop_status}`}</small>
                {r.review && <small className="error">{r.review}</small>}
              </>
            ),
          },
        ]}
      />

      {active && (
        <section className="panel">
          <h4>
            {active.number} · loja #{active.shop_order} · {active.customer.name}
          </h4>
          <p>
            <Badge map={PAYMENT} value={active.payment_state} /> <Badge map={RESERVATION} value={active.reservation_state} />{" "}
            {active.ready_for_preparation && <StatusBadge tone="success">Pronta para preparação</StatusBadge>}
          </p>
          {active.review && <Notice tone="error">{active.review}</Notice>}
          <dl className="record-details">
            <dt>Total</dt>
            <dd>{euro(active.total_cents)}{active.paid_cents ? ` · pago ${euro(active.paid_cents)}` : ""}</dd>
            <dt>Meio de pagamento</dt>
            <dd>{active.payment_kind_label} {active.payment_method && <small>({active.payment_method})</small>}</dd>
            <dt>Reserva</dt>
            <dd>{RESERVATION[active.reservation_state]?.[0] ?? active.reservation_state}{active.reservation_expires_at ? ` até ${when(active.reservation_expires_at)}` : ""}</dd>
            <dt>Cliente</dt>
            <dd>{active.customer.email} {active.customer.phone} {active.customer.shipping_address && <small>{active.customer.shipping_address}</small>}</dd>
          </dl>
          <h5>Artigos</h5>
          <ul>
            {active.lines.map((l) => (
              <li key={l.article_id}>
                {qty(l.quantity)} × {l.description} · reservado {qty(l.reserved)} · entregue {qty(l.delivered)} · faturado {qty(l.invoiced)}
                {Number(l.cancelled) > 0 ? ` · cancelado ${qty(l.cancelled)}` : ""}
              </li>
            ))}
          </ul>
          <h5>Pagamentos</h5>
          {active.payments?.length ? (
            <ul>
              {active.payments.map((p, i) => (
                <li key={i}>
                  {when(p.at)} · {p.status === "paid" ? "Pago" : p.status === "failed" ? "Falhado" : "Reembolsado"} {euro(p.amount_cents)} · {p.provider}
                  {p.transaction_id ? ` · ${p.transaction_id}` : ""} · {VERIFICATION[p.verification] ?? p.verification} · {ACTIONS["payment_" + p.effect] ?? p.effect}
                </li>
              ))}
            </ul>
          ) : (
            <p>Sem pagamentos confirmados pelo prestador.</p>
          )}
          <h5>
            <History size={16} aria-hidden="true" /> Histórico
          </h5>
          <ul>
            {active.history?.map((h, i) => (
              <li key={i}>
                <strong>{when(h.at)}</strong> · {ACTIONS[h.action] ?? h.action} · {h.user}
                {typeof h.data.note === "string" && h.data.note ? ` · ${h.data.note}` : ""}
                {typeof h.data.reason === "string" && h.data.reason ? ` · ${h.data.reason}` : ""}
              </li>
            ))}
          </ul>
          <label>
            Motivo (obrigatório para prolongar, libertar ou resolver)
            <textarea maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <div className="actions">
            {["expired", "released", "review"].includes(active.reservation_state) && active.shop_status !== "cancelled" && (
              <ActionButton className="primary" disabled={busy} onClick={() => act("retry_reservation")}>Tentar reservar de novo</ActionButton>
            )}
            {active.reservation_state === "temporary" && (
              <ActionButton disabled={busy} onClick={() => act("extend")}>Prolongar prazo</ActionButton>
            )}
            {["temporary", "review", "legacy", "confirmed"].includes(active.reservation_state) && (
              <ActionButton className="danger" disabled={busy} onClick={() => act("release")}>Libertar reserva</ActionButton>
            )}
            {(active.review || active.reservation_state === "review") && (
              <ActionButton disabled={busy} onClick={() => act("resolve")}>Marcar como resolvida</ActionButton>
            )}
            <ActionButton onClick={() => setActive(null)}>Fechar</ActionButton>
          </div>
        </section>
      )}
    </section>
  );
}
