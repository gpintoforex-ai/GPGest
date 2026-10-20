/** Passa o período do dashboard para a página de destino (uma vez, durante um minuto). */
const KEY = "iazela-period-handoff";

export function handOffPeriod(page: string, start: string, end: string) {
  try { sessionStorage.setItem(KEY, JSON.stringify({ page, start, end, at: Date.now() })); } catch { /* sem armazenamento: a página usa o seu período */ }
}

export function takePeriod(page: string): { start: string; end: string } | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (value && value.page === page && Date.now() - value.at < 60_000) {
      sessionStorage.removeItem(KEY);
      return { start: value.start, end: value.end };
    }
  } catch { /* ignora valores inválidos */ }
  return null;
}
