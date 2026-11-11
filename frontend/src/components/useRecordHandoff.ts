import { useEffect, useRef } from "react";
import { useLocationSearch } from "./WorkspaceUI";

/** Open an existing record once per search selection; the module/API revalidates access. */
export function useRecordHandoff(
  kind: string,
  pages: readonly string[],
  open: (id: string, stillCurrent: () => boolean) => boolean,
) {
  const location = useLocationSearch(),
    handled = useRef("");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    const params = new URLSearchParams(location);
    if (
      params.get("consulta.tipo") !== kind ||
      !pages.includes(params.get("pagina") || "")
    ) {
      handled.current = "";
      return;
    }
    const id = params.get("consulta.id"),
      key = params.get("consulta.chave") || `${kind}:${id}`;
    const stillCurrent = () =>
      mounted.current &&
      new URLSearchParams(window.location.search).get("consulta.id") === id &&
      (new URLSearchParams(window.location.search).get("consulta.chave") ||
        `${kind}:${id}`) === key &&
      pages.includes(
        new URLSearchParams(window.location.search).get("pagina") || "",
      );
    if (id && key !== handled.current && open(id, stillCurrent))
      handled.current = key;
  }, [location, kind, pages.join("|"), open]);
}
