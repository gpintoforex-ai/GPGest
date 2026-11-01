import { useEffect, useId, useRef, useState } from "react";
import { ArrowUpRight, CircleX, Search } from "lucide-react";
import { api } from "../lib/api";
import { ActionButton } from "./InteractionUI";
import { QuickFilters } from "./OverviewUI";
import "./GlobalSearch.css";

type Target = { page: string; params: Record<string, string> };
type Result = { id: string; label: string; detail: string; target: Target };
type Group = { id: string; label: string; more?: boolean; items: Result[] };
type MenuItem = { id: string; label: string; group: string };
const normalize = (text: string) =>
  text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pt-PT");
const categories = [
  { id: "all", label: "Tudo" },
  { id: "menus", label: "Menus" },
  { id: "articles", label: "Artigos" },
  { id: "customers", label: "Clientes" },
  { id: "suppliers", label: "Fornecedores" },
  { id: "orders", label: "Encomendas" },
  { id: "documents", label: "Documentos" },
];

export default function GlobalSearch({
  store,
  menus,
  onMenu,
  onNavigate,
}: {
  store: string;
  menus: MenuItem[];
  onMenu: (id: string) => Promise<boolean>;
  onNavigate: (
    page: string,
    params: Record<string, string>,
  ) => Promise<boolean>;
}) {
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState("all"),
    [open, setOpen] = useState(false),
    [active, setActive] = useState(0),
    [retry, setRetry] = useState(0);
  const [remote, setRemote] = useState<{
    key: string;
    groups: Group[];
    error: string;
    loading: boolean;
  }>({ key: "", groups: [], error: "", loading: false });
  const root = useRef<HTMLDivElement>(null),
    input = useRef<HTMLInputElement>(null),
    navigating = useRef(false);
  const id = useId();
  const trimmed = query.trim().replace(/\s+/g, " "),
    key = store + ":" + trimmed;
  const terms = normalize(trimmed).split(/\s+/).filter(Boolean);
  const needsRecords = open && category !== "menus" && trimmed.length >= 2;
  const matchedMenus = terms.length
    ? menus.filter((item) =>
        terms.every((term) =>
              normalize(`${item.label} ${item.id} ${item.group}`).includes(term),
        ),
      )
    : [];
  const menuGroup: Group = {
    id: "menus",
    label: "Menus",
    more: matchedMenus.length > 8,
    items: matchedMenus
      .slice(0, 8)
      .map((item) => ({
        id: item.id,
        label: item.label,
        detail: item.group,
        target: { page: "", params: {} },
      })),
  };
  const current =
    remote.key === key
      ? remote
      : { key, groups: [], error: "", loading: trimmed.length >= 2 };
  const groups = [menuGroup, ...current.groups].filter(
    (group) =>
      (category === "all" || group.id === category) && group.items.length > 0,
  );
  const results = groups.flatMap((group) =>
    group.items.map((item) => ({ ...item, kind: group.id })),
  );
  const index = Math.min(active, Math.max(0, results.length - 1));
  useEffect(() => {
    let alive = true;
    if (!needsRecords) return;
    setRemote({ key, groups: [], error: "", loading: true });
    const timer = window.setTimeout(() => {
      void api(
        `/workspace/search?${new URLSearchParams({ q: trimmed, store_id: store })}`,
        undefined,
        { preserveDraft: true, timeoutMs: 15_000 },
      )
        .then((data) => {
          if (data.read_only !== true || !Array.isArray(data.groups))
            throw new Error(
              "A pesquisa de registos não está disponível nesta versão do servidor.",
            );
          if (alive)
            setRemote({ key, groups: data.groups, error: "", loading: false });
        })
        .catch((error) => {
          if (alive)
            setRemote({
              key,
              groups: [],
              error: error.message,
              loading: false,
            });
        });
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [trimmed, store, needsRecords, retry]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const shortcut = (event: KeyboardEvent) => {
      if (
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "k" &&
        !document.querySelector("dialog[open]")
      ) {
        event.preventDefault();
        setOpen(true);
        input.current?.focus();
        input.current?.select();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", shortcut);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", shortcut);
    };
  }, []);
  useEffect(() => {
    if (open)
      document
        .getElementById(`${id}-option-${index}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [open, index, results.length, id]);
  async function choose(item: Result & { kind: string }) {
    if (navigating.current) return;
    navigating.current = true;
    try {
      const accepted =
        item.kind === "menus"
          ? await onMenu(item.id)
          : await onNavigate(item.target.page, {
              ...item.target.params,
              "consulta.chave": crypto.randomUUID(),
            });
      if (accepted) {
        setOpen(false);
        setQuery("");
        setActive(0);
        requestAnimationFrame(() => {
          if (!document.querySelector("dialog[open]"))
            document
              .getElementById("main-workspace")
              ?.focus({ preventScroll: true });
        });
      } else setOpen(true);
    } finally {
      navigating.current = false;
    }
  }
  let optionIndex = 0;
  return (
    <div
      className="workspace-search"
      ref={root}
      data-ignore-dirty
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null))
          setOpen(false);
      }}
    >
      <div className="workspace-search-field">
        <Search size={17} aria-hidden="true" />
        <input
          ref={input}
          type="search"
          role="combobox"
          aria-label="Pesquisa global"
          placeholder="Pesquisar artigos, clientes, encomendas, documentos ou menus…"
          autoComplete="off"
          maxLength={100}
          value={query}
          aria-expanded={open}
          aria-autocomplete="list"
          aria-controls={open ? `${id}-results` : undefined}
          aria-activedescendant={
            open && results.length ? `${id}-option-${index}` : undefined
          }
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              setOpen(false);
            }
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              setOpen(true);
              if (results.length)
                setActive(
                  (index +
                    (event.key === "ArrowDown" ? 1 : -1) +
                    results.length) %
                    results.length,
                );
            }
            if (event.key === "Enter" && open && results[index]) {
              event.preventDefault();
              void choose(results[index]);
            }
          }}
        />
        {query ? (
          <ActionButton
            type="button"
            inferAction={false}
            aria-label="Limpar pesquisa global"
            onClick={() => {
              setQuery("");
              setActive(0);
              input.current?.focus();
            }}
          >
            <CircleX size={15} aria-hidden="true" />
          </ActionButton>
        ) : (
          <kbd aria-hidden="true">Ctrl + K</kbd>
        )}
      </div>
      {open && (
        <div className="workspace-search-panel">
          <QuickFilters
            label="Categorias da pesquisa"
            value={category}
            options={categories}
            onChange={(value) => {
              setCategory(value);
              setActive(0);
              input.current?.focus();
            }}
          />
          <div
            id={`${id}-results`}
            role="listbox"
            aria-label="Resultados da pesquisa global"
          >
            {groups.map((group) => (
              <div role="group" aria-label={group.label} key={group.id}>
                <div className="workspace-search-category" aria-hidden="true">
                  {group.label}
                  {group.more ? " · primeiros resultados" : ""}
                </div>
                {group.items.map((item) => {
                  const position = optionIndex++;
                  return (
                    <div
                      role="option"
                      aria-selected={index === position}
                      id={`${id}-option-${position}`}
                      key={item.id}
                      className="workspace-search-option"
                      onPointerDown={(event) => event.preventDefault()}
                      onMouseMove={() => setActive(position)}
                      onClick={() => void choose({ ...item, kind: group.id })}
                    >
                      <span>
                        <strong>{item.label}</strong>
                        <small>{item.detail}</small>
                      </span>
                      <ArrowUpRight size={16} aria-hidden="true" />
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
          <p className="workspace-search-hint" role="status">
            {!terms.length
              ? "Pesquise um menu ou escreva pelo menos dois caracteres para procurar registos."
              : category !== "menus" && trimmed.length < 2
                ? "Escreva pelo menos dois caracteres para procurar registos."
                : category !== "menus" && current.loading
                  ? "A procurar registos autorizados…"
                  : current.error && category !== "menus"
                    ? "Não foi possível consultar os registos. A pesquisa de menus continua disponível."
                    : !results.length
                      ? "Sem resultados para esta pesquisa e âmbito."
                      : "↑ ↓ para escolher · Enter para consultar · Registos da aplicação, sem consulta externa."}
          </p>
          {current.error && category !== "menus" && (
            <div className="workspace-search-error">
              <span>{current.error}</span>
              <ActionButton
                type="button"
                onClick={() => setRetry((v) => v + 1)}
              >
                Tentar novamente
              </ActionButton>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
