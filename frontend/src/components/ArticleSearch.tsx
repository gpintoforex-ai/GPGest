import { useEffect, useId, useMemo, useState } from "react";
import "./SupplierPicker.css";

// Artigo do catálogo: os restantes campos (fornecedor, preços…) passam para quem escolhe.
type Article = { id: string; code?: string; name?: string; barcodes?: string[]; active?: boolean } & Record<string, any>;
const normalize = (text: string) => text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-PT").trim();
const label = (a: Article) => [a.code, a.name].filter(Boolean).join(" · ");

/**
 * Pesquisa de artigos por código, nome ou EAN (sem acentos, várias palavras em qualquer ordem).
 * Um código ou EAN exato (leitor de códigos de barras) escolhe logo o artigo.
 */
export default function ArticleSearch({ articles, value, onPick, id: inputId, limit = 30, disabled = false }: {
  articles: Article[];
  value?: string | null;
  onPick: (article: Article | null) => void;
  id?: string;
  limit?: number;
  disabled?: boolean;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false), [index, setIndex] = useState(-1);
  useEffect(() => {
    const chosen = value ? articles.find(a => a.id === value) : null;
    if (chosen) setText(label(chosen));
  }, [value, articles]);
  const searchable = useMemo(() => articles.map(a => ({ a, text: normalize(`${a.code || ""} ${a.name || ""} ${(a.barcodes || []).join(" ")}`) })), [articles]);
  const words = normalize(text).split(/\s+/).filter(Boolean);
  const matches = words.length
    ? searchable.filter(item => words.every(w => item.text.includes(w)))
      // Primeiro o código exato e os ativos.
      .sort((x, y) => Number(normalize(y.a.code || "") === words.join(" ")) - Number(normalize(x.a.code || "") === words.join(" "))
        || Number(y.a.active !== false) - Number(x.a.active !== false))
      .slice(0, limit).map(item => item.a)
    : [];
  function select(article: Article) { onPick(article); setText(label(article)); setOpen(false); setIndex(-1); }
  return <div className="supplier-picker article-search" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false); }}>
    <div className="supplier-picker-input">
      <input id={inputId} type="search" role="combobox" aria-label="Pesquisar artigo por código, descrição ou EAN" aria-autocomplete="list"
        aria-expanded={open && words.length > 0} aria-controls={`${id}-list`}
        aria-activedescendant={open && index >= 0 && matches[index] ? `${id}-${index}` : undefined}
        autoComplete="off" placeholder="Código, descrição ou EAN" value={text} disabled={disabled}
        onFocus={() => setOpen(true)}
        onChange={e => {
          const next = e.target.value, exact = normalize(next);
          setText(next); setIndex(-1); setOpen(true);
          const found = exact ? articles.find(a => normalize(a.code || "") === exact || (a.barcodes || []).some(b => normalize(b) === exact) || normalize(label(a)) === exact) : undefined;
          if (found) { onPick(found); setText(label(found)); setOpen(false); }
          else onPick(null);
        }}
        onKeyDown={e => {
          if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setIndex(Math.min(index + 1, matches.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setIndex(Math.max(index - 1, 0)); }
          if (e.key === "Enter" && open && words.length) { e.preventDefault(); const item = matches[Math.max(index, 0)]; if (item) select(item); }
          if (e.key === "Escape" && open) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
        }} />
    </div>
    {open && words.length > 0 && <div className="supplier-picker-dropdown">
      <ul id={`${id}-list`} role="listbox" aria-label="Artigos encontrados">
        {matches.map((a, i) => <li key={a.id} id={`${id}-${i}`} role="option" aria-selected={index === i} onMouseDown={e => e.preventDefault()} onClick={() => select(a)}>
          <strong>{a.code}</strong> · {a.name}{a.active === false && <small> · inativo</small>}{a.barcodes?.[0] && <small> · EAN {a.barcodes[0]}</small>}
        </li>)}
        {!matches.length && <li role="option" aria-disabled="true">Nenhum artigo encontrado</li>}
        {matches.length === limit && <li role="option" aria-disabled="true">Mostrados os primeiros {limit}: escreva mais para afinar.</li>}
      </ul>
    </div>}
  </div>;
}
