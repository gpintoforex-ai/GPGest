import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { ActionButton } from "./components/InteractionUI";
const key = "iazela-theme";
function savedTheme() { try { return localStorage.getItem(key) === "dark" ? "dark" : "current"; } catch { return "current"; } }
export function initializeTheme() { document.documentElement.dataset.theme = savedTheme(); }
export default function ThemeSettings({ compact = false }: { compact?: boolean }) {
  const [theme, setTheme] = useState(savedTheme);
  useEffect(() => { const update = () => { const value = savedTheme(); setTheme(value); document.documentElement.dataset.theme = value; }; window.addEventListener("storage", update); window.addEventListener("iazela-theme-change", update); return () => { window.removeEventListener("storage", update); window.removeEventListener("iazela-theme-change", update); }; }, []);
  const control = <ActionButton type="button" className="theme-toggle" role="switch" aria-label="Tema escuro" aria-checked={theme === "dark"} title={theme === "dark" ? "Ativar tema claro" : "Ativar tema escuro"} onClick={() => { const value = theme === "dark" ? "current" : "dark"; setTheme(value); document.documentElement.dataset.theme = value; try { localStorage.setItem(key, value); } catch { /* Still usable for this session. */ } window.dispatchEvent(new Event("iazela-theme-change")); }}><span className="theme-toggle-icon">{theme === "dark" ? <Moon size={17} /> : <Sun size={17} />}</span><span className="theme-toggle-name">{theme === "dark" ? "Escuro" : "Claro"}</span><span className="theme-toggle-track" aria-hidden="true"><span /></span></ActionButton>;
  return compact ? control : <section className="panel"><h1>Aparência</h1><p>Escolha o tema da plataforma. A preferência é guardada neste navegador.</p>{control}<div className="theme-samples"><div className="theme-sample light surface-light"><strong>IAZELA · Claro</strong><span>Superfícies neutras, azul-petróleo e turquesa.</span></div><div className="theme-sample dark surface-dark"><strong>IAZELA · Escuro</strong><span>Azul-petróleo com apontamentos turquesa.</span></div></div></section>;
}
