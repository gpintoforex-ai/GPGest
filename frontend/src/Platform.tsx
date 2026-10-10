import { useEffect, useState } from "react";
import { ShieldCheck, Building2, Plus, LogOut, KeyRound } from "lucide-react";
import { api, Form, Field } from "./App";
type Company = {
  id: string;
  name: string;
  slug: string;
  active: boolean;
  mode: string;
  api_active: boolean;
  api_url: string;
  api_enterprise: string;
  has_api_key: boolean;
};
export default function Platform() {
  const [admin, setAdmin] = useState<{ email: string } | null>(null),
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
    return <div className="loading">A verificar acesso à plataforma…</div>;
  if (!admin)
    return (
      <div className="login">
        <section className="login-story">
          <div className="brand">
            GP<span>Gest</span>
          </div>
          <ShieldCheck size={40} />
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
          <Form
            submitLabel="Entrar"
            onSubmit={async (v) => {
              const a = await api("/platform/login", v);
              setAdmin(a);
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
            <a href="/">Ir para acesso da empresa</a>
          </p>
        </section>
      </div>
    );
  return (
    <div className="platform-page">
      <header>
        <div>
          <span className="eyebrow">GPGEST · ACESSO RESERVADO</span>
          <h2>Gestão da plataforma</h2>
        </div>
        <button
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
        </button>
      </header>
      <main>
        <div className="page-intro">
          <div>
            <h1>Empresas e ligações</h1>
            <p>
              Dados isolados por empresa. Chaves API cifradas e nunca devolvidas
              ao navegador.
            </p>
          </div>
          <button
            className="primary"
            onClick={() => {
              setCreating(true);
              setSelected(null);
              setNotice("");
            }}
          >
            <Plus size={18} />
            Criar empresa
          </button>
        </div>
        {notice && (
          <div className="success" role="status">
            {notice}
          </div>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {creating && (
          <section className="panel platform-editor">
            <div className="panel-title">
              <h3>Nova empresa</h3>
              <button onClick={() => setCreating(false)}>Cancelar</button>
            </div>
            <Form
              onSubmit={async (v) => {
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
                <Field label="Identificador para entrar (ex.: loja-salutar)">
                  <input
                    name="slug"
                    required
                    pattern="[a-z0-9][a-z0-9-]{2,79}"
                    maxLength={80}
                  />
                </Field>
                <Field label="Nome do primeiro administrador">
                  <input name="admin_name" required minLength={2} />
                </Field>
                <Field label="Email do primeiro administrador">
                  <input name="admin_email" type="email" required />
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
                A nova empresa usa o modo deste ambiente. No ambiente local de
                demonstração, os seus registos também serão de demonstração.
              </p>
            </Form>
          </section>
        )}
        {selected && (
          <section
            className="panel platform-editor"
            key={selected.id + editorVersion}
          >
            <div className="panel-title">
              <h3>{selected.name} · Configuração</h3>
              <button onClick={() => setSelected(null)}>Fechar</button>
            </div>
            <Form
              onSubmit={async (v) => {
                const c = await api(
                  "/platform/companies/" + selected.id + "/config",
                  {
                    name: v.name,
                    active: v.active === "on",
                    api_active: v.api_active === "on",
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
              <label className="checkbox">
                <input
                  type="checkbox"
                  name="active"
                  defaultChecked={selected.active}
                />{" "}
                Empresa ativa (desativar impede o acesso dos seus utilizadores)
              </label>
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
            <button
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
            </button>
          </section>
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
                Identificador: <b>{c.slug}</b> · {c.mode}
              </p>
              <p>
                <KeyRound size={15} />{" "}
                {c.has_api_key ? "Chave API guardada" : "Sem chave API"} ·{" "}
                {c.api_active ? "Ligação ativada" : "Ligação desativada"}
              </p>
              <p>
                <a href={"/?empresa=" + encodeURIComponent(c.slug)}>
                  Abrir acesso desta empresa
                </a>
              </p>
              <button
                onClick={() => {
                  setSelected(c);
                  setCreating(false);
                  setError("");
                  setNotice("");
                }}
              >
                Configurar empresa
              </button>
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
        <footer>
          Administração da plataforma · {admin.email}
          <span>Sessão reservada de 2 horas</span>
        </footer>
      </main>
    </div>
  );
}
