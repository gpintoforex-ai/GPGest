/**
 * Motivo de uma alteração auditada: texto livre com motivos pré-definidos para escolher.
 * O servidor exige um motivo legível (pelo menos duas palavras reais).
 */
export default function ReasonField({ label, value, onChange, options, placeholder }: {
  label: string; value: string; onChange: (value: string) => void; options: string[]; placeholder?: string;
}) {
  return <div className="rn-reason">
    <label>{label}<textarea value={value} onChange={e => onChange(e.target.value)} rows={2} maxLength={500} placeholder={placeholder} /></label>
    <div className="rn-reason-options" role="group" aria-label="Motivos habituais">
      {options.map(option => <button key={option} type="button" className="rn-reason-option" aria-pressed={value === option} onClick={() => onChange(option)}>{option}</button>)}
    </div>
  </div>;
}

export const NATURE_REASONS = [
  "Separar os pagamentos de IVA dos custos operacionais",
  "Analisar as mercadorias como compras",
  "Classificar o equipamento como investimento",
  "Tirar movimentos financeiros dos custos",
  "Corrigir uma configuração anterior",
];
export const RECLASSIFY_REASONS = [
  "Pagamentos de IVA registados em Impostos com a TSU",
  "Pagamento ao Estado confirmado no comprovativo",
];
export const UNDO_REASONS = ["Afinal não era IVA", "Comprovativo indica outro imposto"];
export const REJECT_REASONS = ["Falta confirmar com o contabilista", "A rubrica deve ficar como está"];
