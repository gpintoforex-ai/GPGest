import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { ActionButton, AppDialog, Notice, dismissDisclosure } from "./InteractionUI";
import { ChevronDown } from "lucide-react";
import "./WorkspaceUI.css";

const changed = "iazela-navigation";
const subscribe = (listener: () => void) => {
  window.addEventListener("popstate", listener);
  window.addEventListener(changed, listener);
  return () => {
    window.removeEventListener("popstate", listener);
    window.removeEventListener(changed, listener);
  };
};
export function useLocationSearch() {
  return useSyncExternalStore(subscribe, () => window.location.search);
}
export function updateLocationParam(
  name: string,
  value: string,
  replace = false,
) {
  const url = new URL(window.location.href);
  if (value) url.searchParams.set(name, value);
  else url.searchParams.delete(name);
  if (url.href === window.location.href) return;
  window.history[replace ? "replaceState" : "pushState"](null, "", url);
  window.dispatchEvent(new Event(changed));
}
export function useWorkspaceTab(
  name: string,
  initial: string,
  allowed?: readonly string[],
) {
  const query = useLocationSearch();
  const value = new URLSearchParams(query).get(`area.${name}`) || initial;
  return [
    allowed && !allowed.includes(value) ? initial : value,
    (next: string) => updateLocationParam(`area.${name}`, next),
  ] as const;
}

export type WorkspaceTab = {
  id: string;
  label: string;
  count?: number;
  disabled?: boolean;
};
export function ModuleTabs({
  label,
  tabs,
  value,
  onChange,
  panels = false,
  id,
}: {
  label: string;
  tabs: readonly WorkspaceTab[];
  value: string;
  onChange: (value: string) => void;
  panels?: boolean;
  id?: string;
}) {
  const generated = useId();
  const prefix = id || generated;
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const rail = ref.current;
    if (!rail) return;
    const reveal = () => {
      const active = rail.querySelector<HTMLElement>(
        '[aria-selected="true"],[aria-current="page"]',
      );
      if (!active || !rail.clientWidth || rail.scrollWidth <= rail.clientWidth)
        return;
      const offset =
        active.getBoundingClientRect().left - rail.getBoundingClientRect().left;
      rail.scrollTo({
        left:
          rail.scrollLeft +
          offset -
          (rail.clientWidth - active.offsetWidth) / 2,
        behavior: "instant",
      });
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(rail);
    return () => observer.disconnect();
  }, [value]);
  return (
    <div
      ref={ref}
      className="module-tabs"
      data-ignore-dirty
      role={panels ? "tablist" : "navigation"}
      aria-label={label}
      onKeyDown={(event) => {
        const buttons = Array.from(
          ref.current?.querySelectorAll<HTMLButtonElement>(
            "button:not(:disabled)",
          ) || [],
        );
        const index = buttons.indexOf(
          document.activeElement as HTMLButtonElement,
        );
        if (
          index < 0 ||
          !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
        )
          return;
        event.preventDefault();
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? buttons.length - 1
              : (index +
                  (event.key === "ArrowRight" ? 1 : -1) +
                  buttons.length) %
                buttons.length;
        // Manual activation: arrows move focus; Enter/Space select without discarding a form.
        buttons[next]?.focus();
      }}
    >
      {tabs.map((tab) => (
        <ActionButton
          type="button"
          key={tab.id}
          id={`${prefix}-tab-${encodeURIComponent(tab.id)}`}
          role={panels ? "tab" : undefined}
          aria-selected={panels ? value === tab.id : undefined}
          aria-current={!panels && value === tab.id ? "page" : undefined}
          aria-controls={
            panels ? `${prefix}-panel-${encodeURIComponent(tab.id)}` : undefined
          }
          tabIndex={panels && value !== tab.id ? -1 : 0}
          disabled={tab.disabled}
          className={value === tab.id ? "module-tab is-active" : "module-tab"}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="module-tab-count">{tab.count}</span>
          )}
        </ActionButton>
      ))}
    </div>
  );
}

/** In-memory panels. Visit lazily, preserve mounted forms, and reveal invalid fields. */
export function TaskWorkspace({
  name,
  label,
  items,
  eager = false,
}: {
  name: string;
  label: string;
  items: readonly (WorkspaceTab & { content: ReactNode; defer?: boolean })[];
  eager?: boolean;
}) {
  const [requested, select] = useWorkspaceTab(name, items[0]?.id || "");
  const active = items.some((item) => item.id === requested && !item.disabled)
    ? requested
    : items[0]?.id;
  const [visited, setVisited] = useState<string[]>([]);
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    root.current?.dispatchEvent(
      new CustomEvent("gpgest-fields-mounted", { bubbles: true }),
    );
  }, [active]);
  useEffect(() => {
    setVisited((previous) =>
      previous.includes(active) ? previous : [...previous, active],
    );
  }, [active]);
  return (
    <div
      ref={root}
      className="task-workspace"
      onInvalidCapture={(event) => {
        const panel = (event.target as HTMLElement).closest<HTMLElement>(
          "[data-workspace-panel]",
        );
        if (panel?.hidden && panel.dataset.workspacePanel) {
          event.preventDefault();
          select(panel.dataset.workspacePanel);
          const field = event.target as HTMLInputElement;
          requestAnimationFrame(() => {
            field.focus();
            field.reportValidity?.();
          });
        }
      }}
    >
      <ModuleTabs
        id={id}
        label={label}
        tabs={items}
        value={active}
        onChange={select}
        panels
      />
      {items.map((item) => (
        <div
          key={item.id}
          id={`${id}-panel-${encodeURIComponent(item.id)}`}
          role="tabpanel"
          aria-labelledby={`${id}-tab-${encodeURIComponent(item.id)}`}
          data-workspace-panel={item.id}
          hidden={active !== item.id}
          tabIndex={0}
          className="workspace-panel"
        >
          {((eager && !item.defer) ||
            active === item.id ||
            visited.includes(item.id)) &&
            item.content}
        </div>
      ))}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-intro">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}
export function SectionHeader({
  title,
  actions,
}: {
  title: string;
  actions?: ReactNode;
}) {
  return (
    <div className="panel-title">
      <h2>{title}</h2>
      {actions}
    </div>
  );
}
export function LoadingState({
  children = "A carregar a área de trabalho…",
}: {
  children?: ReactNode;
}) {
  return (
    <p className="workspace-loading" role="status" aria-live="polite">
      {children}
    </p>
  );
}
export function ErrorState({
  children,
  onRetry,
}: {
  children: ReactNode;
  onRetry?: () => void;
}) {
  return (
    <Notice tone="error">
      {children}
      {onRetry && (
        <ActionButton type="button" onClick={onRetry}>
          Tentar novamente
        </ActionButton>
      )}
    </Notice>
  );
}
export function DetailDrawer(props: React.ComponentProps<typeof AppDialog>) {
  return (
    <AppDialog
      {...props}
      onRequestClose={props.onRequestClose}
      className={`detail-drawer ${props.className || ""}`}
    />
  );
}

/**
 * Ações secundárias agrupadas num menu («Mais ações»): as principais ficam visíveis e estas a um clique.
 * Fecha com Escape, clique fora ou depois de escolher uma ação; o foco regressa ao botão.
 */
export function ActionMenu({ label = "Mais ações", children }: { label?: string; children: ReactNode }) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (menu.current?.open && !menu.current.contains(event.target as Node)) menu.current.open = false;
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);
  return (
    <details
      ref={menu}
      className="action-menu"
      onKeyDown={(event) => { if (event.key === "Escape") dismissDisclosure(event); }}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest(".action-menu-panel button") && menu.current) menu.current.open = false;
      }}
    >
      <summary className="ui-action">{label}<ChevronDown size={16} aria-hidden="true" /></summary>
      <div className="action-menu-panel" role="group" aria-label={label}>{children}</div>
    </details>
  );
}
