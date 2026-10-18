import { useEffect, useRef, useState } from "react";
import { api } from "./lib/api";
import { Field, Form } from "./components/Form";
import { calendarDay } from "./RecordList";

type Row = Record<string, any>;
const openingFor = (register?: Row) =>
  ((register?.last_opening_cents ?? 0) / 100).toFixed(2).replace(".", ",");

export default function OpenCashForm({
  registers,
  store,
  onSubmit,
}: {
  registers: Row[];
  store: string;
  onSubmit: (value: Row) => Promise<void>;
}) {
  const [registerId, setRegisterId] = useState(
    () =>
      (store
        ? registers.find((register) => register.store_id === store)
        : registers[0]
      )?.id || "",
  );
  const register = registers.find((row) => row.id === registerId);
  const [opening, setOpening] = useState(() => openingFor(register));
  const [sheetDay, setSheetDay] = useState(() => calendarDay(new Date().toISOString()));
  const [source, setSource] = useState<Row | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const edited = useRef(false);
  useEffect(() => {
    if (!registerId) return;
    let active = true;
    setPending(true); setSource(null); setMessage(""); edited.current = false;
    setOpening(openingFor(registers.find(row => row.id === registerId)));
    api(`/registers/${registerId}/opening-suggestion?sheet_day=${sheetDay}`).then(result => {
      if (!active) return;
      setSource(result.candidate);
      if (!edited.current) setOpening(((result.candidate?.amount_cents ?? result.last_opening_cents ?? 0) / 100).toFixed(2).replace(".", ","));
      setMessage(result.candidate ? `Fundo sugerido pela entrada Wintouch ${result.candidate.document}. Esta entrada não será somada novamente aos movimentos da folha.` : result.ambiguous ? "Existem várias entradas Wintouch neste dia. Confira os movimentos antes de definir o fundo; não foi escolhida uma entrada automaticamente." : "");
    }).catch(() => { if (active) setMessage("Não foi possível consultar o fundo Wintouch. Pode usar o valor da última abertura."); }).finally(() => { if (active) setPending(false); });
    return () => { active = false; };
  }, [registerId, sheetDay]);
  return (
    <Form onSubmit={async value => { if (pending) throw new Error("Aguarde a consulta do fundo de abertura."); await onSubmit(value); }}>
      {source && <input type="hidden" name="opening_wintouch_id" value={source.id} />}
      <Field label="Caixa">
        <select
          name="register_id"
          required
          value={registerId}
          onChange={(e) => {
            setRegisterId(e.target.value);
            setOpening(
              openingFor(registers.find((row) => row.id === e.target.value)),
            );
          }}
        >
          <option value="">Selecionar…</option>
          {registers.map((row) => (
            <option key={row.id} value={row.id}>
              {row.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Fundo de maneio inicial (€)">
        <input
          name="opening"
          inputMode="decimal"
          required
          value={opening}
          onChange={(e) => { edited.current = true; setOpening(e.target.value); }}
        />
      </Field>
      {pending && <p role="status">A consultar o fundo de abertura…</p>}
      {message && <p className="footnote">{message}</p>}
      {register && !source && (
        <p className="footnote">
          {register.last_opening_cents != null
            ? "Valor sugerido a partir da última abertura desta caixa. Pode alterá-lo."
            : "Esta caixa ainda não tem aberturas. Introduza o fundo de maneio inicial."}
        </p>
      )}
      <Field label="Dia da folha de caixa">
        <input
          type="date"
          name="sheet_day"
          value={sheetDay}
          onChange={e => setSheetDay(e.target.value)}
          required
        />
      </Field>
      <p className="footnote">
        O fundo de maneio é o numerário disponível no início. Os movimentos
        reais IAZELA e os recebimentos Wintouch já importados são calculados
        automaticamente para a folha. Cada loja e dia pode incluir Wintouch numa
        única folha.
      </p>
    </Form>
  );
}
