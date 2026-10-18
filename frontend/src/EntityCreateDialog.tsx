import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, Field, Form } from "./App";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import RubricSelect, { type RubricOption } from "./components/RubricSelect";

export type EntityKind = "Clientes" | "Fornecedores";
export type EntityCategory = RubricOption;
export type CreatedEntity = {
  id: string;
  source: string;
  name: string;
  tax_number: string;
  address: string;
  locality: string;
  postal_code: string;
  email: string;
  phone: string;
  is_customer: boolean;
  is_supplier: boolean;
  active: boolean;
  category_id: string | null;
};

type LookupResult = {
  is_valid: boolean;
  name: string;
  address: string;
  locality: string;
  postal_code: string;
};

export default function EntityCreateDialog({
  kind,
  categories,
  initialName = "",
  onCancel,
  onCreated,
}: {
  kind: EntityKind;
  categories?: EntityCategory[];
  initialName?: string;
  onCancel: () => void;
  onCreated: (entity: CreatedEntity) => void;
}) {
  const [step, setStep] = useState<"nif" | "details">("nif");
  const [draft, setDraft] = useState<Partial<CreatedEntity>>({ name: initialName });
  const [categoryId, setCategoryId] = useState("");
  const [lookupMessage, setLookupMessage] = useState("");
  const [categoryOptions, setCategoryOptions] = useState(categories || []);
  const [categoryError, setCategoryError] = useState("");
  const lastLookupNif = useRef("");
  const lookupRequest = useRef(0);

  useEffect(() => {
    if (categories) {
      setCategoryOptions(categories);
      return;
    }
    let active = true;
    api("/categories")
      .then((result) => {
        if (active) setCategoryOptions(result as EntityCategory[]);
      })
      .catch((error) => {
        if (active) setCategoryError((error as Error).message);
      });
    return () => {
      active = false;
    };
  }, [categories]);

  const lookup = async (taxNumber: string): Promise<false> => {
    const normalizedNif = taxNumber
      .trim()
      .toUpperCase()
      .replace(/^PT/, "")
      .replace(/[\s.-]/g, "");
    if (!/^\d{9}$/.test(normalizedNif)) {
      throw new Error("Indique um NIF português com 9 dígitos.");
    }
    const result = (await api(
      `/entities/lookup?tax_number=${encodeURIComponent(taxNumber)}`,
    )) as LookupResult;
    lastLookupNif.current = normalizedNif;
    setDraft((current) => ({
      ...current,
      tax_number: taxNumber,
      name: result.is_valid ? result.name || current.name : current.name,
      address: result.is_valid ? result.address : current.address,
      locality: result.is_valid ? result.locality : current.locality,
      postal_code: result.is_valid ? result.postal_code : current.postal_code,
    }));
    setLookupMessage(
      result.is_valid
        ? "NIF validado no VIES. Confirme os dados e complete os campos que faltam."
        : "O VIES não confirmou este NIF-IVA. Pode continuar e preencher os dados manualmente.",
    );
    setStep("details");
    return false as const;
  };

  const lookupOnBlur = async (input: HTMLInputElement) => {
    const normalizedNif = input.value
      .trim()
      .toUpperCase()
      .replace(/^PT/, "")
      .replace(/[\s.-]/g, "");
    if (!/^\d{9}$/.test(normalizedNif) || normalizedNif === lastLookupNif.current) return;
    const requestId = ++lookupRequest.current;
    setLookupMessage("A consultar o VIES…");
    try {
      const result = (await api(
        `/entities/lookup?tax_number=${encodeURIComponent(input.value)}`,
      )) as LookupResult;
      if (requestId !== lookupRequest.current || !input.form) return;
      lastLookupNif.current = normalizedNif;
      const fillIfEmpty = (field: string, value: string) => {
        const control = input.form?.elements.namedItem(field);
        if (control instanceof HTMLInputElement && !control.value.trim() && value)
          control.value = value;
      };
      if (result.is_valid) {
        fillIfEmpty("name", result.name);
        fillIfEmpty("address", result.address);
        fillIfEmpty("locality", result.locality);
        fillIfEmpty("postal_code", result.postal_code);
        setLookupMessage(
          "NIF validado no VIES. Email e telefone devem ser preenchidos manualmente.",
        );
      } else {
        setLookupMessage(
          "O VIES não confirmou este NIF-IVA. Pode preencher os dados manualmente.",
        );
      }
    } catch (error) {
      if (requestId === lookupRequest.current)
        setLookupMessage(
          `Não foi possível consultar o VIES: ${(error as Error).message} Pode preencher os dados manualmente.`,
        );
    }
  };

  const dialog = (
    <AppDialog
      autoOpen
      className="modal"
      onRequestClose={onCancel}
      aria-labelledby="entity-create-title"
    >
      <div className="panel-title">
        <h2 id="entity-create-title">
          {step === "nif" ? "Criar entidade — começar pelo NIF" : "Criar entidade"}
        </h2>
      </div>
      {step === "nif" ? (
        <Form key="entity-nif-lookup" hideSubmit onSubmit={(values) => lookup(String(values.tax_number || ""))}>
          <Field label="NIF">
            <input
              name="tax_number"
              maxLength={40}
              autoComplete="off"
              defaultValue={draft.tax_number || ""}
              autoFocus
            />
          </Field>
          <p>
            Indique o NIF para procurar o nome e a morada no VIES. Se preferir, pode
            continuar sem consulta e preencher os dados manualmente.
          </p>
          <div className="ui-dialog-actions">
            <ActionButton
              type="button"
              onClick={(event) => {
                const taxNumber = String(
                  new FormData(event.currentTarget.form!).get("tax_number") || "",
                ).trim();
                setDraft((current) => ({ ...current, tax_number: taxNumber }));
                setLookupMessage("");
                setStep("details");
              }}
            >
              Preencher manualmente
            </ActionButton>
            <ActionButton type="submit" className="primary">
              Pesquisar no VIES
            </ActionButton>
          </div>
        </Form>
      ) : (
        <Form
          key="entity-details"
          submitLabel={kind === "Fornecedores" ? "Criar fornecedor" : "Criar cliente"}
          onCancel={onCancel}
          onSubmit={async (values) => {
            const entity = (await api("/entities", {
              name: values.name,
              tax_number: values.tax_number,
              address: values.address,
              locality: values.locality,
              postal_code: values.postal_code,
              email: values.email,
              phone: values.phone,
              is_customer: kind === "Clientes" || values.is_customer === "on",
              is_supplier: kind === "Fornecedores" || values.is_supplier === "on",
              active: values.active === "on",
              category_id: values.category_id || null,
            })) as CreatedEntity;
            onCreated(entity);
          }}
        >
          <Field label="Nome">
            <input
              name="name"
              required
              minLength={2}
              maxLength={300}
              defaultValue={draft.name || ""}
              autoFocus
            />
          </Field>
          <div className="form-grid">
            <Field label="NIF">
              <input
                name="tax_number"
                maxLength={40}
                defaultValue={draft.tax_number || ""}
                onChange={() => {
                  lookupRequest.current++;
                  lastLookupNif.current = "";
                  setLookupMessage("");
                }}
                onBlur={(event) => void lookupOnBlur(event.currentTarget)}
              />
            </Field>
            <Field label="Email">
              <input name="email" type="email" maxLength={200} defaultValue={draft.email || ""} />
            </Field>
            <Field label="Telefone">
              <input name="phone" maxLength={60} defaultValue={draft.phone || ""} />
            </Field>
          </div>
          {lookupMessage && <Notice role="status">{lookupMessage}</Notice>}
          {categoryError && <Notice className="error" role="alert">{categoryError}</Notice>}
          <Field label="Morada">
            <input name="address" maxLength={500} defaultValue={draft.address || ""} />
          </Field>
          <div className="form-grid">
            <Field label="Localidade">
              <input name="locality" maxLength={120} defaultValue={draft.locality || ""} />
            </Field>
            <Field label="Código postal">
              <input name="postal_code" maxLength={20} defaultValue={draft.postal_code || ""} />
            </Field>
          </div>
          <Field label="Rubrica do fornecedor">
            <RubricSelect
              categories={categoryOptions}
              value={categoryId}
              onChange={setCategoryId}
            />
          </Field>
          {kind === "Clientes" && (
            <label className="checkbox">
              <input type="checkbox" name="is_supplier" /> Fornecedor
            </label>
          )}
          {kind === "Fornecedores" && (
            <label className="checkbox">
              <input type="checkbox" name="is_customer" /> Cliente
            </label>
          )}
          <label className="checkbox">
            <input type="checkbox" name="active" defaultChecked /> Ativo
          </label>
        </Form>
      )}
    </AppDialog>
  );

  return createPortal(dialog, document.body);
}
