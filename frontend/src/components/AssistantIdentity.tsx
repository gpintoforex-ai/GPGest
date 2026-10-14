import { Bot } from "lucide-react";
import "./AssistantIdentity.css";

export const ASSISTANTS = {
  iris: {
    name: "Íris",
    area: "Tesouraria",
    mission: "Antecipar necessidades de dinheiro e organizar pagamentos e recebimentos.",
  },
  nuno: {
    name: "Nuno",
    area: "Bancos",
    mission: "Conferir movimentos bancários, depósitos e transferências.",
  },
  clara: {
    name: "Clara",
    area: "Contabilidade",
    mission: "Organizar documentos e identificar o que falta para entregar à contabilidade.",
  },
} as const;

export default function AssistantIdentity({ assistant }: { assistant: keyof typeof ASSISTANTS }) {
  const { name, area, mission } = ASSISTANTS[assistant];
  return <section className={`assistant-identity assistant-${assistant}`} aria-label={`${name} · ${area}`}>
    <span className="assistant-identity-icon"><Bot size={22} aria-hidden="true" /></span>
    <div><h2>{name} <span>· {area}</span></h2><p>{mission}</p></div>
  </section>;
}
