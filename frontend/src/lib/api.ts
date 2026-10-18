import { beginOperation, reportFormError, responseError } from "../components/InteractionUI";

export async function api(path: string, body?: unknown, options?: { timeoutMs?: number; method?: "POST" | "PUT" | "DELETE" }) {
  const operation = beginOperation(path, body !== undefined);
  try {
  const r = await fetch("/api" + path, {
    credentials: "same-origin",
    ...(options?.timeoutMs ? { signal: AbortSignal.timeout(options.timeoutMs) } : {}),
    ...(body !== undefined
      ? {
          method: options?.method || "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
  const text = await r.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error("Não foi possível concluir a operação porque o servidor não respondeu como esperado. Os dados preenchidos foram mantidos. Tente novamente dentro de instantes.");
  }
  if (r.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
  if (!r.ok) {
    const error = responseError(data.detail, r.status);
    reportFormError(operation.form, error);
    throw error;
  }
  operation.saved();
  return data;
  } catch (error) {
    if (error instanceof TypeError) throw new Error("Não foi possível contactar o servidor. Verifique a ligação e volte a tentar; os dados preenchidos foram mantidos.");
    if (error instanceof DOMException && ["TimeoutError", "AbortError"].includes(error.name)) throw new Error(body === undefined ? "O servidor demorou a carregar os dados. Volte a tentar a consulta." : "O servidor demorou a responder. Confirme se a operação ficou registada antes de repetir.");
    throw error;
  } finally { operation.end(); }
}
