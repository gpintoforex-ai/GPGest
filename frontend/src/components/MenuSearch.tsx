import { useEffect, useId, useRef, useState } from "react";
import { Search, X, ArrowUpRight } from "lucide-react";
import "./MenuSearch.css";

export type MenuSearchItem = { id: string; label: string; group: string };
const normalize = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-PT");

export default function MenuSearch({ items, onNavigate }: {
  items: MenuSearchItem[];
  onNavigate: (page: string) => Promise<boolean>;
}) {
  const [query, setQuery] = useState(""), [open, setOpen] = useState(false), [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null), input = useRef<HTMLInputElement>(null), navigating = useRef(false);
  const id = useId();
  const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
  const matches = terms.length ? items.filter(item => terms.every(term => normalize(`${item.label} ${item.id} ${item.group}`).includes(term)))
    .sort((a, b) => Number(normalize(b.label).startsWith(normalize(query).trim())) - Number(normalize(a.label).startsWith(normalize(query).trim())) || a.label.localeCompare(b.label, "pt-PT")) : [];
  const results = matches.slice(0, 12);
  const selected = Math.min(active, Math.max(0, results.length - 1));
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !document.querySelector("dialog[open]")) {
        event.preventDefault(); input.current?.focus(); input.current?.select(); setOpen(true);
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", shortcut);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", shortcut); };
  }, []);
  useEffect(() => { if (open) document.getElementById(`${id}-option-${selected}`)?.scrollIntoView({ block: "nearest" }); }, [selected, open, id]);
  async function choose(item: MenuSearchItem) {
    if (navigating.current) return;
    navigating.current = true;
    try {
      if (await onNavigate(item.id)) {
        setOpen(false); setQuery(""); setActive(0);
        requestAnimationFrame(() => document.getElementById("main-workspace")?.focus({ preventScroll: true }));
      }
    } finally { navigating.current = false; }
  }
  return <div className="menu-search" ref={root} data-ignore-dirty onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false); }}>
    <div className="menu-search-input">
      <Search size={17} aria-hidden="true" />
      <input ref={input} type="search" role="combobox" aria-label="Pesquisar menus" placeholder="Pesquisar menus…" autoComplete="off"
        aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-results`} aria-activedescendant={open && results.length ? `${id}-option-${selected}` : undefined}
        value={query} onFocus={() => setOpen(true)} onChange={event => { setQuery(event.target.value); setActive(0); setOpen(true); }}
        onKeyDown={event => {
          if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); setOpen(true);
            if (results.length) setActive((selected + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length);
          }
          if (event.key === "Enter" && open && results[selected]) { event.preventDefault(); void choose(results[selected]); }
        }} />
      {query ? <button type="button" aria-label="Limpar pesquisa de menus" onClick={() => { setQuery(""); setActive(0); input.current?.focus(); }}><X size={15} /></button> : <kbd aria-hidden="true">Ctrl K</kbd>}
    </div>
    {open && <div className="menu-search-popover">
      <div id={`${id}-results`} role="listbox" aria-label="Menus encontrados">
        {results.map((item, index) => <div key={item.id} role="option" aria-selected={selected === index} id={`${id}-option-${index}`} className="menu-search-option"
          onPointerDown={event => event.preventDefault()} onMouseMove={() => setActive(index)} onClick={() => void choose(item)}>
          <span><strong>{item.label}</strong><small>{item.group}</small></span><ArrowUpRight size={16} aria-hidden="true" />
        </div>)}
      </div>
      <p className="menu-search-hint" role="status">{!terms.length ? "Escreva o nome do menu ou da área pretendida." : !matches.length ? "Nenhum menu encontrado." : `${matches.length} ${matches.length === 1 ? "menu encontrado" : "menus encontrados"}${matches.length > 12 ? " · Refine a pesquisa para ver os restantes" : " · ↑ ↓ para escolher · Enter para abrir"}`}</p>
    </div>}
  </div>;
}
