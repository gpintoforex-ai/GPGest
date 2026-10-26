import { ActionButton } from "./InteractionUI";
import { useEffect, useId, useState } from "react";
import "./SupplierPicker.css";

type Supplier = { id: string; name: string; tax_number?: string };
const normalize = (text: string) => text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-PT").trim();

export default function SupplierPicker({ items, selectedName, loading, canCreate, onSelect, onRequestCreate }: {
  items: Supplier[]; selectedName: string; loading: boolean; canCreate: boolean;
  onSelect: (supplier: Supplier | null) => void;
  onRequestCreate: (name: string) => void;
}) {
  const id = useId();
  const [query, setQuery] = useState(selectedName);
  const [open, setOpen] = useState(false), [index, setIndex] = useState(-1);
  useEffect(() => {
    if (selectedName) setQuery(selectedName);
  }, [selectedName]);
  const matches = items.filter(item => normalize(`${item.name} ${item.tax_number || ""}`).includes(normalize(query))).slice(0, 50);
  function select(item: Supplier) { onSelect(item); setQuery(item.name); setOpen(false); setIndex(-1); }
  return <div className="supplier-picker" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false); }}>
    <label htmlFor={id}>Fornecedor</label>
    <div className="supplier-picker-input">
      <input id={id} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-list`} aria-activedescendant={open && index >= 0 && matches[index] ? `${id}-${index}` : undefined} autoComplete="off" placeholder="Escreva o nome ou NIF…" value={query} disabled={loading} onFocus={() => setOpen(true)} onChange={e => { setQuery(e.target.value); onSelect(null); setIndex(-1); setOpen(true); }} onKeyDown={e => {
        if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setIndex(Math.min(index + 1, matches.length - 1)); }
        if (e.key === "ArrowUp") { e.preventDefault(); setIndex(Math.max(index - 1, 0)); }
        if (e.key === "Enter" && open) { e.preventDefault(); if (index >= 0 && matches[index]) select(matches[index]); }
        if (e.key === "Escape" && open) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
      }} />
      <ActionButton type="button" aria-label="Mostrar fornecedores" aria-expanded={open} disabled={loading} onClick={() => setOpen(!open)}>▾</ActionButton>
    </div>
    {open && <div className="supplier-picker-dropdown">
      <ul id={`${id}-list`} role="listbox" aria-label="Fornecedores encontrados">{matches.map((item, i) => <li key={item.id} id={`${id}-${i}`} role="option" aria-selected={index === i} onMouseDown={e => e.preventDefault()} onClick={() => select(item)}>{item.name}{item.tax_number && <small> · NIF {item.tax_number}</small>}</li>)}{!matches.length && <li role="option" aria-disabled="true">Nenhum fornecedor encontrado</li>}</ul>
      {canCreate && <ActionButton type="button" onClick={() => { onRequestCreate(query); setOpen(false); }}>＋ Criar novo</ActionButton>}
    </div>}
    {loading && <small role="status">A carregar fornecedores…</small>}
  </div>;
}
