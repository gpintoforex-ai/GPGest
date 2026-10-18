import { responseError } from "../components/InteractionUI";

/** Uploads never retry automatically: a lost response may follow a committed import. */
export async function uploadPurchaseFile(store: string, file: File, confirm: boolean) {
  if (file.size > 5_000_000) throw new Error("Limite de 5 MB por ficheiro.");
  const body = new FormData();
  body.append("file", file);
  try {
    const response = await fetch(`/api/purchases/import/${store}?confirm=${confirm}`, {
      method: "POST", body, credentials: "same-origin",
      signal: AbortSignal.timeout(90_000),
    });
    if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
    const text = await response.text();
    let result;
    try { result = JSON.parse(text); }
    catch { throw new Error(confirm
      ? "Não foi possível confirmar a resposta da importação. Atualize a lista e verifique os documentos antes de repetir."
      : "O servidor não devolveu a pré-visualização. O ficheiro foi mantido; tente novamente."); }
    if (!response.ok) throw responseError(result.detail, response.status);
    return result;
  } catch (error) {
    if (error instanceof TypeError || (error instanceof DOMException && ["TimeoutError", "AbortError"].includes(error.name))) {
      throw new Error(confirm
        ? "A ligação terminou antes de confirmar o resultado. A importação pode já estar guardada. Atualize a lista e verifique os documentos antes de repetir."
        : "Não foi possível obter a pré-visualização a tempo. Verifique a ligação e tente novamente; o ficheiro foi mantido.");
    }
    throw error;
  }
}
