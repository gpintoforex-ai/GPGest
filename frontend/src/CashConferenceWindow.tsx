import { useId, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import "./CashConferenceWindow.css";

export default function CashConferenceWindow({ title, context, busy, error, notice, children, footer, information }: {
  title: string; context?: string; busy: boolean; error?: string; notice?: string; children: ReactNode; footer?: ReactNode; information?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  return <>
    <ActionButton type="button" className="primary" onClick={() => setOpen(true)}>{title}</ActionButton>
    {open && createPortal(<AppDialog className="cash-conference-window" busy={busy}
      guardChanges={false} aria-labelledby={titleId} onRequestClose={() => setOpen(false)}>
      <div className="panel-title"><div className="cash-conference-heading"><div className="cash-conference-title"><h2 id={titleId}>{title}</h2>{information}</div>{context && <p className="footnote">{context}</p>}</div>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {notice && <Notice tone="success">{notice}</Notice>}
      <div className="cash-sheet local-cash-sheet">{children}</div>
      {footer && <footer className="cash-conference-footer">{footer}</footer>}
    </AppDialog>, document.body)}
  </>;
}
