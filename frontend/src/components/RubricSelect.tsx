import { useEffect, useRef, useState } from "react";
import RubricBadge from "./RubricBadge";
import { ActionButton } from "./InteractionUI";

export type RubricOption = {
  id: string;
  name: string;
  icon?: string;
  color?: string;
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
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const selected = categories.find((category) => category.id === value);

  useEffect(() => {
    if (!open) return;
    const dismissOutside = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
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
        <div className="rubric-select-options" role="listbox" aria-label="Rubrica do fornecedor">
          <ActionButton
            type="button"
            role="option"
            aria-selected={!value}
            onClick={() => {
              onChange("");
              setOpen(false);
              trigger.current?.focus();
            }}
          >
            <span>Sem rubrica definida</span>
          </ActionButton>
          {categories.map((category) => (
            <ActionButton
              type="button"
              role="option"
              aria-label={`Selecionar rubrica ${category.name}`}
              aria-selected={category.id === value}
              key={category.id}
              onClick={() => {
                onChange(category.id);
                setOpen(false);
                trigger.current?.focus();
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
        </div>
      )}
      <input type="hidden" name="category_id" value={value} />
    </div>
  );
}
