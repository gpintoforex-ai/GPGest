import { confirmAction } from "./components/InteractionUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { useEffect, useRef, useState } from "react";
import { api, Field, Form } from "./App";
import RubricSelect, { type RubricOption } from "./components/RubricSelect";
type Entity = { id: string; source: string; name: string; tax_number: string; address: string; locality: string; postal_code: string; email: string; phone: string; is_customer: boolean; is_supplier: boolean; active: boolean; category_id: string | null; has_movements?: boolean; editable?: boolean };
export default function Entities({ kind, manager, categories }: { kind: "Clientes" | "Fornecedores"; manager: boolean; categories: RubricOption[] }) {
  const [rows, setRows] = useState<Entity[]>([]), [search, setSearch] = useState(""), [error, setError] = useState(""), [editing, setEditing] = useState<Entity | null>(null), [opened, setOpened] = useState(false);
  const [notice, setNotice] = useState("");
  const [lookupMessage, setLookupMessage] = useState("");
  const [creationStep, setCreationStep] = useState<"nif" | "details">("nif");
  const [creationDraft, setCreationDraft] = useState<Partial<Entity>>({});
  const [categoryId, setCategoryId] = useState("");
  const lookupRequest = useRef(0);
  const lastLookupNif = useRef("");
  const [removing, setRemoving] = useState<string | null>(null);
  const refresh = async () => setRows(await api("/entities"));
  useEffect(() => { refresh().catch(e => setError(e.message)); setOpened(false); }, [kind]);
  const visible = rows.filter(e => (kind === "Clientes" ? e.is_customer : e.is_supplier) && [e.name, e.tax_number, e.email, e.phone].join(" ").toLowerCase().includes(search.toLowerCase()));
  const closeDialog = () => { lookupRequest.current++; lastLookupNif.current = ""; setLookupMessage(""); setOpened(false); };
  const lookupNif = async (input: HTMLInputElement) => {
    const nif = input.value.trim().toUpperCase().replace(/^PT/, "").replace(/[\s.-]/g, "");
    if (!/^\d{9}$/.test(nif)) return;
    if (lastLookupNif.current === nif) return;
    const requestId = ++lookupRequest.current;
    lastLookupNif.current = nif;
    setLookupMessage("A consultar o VIES…");
    try {
      const result = await api(`/entities/lookup?tax_number=${encodeURIComponent(input.value)}`) as { is_valid: boolean; name: string; address: string; locality: string; postal_code: string };
      if (requestId !== lookupRequest.current || !input.form) return;
      const fillIfEmpty = (field: string, value: string) => {
        const control = input.form?.elements.namedItem(field);
        if (control instanceof HTMLInputElement && !control.value.trim() && value) control.value = value;
      };
      if (result.is_valid) {
        fillIfEmpty("name", result.name);
        fillIfEmpty("address", result.address);
        fillIfEmpty("locality", result.locality);
        fillIfEmpty("postal_code", result.postal_code);
        setLookupMessage("NIF validado no VIES. Email, telefone e quaisquer campos não devolvidos pelo serviço devem ser preenchidos manualmente.");
      } else {
        setLookupMessage("O VIES não confirmou este NIF-IVA. Pode continuar e preencher os dados manualmente; este NIF pode não estar registado para operações intracomunitárias.");
      }
    } catch (err) {
      if (requestId === lookupRequest.current) setLookupMessage(`Não foi possível consultar o VIES: ${(err as Error).message} Pode preencher os dados manualmente.`);
    }
  };
  return <>
    <div className="page-intro"><div><h1>{kind}</h1><p>Entidades da empresa, importadas da Wintouch ou registadas na Iazela.</p></div>{manager && <div className="entity-create-controls"><ActionButton className="primary" onClick={() => { setEditing(null); setCreationDraft({}); setCategoryId(""); setCreationStep("nif"); setLookupMessage(""); lastLookupNif.current = ""; setOpened(true); }}>Criar {kind === "Clientes" ? "cliente" : "fornecedor"}</ActionButton><small>Ao indicar o NIF, o VIES pode preencher automaticamente o nome e a morada.</small></div>}</div>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {notice && <Notice onDismiss={() => setNotice("")} tone="success">{notice}</Notice>}
    <section className="panel"><Field label="Pesquisar por nome, NIF ou contacto"><input value={search} onChange={e => setSearch(e.target.value)} /></Field>
    <div className="table-wrap"><table><thead><tr><th>Nome</th><th>NIF</th><th>Email</th><th>Telefone</th><th>Rubrica</th><th>Tipo</th><th>Origem</th><th>Estado</th>{manager && <th>Ações</th>}</tr></thead><tbody>{visible.map(e => <tr key={e.id}><td>{e.name}</td><td>{e.tax_number || "—"}</td><td>{e.email || "—"}</td><td>{e.phone || "—"}</td><td>{categories.find(c => c.id === e.category_id)?.name || "?"}</td><td>{[e.is_customer && "Cliente", e.is_supplier && "Fornecedor"].filter(Boolean).join(" / ")}</td><td>{e.source === "GPGest" ? "Iazela" : e.source}</td><td>{e.active ? "Ativo" : "Desativado"}</td>{manager && <td><ActionButton disabled={removing !== null} onClick={() => { setEditing(e); setCategoryId(e.category_id || ""); setCreationStep("details"); setLookupMessage(""); lastLookupNif.current = ""; setOpened(true); }}>{e.editable === false ? "Informações locais" : "Editar"}</ActionButton> <ActionButton disabled={e.editable === false || removing !== null || (e.has_movements && !e.active)} onClick={async () => {
      if (removing) return;
      const action = e.has_movements ? "deactivate" : "remove";
      if (!(await confirmAction({
        title: action === "deactivate" ? "Inativar entidade?" : "Remover entidade da lista?",
        message: action === "deactivate"
          ? `«${e.name}» tem movimentos associados. Será inativada para preservar o histórico e deixará de poder ser usada em novos movimentos.`
          : `Pretende remover «${e.name}» da lista? O registo e o histórico ficam preservados. A Wintouch não será alterada.`,
        confirmLabel: action === "deactivate" ? "Inativar entidade" : "Remover entidade",
        tone: "danger",
      }))) return;
      setRemoving(e.id);
      try {
        const result = await api("/entities/" + encodeURIComponent(e.id) + "/remove", {}) as { action: "deactivated" | "removed" };
        if (result.action === "deactivated") {
          setRows(current => current.map(row => row.id === e.id ? { ...row, active: false } : row));
          setNotice(`«${e.name}» foi inativada. O histórico foi preservado.`);
        } else {
          setRows(current => current.filter(row => row.id !== e.id));
          setNotice(`«${e.name}» foi removida da lista. O histórico foi preservado.`);
        }
        setError("");
      }
      catch (err) { setError((err as Error).message); }
      finally { setRemoving(null); }
    }}>{e.has_movements ? (e.active ? "Inativar" : "Inativada") : "Remover"}</ActionButton></td>}</tr>)}</tbody></table></div>{!visible.length && <p>Não existem entidades para esta seleção.</p>}</section>
    {opened && <AppDialog autoOpen className="modal" onRequestClose={closeDialog} aria-labelledby="entity-title"><div className="panel-title"><h2 id="entity-title">{editing ? "Editar entidade" : creationStep === "nif" ? "Criar entidade — começar pelo NIF" : "Criar entidade"}</h2></div>
    {!editing && creationStep === "nif" ? <Form key="entity-nif-lookup" hideSubmit onSubmit={async v => {
      const taxNumber = String(v.tax_number || "").trim();
      const result = await api(`/entities/lookup?tax_number=${encodeURIComponent(taxNumber)}`) as { is_valid: boolean; name: string; address: string; locality: string; postal_code: string };
      const normalizedNif = taxNumber.toUpperCase().replace(/^PT/, "").replace(/[\s.-]/g, "");
      lastLookupNif.current = normalizedNif;
      setCreationDraft({ tax_number: taxNumber, name: result.is_valid ? result.name : "", address: result.is_valid ? result.address : "", locality: result.is_valid ? result.locality : "", postal_code: result.is_valid ? result.postal_code : "" });
      setLookupMessage(result.is_valid ? "NIF validado no VIES. Confirme os dados e complete os campos que faltam." : "O VIES não confirmou este NIF-IVA. Pode continuar e preencher os dados manualmente.");
      setCreationStep("details");
      return false;
    }}>
      <Field label="NIF"><input name="tax_number" maxLength={40} autoComplete="off" defaultValue={creationDraft.tax_number || ""} /></Field>
      <p>Indique o NIF para procurar automaticamente o nome e a morada no VIES. Também pode continuar sem consulta e preencher os dados manualmente.</p>
      <div className="ui-dialog-actions"><ActionButton type="button" onClick={event => {
        const taxNumber = String(new FormData(event.currentTarget.form!).get("tax_number") || "").trim();
        lastLookupNif.current = "";
        setCreationDraft({ tax_number: taxNumber });
        setLookupMessage("");
        setCreationStep("details");
      }}>Preencher manualmente</ActionButton><ActionButton type="submit" className="primary">Pesquisar no VIES</ActionButton></div>
    </Form> :     <Form key={editing ? `entity-${editing.id}` : "entity-details"} onSubmit={async v => { const saved = await api(editing ? "/entities/" + encodeURIComponent(editing.id) : "/entities", { ...(editing?.editable === false ? { name: editing.name, tax_number: editing.tax_number || "", address: editing.address || "", locality: editing.locality || "", postal_code: editing.postal_code || "", email: editing.email || "", phone: editing.phone || "", is_customer: !!editing.is_customer, is_supplier: !!editing.is_supplier, active: !!editing.active } : { name: v.name, tax_number: v.tax_number, address: v.address, locality: v.locality, postal_code: v.postal_code, email: v.email, phone: v.phone, is_customer: v.is_customer === "on", is_supplier: v.is_supplier === "on", active: v.active === "on" }), category_id: categoryId || null }); setRows(current => (editing ? current.map(row => row.id === editing.id ? { ...saved, has_movements: row.has_movements } : row) : [...current, saved]).sort((left, right) => left.name.localeCompare(right.name, "pt-PT"))); closeDialog(); setNotice(`«${saved.name}» foi guardado com sucesso.`); }}>
      <Field label="Nome"><input readOnly={editing?.editable === false} name="name" required minLength={2} maxLength={300} defaultValue={editing?.name || creationDraft.name || ""} /></Field>
      <div className="form-grid"><Field label="NIF"><input readOnly={editing?.editable === false} name="tax_number" maxLength={40} defaultValue={editing?.tax_number || creationDraft.tax_number || ""} onChange={() => { lookupRequest.current++; setLookupMessage(""); lastLookupNif.current = ""; }} onBlur={event => { if (editing?.editable !== false) void lookupNif(event.currentTarget); }} /></Field><Field label="Email"><input readOnly={editing?.editable === false} name="email" type="email" maxLength={200} defaultValue={editing?.email || creationDraft.email || ""} /></Field><Field label="Telefone"><input readOnly={editing?.editable === false} name="phone" maxLength={60} defaultValue={editing?.phone || creationDraft.phone || ""} /></Field></div>
      {lookupMessage && <Notice role="status">{lookupMessage}</Notice>}
      <Field label="Morada"><input readOnly={editing?.editable === false} name="address" maxLength={500} defaultValue={editing?.address || creationDraft.address || ""} /></Field>
      <div className="form-grid"><Field label="Localidade"><input readOnly={editing?.editable === false} name="locality" maxLength={120} defaultValue={editing?.locality || creationDraft.locality || ""} /></Field><Field label="Código postal"><input readOnly={editing?.editable === false} name="postal_code" maxLength={20} defaultValue={editing?.postal_code || creationDraft.postal_code || ""} /></Field></div>
      <Field label="Rubrica do fornecedor"><RubricSelect categories={categories} value={categoryId} onChange={setCategoryId} disabled={editing?.editable === false} /></Field>
      <label className="checkbox"><input type="checkbox" disabled={editing?.editable === false} name="is_customer" defaultChecked={editing ? !!editing.is_customer : kind === "Clientes"} /> Cliente</label>
      <label className="checkbox"><input type="checkbox" disabled={editing?.editable === false} name="is_supplier" defaultChecked={editing ? !!editing.is_supplier : kind === "Fornecedores"} /> Fornecedor</label>
      <label className="checkbox"><input type="checkbox" disabled={editing?.editable === false} name="active" defaultChecked={editing ? editing.active : true} /> Ativo</label>
      {editing?.source.startsWith("Wintouch") && <p>{editing.editable === false ? "Os dados principais pertencem ao ERP. A rubrica associada fica guardada localmente." : "A edição fica guardada na Iazela e preservada nas sincronizações, sem alterar o ERP."}</p>}
    </Form>}</AppDialog>}
  </>;
}
