import { confirmAction } from "./components/InteractionUI";
import { ActionButton, AppDialog, ManagedForm, Notice } from "./components/InteractionUI";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CircleCheck,
  CircleMinus,
  Pencil,
  Plus,
  UserPlus,
  X,
} from "lucide-react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import "./Employees.css";

type Store = { id: string; name: string };
type Employee = {
  id: string;
  name: string;
  email: string;
  phone: string;
  active: boolean;
  store_ids: string[];
};
type Account = {
  id: string;
  name: string;
  email: string;
  role: string;
  active?: boolean;
  store_ids?: string[];
};
const roles: Record<string, string> = {
  administrador: "Administrador",
  gestor: "Gestor",
  operador: "Operador",
  consulta: "Consulta",
};
const normalize = (value: string) =>
  value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pt-PT");

function EmployeeDialog({
  employee,
  stores,
  store,
  onCancel,
  onSaved,
}: {
  employee: Employee | null;
  stores: Store[];
  store: string;
  onCancel: () => void;
  onSaved: (employee: Employee) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const [name, setName] = useState(employee?.name || "");
  const [email, setEmail] = useState(employee?.email || "");
  const [phone, setPhone] = useState(employee?.phone || "");
  const [storeIds, setStoreIds] = useState(
    employee?.store_ids || [store || stores[0]?.id].filter(Boolean),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => {
      element.close();
      previous?.focus();
    };
  }, []);
  return createPortal(
    <AppDialog busy={busy}
      ref={dialog}
      className="doc-dialog employee-dialog"
      aria-labelledby={title}
      onRequestClose={() => {

        if (!busy) onCancel();
      }}
    >
      <div className="panel-title">
        <h2 id={title}>
          {employee ? "Editar funcionário" : "Novo funcionário"}
        </h2>

      </div>
      <p className="footnote">
        Este registo não cria credenciais de entrada na IAZELA.
      </p>
      <ManagedForm
        className="doc-form"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || !storeIds.length || name.trim().length < 2) return;
          setBusy(true);
          setError("");
          try {
            const saved = await api(
              employee ? `/employees/${employee.id}` : "/employees",
              {
                name: name.trim(),
                email: email.trim(),
                phone: phone.trim(),
                store_ids: storeIds,
              },
            );
            onSaved(saved);
          } catch (e) {
            setError((e as Error).message);
            setBusy(false);
          }
        }}
      >
        <Field label="Nome">
          <input
            autoFocus
            required
            minLength={2}
            maxLength={100}
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="Email (opcional)">
          <input
            type="email"
            maxLength={200}
            value={email}
            disabled={busy}
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        <Field label="Telefone (opcional)">
          <input
            type="tel"
            maxLength={60}
            value={phone}
            disabled={busy}
            onChange={(event) => setPhone(event.target.value)}
          />
        </Field>
        <fieldset disabled={busy} className="employee-stores">
          <legend>Estabelecimentos</legend>
          {stores.map((item) => (
            <label className="doc-check" key={item.id}>
              <input
                type="checkbox"
                checked={storeIds.includes(item.id)}
                onChange={(event) =>
                  setStoreIds((previous) =>
                    event.target.checked
                      ? [...previous, item.id]
                      : previous.filter((id) => id !== item.id),
                  )
                }
              />
              {item.name}
            </label>
          ))}
        </fieldset>
        {!storeIds.length && (
          <p className="footnote">Selecione pelo menos um estabelecimento.</p>
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
            className="primary"
            disabled={busy || name.trim().length < 2 || !storeIds.length}
          >
            {busy ? "A guardar…" : "Guardar funcionário"}
          </ActionButton>
        </div>
      </ManagedForm>
    </AppDialog>,
    document.body,
  );
}

export default function Employees({
  users,
  stores,
  store,
  admin,
  onNewUser,
}: {
  users: Account[];
  stores: Store[];
  store: string;
  admin: boolean;
  onNewUser: () => void;
}) {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("all");
  const [editing, setEditing] = useState<Employee | null>(null);
  const [opened, setOpened] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api("/employees")
      .then((result) => {
        if (active) setEmployees(result);
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  const rows = [
    ...users.map((user) => ({
      key: `user:${user.id}`,
      name: user.name,
      email: user.email,
      phone: "",
      store_ids: user.store_ids || [],
      active: user.active !== false,
      account: true,
      role: roles[user.role] || user.role,
      allStores: user.role === "administrador",
      employee: null as Employee | null,
    })),
    ...employees.map((employee) => ({
      key: `employee:${employee.id}`,
      ...employee,
      account: false,
      role: "Sem acesso",
      allStores: false,
      employee,
    })),
  ]
    .filter(
      (row) =>
        (!store || row.allStores || row.store_ids.includes(store)) &&
        (kind === "all" || row.account === (kind === "with")) &&
        normalize([row.name, row.email, row.phone].join(" ")).includes(
          normalize(search.trim()),
        ),
    )
    .sort((a, b) => a.name.localeCompare(b.name, "pt-PT"));
  async function toggle(employee: Employee) {
    if (employee.active && !(await confirmAction({ title: "Inativar funcionário?", message: `«${employee.name}» deixará de estar disponível para novas atribuições. O histórico será preservado.`, confirmLabel: "Inativar funcionário", tone: "warning" }))) return;
    setBusy(employee.id);
    setError("");
    setNotice("");
    try {
      const saved = await api(`/employees/${employee.id}/status`, {
        active: !employee.active,
      });
      setEmployees((previous) =>
        previous.map((item) => (item.id === saved.id ? saved : item)),
      );
      setNotice(
        `${employee.name}: funcionário ${saved.active ? "ativado" : "inativado"}.`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="employees-page">
      <div className="page-intro">
        <div>
          <h1>Operadores / funcionários</h1>
          <p>
            Utilizadores com acesso à IAZELA e funcionários sem acesso à
            aplicação.
          </p>
        </div>
        {admin && (
          <div className="actions">
            <ActionButton disabled={busy !== null} onClick={onNewUser}>
              <UserPlus size={18} aria-hidden="true" />
              Novo utilizador
            </ActionButton>
            <ActionButton
              className="primary"
              disabled={busy !== null || !stores.length}
              onClick={() => {
                setEditing(null);
                setOpened(true);
              }}
            >
              <Plus size={18} aria-hidden="true" />
              Novo funcionário
            </ActionButton>
          </div>
        )}
      </div>
      {error && (
        <div>
          <Notice className="error" role="alert">
            {error}
          </Notice>
          <ActionButton onClick={() => setAttempt((value) => value + 1)}>
            Tentar novamente
          </ActionButton>
        </div>
      )}
      {notice && (
        <Notice onDismiss={() => setNotice("")} className="success" role="status">
          {notice}
        </Notice>
      )}
      <section className="panel" aria-busy={loading || busy !== null}>
        <div className="employee-filters">
          <Field label="Pesquisar por nome ou contacto">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </Field>
          <Field label="Tipo de registo">
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
            >
              <option value="all">Todos</option>
              <option value="with">Com acesso à IAZELA</option>
              <option value="without">Sem acesso à IAZELA</option>
            </select>
          </Field>
        </div>
        {loading && <p role="status">A carregar funcionários…</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Nome</th>
                <th>Email</th>
                <th>Telefone</th>
                <th>Estabelecimentos</th>
                <th>Perfil / acesso</th>
                <th>Estado</th>
                {admin && <th>Ações</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td>
                    <strong>{row.name}</strong>
                    <small>{row.account ? "Utilizador" : "Funcionário"}</small>
                  </td>
                  <td>{row.email || "—"}</td>
                  <td>{row.phone || "—"}</td>
                  <td>
                    {row.allStores
                      ? "Todos os estabelecimentos"
                      : stores
                          .filter((item) => row.store_ids.includes(item.id))
                          .map((item) => item.name)
                          .join(", ") || "Sem estabelecimento"}
                  </td>
                  <td>
                    <span className={`pill${row.account ? "" : " neutral"}`}>
                      {row.role}
                    </span>
                  </td>
                  <td>
                    <span className={`pill${row.active ? "" : " neutral"}`}>
                      {row.active ? "Ativo" : "Inativo"}
                    </span>
                  </td>
                  {admin && (
                    <td>
                      {row.employee ? (
                        <div className="employee-row-actions">
                          <ActionButton
                            disabled={busy !== null}
                            aria-label={`Editar ${row.name}`}
                            onClick={() => {
                              setEditing(row.employee);
                              setOpened(true);
                            }}
                          >
                            <Pencil size={16} aria-hidden="true" />
                            Editar
                          </ActionButton>
                          <ActionButton
                            disabled={busy !== null}
                            className={
                              row.active ? "danger" : "employee-activate"
                            }
                            aria-label={`${row.active ? "Inativar" : "Ativar"} ${row.name}`}
                            onClick={() => toggle(row.employee!)}
                          >
                            {row.active ? (
                              <CircleMinus size={16} aria-hidden="true" />
                            ) : (
                              <CircleCheck size={16} aria-hidden="true" />
                            )}
                            {row.active ? "Inativar" : "Ativar"}
                          </ActionButton>
                        </div>
                      ) : (
                        "—"
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && !rows.length && (
          <p>Não existem registos para esta seleção.</p>
        )}
        {admin && !stores.length && (
          <p className="footnote">
            Crie primeiro um estabelecimento em Organização para registar
            funcionários.
          </p>
        )}
      </section>
      {opened && (
        <EmployeeDialog
          employee={editing}
          stores={stores}
          store={store}
          onCancel={() => setOpened(false)}
          onSaved={(saved) => {
            setEmployees((previous) => [
              ...previous.filter((item) => item.id !== saved.id),
              saved,
            ]);
            setOpened(false);
            setError("");
            setNotice(
              editing
                ? "Funcionário atualizado."
                : "Funcionário criado sem acesso à aplicação.",
            );
          }}
        />
      )}
    </div>
  );
}
