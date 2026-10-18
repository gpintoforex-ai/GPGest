import { ActionButton, CloseButton, Notice } from "./components/InteractionUI";
import { useEffect, useState, type ReactNode } from "react";
import { Settings, X } from "lucide-react";
import { useCashPreferences } from "./CashPreferences";
import "./RecordList.css";
import "./CashSheetList.css";

type Row = Record<string, any>;

export default function CashSheetList({
  rows,
  store,
  information,
  configuration,
  actions,
  children,
}: {
  rows: Row[];
  store: string;
  information?: ReactNode;
  configuration?: ReactNode;
  actions?: ReactNode;
  children: (row: Row) => ReactNode;
}) {
  const { preferences, updatePreferences, error } = useCashPreferences();
  const [size, setSize] = useState(preferences.pageSize);
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [store]);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const current = Math.min(page, pages);
  const start = (current - 1) * size;

  return (
    <>
      <div className="cash-sheet-toolbar">
        <div className="cash-page-controls">
          <label className="record-field">
            Folhas por página
            <select
              value={size}
              onChange={(e) => {
                setSize(Number(e.target.value));
                setPage(1);
              }}
            >
              {[5, 10, 25, 50].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          {information}
          <details
            className="cash-page-settings"
            name="cash-page-options"
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.currentTarget.open = false;
                e.currentTarget.querySelector("summary")?.focus();
              }
            }}
          >
            <summary
              aria-label="Parâmetros da folha de caixa"
              title="Parâmetros da folha de caixa"
            >
              <Settings size={22} aria-hidden="true" />
            </summary>
            <div className="cash-page-settings-content">
              <div className="cash-page-settings-heading">
                <h3>Parâmetros da folha de caixa</h3>
                <CloseButton
                  type="button"
                  className="cash-page-settings-close"
                  aria-label="Fechar parâmetros da folha de caixa"
                  title="Fechar"
                  onClick={(e) => {
                    const details = e.currentTarget.closest("details");
                    if (details) {
                      details.open = false;
                      details.querySelector("summary")?.focus();
                    }
                  }}
                />
              </div>
              <label className="record-field">
                Folhas por página por defeito
                <select
                  value={preferences.pageSize}
                  onChange={(e) => {
                    const pageSize = Number(e.target.value);
                    updatePreferences({ pageSize });
                    setSize(pageSize);
                    setPage(1);
                  }}
                >
                  {[5, 10, 25, 50].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <label className="record-field">
                Modo de contagem por defeito
                <select
                  value={preferences.countMode}
                  onChange={(e) =>
                    updatePreferences({
                      countMode: e.target.value as "total" | "denominations",
                    })
                  }
                >
                  <option value="total">Valor total</option>
                  <option value="denominations">Notas e moedas</option>
                </select>
              </label>
              <p className="footnote">
                Preferências guardadas automaticamente neste navegador para este
                utilizador e empresa.
              </p>
              {error && (
                <Notice className="error" role="alert">
                  {error}
                </Notice>
              )}
              {configuration && (
                <div className="cash-page-settings-links">{configuration}</div>
              )}
            </div>
          </details>
        </div>
        {actions}
      </div>
      <div className="cash-sheet-list">
        {rows.slice(start, start + size).map(children)}
      </div>
      <nav
        className="record-pagination"
        aria-label="Paginação das folhas de caixa"
      >
        <span role="status">
          A mostrar {rows.length ? start + 1 : 0}–
          {Math.min(start + size, rows.length)} de {rows.length} folhas
        </span>
        <div>
          <ActionButton
            type="button"
            disabled={current === 1}
            onClick={() => setPage(current - 1)}
          >
            Anterior
          </ActionButton>
          <span>
            Página {current} de {pages}
          </span>
          <ActionButton
            type="button"
            disabled={current === pages}
            onClick={() => setPage(current + 1)}
          >
            Seguinte
          </ActionButton>
        </div>
      </nav>
    </>
  );
}
