import { useEffect, useState } from "react";
import { api, Field, Form } from "./App";
type Entity = { id: string; source: string; name: string; tax_number: string; email: string; phone: string; is_customer: boolean; is_supplier: boolean; active: boolean; category_id: string | null; editable?: boolean };
export default function Entities({ kind, manager, categories }: { kind: "Clientes" | "Fornecedores"; manager: boolean; categories: { id: string; name: string }[] }) {
  const [rows, setRows] = useState<Entity[]>([]), [search, setSearch] = useState(""), [error, setError] = useState(""), [editing, setEditing] = useState<Entity | null>(null), [opened, setOpened] = useState(false);
  const refresh = async () => setRows(await api("/entities"));
  useEffect(() => { refresh().catch(e => setError(e.message)); setOpened(false); }, [kind]);
  const visible = rows.filter(e => (kind === "Clientes" ? e.is_customer : e.is_supplier) && [e.name, e.tax_number, e.email, e.phone].join(" ").toLowerCase().includes(search.toLowerCase()));
  return <>
    <div className="page-intro"><div><h1>{kind}</h1><p>Entidades da empresa, importadas da Wintouch ou registadas no GPGest.</p></div>{manager && <button className="primary" onClick={() => { setEditing(null); setOpened(true); }}>Criar {kind === "Clientes" ? "cliente" : "fornecedor"}</button>}</div>
    {error && <p className="error" role="alert">{error}</p>}
    <section className="panel"><Field label="Pesquisar por nome, NIF ou contacto"><input value={search} onChange={e => setSearch(e.target.value)} /></Field>
    <div className="table-wrap"><table><thead><tr><th>Nome</th><th>NIF</th><th>Email</th><th>Telefone</th><th>Rubrica</th><th>Tipo</th><th>Origem</th><th>Estado</th>{manager && <th>Ações</th>}</tr></thead><tbody>{visible.map(e => <tr key={e.id}><td>{e.name}</td><td>{e.tax_number || "—"}</td><td>{e.email || "—"}</td><td>{e.phone || "—"}</td><td>{categories.find(c => c.id === e.category_id)?.name || "?"}</td><td>{[e.is_customer && "Cliente", e.is_supplier && "Fornecedor"].filter(Boolean).join(" / ")}</td><td>{e.source}</td><td>{e.active ? "Ativo" : "Desativado"}</td>{manager && <td><button onClick={() => { setEditing(e); setOpened(true); }}>{e.editable === false ? "Informações locais" : "Editar"}</button> <button disabled={e.editable === false} onClick={async () => {
      if (!window.confirm(`Remover ${e.name} da lista de clientes e fornecedores? O registo e o histórico ficam preservados. A Wintouch não será alterada.`)) return;
      try { await api("/entities/" + encodeURIComponent(e.id) + "/remove", {}); await refresh(); setError(""); }
      catch (err) { setError((err as Error).message); }
    }}>Remover</button></td>}</tr>)}</tbody></table></div>{!visible.length && <p>Não existem entidades para esta seleção.</p>}</section>
    {opened && <div className="overlay" onClick={e => { if (e.target === e.currentTarget) setOpened(false); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="entity-title"><div className="panel-title"><h2 id="entity-title">{editing ? "Editar entidade" : "Criar entidade"}</h2><button onClick={() => setOpened(false)}>Fechar</button></div>
    <Form onSubmit={async v => { await api(editing ? "/entities/" + encodeURIComponent(editing.id) : "/entities", { ...(editing?.editable === false ? { name: editing.name, tax_number: editing.tax_number || "", email: editing.email || "", phone: editing.phone || "", is_customer: !!editing.is_customer, is_supplier: !!editing.is_supplier, active: !!editing.active } : { name: v.name, tax_number: v.tax_number, email: v.email, phone: v.phone, is_customer: v.is_customer === "on", is_supplier: v.is_supplier === "on", active: v.active === "on" }), category_id: v.category_id || null }); await refresh(); setOpened(false); }}>
      <Field label="Nome"><input readOnly={editing?.editable === false} name="name" required minLength={2} maxLength={300} defaultValue={editing?.name || ""} /></Field>
      <div className="form-grid"><Field label="NIF"><input readOnly={editing?.editable === false} name="tax_number" maxLength={40} defaultValue={editing?.tax_number || ""} /></Field><Field label="Email"><input readOnly={editing?.editable === false} name="email" type="email" maxLength={200} defaultValue={editing?.email || ""} /></Field><Field label="Telefone"><input readOnly={editing?.editable === false} name="phone" maxLength={60} defaultValue={editing?.phone || ""} /></Field></div>
      <Field label="Rubrica do fornecedor"><select name="category_id" defaultValue={editing?.category_id || ""}><option value="">Sem rubrica definida</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      <label className="checkbox"><input type="checkbox" disabled={editing?.editable === false} name="is_customer" defaultChecked={editing ? !!editing.is_customer : kind === "Clientes"} /> Cliente</label>
      <label className="checkbox"><input type="checkbox" disabled={editing?.editable === false} name="is_supplier" defaultChecked={editing ? !!editing.is_supplier : kind === "Fornecedores"} /> Fornecedor</label>
      <label className="checkbox"><input type="checkbox" disabled={editing?.editable === false} name="active" defaultChecked={editing ? editing.active : true} /> Ativo</label>
      {editing?.source.startsWith("Wintouch") && <p>{editing.editable === false ? "Os dados principais pertencem ao ERP. A rubrica associada fica guardada localmente." : "A edição fica guardada no GPGest e preservada nas sincronizações, sem alterar o ERP."}</p>}
    </Form></section></div>}
  </>;
}
