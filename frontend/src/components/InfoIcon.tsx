import { Info } from "lucide-react";
import "./InfoIcon.css";

export default function InfoIcon({ size = 18 }: { text: string; size?: number; "aria-hidden"?: string }) {
  return <span className="info-icon" title="Clicar para saber mais">
    <Info size={size} aria-hidden="true" />
    <span className="info-icon-note" role="tooltip">Clicar para saber mais</span>
  </span>;
}
