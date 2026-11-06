import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Send, Sparkles } from "lucide-react";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import "./Assistant.css";

type Turn = { role: "user" | "assistant"; content: string; tools?: { name: string }[] };

/** Perguntas sugeridas: as consultas que o assistente sabe fazer. */
const SUGGESTIONS = [
  "Que artigos estão a terminar a validade nos próximos 60 dias?",
  "Que produtos é necessário encomendar?",
  "Compara as vendas deste mês com o mesmo mês do ano passado.",
  "Quais foram os 10 artigos mais vendidos nos últimos 30 dias?",
  "Que produtos o cliente … já comprou?",
  "Que clientes já não compram há mais de 90 dias?",
  "Qual é a previsão do IVA a pagar neste período?",
  "Que documentos estão em falta para a contabilidade?",
];

const TOOL_LABELS: Record<string, string> = {
  artigos_a_terminar_validade: "Lotes e validades",
  produtos_a_encomendar: "Reposição de stock",
  comparativo_vendas: "Faturação",
  artigos_mais_vendidos: "Vendas por artigo",
  compras_do_cliente: "Compras do cliente",
  clientes_sem_compras: "Clientes sem compras",
  previsao_iva: "Mapa de IVA",
  documentos_em_falta_contabilidade: "Conferência documental",
};

/** Negrito (**texto**) numa linha, sem HTML vindo da resposta. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>);
}

/** Texto simples da resposta: parágrafos e listas com «- ». */
function Reply({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (items.length) blocks.push(<ul key={`l${blocks.length}`}>{items.map((item, i) => <li key={i}>{inline(item)}</li>)}</ul>);
    items = [];
  };
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (/^[-•*] /.test(trimmed)) items.push(trimmed.slice(2));
    else {
      flush();
      if (trimmed) blocks.push(<p key={`p${blocks.length}`}>{inline(trimmed)}</p>);
    }
  }
  flush();
  return <>{blocks}</>;
}

/**
 * Assistente IAZELA: perguntas sobre os dados da empresa (validades, reposição, vendas, clientes, IVA,
 * documentos), respondidas com a Claude API no servidor. Só consulta; a conversa vive nesta janela.
 */
export default function Assistant({ store }: { store: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<{ available: boolean; message: string } | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open || state) return;
    api("/assistente/estado").then(setState).catch(e => setState({ available: false, message: (e as Error).message }));
  }, [open, state]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [turns, busy]);

  async function send(question: string) {
    const content = question.trim();
    if (!content || busy) return;
    if (content.includes("…")) {
      setText(content);
      requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(content.indexOf("…"), content.indexOf("…") + 1); });
      return;
    }
    const history: Turn[] = [...turns, { role: "user", content }];
    setTurns(history); setText(""); setError(""); setBusy(true);
    try {
      const result = await api("/assistente/mensagens", {
        messages: history.map(t => ({ role: t.role, content: t.content })),
        store_id: store || "",
      }, { timeoutMs: 180_000 });
      setTurns([...history, { role: "assistant", content: result.reply, tools: result.tools }]);
    } catch (e) {
      setError((e as Error).message);
      setTurns(turns);
      setText(content);
    } finally {
      setBusy(false);
    }
  }

  return <>
    <ActionButton type="button" className="assistant-trigger" aria-label="Assistente IAZELA" title="Perguntar ao assistente" onClick={() => setOpen(true)}>
      <Sparkles size={18} aria-hidden="true" />
    </ActionButton>
    {open && <AppDialog className="assistant-dialog" aria-labelledby="assistant-title" busy={busy} onRequestClose={() => setOpen(false)}>
      <div className="assistant-head">
        <h2 id="assistant-title"><Sparkles size={20} aria-hidden="true" />Assistente IAZELA</h2>
        {turns.length > 0 && <ActionButton type="button" disabled={busy} onClick={() => { setTurns([]); setError(""); }}>Nova conversa</ActionButton>}
      </div>
      <p className="assistant-privacy">Responde a partir dos dados da empresa a que tem acesso, sem alterar nada. A pergunta e os dados consultados são enviados à Anthropic (Claude) para gerar a resposta.</p>
      {state && !state.available && <Notice tone="warning">{state.message}</Notice>}
      <div className="assistant-log" aria-live="polite">
        {!turns.length && <div className="assistant-suggestions" role="group" aria-label="Perguntas sugeridas">
          {SUGGESTIONS.map(s => <button type="button" key={s} disabled={busy || !state?.available} onClick={() => void send(s)}>{s}</button>)}
        </div>}
        {turns.map((turn, i) => <div key={i} className={`assistant-turn is-${turn.role}`}>
          {turn.role === "user" ? <p>{turn.content}</p> : <Reply text={turn.content} />}
          {!!turn.tools?.length && <small className="assistant-sources">Consultou: {[...new Set(turn.tools.map(t => TOOL_LABELS[t.name] || t.name))].join(" · ")}</small>}
        </div>)}
        {busy && <p className="assistant-turn is-assistant assistant-thinking" role="status">A consultar os dados…</p>}
        <div ref={end} />
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <form className="assistant-form" onSubmit={e => { e.preventDefault(); void send(text); }}>
        <textarea ref={input} aria-label="Pergunta ao assistente" rows={2} maxLength={2000} value={text} disabled={!state?.available}
          placeholder="Escreva a pergunta (por exemplo: artigos mais vendidos em outubro)"
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } }} />
        <ActionButton type="submit" variant="primary" aria-label="Enviar pergunta" disabled={busy || !text.trim() || !state?.available}><Send size={18} aria-hidden="true" /></ActionButton>
      </form>
    </AppDialog>}
  </>;
}
