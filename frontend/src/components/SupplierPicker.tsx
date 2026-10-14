import { useId, useState } from "react";
import { api } from "../App";

type Supplier = { id: string; name: string; tax_number?: string };
const normalize = (text: string) => text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-PT").trim();

export default function SupplierPicker({ items, selectedName, loading, canCreate, onSelect, onCreated }: {
  items: Supplier[]; selectedName: string; loading: boolean; canCreate: boolean;
  onSelect: (supplier: Supplier | null) => void; onCreated: (supplier: Supplier) => void;
}) {
  const id = useId();
  const [query, setQuery] = useState(selectedName);
  const [open, setOpen] = useState(false), [index, setIndex] = useState(-1);
  const [creating, setCreating] = useState(false), [busy, setBusy] = useState(false);
  const [name, setName] = useState(""), [nif, setNif] = useState(""), [error, setError] = useState("");
  const matches = items.filter(item => normalize(`${item.name} ${item.tax_number || ""}`).includes(normalize(query))).slice(0, 50);
  function select(item: Supplier) { onSelect(item); setQuery(item.name); setOpen(false); setIndex(-1); }
  return <div className="supplier-picker" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false); }}>
    <label htmlFor={id}>Fornecedor</label>
    <div className="supplier-picker-input">
      <input id={id} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-list`} aria-activedescendant={open && index >= 0 && matches[index] ? `${id}-${index}` : undefined} autoComplete="off" placeholder="Escreva o nome ou NIF…" value={query} disabled={loading || busy} onFocus={() => setOpen(true)} onChange={e => { setQuery(e.target.value); onSelect(null); setIndex(-1); setOpen(true); }} onKeyDown={e => {
        if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setIndex(Math.min(index + 1, matches.length - 1)); }
        if (e.key === "ArrowUp") { e.preventDefault(); setIndex(Math.max(index - 1, 0)); }
        if (e.key === "Enter" && open) { e.preventDefault(); if (index >= 0 && matches[index]) select(matches[index]); }
        if (e.key === "Escape" && open) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
      }} />
      <button type="button" aria-label="Mostrar fornecedores" aria-expanded={open} disabled={loading || busy} onClick={() => setOpen(!open)}>▾</button>
    </div>
    {open && <div className="supplier-picker-dropdown">
      <ul id={`${id}-list`} role="listbox" aria-label="Fornecedores encontrados">{matches.map((item, i) => <li key={item.id} id={`${id}-${i}`} role="option" aria-selected={index === i} onMouseDown={e => e.preventDefault()} onClick={() => select(item)}>{item.name}{item.tax_number && <small> · NIF {item.tax_number}</small>}</li>)}{!matches.length && <li role="option" aria-disabled="true">Nenhum fornecedor encontrado</li>}</ul>
      {canCreate && <button type="button" onClick={() => { setName(query); setCreating(true); setOpen(false); setError(""); }}>＋ Criar novo</button>}
    </div>}
    {loading && <small role="status">A carregar fornecedores…</small>}
    {creating && <div className="supplier-picker-create">
      <label>Nome do novo fornecedor<input value={name} maxLength={300} disabled={busy} onChange={e => setName(e.target.value)} /></label>
      <label>NIF (opcional)<input value={nif} maxLength={40} disabled={busy} onChange={e => setNif(e.target.value)} /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions"><button type="button" disabled={busy} onClick={() => setCreating(false)}>Cancelar criação</button><button type="button" className="primary" disabled={busy || name.trim().length < 2} onClick={async () => {
        setBusy(true); setError("");
        try { const created = await api("/entities", { name: name.trim(), tax_number: nif.trim(), email: "", phone: "", is_supplier: true, is_customer: false, active: true, category_id: null }); onCreated(created); select(created); setCreating(false); }
        catch(e) { setError((e as Error).message); } finally { setBusy(false); }
      }}>{busy ? "A criar…" : "Criar e selecionar"}</button></div>
    </div>}
  </div>;
}
