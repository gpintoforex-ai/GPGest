import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { ActionLink, Notice } from "./components/InteractionUI";
import { ScanButton } from "./DocumentScanner";

type Row = Record<string, any>;

export default function AttachmentPanel({
  movement,
  writable,
  canScan = false,
}: {
  movement: Row;
  writable: boolean;
  canScan?: boolean;
}) {
  const [rows, setRows] = useState<Row[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const load = () =>
    api("/movements/" + movement.id + "/attachments").then(setRows);
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [movement.id]);
  return (
    <div>
      {rows.map((r) => (
        <p key={r.id}>
          <ActionLink href={r.view_url || "/api/attachments/" + r.id}>{r.name} · Descarregar</ActionLink>
        </p>
      ))}
      {!rows.length && <p>Sem anexos.</p>}
      {canScan && writable && <ScanButton movement={movement} onSaved={load} />}
      {rows.filter(r => r.scan_id && r.validation_status !== "validado").map(r => <p key={r.id}>Anexo por validar: {r.name}</p>)}
      {writable && (
        <Field label="Adicionar PDF, JPG ou PNG (até 5 MB, máximo 3)">
          <input
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            disabled={busy}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              setBusy(true);
              setError("");
              try {
                if (file.size > 5 * 1024 * 1024)
                  throw new Error("O ficheiro excede 5 MB.");
                const data = new FormData();
                data.append("file", file);
                const r = await fetch(
                  "/api/movements/" + movement.id + "/attachments",
                  { method: "POST", body: data },
                );
                if (!r.ok) {
                  const v = await r.json();
                  throw new Error(
                    v.detail || "Não foi possível guardar o anexo.",
                  );
                }
                await load();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          />
        </Field>
      )}
      {error && <Notice className="error">{error}</Notice>}
    </div>
  );
}
