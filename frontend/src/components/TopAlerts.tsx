import { useEffect, useRef, useState } from "react";
import { Bell, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import { ActionButton } from "./InteractionUI";
import { StatusBadge } from "./BusinessUI";
import { useDrivePending } from "../DrivePendingNotice";
import "./TopAlerts.css";

type Group = {
  id: string;
  label: string;
  count: number;
  tone: "danger" | "warning" | "info";
  page: string;
  params: Record<string, string>;
  items: string[];
};

const plural = (count: number, one: string, many: string) =>
  `${count} ${count === 1 ? one : many}`;

/**
 * Avisos de todos os módulos na barra do menu: total no sino e, ao abrir, cada tema com os primeiros
 * casos e o caminho para os resolver. Atualiza ao mudar de página ou de loja e a cada 2 minutos.
 * O contador agrupa avisos/ficheiros do Google Drive (só gestores), mantendo os totais separados.
 */
export default function TopAlerts({
  store,
  page,
  drive = false,
  onNavigate,
}: {
  store: string;
  page: string;
  drive?: boolean;
  onNavigate: (
    page: string,
    params?: Record<string, string>,
  ) => Promise<boolean>;
}) {
  const [data, setData] = useState<{
    store: string;
    groups: Group[];
    total: number;
    at: string;
  } | null>(null);
  const [open, setOpen] = useState(false),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [version, setVersion] = useState(0);
  const [align, setAlign] = useState<"left" | "right">("right");
  const root = useRef<HTMLDetailsElement>(null),
    sequence = useRef(0);
  const current = data?.store === store ? data : null;
  const { saft, statement } = useDrivePending(drive);
  const driveTotal = saft + statement;
  useEffect(() => {
    let alive = true;
    const read = async () => {
      const request = ++sequence.current;
      setLoading(true);
      try {
        const result = await api(
          `/alerts${store ? `?store_id=${encodeURIComponent(store)}` : ""}`,
          undefined,
          { preserveDraft: true, timeoutMs: 30_000 },
        );
        if (!Array.isArray(result.groups) || typeof result.total !== "number")
          throw new Error("O servidor não disponibilizou os avisos.");
        if (alive && request === sequence.current) {
          setData({
            store,
            groups: result.groups,
            total: result.total,
            at: result.generated_at || new Date().toISOString(),
          });
          setError("");
        }
      } catch (e) {
        if (alive && request === sequence.current)
          setError((e as Error).message);
      } finally {
        if (alive && request === sequence.current) setLoading(false);
      }
    };
    void read();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void read();
    }, 120_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [store, page, version]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);
  const urgent = current?.groups.some((group) => group.tone === "danger");
  const go = async (next: string, params?: Record<string, string>) => {
    if (await onNavigate(next, params)) setOpen(false);
  };
  const label = [
    !current
      ? error ? "Avisos: indisponíveis" : "Avisos: a consultar"
      : `${current.total ? `Avisos: ${current.total}` : "Avisos: nenhum"}${error ? "; dados da última consulta" : ""}`,
    driveTotal ? `${driveTotal} no Google Drive por importar` : "",
  ]
    .filter(Boolean)
    .join("; ");
  return (
    <details
      ref={root}
      className={`top-alerts align-${align}${(current?.total || 0) > 0 || driveTotal > 0 ? " has-alerts" : ""}`}
      open={open}
      onToggle={(event) => {
        const box = event.currentTarget.getBoundingClientRect();
        setAlign(
          box.left + Math.min(420, window.innerWidth - 24) >
            window.innerWidth - 12
            ? "right"
            : "left",
        );
        setOpen(event.currentTarget.open);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          setOpen(false);
          root.current?.querySelector("summary")?.focus();
        }
      }}
    >
      <summary aria-label={label} title="Avisos">
        <Bell size={18} aria-hidden="true" />
        {((current && current.total > 0) || driveTotal > 0) && (
          <span
            className="top-alerts-count"
            aria-hidden="true"
          >
            {current && current.total > 0 && (
              <span className={urgent ? "is-danger" : "is-warning"}>
                {current.total > 99 ? "99+" : current.total}
              </span>
            )}
            {current && current.total > 0 && driveTotal > 0 && (
              <span className="top-alerts-separator">/</span>
            )}
            {driveTotal > 0 && (
              <span className="is-info">
                {driveTotal > 99 ? "99+" : driveTotal}
              </span>
            )}
          </span>
        )}
      </summary>
      <div
        className="top-alerts-panel"
        role="region"
        aria-label="Avisos pendentes"
      >
        <div className="top-alerts-title">
          <strong>Atenção e próximos passos</strong>
          <ActionButton
            type="button"
            variant="quiet"
            disabled={loading}
            aria-label="Atualizar avisos"
            onClick={() => setVersion((v) => v + 1)}
          >
            <RefreshCw size={14} aria-hidden="true" />
          </ActionButton>
        </div>
        {loading && !current && <p role="status">A consultar os avisos…</p>}
        {error && (
          <p role="status" className="top-alerts-error">
            Não foi possível atualizar os avisos.
            {current
              ? " São apresentados os dados da última consulta nesta loja."
              : " Tente atualizar dentro de instantes."}
          </p>
        )}
        {current && (
          <p className="top-alerts-meta">
            Ocorrências de atenção, não mensagens por ler · Consultado{" "}
            {new Date(current.at).toLocaleTimeString("pt-PT", {
              hour: "2-digit",
              minute: "2-digit",
              timeZone: "Europe/Lisbon",
            })}
          </p>
        )}
        {current &&
          (!current.groups.length ? (
            <p>Sem avisos pendentes.</p>
          ) : (
            <ul>
              {current.groups.map((group) => (
                <li
                  key={group.id}
                  className={`top-alert top-alert-${group.tone}`}
                >
                  <div className="top-alert-head">
                    <StatusBadge tone={group.tone}>{group.count}</StatusBadge>
                    <b>{group.label}</b>
                  </div>
                  <small>
                    {group.items.join(" · ")}
                    {group.count > group.items.length
                      ? ` · e mais ${group.count - group.items.length}`
                      : ""}
                  </small>
                  <ActionButton
                    onClick={() =>
                      go(
                        group.page,
                        Object.keys(group.params || {}).length
                          ? group.params
                          : undefined,
                      )
                    }
                  >
                    Abrir {group.page}
                  </ActionButton>
                </li>
              ))}
            </ul>
          ))}
        {driveTotal > 0 && (
          <>
            <strong className="top-alerts-subtitle">Google Drive</strong>
            <div className="top-alert top-alert-info">
              <div className="top-alert-head">
                <StatusBadge tone="info">{driveTotal}</StatusBadge>
                <b>Novos por importar</b>
              </div>
              <small>
                {[
                  saft && plural(saft, "ficheiro SAF-T", "ficheiros SAF-T"),
                  statement &&
                    plural(statement, "extrato bancário", "extratos bancários"),
                ]
                  .filter(Boolean)
                  .join(" e ")}
              </small>
              <div className="top-alert-actions">
                {saft > 0 && (
                  <ActionButton onClick={() => go("Importar SAF-T de vendas")}>
                    Ver SAF-T
                  </ActionButton>
                )}
                {statement > 0 && (
                  <ActionButton onClick={() => go("Extratos")}>
                    Ver extratos
                  </ActionButton>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </details>
  );
}
