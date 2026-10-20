import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice } from "./components/InteractionUI";
import { StatusBadge } from "./components/BusinessUI";
import { DRIVE_IMPORTED_EVENT } from "./DrivePendingNotice";

export type DriveFile = { id: string; name: string; size: number; modified_at: string; imported: boolean; status?: "new" | "imported" | "updated"; format?: string; note?: string };
export type IgnoredDriveFile = { id: string; name: string; modified_at: string; reason: string };
export type DriveSearch = {
  configured: boolean | null;
  data: { configured: boolean; files: DriveFile[]; source?: string; subfolders?: boolean; ignored?: IgnoredDriveFile[] } | null;
  error: string;
  searching: boolean;
  refresh: () => void;
};

const size = (bytes: number) => bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(bytes / 1000))} kB`;
const when = (value: string) => value ? new Intl.DateTimeFormat("pt-PT", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Lisbon" }).format(new Date(value)) : "—";

/** Procura, na pasta Google Drive configurada, os ficheiros do endpoint indicado (null: não procura). */
export function useDriveFiles(endpoint: string | null): DriveSearch {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [data, setData] = useState<DriveSearch["data"]>(null);
  const [error, setError] = useState(""), [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    setData(null); setError("");
    if (!endpoint) return;
    // A verificação da configuração é local e imediata; só depois se contacta a Google.
    api("/drive/status")
      .then(status => {
        if (!active) return;
        setConfigured(status.configured);
        if (!status.configured) { setData({ configured: false, files: [] }); return; }
        return api(endpoint, undefined, { timeoutMs: 60000 }).then(result => { if (active) setData(result); });
      })
      .catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [endpoint, version]);
  const refresh = useCallback(() => setVersion(v => v + 1), []);
  return { configured, data, error, searching: !!endpoint && configured !== false && !data && !error, refresh };
}

/** Aviso no topo da página enquanto decorre a procura automática no Google Drive. */
export function DriveSearchNotice({ search, what }: { search: DriveSearch; what: string }) {
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => { setDismissed(false); }, [search.data]);
  if (!search.configured) return null;
  // Amarelo durante a procura, verde quando termina; as falhas aparecem na secção do Google Drive.
  if (search.searching) return <Notice tone="warning" role="status">A procurar {what} no Google Drive… Pode continuar a usar a página; os ficheiros encontrados aparecem na secção do Google Drive.</Notice>;
  if (!search.data || dismissed) return null;
  const fresh = search.data.files.filter(file => !file.imported).length;
  return <Notice tone="success" role="status" onDismiss={() => setDismissed(true)}>Procura no Google Drive concluída: {fresh ? `${fresh === 1 ? "1 ficheiro novo" : `${fresh} ficheiros novos`} por importar. Consulte a secção do Google Drive nesta página.` : "sem ficheiros novos por importar."}</Notice>;
}

/** Lista os ficheiros encontrados no Google Drive, prontos a importar. */
export default function DriveImportFiles({ search, title, actionLabel, disabled, onPick, notConfigured }: {
  search: DriveSearch;
  title: string;
  actionLabel: string;
  disabled?: boolean;
  onPick: (file: DriveFile) => Promise<void>;
  notConfigured?: string;
}) {
  const { data, refresh } = search;
  const [error, setError] = useState(""), [busy, setBusy] = useState("");
  async function pick(file: DriveFile) {
    setBusy(file.id); setError("");
    try { await onPick(file); refresh(); window.dispatchEvent(new Event(DRIVE_IMPORTED_EVENT)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(""); }
  }
  if (data && !data.configured) return <p className="footnote">{notConfigured || "Para procurar estes ficheiros automaticamente no Google Drive, configure a pasta e ligue a conta Google em Configurações → Anexos."}</p>;
  const fresh = data?.files.filter(file => !file.imported).length || 0;
  const failure = error || search.error;
  return <div className="drive-import-files">
    <h3>{title}{data && <> · {fresh ? `${fresh} por importar` : "sem ficheiros novos"}</>}</h3>
    {search.searching && <p role="status">A procurar no Google Drive…</p>}
    {failure && <Notice tone="warning">Google Drive: {failure}</Notice>}
    {data && !data.files.length && <p>Não foram encontrados ficheiros deste tipo na pasta configurada nem nas subpastas.</p>}
    {!!data?.files.length && <div className="table-wrap"><table><thead><tr><th>Ficheiro</th><th>Alterado</th><th className="numeric">Tamanho</th><th>Estado</th><th>Ações</th></tr></thead><tbody>
      {data.files.map(file => <tr key={file.id}>
        <td>{file.name}{(file.format || file.note) && <><br /><small>{[file.format, file.note].filter(Boolean).join(" · ")}</small></>}</td><td>{when(file.modified_at)}</td><td className="numeric">{size(file.size)}</td>
        <td><StatusBadge tone={file.imported ? "success" : "warning"}>{file.imported ? "Já importado" : file.status === "updated" ? "Alterado desde a importação" : "Novo"}</StatusBadge></td>
        <td><ActionButton disabled={disabled || !!busy} onClick={() => pick(file)}>{busy === file.id ? "A obter do Drive…" : actionLabel}</ActionButton></td>
      </tr>)}
    </tbody></table></div>}
    <ActionButton disabled={!!busy || search.searching} onClick={refresh}><RefreshCw size={16} />Procurar novamente</ActionButton>
  </div>;
}
