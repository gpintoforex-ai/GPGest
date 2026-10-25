/** Official IAZELA artwork supplied by the owner; original files are preserved. */
import "./Brand.css";
export default function Brand({ compact = false, transparent = false, className = "" }: {
  compact?: boolean;
  transparent?: boolean;
  className?: string;
}) {
  return <span className={`zela-brand ${compact ? "zela-brand-symbol" : ""} ${className}`}>
    <img className="brand-art-light"
      src={transparent
        ? compact ? "/brand/iazela/IAZELA_icone_transparente.png" : "/brand/iazela/IAZELA_logo_transparente.png"
        : compact ? "/brand/iazela/IAZELA_icone_claro.png" : "/brand/iazela/IAZELA_logo_fundo_claro.png"}
      alt="IAZELA"
      width={compact ? 157 : 903}
      height={compact ? 155 : 371}
      decoding="async"
    />
    <img className="brand-art-dark" src={compact ? "/brand/iazela/IAZELA_icone_escuro.png" : "/brand/iazela/IAZELA_logo_fundo_escuro.png"} alt="IAZELA" width={compact ? 156 : 616} height={compact ? 155 : 371} decoding="async" />
  </span>;
}
