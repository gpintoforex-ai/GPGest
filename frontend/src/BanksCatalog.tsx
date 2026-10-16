import { useEffect, useState } from "react";
import { api, Field, Form } from "./App";

type Institution = { id: string; name: string; active: boolean };

export default function BanksCatalog({ manager, onChanged }: { manager: boolean; onChanged: () => Promise<void> }) {
  const [rows, setRows] = useState<Institution[]>([]);
  const [editing, setEditing] = useState<Institution | null>(null);
  const [opened, setOpened] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function refresh() {
    setRows(await api("/banks"));
  }

  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, []);

  return <>
    <div className="page-intro">
      <div>
        <h1>Bancos</h1>
        <p>Gira as instituições financeiras disponíveis para as contas bancárias.</p>
      </div>
      {manager && <button className="primary" onClick={() => { setEditing(null); setOpened(true); setError(""); setNotice(""); }}>Adicionar banco</button>}
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="success" role="status">{notice}</p>}
    <section className="panel">
      <div className="table-wrap">
        <table>
          <thead><tr><th>Instituição financeira</th><th>Estado</th>{manager && <th>Ações</th>}</tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id}>
            <td>{row.name}</td>
            <td>{row.active ? "Ativo" : "Inativo"}</td>
            {manager && <td><button onClick={() => { setEditing(row); setOpened(true); setError(""); setNotice(""); }}>Editar</button></td>}
          </tr>)}</tbody>
        </table>
      </div>
      {!rows.length && <p>Não existem instituições financeiras registadas.</p>}
    </section>
    {opened && <div className="overlay" onClick={(event) => { if (event.target === event.currentTarget) setOpened(false); }}>
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="bank-title">
        <div className="panel-title"><h2 id="bank-title">{editing ? "Editar banco" : "Adicionar banco"}</h2><button onClick={() => setOpened(false)}>Fechar</button></div>
        <Form onSubmit={async (values) => {
          const saved = await api(editing ? `/banks/${encodeURIComponent(editing.id)}` : "/banks", {
            name: values.name,
            ...(editing ? { active: values.active === "on" } : {}),
          });
          setRows((current) => {
            const updated = editing
              ? current.map((row) => row.id === editing.id ? saved : row)
              : [...current, saved];
            return updated.sort((left, right) => left.name.localeCompare(right.name, "pt-PT"));
          });
          setOpened(false);
          setNotice(editing ? "Banco atualizado." : "Banco adicionado.");
          try {
            await onChanged();
          } catch (err) {
            setError(`Banco guardado, mas não foi possível atualizar as contas bancárias: ${(err as Error).message}`);
          }
        }}>
          <Field label="Nome do banco"><input name="name" required minLength={2} maxLength={100} defaultValue={editing?.name || ""} /></Field>
          {editing && <label className="checkbox"><input name="active" type="checkbox" defaultChecked={editing.active} /> Ativo</label>}
        </Form>
      </section>
    </div>}
  </>;
}
