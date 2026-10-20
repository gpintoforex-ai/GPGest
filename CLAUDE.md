# GPGest (marca IAZELA)

Gestão financeira e operacional de lojas, multi-empresa, em **português de Portugal**.
Dois modos: **local autónomo** e **integrado** com o ERP Wintouch (ver `docs/MODOS-FUNCIONAMENTO.md`).
Não emite documentos fiscais nem substitui o ERP.

## Pilha

- Backend: FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16 (`backend/`, Python 3.13, venv em `.venv`).
- Frontend: React 19 + TypeScript + Vite 6 (`frontend/`), sem router nem biblioteca de estado; ícones `lucide-react`.
- Deploy: Cloudflare Worker (`cloudflare/worker.mjs`, `wrangler.jsonc`) serve `frontend/dist` e encaminha `/api`; base Neon (`docs/NEON.md`).

## Comandos (PowerShell, a partir da raiz)

```powershell
.\Iniciar.ps1                     # PostgreSQL local (porta 55432) + API 8001 + Vite 5174
.\Parar.ps1                       # para os processos iniciados pelo script
cd backend; ..\.venv\Scripts\python.exe -m pytest -q tests\test_cash.py   # testes de um módulo
cd backend; ..\.venv\Scripts\alembic.exe upgrade head                    # migrações
cd frontend; npx tsc -b --noEmit  # verificação de tipos
cd frontend; npm.cmd run build
.\.venv\Scripts\ruff.exe check backend scripts
.\.venv\Scripts\python.exe scripts\test-<area>-ui.py   # testes Playwright de interface
```

- A suite pytest completa é lenta (vários minutos): prefira correr os ficheiros do módulo alterado.
- Os testes exigem `APP_MODE=demonstracao` (`.env`) e criam um esquema PostgreSQL temporário; nunca apontar para uma base real.
- Logs do arranque por script: `.local\backend*.log`, `.local\frontend*.log`. Credenciais locais: `.local\ACESSO-LOCAL.txt`.

## Arquitetura do backend

- `app/main.py`: autenticação (`current`, `authorize`, `scope`), rotas base e, no fim, a chamada de
  `register_routes(app, current, authorize, ...)` de cada módulo de domínio (`inventory.py`, `deposits.py`,
  `saft_sales.py`, ...). Novos domínios seguem este padrão em vez de crescer `main.py`.
- `app/models.py` (dados por empresa) e `app/platform_models.py` (plataforma central).
- Multi-empresa: um esquema PostgreSQL por empresa; a plataforma (`platform.py`, `/plataforma`) gere empresas e chaves API cifradas.
- Integrações em `app/integrations/` (contrato ERP comum em `erp.py`, Wintouch, e-Fatura, bancos).
- Migrações em `backend/migrations/versions/NNNN_nome.py`, numeração sequencial (`revision = "0046"`, `down_revision = "0045"`).

## Regras de domínio (não quebrar)

- Dinheiro sempre em **cêntimos inteiros**; conversão de entrada com `Decimal`, nunca `float`.
- Datas com fuso horário, apresentadas e agrupadas por dia em `Europe/Lisbon`.
- Movimentos confirmados são imutáveis: corrigir com **estorno** (movimento inverso), nunca apagar.
- Autorização por loja feita no servidor; toda a escrita relevante fica na auditoria.
- Dados importados do ERP ficam separados dos saldos GPGest até validação explícita.
- Concorrência em caixa/fecho com bloqueios transacionais PostgreSQL.

## Convenções

- Textos de interface, mensagens de erro e documentação em português de Portugal (não brasileiro).
- Documentação funcional em `docs/*.md`, ligada a partir do `README.md`; atualizar quando o comportamento muda.
- Estilos: tokens em `frontend/src/design-system.css` e `themes.css`; verificar contraste (`docs/CONTRASTE-E-LEGIBILIDADE.md`).
- Python: ruff (`ruff.toml`, linha 100). Finais de linha LF (ver `.gitattributes`).
- `.env`, `.local/` e `backups/` são privados e nunca vão para o Git.
