import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Field, Form } from "./components/Form";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
type Row = Record<string, any>;
export default function Warehouses({ stores }: { stores: Row[] }) {
  const [rows, setRows] = useState<Row[]>([]), [allowed, setAllowed] = useState(false), [search, setSearch] = useState(""), [editing, setEditing] = useState<Row | null>(null), [open, setOpen] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  async function load() { const data = await api("/stock/catalog"); setRows(data.warehouses); setAllowed(!!data.permissions.adjust_stock); }
  useEffect(() => { load().catch(e => setError(e.message)); }, []);
  return <><div className="page-intro"><div><h1>Armazéns</h1><p>Armazéns por estabelecimento, partilhados com a gestão de artigos e stock.</p></div>{allowed && <ActionButton className="primary" onClick={() => { setEditing(null); setOpen(true); }}>Criar armazém</ActionButton>}</div>
    {error && <Notice tone="error">{error}</Notice>}{notice && <p role="status">{notice}</p>}
    <section className="panel"><Field label="Pesquisar armazém"><input value={search} onChange={e => setSearch(e.target.value)} /></Field><div className="table-wrap"><table><thead><tr><th>Armazém</th><th>Estabelecimento</th>{allowed && <th>Ações</th>}</tr></thead><tbody>{rows.filter(w => `${w.name} ${stores.find(s => s.id === w.store_id)?.name || ""}`.toLowerCase().includes(search.toLowerCase())).map(w => <tr key={w.id}><td>{w.name}</td><td>{stores.find(s => s.id === w.store_id)?.name || "—"}</td>{allowed && <td><ActionButton onClick={() => { setEditing(w); setOpen(true); }}>Editar</ActionButton></td>}</tr>)}</tbody></table></div>{!rows.length && <p>Ainda não existem armazéns.</p>}</section>
    {open && <AppDialog className="modal" onRequestClose={() => setOpen(false)} aria-labelledby="warehouse-title"><h2 id="warehouse-title">{editing ? "Editar armazém" : "Criar armazém"}</h2><Form onSubmit={async values => { await api(editing ? `/stock/warehouses/${editing.id}` : "/stock/warehouses", { name: values.name, store_id: editing?.store_id || values.store_id }); await load(); setOpen(false); setNotice(editing ? "Armazém atualizado." : "Armazém criado."); }}><Field label="Nome do armazém"><input name="name" required maxLength={100} defaultValue={editing?.name || ""} /></Field><Field label="Estabelecimento"><select name="store_id" required disabled={!!editing} defaultValue={editing?.store_id || stores[0]?.id || ""}>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>{editing && <p>O estabelecimento mantém-se para preservar os movimentos e inventários associados.</p>}</Form></AppDialog>}
  </>;
}

