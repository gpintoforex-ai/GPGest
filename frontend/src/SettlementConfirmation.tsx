import { useEffect, useId, useRef } from "react";
import { CheckCircle } from "lucide-react";
import "./RecordList.css";

export default function SettlementConfirmation({
  message,
  onClose,
}: {
  message: string;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const messageId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    return () => {
      if (element?.open) element.close();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="settlement-confirmation"
      aria-labelledby={titleId}
      aria-describedby={messageId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <CheckCircle size={44} aria-hidden="true" />
      <h2 id={titleId}>Liquidação confirmada</h2>
      <p id={messageId}>{message}</p>
      <button type="button" className="primary" autoFocus onClick={onClose}>
        OK
      </button>
    </dialog>
  );
}
