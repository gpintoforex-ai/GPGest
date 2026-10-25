import RubricBadge from "./components/RubricBadge";
import PaymentIdentity from "./components/PaymentIdentity";
import { GitBranch, CircleCheck, Settings2 } from "lucide-react";
import MovementIdentity, { movementIdentity } from "./components/MovementIdentity";
import { Empty } from "./components/Form";
import { ActionButton } from "./components/InteractionUI";
import { formatEuro as money } from "./utils/format";
import { formatDateTime as date, movementLabels as labels } from "./utils/movements";

type Row = Record<string, any>;

export default function MovementTable({
  rows,
  categories,
  registers = [],
  onAttachments,
  onReverse,
}: {
  rows: Row[];
  categories: Row[];
  registers?: Row[];
  onAttachments: (r: Row) => void;
  onReverse?: (r: Row) => void;
}) {
  return rows.length ? (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Data</th>
            <th>Movimento</th>
            <th>Rubrica</th>
            <th>Documento</th>
            <th>Entidade</th>
            <th>Pagamento</th>
            <th className="numeric">Montante</th>
            <th className="movement-header-origin"><span><GitBranch size={15} aria-hidden="true" />Origem</span></th>
            <th className="movement-header-status"><span><CircleCheck size={15} aria-hidden="true" />Estado</span></th>
            <th className="movement-header-actions"><span><Settings2 size={15} aria-hidden="true" />Ações</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const identity = movementIdentity(m);
            const label = m.origin === "deposito_banco"
              ? m.direction > 0 ? "Depósito no banco" : "Estorno de depósito no banco"
              : m.origin === "deposito_modulo" && m.reversal_of ? "Estorno da retirada para depósito"
              : m.kind === "documento" ? "Documento de venda"
              : m.origin === "bank_statement" ? "Movimento bancário" : labels[m.kind] || m.kind;
            return (
            <tr key={m.id}>
              <td>
                {m.origin === "deposito_modulo" || m.origin === "deposito_banco"
                  ? <time dateTime={m.occurred_at} title="Hora não registada">{m.occurred_at.slice(0, 10).split("-").reverse().join("/")}</time>
                  : m.external && !/(Z|[+-]\d{2}:?\d{2})$/.test(m.occurred_at)
                  ? m.occurred_at
                      .slice(0, 10)
                      .split("-")
                      .reverse()
                      .join("/") +
                    " " +
                    m.occurred_at.slice(11, 16)
                  : date(m.occurred_at)}
              </td>
              <td>
                <strong>
                  {identity ? <MovementIdentity reversal={identity === "reversal"}>{identity === "reversal" && !label?.startsWith("Estorno") ? `Estorno · ${label}` : label}</MovementIdentity> : label}
                </strong>
                <small>
                  {m.entity || m.document || m.reference || "Manual"}
                </small>
              </td>
              <td>{(() => { const category = categories.find(row => row.id === m.category_id || (m.category_name && row.name.toLocaleLowerCase("pt-PT") === m.category_name.toLocaleLowerCase("pt-PT"))); return <RubricBadge name={m.category_name || category?.name || (m.external ? "Não indicada na origem" : "—")} icon={category?.icon} color={category?.color} />; })()}</td>
              <td>{m.document || "—"}</td>
              <td>{m.entity || "—"}</td>
              <td><PaymentIdentity name={m.payment_name || labels[m.payment] || m.payment || "—"} code={m.payment} /></td>
              <td className={["numeric", m.direction > 0 ? "positive" : "negative"].filter(Boolean).join(" ")}>
                {m.direction > 0 ? "+" : "−"}
                {money(m.amount_cents)}
              </td>
              <td>
                <span className="pill neutral">
                  {m.origin === "bank_statement" ? "Extrato bancário" : m.origin === "fecho_local" ? "Fecho de caixa" : m.origin === "deposito_banco" || m.origin === "deposito_modulo" ? "Depósito" : m.external ? "Wintouch" : "IAZELA"}
                </span>
                {(() => {
                  const bank = m.origin === "deposito_banco" || m.origin === "bank_statement" || m.bank_account_id || m.filter_register?.startsWith("bank:");
                  const name = bank
                    ? m.bank_account_name || m.bank_name || m.filter_register_name?.replace(/^Bancos? · /, "")
                    : registers.find(r => r.id === m.register_id)?.name || m.filter_register_name || m.account_name;
                  return <small>{bank ? "Banco" : "Caixa"} · {name || (bank ? "Conta por associar" : "Por identificar")}</small>;
                })()}
              </td>
              <td>
                <span
                  className={
                    "pill " +
                    (m.reversed_by || m.reversal_of ? "neutral" : "")
                  }
                >
                  {m.cancelled
                    ? "Anulado"
                    : m.kind === "documento"
                      ? "Documento"
                      : m.reversed_by
                        ? "Estornado"
                        : m.reversal_of
                          ? "Estorno"
                          : "Confirmado"}
                </span>
              </td>
              <td>
                {!m.external && m.origin !== "deposito_banco" && (
                  <ActionButton onClick={() => onAttachments(m.movement_id ? { ...m, id: m.movement_id } : m)}>
                    Anexos
                  </ActionButton>
                )}
                {m.external && <small>{m.origin === "bank_statement" ? "Gerido nos extratos bancários" : "Gerido na Wintouch"}</small>}
                {m.origin === "fecho_local" && <small>Gerido na folha de caixa</small>}
                {(m.origin === "deposito_banco" || m.origin === "deposito_modulo") && <small>Gerido em Depósitos</small>}
                {onReverse &&
                  !m.external &&
                  m.origin !== "deposito_banco" &&
                  m.origin !== "deposito_modulo" &&
                  m.origin !== "fecho_caixa" &&
                  m.origin !== "fecho_local" &&
                  !m.reversed_by &&
                  !m.reversal_of &&
                  !m.transfer_id && (
                    <ActionButton onClick={() => onReverse(m)}>Estornar</ActionButton>
                  )}
              </td>
            </tr>
          ); })}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty text="Sem movimentos registados" />
  );
}
