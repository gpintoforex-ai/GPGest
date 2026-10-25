import { useEffect, useRef, useState } from "react";
import RubricBadge from "./RubricBadge";
import { ActionButton } from "./InteractionUI";

const normalize = (text: string) =>
  text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLocaleLowerCase("pt-PT").trim();

export type RubricOption = {
  id: string;
  name: string;
  icon?: string;
  color?: string;
  // Inativa: só aparece quando já é o valor escolhido (histórico preservado).
  active?: boolean;
};

export default function RubricSelect({
  categories,
  value,
  onChange,
  disabled = false,
}: {
  categories: RubricOption[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const selected = categories.find((category) => category.id === value);
  const matches = categories.filter(
    (category) => (category.active !== false || category.id === value) && normalize(category.name).includes(normalize(query)),
  );
  const close = () => {
    setOpen(false);
    setQuery("");
    trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const dismissOutside = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("pointerdown", dismissOutside);
    return () => document.removeEventListener("pointerdown", dismissOutside);
  }, [open]);

  return (
    <div className="rubric-select" ref={wrapper}>
      <ActionButton
        ref={trigger}
        type="button"
        className="rubric-select-trigger"
        aria-label="Selecionar rubrica do fornecedor"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setOpen(true);
            wrapper.current
              ?.querySelector<HTMLButtonElement>('[role="option"]')
              ?.focus();
          }
        }}
      >
        {selected ? (
          <RubricBadge
            name={selected.name}
            icon={selected.icon}
            color={selected.color}
          />
        ) : (
          <span>Sem rubrica definida</span>
        )}
        <span aria-hidden="true">▾</span>
      </ActionButton>
      {open && (
        <div className="rubric-select-options">
          <input
            type="search"
            className="rubric-select-search"
            aria-label="Pesquisar rubrica"
            placeholder="Pesquisar rubrica…"
            autoComplete="off"
            data-ignore-dirty
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                close();
              }
              if (event.key === "Enter") {
                event.preventDefault();
                if (matches[0]) {
                  onChange(matches[0].id);
                  close();
                }
              }
              if (event.key === "ArrowDown") {
                event.preventDefault();
                wrapper.current?.querySelector<HTMLButtonElement>('[role="option"]')?.focus();
              }
            }}
          />
          <div role="listbox" aria-label="Rubrica do fornecedor">
          {!query && <ActionButton
            type="button"
            role="option"
            aria-selected={!value}
            onClick={() => {
              onChange("");
              close();
            }}
          >
            <span>Sem rubrica definida</span>
          </ActionButton>}
          {matches.map((category) => (
            <ActionButton
              type="button"
              role="option"
              aria-label={`Selecionar rubrica ${category.name}`}
              aria-selected={category.id === value}
              key={category.id}
              onClick={() => {
                onChange(category.id);
                close();
              }}
              onKeyDown={(event) => {
                if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
                event.preventDefault();
                const options = Array.from(
                  wrapper.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') || [],
                );
                const currentIndex = options.indexOf(event.currentTarget);
                options[
                  (currentIndex + (event.key === "ArrowDown" ? 1 : options.length - 1)) %
                    options.length
                ]?.focus();
              }}
            >
              <RubricBadge
                name={category.name}
                icon={category.icon}
                color={category.color}
              />
            </ActionButton>
          ))}
          {!matches.length && <p className="rubric-select-empty">Nenhuma rubrica encontrada.</p>}
          </div>
        </div>
      )}
      <input type="hidden" name="category_id" value={value} />
    </div>
  );
}
