import { useState } from "react";
import { Field, Form } from "./App";
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
  return (
    <Form onSubmit={onSubmit}>
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
          onChange={(e) => setOpening(e.target.value)}
        />
      </Field>
      {register && (
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
          defaultValue={calendarDay(new Date().toISOString())}
          required
        />
      </Field>
      <p className="footnote">
        O fundo de maneio é o numerário disponível no início. Os movimentos
        reais GPGest e os recebimentos Wintouch já importados são calculados
        automaticamente para a folha. Cada loja e dia pode incluir Wintouch numa
        única folha.
      </p>
    </Form>
  );
}
