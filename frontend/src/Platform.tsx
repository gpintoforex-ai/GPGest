import { ActionLink } from "./components/InteractionUI";
import Brand from "./components/Brand";
import ThemeSettings from "./ThemeSettings";
import LegalAdmin from "./LegalAdmin";
import { PrivacyNotice } from "./Legal";
import { ActionButton, AppDialog, Notice, confirmFormExit } from "./components/InteractionUI";
import { useEffect, useRef, useState } from "react";
import { ShieldCheck, Building2, Plus, LogOut, KeyRound } from "lucide-react";
import { api } from "./lib/api";
import { Field, Form } from "./components/Form";
type Company = {
  id: string;
  name: string;
  slug: string;
  nif: string | null;
  active: boolean;
  mode: string;
  api_active: boolean;
  api_url: string;
  api_enterprise: string;
  has_api_key: boolean;
};
type CompanyUser = { id: string; name: string; email: string; role: string; active: boolean };
export default function Platform() {
  const companyDialog = useRef<HTMLDialogElement>(null);
  const [users, setUsers] = useState<CompanyUser[]>([]);
  const [resetUser, setResetUser] = useState<string | null>(null);
  const [editingEmail, setEditingEmail] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [resettingData, setResettingData] = useState<"movements" | "all" | null>(null);
  const [previousCompanies, setPreviousCompanies] = useState<Company[]>([]);
  const [pendingCreation, setPendingCreation] = useState<Record<string, any> | null>(null);
  const [admin, setAdmin] = useState<{ email: string; mode: string } | null>(null),
    [companies, setCompanies] = useState<Company[]>([]),
    [selected, setSelected] = useState<Company | null>(null),
    [editorVersion, setEditorVersion] = useState(0),
    [creating, setCreating] = useState(false),
    [loading, setLoading] = useState(true),
    [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [testing, setTesting] = useState(false),
    [audit, setAudit] = useState<any[]>([]);
  async function refresh() {
    setCompanies(await api("/platform/companies"));
    setAudit(await api("/platform/audit"));
  }
  useEffect(() => {
    document.title = "Plataforma · IAZELA";
  }, []);
  useEffect(() => {
    if (!selected || !companyDialog.current) return;
    const dialog = companyDialog.current;
    dialog.showModal();
    return () => {
      dialog.close();
    };
  }, [selected?.id]);
  useEffect(() => {
    let current = true;
    setUsers([]);
    setResetUser(null);
    setEditingEmail(null);
    setRemoving(false);
    setResettingData(null);
    if (selected) api("/platform/companies/" + selected.id + "/users")
      .then((rows) => { if (current) setUsers(rows); })
      .catch((e) => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [selected?.id]);
  useEffect(() => {
    api("/platform/me")
      .then(async (a) => {
        setAdmin(a);
        await refresh();
      })
      .catch(() => {})
      .finally(() => setLoading(false));
    const expired = () => {
      setAdmin(null);
      setCompanies([]);
      setSelected(null);
    };
    window.addEventListener("gpgest-expired", expired);
    return () => window.removeEventListener("gpgest-expired", expired);
  }, []);
  if (loading)
    return <div className="loading" role="status">A verificar acesso à plataforma…</div>;
  if (!admin)
    return (
      <div className="login">
        <section className="login-story">
          <div className="brand">
            <Brand /><ThemeSettings compact />
          </div>
          <h1>
            Administração
            <br />
            da plataforma.
          </h1>
          <p>Acesso exclusivo à gestão de empresas e ligações Wintouch.</p>
        </section>
        <section className="login-form">
          <span className="eyebrow">ACESSO RESERVADO</span>
          <h2>Entrar na plataforma</h2>
          <p>As credenciais de uma loja não dão acesso a esta área.</p>
          <PrivacyNotice />
          <Form
            submitLabel="Entrar"
            onSubmit={async (v) => {
              await api("/platform/login", v);
              setAdmin(await api("/platform/me"));
              await refresh();
            }}
          >
            <Field label="Email da plataforma">
              <input
                name="email"
                type="email"
                autoComplete="username"
                required
              />
            </Field>
            <Field label="Palavra-passe da plataforma">
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </Field>
          </Form>
          <p>
            <ActionLink href="/">Ir para acesso da empresa</ActionLink>
          </p>
        </section>
      </div>
    );
  return (
    <div className="platform-page">
      <header>
        <Brand /><ThemeSettings compact />
        <div>
          <span className="eyebrow">IAZELA · ACESSO RESERVADO</span>
          <h2>Gestão da plataforma</h2>
        </div>
        <ActionButton
          onClick={async () => {
            try {
              await api("/platform/logout", {});
              setAdmin(null);
              setCompanies([]);
              setSelected(null);
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          <LogOut size={18} />
          Sair
        </ActionButton>
      </header>
      <main>
        <div className="page-intro">
          <div>
            <h1>Empresas e ligações</h1>
            <p>
              Crie várias empresas, cada uma com o seu NIF, utilizadores, lojas e dados próprios.
            </p>
          </div>
          <ActionButton
            className="primary"
            onClick={() => {
              setCreating(true);
              setPendingCreation(null);
              setSelected(null);
              setNotice("");
            }}
          >
            <Plus size={18} />
            Criar empresa
          </ActionButton>
        </div>
        {notice && (
          <Notice onDismiss={() => setNotice("")} className="success" role="status">
            {notice}
          </Notice>
        )}
        {error && (
          <Notice className="error" role="alert">
            {error}
          </Notice>
        )}
        {creating && (
          <section className="panel platform-editor">
            <div className="panel-title">
              <h3>Nova empresa</h3>
              <ActionButton onClick={() => setCreating(false)}>Cancelar</ActionButton>
            </div>
            <PrivacyNotice />
            <Notice tone="info" role="note">
              <strong>Modo da nova empresa: {admin.mode === "demonstracao" ? "Demonstração" : "Real"}</strong>
              <p>{admin.mode === "demonstracao"
                ? "Esta empresa será criada em modo de demonstração, conforme a configuração atual da plataforma."
                : "Esta empresa será criada em modo real, conforme a configuração atual da plataforma."}</p>
            </Notice>
            <Form
              onSubmit={async (v) => {
                const previous = await api("/platform/removed-companies/" + v.nif);
                if (previous.length) {
                  setPreviousCompanies(previous);
                  setPendingCreation(v);
                  return;
                }
                await api("/platform/companies", v);
                setCreating(false);
                await refresh();
                setNotice(
                  "Empresa criada, com loja, caixa e administrador próprios.",
                );
              }}
            >
              <div className="form-grid">
                <Field label="Nome da empresa">
                  <input name="name" required minLength={2} maxLength={100} />
                </Field>
                <Field label="NIF da empresa">
                  <input name="nif" required inputMode="numeric" pattern="[0-9]{9}" minLength={9} maxLength={9} />
                </Field>
                <Field label="Nome do primeiro administrador">
                  <input
                    name="admin_name"
                    required
                    minLength={2}
                    maxLength={100}
                  />
                </Field>
                <Field label="Email do primeiro administrador">
                  <input
                    name="admin_email"
                    type="email"
                    required
                    maxLength={200}
                  />
                </Field>
              </div>
              <Field label="Palavra-passe inicial (mínimo 12 caracteres)">
                <input
                  name="admin_password"
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  maxLength={200}
                  required
                />
              </Field>
              <p className="footnote">
                O modo é definido na configuração da plataforma. Recuperar uma empresa anterior mantém o modo dessa empresa.
              </p>
            </Form>
            {pendingCreation && (
              <section className="panel" style={{ marginTop: 20 }}>
                <h3>Já existem dados guardados para o NIF {pendingCreation.nif}</h3>
                <p>Pretende recuperar uma empresa anterior ou criar uma nova empresa?</p>
                <Form submitLabel="Confirmar escolha" onSubmit={async (v) => {
                  if (v.action === "restore") {
                    const recovered = await api("/platform/companies/" + v.previous_company + "/restore", { confirmation: pendingCreation.nif });
                    setSelected(recovered);
                    setNotice("Empresa recuperada com os dados e utilizadores anteriores. As palavras-passe anteriores mantêm-se; pode repô-las na configuração. Reveja a ligação Wintouch antes de a ativar.");
                  } else if (v.action === "new") {
                    await api("/platform/companies", { ...pendingCreation, removed_action: "new" });
                    setNotice("Nova empresa criada sem os dados anteriores. Os registos antigos continuam preservados.");
                  } else throw new Error("Escolha uma opção.");
                  setPendingCreation(null);
                  setCreating(false);
                  await refresh();
                }}>
                  <label className="checkbox"><input type="radio" name="action" value="restore" required /> Recuperar os dados e utilizadores anteriores</label>
                  <Field label="Empresa a recuperar (mais recente primeiro)">
                    <select name="previous_company">{previousCompanies.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.id.slice(0, 8)}</option>)}</select>
                  </Field>
                  <label className="checkbox"><input type="radio" name="action" value="new" required /> Criar uma nova empresa, sem recuperar os dados</label>
                  <p>Ao recuperar, os dados e credenciais preenchidos para a nova empresa não substituem os anteriores.</p>
                </Form>
              </section>
            )}
          </section>
        )}
        {selected && (
          <AppDialog
            ref={companyDialog}
            className="company-config-dialog"
            aria-labelledby="company-config-title"
            onRequestClose={() => setSelected(null)}
          >
          <section
            className="panel platform-editor"
            key={selected.id + editorVersion}
          >
            <div className="panel-title">
              <h3 id="company-config-title">{selected.name} · Configuração</h3>

            </div>
            {notice && <Notice onDismiss={() => setNotice("")} className="success" role="status">{notice}</Notice>}
            {error && <Notice className="error" role="alert">{error}</Notice>}
            <h3>Utilizadores e acesso</h3>
            {users.map((u) => (
              <div key={u.id}>
                <p><b>{u.name}</b> · {u.email} · {u.role} · {u.active ? "Ativo" : "Desativado"}</p>
                <ActionButton type="button" onClick={() => setEditingEmail(editingEmail === u.id ? null : u.id)}>
                  {editingEmail === u.id ? "Cancelar edição do email" : "Editar email"}
                </ActionButton>
                {editingEmail === u.id && (
                  <Form submitLabel="Guardar email" onSubmit={async (v) => {
                    await api("/platform/companies/" + selected.id + "/users/" + u.id + "/email", { email: v.email });
                    setUsers(await api("/platform/companies/" + selected.id + "/users"));
                    setEditingEmail(null);
                    await refresh();
                    setNotice("Email atualizado. Use o novo email para entrar; a palavra-passe mantém-se.");
                  }}>
                    <Field label="Email do utilizador"><input name="email" type="email" defaultValue={u.email} required maxLength={200} /></Field>
                  </Form>
                )}
                <ActionButton type="button" onClick={() => setResetUser(resetUser === u.id ? null : u.id)}>
                  {resetUser === u.id ? "Cancelar reposição" : "Repor palavra-passe"}
                </ActionButton>
                {resetUser === u.id && (
                  <Form submitLabel="Guardar nova palavra-passe" onSubmit={async (v) => {
                    if (v.new_password !== v.confirm_password) throw new Error("As palavras-passe não coincidem.");
                    await api("/platform/companies/" + selected.id + "/users/" + u.id + "/reset-password", { new_password: v.new_password });
                    setResetUser(null);
                    await refresh();
                    setNotice("Palavra-passe reposta para " + u.email + ". As sessões anteriores foram terminadas.");
                  }}>
                    <Field label="Nova palavra-passe (mínimo 12 caracteres)">
                      <input name="new_password" type="password" autoComplete="new-password" required minLength={12} maxLength={200} />
                    </Field>
                    <Field label="Confirmar nova palavra-passe">
                      <input name="confirm_password" type="password" autoComplete="new-password" required minLength={12} maxLength={200} />
                    </Field>
                    <p>A reposição termina as sessões deste utilizador. Entregue-lhe a nova palavra-passe.</p>
                  </Form>
                )}
              </div>
            ))}
            <Form
              onSubmit={async (v) => {
                const c = await api(
                  "/platform/companies/" + selected.id + "/config",
                  {
                    name: v.name,
                    nif: v.nif,
                    mode: v.mode,
                    active: v.active === "on",
                    api_active: v.api_active === "on",
                    erp_provider: v.erp_provider,
                    rubric_code_prefix: v.rubric_code_prefix,
                    api_url: v.api_url,
                    api_enterprise: v.api_enterprise,
                    api_key: v.api_key || null,
                    clear_api_key: v.clear_api_key === "on",
                  },
                );
                setSelected(c);
                setEditorVersion((n) => n + 1);
                await refresh();
                setNotice("Configuração guardada.");
              }}
            >
              <Field label="Nome da empresa">
                <input
                  name="name"
                  defaultValue={selected.name}
                  required
                  minLength={2}
                />
              </Field>
              <Field label="NIF da empresa">
                <input name="nif" defaultValue={selected.nif || ""} required inputMode="numeric" pattern="[0-9]{9}" minLength={9} maxLength={9} />
              </Field>
              <Field label="Modo da empresa">
                <select name="mode" defaultValue={selected.mode} required>
                  <option value="real">Empresa real</option>
                  <option value="demonstracao">Empresa de demonstração</option>
                </select>
              </Field>
              <p className="footnote">Alterar o modo mantém os dados e utilizadores existentes. Os dados de demonstração não são apagados ao mudar para real.</p>
              <label className="checkbox">
                <input
                  type="checkbox"
                  name="active"
                  defaultChecked={selected.active}
                />{" "}
                Empresa ativa (desativar impede o acesso dos seus utilizadores)
              </label>
              <Field label="ERP para sincronização">
                <select name="erp_provider" defaultValue={(selected as any).erp_provider || "wintouch"}>
                  <option value="none">Sem ERP — gestão local</option>
                  <option value="wintouch">Wintouch</option>
                </select>
              </Field>
              <Field label="Prefixo dos códigos de rubricas no ERP">
                <input name="rubric_code_prefix" maxLength={30} pattern="[A-Za-z0-9_-]*" defaultValue={(selected as any).rubric_code_prefix ?? "RUB"} />
              </Field>
              <Field label="URL da API Wintouch">
                <input
                  name="api_url"
                  type="url"
                  defaultValue={selected.api_url}
                  required
                />
              </Field>
              <Field label="Identificador da empresa Wintouch (x-current-enterprise)">
                <input
                  name="api_enterprise"
                  defaultValue={selected.api_enterprise}
                  maxLength={100}
                />
              </Field>
              <Field
                label={
                  selected.has_api_key
                    ? "Nova chave API (deixe vazio para manter a guardada)"
                    : "Chave API"
                }
              >
                <input
                  name="api_key"
                  type="password"
                  autoComplete="new-password"
                  maxLength={4096}
                />
              </Field>
              {selected.has_api_key && (
                <label className="checkbox">
                  <input type="checkbox" name="clear_api_key" /> Remover a chave
                  guardada
                </label>
              )}
              <label className="checkbox">
                <input
                  type="checkbox"
                  name="api_active"
                  defaultChecked={selected.api_active}
                />{" "}
                Ativar ligação Wintouch
              </label>
              <p className="footnote">
                O teste consulta apenas um artigo pela API. Não emite
                documentos, não altera o Wintouch nem importa movimentos.
              </p>
            </Form>
            <ActionButton
              disabled={testing || !selected.api_active || !selected.active}
              onClick={async () => {
                setTesting(true);
                setError("");
                try {
                  const r = await api(
                    "/platform/companies/" + selected.id + "/test",
                    {},
                  );
                  if (r.success) setNotice(r.message);
                  else setError(r.message);
                  await refresh();
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setTesting(false);
                }
              }}
            >
              {testing ? "A testar…" : "Testar ligação de leitura"}
            </ActionButton>
            <div className="company-removal">
              <div className="actions">
                <ActionButton type="button" onClick={async e => { if (resettingData && !(await confirmFormExit(e.currentTarget.closest(".company-removal")))) return; setResettingData(resettingData === "movements" ? null : "movements"); }}>
                  {resettingData === "movements" ? "Cancelar reinicialização" : "Reinicializar movimentos e documentos"}
                </ActionButton>
                <ActionButton type="button" onClick={async e => { if (resettingData && !(await confirmFormExit(e.currentTarget.closest(".company-removal")))) return; setResettingData(resettingData === "all" ? null : "all"); }}>
                  {resettingData === "all" ? "Cancelar reinicialização" : "Reinicializar todos os dados"}
                </ActionButton>
              </div>
              {resettingData && (
                <Form key={resettingData} destructive submitLabel={resettingData === "movements" ? "Confirmar reinicialização dos movimentos e documentos" : "Confirmar reinicialização dos dados"} onSubmit={async (v) => {
                  const scope = resettingData;
                  await api("/platform/companies/" + selected.id + (scope === "movements" ? "/reset-movements" : "/reset-data"), {
                    confirmation: v.confirmation, acknowledged: v.acknowledged === "on",
                  });
                  setResettingData(null);
                  setNotice(scope === "movements"
                    ? "Movimentos e documentos reinicializados. Tabelas, bancos e configurações preservados. As sessões anteriores terminaram; os dados anteriores ficaram numa cópia interna."
                    : "Dados reinicializados. Utilizadores e configuração mantidos. As sessões anteriores terminaram; os dados anteriores ficaram numa cópia interna.");
                  void refresh().catch(() => setError("A reinicialização foi concluída, mas não foi possível atualizar a plataforma. Atualize a página; não repita a operação."));
                }}>
                  <h4>{resettingData === "movements" ? "Reinicializar movimentos e documentos" : "Reinicializar todos os dados"} · {selected.name}</h4>
                  {resettingData === "movements" ? <>
                    <p>Retira folhas e movimentos de caixa, extratos e movimentos bancários, e-Fatura, compras, documentos e anexos guardados na aplicação, documentos a crédito, pagamentos, depósitos, reconciliações, saldos de referência e histórico operacional desta empresa.</p>
                    <p><strong>Preserva tabelas e bancos:</strong> utilizadores, funcionários, lojas, caixas, rubricas, clientes, fornecedores, artigos, instituições e contas bancárias, terminais, meios de pagamento, associações aos bancos e configurações. Não altera dados nem ficheiros no ERP, na AT ou no Google Drive.</p>
                  </> : <p>Retira os movimentos, importações, documentos, pagamentos, reconciliações e histórico operacional da empresa. Mantém utilizadores, palavras-passe, lojas, caixas, categorias e ligação Wintouch.</p>}
                  <p>Termina as sessões da empresa. Os dados anteriores ficam guardados numa cópia interna, sem recuperação automática nesta janela.</p>
                  <Field label={"Escreva " + (selected.nif || selected.name) + " para confirmar"}>
                    <input name="confirmation" required autoComplete="off" />
                  </Field>
                  <label className="checkbox"><input name="acknowledged" type="checkbox" required /> Confirmo que pretendo reinicializar {resettingData === "movements" ? "os movimentos e documentos, mantendo tabelas e bancos," : "os dados"} desta empresa e terminar as sessões atuais.</label>
                </Form>
              )}
              <ActionButton type="button" onClick={() => setRemoving(!removing)}>
                {removing ? "Cancelar remoção" : "Remover empresa"}
              </ActionButton>
              {removing && (
                <Form destructive submitLabel="Confirmar remoção da empresa" onSubmit={async (v) => {
                  await api("/platform/companies/" + selected.id + "/remove", { confirmation: v.confirmation });
                  setSelected(null);
                  await refresh();
                  setNotice("Empresa removida da lista e acesso bloqueado. Os dados e o histórico foram preservados.");
                }}>
                  <p>A empresa deixará de aparecer na lista e os seus utilizadores perderão o acesso. Os dados e o histórico serão preservados.</p>
                  <Field label={"Escreva " + (selected.nif || selected.name) + " para confirmar"}>
                    <input name="confirmation" required autoComplete="off" />
                  </Field>
                </Form>
              )}
            </div>
          </section>
          </AppDialog>
        )}
        <div className="cash-grid">
          {companies.map((c) => (
            <section className="panel" key={c.id}>
              <div className="panel-title">
                <Building2 />
                <span className={"pill " + (!c.active ? "neutral" : "")}>
                  {c.active ? "Ativa" : "Desativada"}
                </span>
              </div>
              <h3>{c.name}</h3>
              <p>
                NIF: <b>{c.nif || "Por definir na configuração"}</b> · {c.mode}
              </p>
              <p>
                <KeyRound size={15} />{" "}
                {c.has_api_key ? "Chave API guardada" : "Sem chave API"} ·{" "}
                {c.api_active ? "Ligação ativada" : "Ligação desativada"}
              </p>
              <p>
                <ActionLink href={c.nif ? "/?nif=" + encodeURIComponent(c.nif) : "/?empresa=" + encodeURIComponent(c.slug)}>
                  Abrir acesso desta empresa
                </ActionLink>
              </p>
              <ActionButton
                onClick={() => {
                  setSelected(c);
                  setCreating(false);
                  setError("");
                  setNotice("");
                }}
              >
                Configurar empresa
              </ActionButton>
            </section>
          ))}
        </div>
        <section className="panel" style={{ marginTop: 24 }}>
          <h3>Histórico da plataforma</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Data</th>
                  <th>Empresa</th>
                  <th>Ação</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((a) => (
                  <tr key={a.id}>
                    <td>
                      {new Intl.DateTimeFormat("pt-PT", {
                        day: "2-digit",
                        month: "2-digit",
                        year: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                        timeZone: "Europe/Lisbon",
                      }).format(new Date(a.created_at))}
                    </td>
                    <td>
                      {companies.find((c) => c.id === a.company_id)?.name ||
                        "Plataforma"}
                    </td>
                    <td>{a.action.replaceAll("_", " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <LegalAdmin />
        <footer>
          Administração da plataforma · {admin.email}
          <span>Sessão reservada de 2 horas</span>
        </footer>
      </main>
    </div>
  );
}
