import { confirmAction } from "./components/InteractionUI";
import { ActionButton, Notice } from "./components/InteractionUI";
import { useEffect, useState } from "react";
import {
  CircleCheck,
  CircleMinus,
  Landmark,
  LockKeyhole,
  Plus,
  X,
} from "lucide-react";
import { api } from "./lib/api";
import "./PaymentMethods.css";

export type PaymentMethod = {
  id: string;
  name: string;
  active: boolean;
  kind: "base" | "custom";
  required: boolean;
};

export type PaymentBankDefault = {
  method_id: string;
  store_id: string;
  account_id: string;
};

type Account = {
  id: string;
  store_id: string;
  name: string;
  bank: string;
  iban_last4: string;
};
type Store = { id: string; name: string };

export default function PaymentMethods({
  methods,
  stores,
  accounts,
  bankDefaults,
  store,
  local,
  manager,
  reload,
  onNew,
  onClose,
}: {
  methods: PaymentMethod[];
  stores: Store[];
  accounts: Account[];
  bankDefaults: PaymentBankDefault[];
  store: string;
  local: boolean;
  manager: boolean;
  reload: () => Promise<void>;
  onNew: () => void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedStore, setSelectedStore] = useState(
    store || stores[0]?.id || "",
  );
  useEffect(() => {
    setSelectedStore(
      (previous) =>
        store ||
        (stores.some((item) => item.id === previous)
          ? previous
          : stores[0]?.id || ""),
    );
  }, [store, stores]);
  const storeAccounts = accounts.filter(
    (account) => account.store_id === selectedStore,
  );
  function accountLabel(account: Account) {
    return `${account.bank} · ${account.name} · IBAN …${account.iban_last4}`;
  }
  async function saveBank(method: PaymentMethod, accountId: string) {
    setBusy(method.id);
    setError("");
    setNotice("");
    try {
      await api(`/cash/payment-methods/${method.id}/bank-default`, {
        store_id: selectedStore,
        account_id: accountId || null,
      });
      await reload();
      setNotice(
        `${method.name}: ${accountId ? "conta bancária por defeito guardada" : "associação bancária removida"}.`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  async function toggle(method: PaymentMethod) {
    if (method.active && !(await confirmAction({ title: "Inativar meio de pagamento?", message: `«${method.name}» deixará de ser considerado nas novas conferências da folha de caixa. O histórico será preservado.`, confirmLabel: "Inativar meio", tone: "warning" }))) return;
    setBusy(method.id);
    setError("");
    setNotice("");
    try {
      await api(`/cash/payment-methods/${method.id}/status`, {
        active: !method.active,
      });
      await reload();
      setNotice(
        `${method.name}: meio ${method.active ? "inativado" : "ativado"}. O histórico foi preservado.`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <>
      <div className="page-intro">
        <div>
          <h1>Meios de pagamento</h1>
          <p>
            Meios de pagamento e contas bancárias por defeito de cada
            estabelecimento.
          </p>
        </div>
        <div className="payment-method-page-actions">
          {manager && local && (
            <ActionButton
              className="primary"
              disabled={busy !== null}
              onClick={onNew}
            >
              <Plus size={18} aria-hidden="true" /> Novo meio de pagamento
            </ActionButton>
          )}
          <ActionButton
            type="button"
            className="ui-return"
            aria-label="Voltar ao local anterior"
            title="Voltar ao local anterior"
            disabled={busy !== null}
            onClick={onClose}
          >
            Voltar
          </ActionButton>
        </div>
      </div>
      <section className="panel" aria-busy={busy !== null}>
        {local && stores.length > 0 && (
          <div className="payment-method-bank-context">
            <label>
              Estabelecimento
              <select
                aria-label="Estabelecimento"
                value={selectedStore}
                disabled={busy !== null || Boolean(store)}
                onChange={(e) => {
                  setSelectedStore(e.target.value);
                  setNotice("");
                  setError("");
                }}
              >
                {stores.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <p>
              <Landmark size={18} aria-hidden="true" /> Associe a conta que
              recebe os pagamentos de Multibanco / TPA, MB WAY, transferência ou
              outros meios.
            </p>
          </div>
        )}
        {local && selectedStore && !storeAccounts.length && (
          <p className="footnote">
            Este estabelecimento ainda não tem contas bancárias. Adicione uma em
            «Contas bancárias» para a poder associar.
          </p>
        )}
        {error && (
          <Notice className="error" role="alert">
            {error}
          </Notice>
        )}
        {notice && (
          <Notice onDismiss={() => setNotice("")} className="success" role="status">
            {notice}
          </Notice>
        )}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Meio de pagamento</th>
                {local && <th>Conta bancária por defeito</th>}
                <th>Tipo</th>
                <th>Estado</th>
                {manager && local && <th>Ações</th>}
              </tr>
            </thead>
            <tbody>
              {methods.map((method) => (
                <tr key={method.id}>
                  <td>{method.name}</td>
                  {local && (
                    <td className="payment-method-bank-cell">
                      {method.id === "numerario" ? (
                        <span className="muted">
                          Não aplicável — dinheiro na gaveta
                        </span>
                      ) : manager ? (
                        <select
                          aria-label={`Conta bancária por defeito de ${method.name}`}
                          disabled={
                            busy !== null ||
                            !selectedStore ||
                            !storeAccounts.length
                          }
                          value={
                            bankDefaults.find(
                              (item) =>
                                item.method_id === method.id &&
                                item.store_id === selectedStore,
                            )?.account_id || ""
                          }
                          onChange={(e) => saveBank(method, e.target.value)}
                        >
                          <option value="">Sem conta associada</option>
                          {storeAccounts.map((account) => (
                            <option key={account.id} value={account.id}>
                              {accountLabel(account)}
                            </option>
                          ))}
                        </select>
                      ) : (
                        (() => {
                          const account = storeAccounts.find(
                            (account) =>
                              account.id ===
                              bankDefaults.find(
                                (item) =>
                                  item.method_id === method.id &&
                                  item.store_id === selectedStore,
                              )?.account_id,
                          );
                          return account
                            ? accountLabel(account)
                            : "Sem conta associada";
                        })()
                      )}
                    </td>
                  )}
                  <td>
                    <span className="pill neutral">
                      {method.kind === "custom" ? "Personalizado" : "Base"}
                    </span>
                  </td>
                  <td>
                    <span className={`pill${method.active ? "" : " neutral"}`}>
                      {method.active ? "Ativo" : "Inativo"}
                    </span>
                  </td>
                  {manager && local && (
                    <td>
                      {method.required ? (
                        <span
                          className="payment-method-required"
                          title="Necessário para conferir o dinheiro físico da gaveta"
                        >
                          <LockKeyhole size={16} aria-hidden="true" />
                          Obrigatório
                        </span>
                      ) : (
                        <ActionButton
                          type="button"
                          className={`payment-method-action ${method.active ? "is-deactivate" : "is-activate"}`}
                          disabled={busy !== null}
                          aria-label={`${method.active ? "Inativar" : "Ativar"} ${method.name}`}
                          onClick={() => toggle(method)}
                        >
                          {method.active ? (
                            <CircleMinus size={18} aria-hidden="true" />
                          ) : (
                            <CircleCheck size={18} aria-hidden="true" />
                          )}
                          {busy === method.id
                            ? "A guardar…"
                            : method.active
                              ? "Inativar"
                              : "Ativar"}
                        </ActionButton>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="footnote">
          {local
            ? "As associações bancárias ficam guardadas por estabelecimento. Alterar a conta por defeito não cria movimentos nem altera os saldos. Os meios inativos deixam de estar disponíveis para novos registos; o histórico mantém-se. O numerário é obrigatório para conferir a gaveta."
            : "Os meios dos documentos importados são definidos pelo ERP. Pode configurar meios personalizados no modo local autónomo."}
        </p>
      </section>
    </>
  );
}
