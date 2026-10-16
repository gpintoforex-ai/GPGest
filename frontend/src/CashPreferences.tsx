import { createContext, useContext, useState, type ReactNode } from "react";

export type CashPreferences = {
  pageSize: number;
  countMode: "total" | "denominations";
};

const defaults: CashPreferences = { pageSize: 5, countMode: "total" };
const pageSizes = [5, 10, 25, 50];

function readPreferences(key: string): CashPreferences {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return {
      pageSize: pageSizes.includes(value?.pageSize)
        ? value.pageSize
        : defaults.pageSize,
      countMode:
        value?.countMode === "denominations" ? "denominations" : "total",
    };
  } catch {
    return defaults;
  }
}

const Context = createContext<{
  preferences: CashPreferences;
  updatePreferences: (patch: Partial<CashPreferences>) => void;
  error: string;
} | null>(null);

export function CashPreferencesProvider({
  scope,
  children,
}: {
  scope: string;
  children: ReactNode;
}) {
  const key = `gpgest.cash.preferences.${scope}`;
  const [preferences, setPreferences] = useState(() => readPreferences(key));
  const [error, setError] = useState("");
  function updatePreferences(patch: Partial<CashPreferences>) {
    const next = { ...preferences, ...patch };
    setPreferences(next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
      setError("");
    } catch {
      setError("Não foi possível guardar as preferências neste navegador.");
    }
  }
  return (
    <Context.Provider value={{ preferences, updatePreferences, error }}>
      {children}
    </Context.Provider>
  );
}

export function useCashPreferences() {
  const value = useContext(Context);
  if (!value)
    throw new Error("As preferências da folha de caixa não estão disponíveis.");
  return value;
}
