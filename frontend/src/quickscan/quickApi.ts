/**
 * Pedidos do acesso rápido. Não usa cookies nem a sessão da aplicação: o token de dispositivo fica
 * no armazenamento local (emparelhamento) e o token de sessão só em memória.
 */
const DEVICE_KEY = "iazela_digitalizacao_dispositivo";

export const deviceToken = {
  get() { try { return localStorage.getItem(DEVICE_KEY) || ""; } catch { return ""; } },
  set(value: string) { try { localStorage.setItem(DEVICE_KEY, value); } catch { /* sem armazenamento: emparelhar de novo */ } },
  clear() { try { localStorage.removeItem(DEVICE_KEY); } catch { /* nada a limpar */ } },
};

export class QuickError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

const OFFLINE = "Sem ligação ao servidor. Verifique a rede e tente novamente; as páginas foram mantidas.";

function failure(status: number, text: string) {
  let detail = "";
  try { detail = JSON.parse(text).detail; } catch { /* resposta sem JSON */ }
  return new QuickError(typeof detail === "string" && detail ? detail : status === 413 ? "As fotografias são demasiado grandes." : "Não foi possível concluir a operação.", status);
}

export async function quick(path: string, options: { session?: string; device?: string; pairing?: string; body?: unknown; form?: FormData; blob?: boolean } = {}) {
  const headers: Record<string, string> = {};
  if (options.session) headers.Authorization = "Bearer " + options.session;
  if (options.device) headers["X-Quick-Scan-Device"] = options.device;
  if (options.pairing) headers["X-Quick-Scan-Pairing"] = options.pairing;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch("/api/quick-scan" + path, {
      method: options.body !== undefined || options.form || options.pairing ? "POST" : "GET",
      credentials: "omit", cache: "no-store", headers,
      body: options.form ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    });
  } catch {
    throw new QuickError(OFFLINE, 0);
  }
  if (!response.ok) throw failure(response.status, await response.text());
  return options.blob ? response.blob() : response.json();
}

/** Envio com progresso (XMLHttpRequest), para as fotografias da geração do PDF. */
export function sendWithProgress(path: string, form: FormData, session: string, progress: (value: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/quick-scan" + path);
    request.withCredentials = false;
    request.setRequestHeader("Authorization", "Bearer " + session);
    request.upload.onprogress = event => { if (event.lengthComputable) progress(event.loaded / event.total); };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        try { resolve(JSON.parse(request.responseText)); } catch { reject(new QuickError("Resposta inesperada do servidor.", request.status)); }
      } else reject(failure(request.status, request.responseText));
    };
    request.onerror = () => reject(new QuickError(OFFLINE, 0));
    request.ontimeout = () => reject(new QuickError(OFFLINE, 0));
    request.timeout = 180_000;
    request.send(form);
  });
}
