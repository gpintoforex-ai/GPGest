import hashlib
import json
import os
import secrets
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from decimal import Decimal
from time import monotonic

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import db as database
from . import cash_local
from . import cash_posting
from .payment_methods import bank_defaults
from .operating_modes import policy as integration_policy
from .cash_sheet import (
    belongs_to_register,
    daily_enterprise,
    is_real_cash_movement,
    payment_key,
    real_receipts,
    reserve_auto_source,
    sheet_source_context,
    validate_count,
)
from .cash_sheet import day as sheet_day_of
from .cash_sheet import summary as sheet_summary
from .access import authorize, cents, current, record, scope, serial
from .db import db_session
from .entities import list_entities
from .table_rules import ensure_usable
from .models import (
    Access,
    Attachment,
    Audit,
    BankAccount,
    BankInstitution,
    PaymentTerminal,
    CompanySetup,
    AccountBalanceReference,
    BankDeposit,
    CashSession,
    Category,
    LoginSession,
    Movement,
    LocalPaymentMethod,
    PlannedAllocation,
    PlannedMovement,
    PlannedSettlement,
    Register,
    Store,
    User,
    WintouchCashAccount,
    WintouchSaleRecord,
    now,
    uid,
)
from .platform import SyncRequest, selection_router, sync_company, sync_history
from .platform import router as platform_router
from .platform_models import Company
from .schemas import (
    CashCount,
    CashSources,
    CloseCash,
    Handover,
    Login,
    MovementCreate,
    Name,
    OpenCash,
    PasswordChange,
    PaymentMethodStatus,
    Strict,
    PlannedCreate,
    PlannedPay,
    Reason,
    RegisterCreate,
    SalesImport,
    Transfer,
    UserCreate,
    WintouchSettlement,
)

@asynccontextmanager
async def integration_lifespan(app):
    from .integration_scheduler import start_scheduler
    stop = start_scheduler()
    try:
        yield
    finally:
        stop.set()


app = FastAPI(
    title="IAZELA",
    version="0.1.0",
    description="Gestão interna. Não emite documentos fiscais.",
    lifespan=integration_lifespan,
)
app.include_router(platform_router)
app.include_router(selection_router)
from .legal import router as legal_router
app.include_router(legal_router)


hasher = PasswordHash.recommended()
dummy_hash = hasher.hash(secrets.token_urlsafe(32))
attempts = defaultdict(deque)
IN = {"venda", "cliente", "entrada_extra", "reforco"}
DENOMINATIONS = {
    1,
    2,
    5,
    10,
    20,
    50,
    100,
    200,
    500,
    1000,
    2000,
    5000,
    10000,
    20000,
    50000,
}


@app.middleware("http")
async def protection(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = {str(request.base_url).rstrip("/")} | {
            v.strip() for v in os.getenv("ALLOWED_ORIGINS", "").split(",") if v.strip()
        }
        if "http://127.0.0.1:5174" in allowed:
            allowed.add("http://localhost:5174")
        if origin and origin not in allowed:
            return JSONResponse({"detail": "Origem do pedido não permitida."}, status_code=403)
    result = await call_next(request)
    result.headers["Cache-Control"] = "no-store"
    result.headers["X-Content-Type-Options"] = "nosniff"
    result.headers["X-Frame-Options"] = "DENY"
    result.headers["Referrer-Policy"] = "no-referrer"
    result.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    return result


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse(
        {"detail": "Operação duplicada ou conflito. Atualize os dados antes de repetir."},
        status_code=409,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/platform/"):
        return await request_validation_exception_handler(request, exc)
    labels = {
        "name": "Nome da empresa",
        "nif": "NIF da empresa",
        "slug": "Identificador da empresa",
        "admin_name": "Nome do administrador",
        "admin_email": "Email do administrador",
        "admin_password": "Palavra-passe inicial",
        "email": "Email",
        "password": "Palavra-passe",
        "api_url": "URL da API",
        "api_enterprise": "Identificador Wintouch",
        "api_key": "Chave API",
    }
    messages = []
    for error in exc.errors():
        field = str(error["loc"][-1])
        label = labels.get(field, "Formulário")
        kind = error["type"]
        ctx = error.get("ctx", {})
        if kind == "missing":
            reason = "preencha este campo"
        elif kind == "string_too_short":
            reason = f"introduza pelo menos {ctx['min_length']} caracteres"
        elif kind == "string_too_long":
            reason = f"introduza no máximo {ctx['max_length']} caracteres"
        elif kind == "string_pattern_mismatch" and field == "nif":
            reason = "introduza exatamente 9 dígitos"
        elif kind == "string_pattern_mismatch" and field == "slug":
            reason = "use entre 3 e 80 caracteres: letras sem acentos, números e hífen; comece com uma letra ou número"
        elif kind == "extra_forbidden":
            reason = "foi enviado um campo não permitido"
        else:
            reason = "valor inválido"
        messages.append(f"{label}: {reason}.")
    # Não devolver valores submetidos, sobretudo palavras-passe e chaves API.
    return JSONResponse({"detail": " ".join(messages)}, status_code=422)


def cash(db, user, session_id, write=False):
    session = (
        db.scalar(select(CashSession).where(CashSession.id == session_id).with_for_update())
        if write
        else db.get(CashSession, session_id)
    )
    if not session:
        raise HTTPException(404, "Sessão de caixa não encontrada.")
    register = db.get(Register, session.register_id)
    authorize(
        user,
        db,
        register.store_id,
        ["administrador", "gestor", "operador"] if write else None,
    )
    if write:
        if session.closed_at:
            raise HTTPException(409, "A caixa está fechada. Os movimentos estão bloqueados.")
        if user.role == "operador" and session.operator_id != user.id:
            raise HTTPException(403, "Esta caixa pertence a outro operador.")
        reserve_auto_source(db, session)
    return session, register


def balance(db, session):
    stored = json.loads(session.count_data or "{}")
    if session.closed_at and stored.get("final"):
        return stored.get(
            "ledger_balance_cents",
            next(row["expected_cents"] for row in stored["summary"] if row["key"] == "numerario"),
        )
    if cash_local.is_local(db, session, integration_policy):
        return next(row["expected_cents"] for row in cash_local.drawer_rows(db, session) if row["key"] == "numerario")
    total = session.opening_cents + int(
        db.scalar(
            select(func.sum(Movement.direction * Movement.amount_cents)).where(
                Movement.session_id == session.id, Movement.payment == "numerario", Movement.origin != "deposito_modulo"
            )
        )
        or 0
    )
    for move in db.scalars(select(Movement).where(Movement.session_id == session.id, Movement.origin == "deposito_modulo")):
        deposit = db.scalar(select(BankDeposit).where((BankDeposit.movement_id == move.id) | (BankDeposit.reversal_id == move.id)))
        if deposit:
            total += int(deposit.cash_amount * 100) * move.direction
    token, enterprise = sheet_source_context(db, session)
    if token:
        register = db.get(Register, session.register_id)
        for data in real_receipts(
            db, register.store_id, session.sheet_day or sheet_day_of(session.opened_at), enterprise
        ):
            if payment_key(data)[0] == "numerario":
                from .opening_fund import included_as_opening
                if included_as_opening(session, data):
                    continue
                total += data["amount_cents"] * data["direction"]
    return total


@app.get("/api/health")
def health(db: Session = Depends(db_session)):
    db.execute(select(1))
    return {"status": "ok", "mode": os.getenv("APP_MODE", "real")}


@app.post("/api/auth/login")
def login(data: Login, request: Request, response: Response, db: Session = Depends(db_session)):
    key = request.client.host if request.client else "local"
    times = attempts[key]
    while times and times[0] < monotonic() - 900:
        times.popleft()
    if len(times) >= 10:
        raise HTTPException(429, "Demasiadas tentativas. Aguarde 15 minutos.")
    times.append(monotonic())
    user = db.scalar(select(User).where(User.email == data.email.strip().lower()))
    valid = hasher.verify(data.password, user.password_hash if user else dummy_hash)
    if not user or not valid or not user.active:
        raise HTTPException(401, "Email ou palavra-passe incorretos.")
    times.clear()
    token = secrets.token_urlsafe(48)
    db.add(
        LoginSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires_at=now() + timedelta(hours=8),
        )
    )
    record(db, user, "login", user.id, {})
    db.commit()
    response.set_cookie(
        "gpgest_session",
        token,
        max_age=28800,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE") == "true",
        samesite="strict",
    )
    return serial(user)


@app.post("/api/auth/logout")
def logout(
    request: Request,
    response: Response,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    item = db.get(
        LoginSession,
        hashlib.sha256(request.cookies.get("gpgest_session", "").encode()).hexdigest(),
    )
    if item:
        db.delete(item)
    db.commit()
    response.delete_cookie("gpgest_session")
    return {"ok": True}


@app.post("/api/auth/password")
def change_password(
    data: PasswordChange,
    request: Request,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    if not hasher.verify(data.current_password, user.password_hash):
        raise HTTPException(403, "A palavra-passe atual está incorreta.")
    user.password_hash = hasher.hash(data.new_password)
    keep = hashlib.sha256(request.cookies.get("gpgest_session", "").encode()).hexdigest()
    for login in db.scalars(
        select(LoginSession).where(LoginSession.user_id == user.id, LoginSession.token_hash != keep)
    ):
        db.delete(login)
    record(db, user, "palavra_passe_alterada", user.id, {})
    db.commit()
    return {"ok": True}


@app.get("/api/bootstrap")
def bootstrap(user=Depends(current), db: Session = Depends(db_session)):
    ids = scope(user, db)
    last_opening = (
        select(CashSession.opening_cents)
        .where(CashSession.register_id == Register.id)
        .order_by(CashSession.opened_at.desc(), CashSession.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    users = (
        list(db.scalars(select(User)))
        if user.role == "administrador"
        else list(db.scalars(select(User).join(Access).where(Access.store_id.in_(ids)).distinct()))
    )
    user_stores = {u.id: [] for u in users}
    for user_id, store_id in db.execute(
        select(Access.user_id, Access.store_id).where(Access.user_id.in_(user_stores))
    ):
        user_stores[user_id].append(store_id)
    integration = integration_policy(db)
    catalog = cash_local.method_catalog(db) if integration["mode"] == "local" else None
    setup = db.get(CompanySetup, "default")
    profile = json.loads(setup.settings or "{}").get("company", {}) if setup else {}
    return {
        "integration": integration,
        "payment_methods": {row["id"]: row["name"] for row in catalog if row["active"]} if catalog is not None else None,
        "payment_method_catalog": catalog,
        "payment_method_bank_defaults": bank_defaults(db, ids),
        "payment_terminals": [serial(t) for t in db.scalars(select(PaymentTerminal).where(PaymentTerminal.store_id.in_(ids), PaymentTerminal.active.is_(True)))],
        "user": serial(user),
        "mode": db.info.get("company", {}).get("mode", os.getenv("APP_MODE", "real")),
        "company": {**profile, **db.info.get("company", {})},
        "stores": [serial(s) for s in db.scalars(select(Store).where(Store.id.in_(ids)))],
        "registers": [
            {**serial(r), "last_opening_cents": opening}
            for r, opening in db.execute(
                select(Register, last_opening).where(Register.store_id.in_(ids))
            )
        ],
        "accounts": [
            serial(a) for a in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(ids)))
        ],
        "bank_institutions": [
            serial(bank) for bank in db.scalars(select(BankInstitution).where(BankInstitution.active.is_(True)).order_by(BankInstitution.name))
        ],
        "categories": [serial(c) for c in db.scalars(select(Category).order_by(Category.name))],
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "role": u.role,
                "email": u.email,
                "active": bool(u.active),
                "store_ids": user_stores[u.id],
            }
            for u in users
        ],
    }


@app.post("/api/stores")
def create_store(data: Name, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    obj = Store(name=data.name.strip())
    db.add(obj)
    db.flush()
    record(db, user, "loja_criada", obj.id, {"name": obj.name})
    db.commit()
    return serial(obj)


@app.post("/api/registers")
def create_register(data: RegisterCreate, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, data.store_id, ["administrador", "gestor"])
    if not db.get(Store, data.store_id):
        raise HTTPException(404, "Loja não encontrada.")
    obj = Register(**data.model_dump())
    db.add(obj)
    db.flush()
    record(db, user, "caixa_criada", obj.id, {}, data.store_id)
    db.commit()
    return serial(obj)


from .banks import router as banks_router

app.include_router(banks_router)


from .rubrics import CategoryEdit, cash_difference_category, set_cash_difference


@app.post("/api/categories")
def create_category(data: CategoryEdit, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    if len(data.name.strip()) < 2:
        raise HTTPException(422, "Indique o nome da rubrica.")
    obj = Category(name=data.name.strip(), icon=data.icon, color=data.color, vat_deduction_percent=data.vat_deduction_percent)
    db.add(obj)
    db.flush()
    record(db, user, "categoria_criada", obj.id, {"name": obj.name})
    set_cash_difference(db, obj, data.cash_difference)
    db.commit()
    return serial(obj)


@app.post("/api/users")
def create_user(data: UserCreate, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    if "@" not in data.email:
        raise HTTPException(422, "Email inválido.")
    for store_id in set(data.store_ids):
        if not db.get(Store, store_id):
            raise HTTPException(422, "Loja inválida.")
    obj = User(
        name=data.name,
        email=data.email.strip().lower(),
        password_hash=hasher.hash(data.password),
        role=data.role,
    )
    db.add(obj)
    db.flush()
    for store_id in set(data.store_ids):
        db.add(Access(user_id=obj.id, store_id=store_id))
    record(db, user, "utilizador_criado", obj.id, {"role": obj.role})
    db.commit()
    return serial(obj)


@app.get("/api/sessions")
def sessions(user=Depends(current), db: Session = Depends(db_session)):
    rows = db.scalars(
        select(CashSession)
        .join(Register)
        .where(Register.store_id.in_(scope(user, db)))
        .order_by(CashSession.opened_at.desc())
        .limit(200)
    )
    rows = list(rows)
    open_ids = {s.register_id for s in rows if not s.closed_at}
    context = {"registers": {r.id: r for r in db.scalars(select(Register).where(Register.id.in_(open_ids)))} if open_ids else {},
               "setup": db.get(CompanySetup, "default") if open_ids else None}
    return [session_result(db, s, context=context) for s in rows]


def session_result(db, s, rows=None, context=None):
    context = context or {}
    register = (context.get("registers", {}).get(s.register_id) or db.get(Register, s.register_id)) if not s.closed_at else None
    setup = (context["setup"] if "setup" in context else db.get(CompanySetup, "default")) if not s.closed_at else None
    local = cash_local.is_local(db, s, integration_policy)
    if rows is None:
        rows = cash_local.drawer_rows(db, s) if local else sheet_summary(db, s)
    counting = json.loads(s.count_data or "{}")
    if s.closed_at and not counting:
        counting = {
            "values": {"numerario": s.counted_cents},
            "counts": json.loads(s.denominations),
            "reason": s.close_reason,
            "cash_mode": "denominations",
        }
    return {
        **serial(s),
        "count_data": counting,
        "balance_cents": next(row["expected_cents"] for row in rows if row["key"] == "numerario") if local else balance(db, s),
        "totals": {row["key"]: row["net_cents"] for row in rows},
        "sheet": {
            "workflow_mode": "local" if local else "integrated",
            "day": s.sheet_day or sheet_day_of(s.opened_at),
            "include_wintouch": bool(daily_enterprise(db, s))
            if not s.closed_at
            else counting.get("include_wintouch", bool(s.wintouch_scope)),
            "scope": "register_day" if local else (
                "store_day" if not s.closed_at or counting.get("scope") == "store_day" else "session"
            ),
            "rows": rows,
            "currency": counting.get("currency", "EUR") if s.closed_at else register.currency,
            "preferences": counting.get("register_preferences", {}) if s.closed_at else json.loads(register.settings or "{}"),
            "require_reason": counting.get("require_reason", True) if s.closed_at else json.loads(setup.settings or "{}").get("preferences", {}).get("require_reason", True) if setup else True,
            "manual_realized": counting.get("manual_realized", True) if s.closed_at else json.loads(setup.settings or "{}").get("preferences", {}).get("manual_realized", True) if setup else True,
            "reconciliation": cash_local.reconciliation(db, s, rows) if local else None,
        },
    }


@app.get("/api/registers/{register_id}/opening-suggestion")
def opening_suggestion(register_id: str, sheet_day: str, user=Depends(current), db: Session = Depends(db_session)):
    from datetime import date
    from .opening_fund import candidates
    try:
        selected_day = date.fromisoformat(sheet_day).isoformat()
    except ValueError:
        raise HTTPException(422, "Indique um dia válido.") from None
    register = db.get(Register, register_id)
    if not register:
        raise HTTPException(404, "Caixa não encontrada.")
    authorize(user, db, register.store_id, ["administrador", "gestor", "operador"])
    available = candidates(db, register, selected_day)
    last = db.scalar(select(CashSession.opening_cents).where(CashSession.register_id == register_id).order_by(CashSession.opened_at.desc(), CashSession.id.desc()).limit(1))
    preferences = json.loads(register.settings or "{}")
    suggested = last
    if preferences:
        initial = db.get(AccountBalanceReference, "cash:" + register.id)
        previous = db.scalar(select(CashSession).where(CashSession.register_id == register_id).order_by(CashSession.opened_at.desc(), CashSession.id.desc()).limit(1))
        if not previous and initial and selected_day >= initial.reference_date:
            suggested = initial.amount_cents
        elif preferences.get("balance_mode") == "carry" and previous and previous.closed_at and previous.counted_cents is not None:
            suggested = previous.counted_cents
        else:
            suggested = preferences.get("fund_cents", last)
    return {"candidate": available[0] if len(available) == 1 else None, "ambiguous": len(available) > 1, "last_opening_cents": suggested}


@app.post("/api/sessions")
def open_session(data: OpenCash, user=Depends(current), db: Session = Depends(db_session)):
    r = db.scalar(select(Register).where(Register.id == data.register_id).with_for_update())
    if not r:
        raise HTTPException(404, "Caixa não encontrada.")
    authorize(user, db, r.store_id, ["administrador", "gestor", "operador"])
    if not r.active:
        raise HTTPException(409, f"A caixa «{r.name}» está inativa e não abre novas folhas de caixa. Reative-a em Tabelas → Caixas.")
    if db.scalar(
        select(CashSession).where(CashSession.register_id == r.id, CashSession.closed_at.is_(None))
    ):
        raise HTTPException(409, "Esta caixa já tem uma sessão aberta.")
    from zoneinfo import ZoneInfo

    sheet_day = (data.sheet_day or now().astimezone(ZoneInfo(json.loads(r.settings or "{}").get("timezone", "Europe/Lisbon"))).date()).isoformat()
    if data.opening_wintouch_id:
        from .models import WintouchSaleRecord
        from .opening_fund import candidates
        source = db.scalar(select(WintouchSaleRecord).where(WintouchSaleRecord.id == data.opening_wintouch_id).with_for_update())
        if not source or data.include_wintouch is False or not any(c["id"] == source.id for c in candidates(db, r, sheet_day)):
            raise HTTPException(409, "O fundo Wintouch não está disponível para esta caixa e dia. Atualize a sugestão.")
    wintouch_scope, enterprise = sheet_source(db, r.store_id, sheet_day, data.include_wintouch)
    s = CashSession(
        register_id=r.id,
        operator_id=user.id,
        opening_cents=cents(data.opening, True),
        retained_fund_cents=json.loads(r.settings or "{}").get("fund_cents"),
        sheet_day=sheet_day,
        wintouch_scope=wintouch_scope,
        wintouch_enterprise=enterprise,
        count_data=json.dumps({"wintouch_excluded": data.include_wintouch is False, "opening_wintouch_id": data.opening_wintouch_id}),
    )
    db.add(s)
    db.flush()
    if data.include_wintouch is None:
        reserve_auto_source(db, s)
    record(
        db,
        user,
        "abertura",
        s.id,
        {
            "opening_cents": s.opening_cents,
            "opening_wintouch_id": data.opening_wintouch_id,
            "sheet_day": sheet_day,
            "include_wintouch": bool(s.wintouch_scope),
        },
        r.store_id,
    )
    db.commit()
    return serial(s)


def sheet_source(db, store_id, sheet_day, include, exclude_id=None):
    if not include:
        return None, None
    with database.next_platform_session() as core:
        company = core.get(Company, db.info.get("company", {}).get("id"))
        if not company or not company.api_enterprise:
            raise HTTPException(
                422, "Configure a ligação Wintouch desta empresa antes de incluir os recebimentos."
            )
        enterprise = company.api_enterprise
    token = store_id + ":" + sheet_day
    query = select(CashSession).where(CashSession.wintouch_scope == token)
    if exclude_id:
        query = query.where(CashSession.id != exclude_id)
    if db.scalar(query):
        raise HTTPException(
            409, "Os recebimentos Wintouch deste dia já pertencem a outra folha desta loja."
        )
    return token, enterprise


@app.post("/api/sessions/{session_id}/sources")
def change_sheet_sources(
    session_id: str, data: CashSources, user=Depends(current), db: Session = Depends(db_session)
):
    s, r = cash(db, user, session_id, True)
    saved = json.loads(s.count_data or "{}")
    if (data.sheet_day.isoformat() != (s.sheet_day or sheet_day_of(s.opened_at))
            and any(value is not None for field in ("values", "debits", "credits")
                    for value in saved.get("realized", {}).get(field, {}).values())):
        raise HTTPException(409, "Esta folha já tem faturado, débitos ou créditos manuais guardados. Limpe os valores e guarde o rascunho antes de alterar o dia, ou crie outra folha.")
    previous = {"day": s.sheet_day, "include_wintouch": bool(s.wintouch_scope)}
    if data.include_wintouch is None:
        token, enterprise = (
            (s.wintouch_scope, s.wintouch_enterprise)
            if s.sheet_day == data.sheet_day.isoformat()
            else (None, None)
        )
    else:
        token, enterprise = sheet_source(
            db, r.store_id, data.sheet_day.isoformat(), data.include_wintouch, s.id
        )
    s.sheet_day = data.sheet_day.isoformat()
    s.wintouch_scope, s.wintouch_enterprise = token, enterprise
    counting = json.loads(s.count_data or "{}")
    if data.include_wintouch is not None:
        counting["wintouch_excluded"] = not data.include_wintouch
    s.count_data = json.dumps(counting, ensure_ascii=False)
    if data.include_wintouch is None:
        reserve_auto_source(db, s)
    record(
        db,
        user,
        "origens_folha_caixa",
        s.id,
        {"before": previous, "after": data.model_dump(mode="json")},
        r.store_id,
    )
    db.commit()
    return session_result(db, s)


@app.post("/api/sessions/{session_id}/count")
def save_count(
    session_id: str, data: CashCount, user=Depends(current), db: Session = Depends(db_session)
):
    s, r = cash(db, user, session_id, True)
    local = cash_local.is_local(db, s, integration_policy)
    rows = cash_local.drawer_rows(db, s) if local else sheet_summary(db, s)
    if local:
        counting = cash_local.validate(db, s, data, rows, cents, user)
    else:
        if data.realized is not None:
            raise HTTPException(409, "O realizado manual está disponível no modo local. Os valores ERP foram preservados.")
        counting = {**json.loads(s.count_data or "{}"), **validate_count(data, rows, cents)}
    counting["opening_wintouch_id"] = json.loads(s.count_data or "{}").get("opening_wintouch_id")
    counting["wintouch_excluded"] = json.loads(s.count_data or "{}").get("wintouch_excluded", False)
    counting["updated_at"] = now().isoformat()
    counting.update(updated_by=user.id, updated_by_name=user.name)
    s.count_data = json.dumps(counting, ensure_ascii=False)
    record(db, user, "contagem_guardada", s.id, counting, r.store_id)
    db.commit()
    return session_result(db, s, rows)


@app.post("/api/sessions/{session_id}/reconcile")
def reconcile_cash(session_id: str, data: CashCount, user=Depends(current), db: Session = Depends(db_session)):
    s, r = cash(db, user, session_id, True)
    if not cash_local.is_local(db, s, integration_policy):
        raise HTTPException(409, "Use a conferência de valores ERP no modo integrado.")
    rows = cash_local.drawer_rows(db, s)
    counting = cash_local.validate(db, s, data, rows, cents, user, complete=True)
    s.count_data = json.dumps(counting, ensure_ascii=False)
    record(db, user, "conferencia_folha_caixa", s.id, counting, r.store_id)
    db.commit()
    return session_result(db, s, rows)


@app.post("/api/cash/payment-methods")
def create_cash_payment_method(data: Name, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    if integration_policy(db)["mode"] != "local":
        raise HTTPException(409, "Configure os meios locais no modo local autónomo.")
    name = data.name.strip()
    if not name or name.casefold() in {label.casefold() for label in cash_local.methods(db).values()}:
        raise HTTPException(422, "Indique um nome distinto para o meio de pagamento.")
    method = LocalPaymentMethod(id="pm_" + uid().replace("-", "")[:16], name=name)
    db.add(method)
    record(db, user, "meio_pagamento_local_criado", method.id, {"name": name})
    db.commit()
    return serial(method)


@app.post("/api/cash/payment-methods/{method_id}/status")
def cash_payment_method_status(method_id: str, data: PaymentMethodStatus, user=Depends(current), db: Session = Depends(db_session)):
    from sqlalchemy.dialects.postgresql import insert

    authorize(user, db, roles=["administrador", "gestor"])
    if integration_policy(db)["mode"] != "local":
        raise HTTPException(409, "Configure os meios locais no modo local autónomo.")
    current_method = next((row for row in cash_local.method_catalog(db) if row["id"] == method_id), None)
    if current_method is None:
        raise HTTPException(404, "Meio de pagamento não encontrado nesta empresa.")
    if current_method["required"] and not data.active:
        raise HTTPException(422, "O numerário é obrigatório para conferir o dinheiro físico da gaveta.")
    if current_method["active"] != data.active:
        db.execute(
            insert(LocalPaymentMethod)
            .values(id=method_id, name=current_method["name"], active=data.active)
            .on_conflict_do_update(index_elements=[LocalPaymentMethod.id], set_={"active": data.active})
        )
        record(db, user, "meio_pagamento_local_ativado" if data.active else "meio_pagamento_local_inativado", method_id,
               {"name": current_method["name"], "before": current_method["active"], "active": data.active})
        db.commit()
    return {**current_method, "active": data.active}


@app.post("/api/sessions/{session_id}/close")
def close_session(
    session_id: str,
    data: CloseCash,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    s, r = cash(db, user, session_id, True)
    reserve_auto_source(db, s)
    local = cash_local.is_local(db, s, integration_policy)
    rows = cash_local.drawer_rows(db, s) if local else sheet_summary(db, s)
    if local:
        counting = cash_local.validate(db, s, data, rows, cents, user, closing=True)
    else:
        if data.realized is not None:
            raise HTTPException(409, "Os valores ERP não podem ser substituídos por realizado manual no modo integrado.")
        setup = db.get(CompanySetup, "default")
        counting = {**json.loads(s.count_data or "{}"), **validate_count(data, rows, cents, closing=True,
            count_required=json.loads(r.settings or "{}").get("count_required", True),
            require_reason=json.loads(setup.settings or "{}").get("preferences", {}).get("require_reason", True) if setup else True)}
    local_result = cash_local.reconciliation(db, s, rows, counting) if local else None
    debit_total = sum(row["debits_cents"] for row in (local_result["rows"] if local else rows))
    if debit_total > 0 and data.supplier_answer != "indicado":
        raise HTTPException(422, "Indique o fornecedor dos débitos antes de fechar a caixa.")
    # A reopened sheet must not silently reuse a supplier from a previous close.
    counting.pop("debit_supplier", None)
    supplier = None
    supplier_allocations = []
    if data.supplier_allocations is not None:
        if data.supplier_answer != "indicado":
            raise HTTPException(422, "Confirme que pretende indicar fornecedores.")
        if not data.supplier_allocations:
            raise HTTPException(422, "Indique pelo menos um fornecedor e respetivo valor.")
        allocation_ids = [(item.supplier_id, item.payment if local else None) for item in data.supplier_allocations]
        if len(allocation_ids) != len(set(allocation_ids)):
            raise HTTPException(422, "Cada fornecedor só pode ser indicado uma vez por meio de pagamento.")
        allocated_total = sum(item.amount_cents for item in data.supplier_allocations)
        if allocated_total > debit_total:
            raise HTTPException(422, "O valor atribuído ultrapassa o total dos débitos.")
        active_suppliers = {
            item["id"]: item
            for item in list_entities(user, db)
            if item["is_supplier"] and item["active"]
        }
        for allocation in data.supplier_allocations:
            selected = active_suppliers.get(allocation.supplier_id)
            if not selected:
                raise HTTPException(404, "Fornecedor ativo não encontrado nesta empresa.")
            supplier_allocations.append({
                "id": selected["id"],
                "name": selected["name"],
                "tax_number": selected["tax_number"],
                "source": selected["source"],
                "amount_cents": allocation.amount_cents,
                **({"payment": allocation.payment} if local and allocation.payment else {}),
            })
        if local:
            debit_methods = {row["key"]: row["debits_cents"] for row in local_result["rows"] if row["debits_cents"] > 0}
            for allocation in supplier_allocations:
                if not allocation.get("payment") and len(debit_methods) == 1:
                    allocation["payment"] = next(iter(debit_methods))
                if allocation.get("payment") and allocation["payment"] not in debit_methods:
                    raise HTTPException(422, "Escolha um meio de pagamento com débitos nesta folha.")
            for key, total in debit_methods.items():
                if sum(a["amount_cents"] for a in supplier_allocations if a.get("payment") == key) > total:
                    raise HTTPException(422, "O valor atribuído aos fornecedores ultrapassa os débitos desse meio de pagamento.")
            if len({(a["id"], a.get("payment")) for a in supplier_allocations}) != len(supplier_allocations):
                raise HTTPException(422, "Cada fornecedor só pode ser indicado uma vez por meio de pagamento.")
        if len(supplier_allocations) == 1:
            supplier = active_suppliers[supplier_allocations[0]["id"]]
    elif data.supplier_id:
        if data.supplier_answer != "indicado":
            raise HTTPException(422, "Confirme que pretende indicar um fornecedor.")
        supplier = next((e for e in list_entities(user, db) if e["id"] == data.supplier_id and e["is_supplier"] and e["active"]), None)
        if not supplier:
            raise HTTPException(404, "Fornecedor ativo não encontrado nesta empresa.")
    supplier_name = supplier["name"] if supplier else data.supplier_name.strip()
    if data.supplier_answer == "indicado" and not supplier_allocations and len(supplier_name) < 2:
        raise HTTPException(422, "Indique o nome do fornecedor dos débitos.")
    display_supplier_name = ""
    if data.supplier_answer == "indicado":
        if data.supplier_allocations is None or supplier:
            display_supplier_name = supplier_name
        elif len(supplier_allocations) == 1:
            display_supplier_name = supplier_allocations[0]["name"]
    if data.supplier_answer:
        assigned_total = sum(item["amount_cents"] for item in supplier_allocations) if supplier_allocations else debit_total
        counting["debit_supplier"] = {
            "answer": data.supplier_answer,
            "name": display_supplier_name,
            "debits_cents": assigned_total,
            "unassigned_cents": debit_total - assigned_total,
        }
        if supplier_allocations:
            counting["debit_supplier"]["allocations"] = supplier_allocations
        if supplier and not supplier_allocations:
            counting["debit_supplier"].update(id=supplier["id"], tax_number=supplier["tax_number"], source=supplier["source"])
        can_associate_movements = (
            data.supplier_answer == "indicado"
            and (not supplier_allocations or (
                len(supplier_allocations) == 1
                and supplier_allocations[0]["amount_cents"] == debit_total
            ))
        )
        if can_associate_movements:
            # Associate only unidentified debits covered by this store/day sheet.
            local_debit_methods = {row["key"] for row in local_result["rows"] if row["debits_cents"] > 0} if local else set()
            debit_query = select(Movement).join(CashSession).join(Register).where(
                Register.store_id == r.store_id, Movement.direction == -1,
                Movement.reversed_by.is_(None), Movement.reversal_of.is_(None),
                Movement.origin != "fecho_caixa",
                Movement.origin != "deposito_modulo",
            )
            if local:
                debit_query = debit_query.where(Register.id == r.id, Movement.kind.in_(("despesa", "fornecedor")), Movement.transfer_id.is_(None), Movement.payment.in_(local_debit_methods))
            local_debits = db.scalars(debit_query)
            local_ids = []
            for debit in local_debits:
                if (debit.entity or "").strip() in ("", "Entidade não indicada") and sheet_day_of(debit.occurred_at) == (s.sheet_day or sheet_day_of(s.opened_at)):
                    debit.entity = supplier_name
                    local_ids.append(debit.id)
            def covers_imported_debit(item):
                if not local:
                    return True
                key = "mbway" if str(item.get("payment", "")).casefold().replace(" ", "") == "mbway" else payment_key(item)[0]
                return key in local_debit_methods and belongs_to_register(db, s, r, item)

            imported_ids = [item["_record_id"] for item in real_receipts(
                db, r.store_id, s.sheet_day or sheet_day_of(s.opened_at), daily_enterprise(db, s)
            ) if item.get("source_kind") == "cash_movement" and item.get("direction") == -1
                and (item.get("entity") or "").strip() in ("", "Entidade não indicada")
                and covers_imported_debit(item)]
            counting["debit_supplier"].update(local_movement_ids=local_ids, imported_movement_ids=imported_ids)
    counting["opening_wintouch_id"] = json.loads(s.count_data or "{}").get("opening_wintouch_id")
    counting["wintouch_excluded"] = json.loads(s.count_data or "{}").get("wintouch_excluded", False)
    counted = counting["values"].get("numerario")
    expected = next(row["expected_cents"] for row in rows if row["key"] == "numerario")
    ledger_expected = expected
    if local:
        expected = next(row["expected_cents"] for row in local_result["rows"] if row["key"] == "numerario")
        cash_posting.post_close(db, s, r, local_result, counting, user, record)
        # Freeze the actual ledger after posting. The declared totals and count
        # discrepancy remain separate, so nothing is added a second time.
        rows = cash_local.drawer_rows(db, s)
        ledger_expected = next(row["expected_cents"] for row in rows if row["key"] == "numerario")
    counting.update(
        final=True,
        summary=rows,
        updated_at=now().isoformat(),
        updated_by=user.id,
        updated_by_name=user.name,
        closed_by=user.id,
        closed_by_name=user.name,
        workflow_mode="local" if local else "integrated",
        register_preferences=json.loads(r.settings or "{}"),
        currency=r.currency,
        manual_realized=json.loads(db.get(CompanySetup, "default").settings or "{}").get("preferences", {}).get("manual_realized", True) if db.get(CompanySetup, "default") else True,
        require_reason=json.loads(db.get(CompanySetup, "default").settings or "{}").get("preferences", {}).get("require_reason", True) if db.get(CompanySetup, "default") else True,
        scope="register_day" if local else "store_day",
        ledger_balance_cents=ledger_expected if local else balance(db, s),
        include_wintouch=bool(daily_enterprise(db, s)),
    )
    if local:
        counting["reconciliation_check"]["signature"] = cash_local.reconciliation(db, s, rows, counting)["signature"]
        counting["local_reconciliation"] = cash_local.reconciliation(db, s, rows, counting)
    s.count_data = json.dumps(counting, ensure_ascii=False)
    s.counted_cents = counted
    s.expected_cents = expected
    s.closed_at = now()
    s.close_reason = data.reason
    s.denominations = json.dumps(data.counts)
    difference = counted - expected if counted is not None else None
    if difference and not local:
        # Keep the pre-adjustment snapshot so the closed sheet retains its discrepancy.
        category = cash_difference_category(db)
        adjustment = Movement(
            session_id=s.id,
            user_id=user.id,
            category_id=category.id,
            kind="diferenca_caixa",
            payment="numerario",
            amount_cents=abs(difference),
            direction=1 if difference > 0 else -1,
            occurred_at=s.closed_at,
            document=f"Fecho de caixa {s.sheet_day}",
            reference=s.id,
            notes=data.reason,
            origin="fecho_caixa",
            request_key=uid(),
        )
        db.add(adjustment)
        db.flush()
        counting["adjustment_id"] = adjustment.id
        s.count_data = json.dumps(counting, ensure_ascii=False)
        record(
            db,
            user,
            "diferenca_caixa",
            adjustment.id,
            {"session_id": s.id, "difference_cents": difference, "reason": data.reason},
            r.store_id,
        )
    record(
        db,
        user,
        "fecho",
        s.id,
        {
            "counted_cents": counted,
            "expected_cents": expected,
            "difference_cents": difference,
            "reason": data.reason,
            "payment_counts": counting["values"],
            "debit_supplier": counting.get("debit_supplier"),
        },
        r.store_id,
    )
    db.commit()
    return serial(s)


@app.post("/api/sessions/{session_id}/reopen")
def reopen_session(
    session_id: str,
    data: Reason,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    s, r = cash(db, user, session_id)
    authorize(user, db, r.store_id, ["administrador", "gestor"])
    db.scalar(select(Register).where(Register.id == r.id).with_for_update())
    s = db.scalar(select(CashSession).where(CashSession.id == session_id).with_for_update())
    if not s.closed_at:
        raise HTTPException(409, "A caixa já está aberta.")
    if db.scalar(
        select(CashSession).where(CashSession.register_id == r.id, CashSession.closed_at.is_(None))
    ):
        raise HTTPException(409, "Existe outra sessão aberta nesta caixa.")
    cash_posting.reverse_close(db, s, r, user, data.reason, record)
    record(
        db,
        user,
        "reabertura",
        s.id,
        {
            "reason": data.reason,
            "previous_close": s.closed_at.isoformat(),
            "counted_cents": s.counted_cents,
            "expected_cents": s.expected_cents,
            "denominations": s.denominations,
            "count_data": json.loads(s.count_data or "{}"),
        },
        r.store_id,
    )
    s.closed_at = None
    s.counted_cents = None
    s.expected_cents = None
    counting = json.loads(s.count_data or "{}")
    counting.pop("adjustment_id", None)
    adjustments = db.scalars(
        select(Movement).where(
            Movement.session_id == s.id,
            Movement.origin == "fecho_caixa",
            Movement.kind == "diferenca_caixa",
            Movement.reversed_by.is_(None),
            Movement.reversal_of.is_(None),
        )
    ).all()
    for adjustment in adjustments:
        if not adjustment.reversed_by:
            reversal = Movement(
                session_id=s.id,
                user_id=user.id,
                category_id=adjustment.category_id,
                kind="diferenca_caixa",
                payment=adjustment.payment,
                amount_cents=adjustment.amount_cents,
                direction=-adjustment.direction,
                occurred_at=adjustment.occurred_at,
                origin="fecho_caixa",
                reference=s.id,
                notes=f"Reabertura: {data.reason}",
                request_key=uid(),
                reversal_of=adjustment.id,
            )
            db.add(reversal)
            db.flush()
            adjustment.reversed_by = reversal.id
            record(
                db,
                user,
                "estorno",
                adjustment.id,
                {"reversal": reversal.id, "reason": data.reason},
                r.store_id,
            )
    counting.pop("final", None)
    counting.pop("summary", None)
    counting.pop("local_reconciliation", None)
    counting.pop("reconciliation_check", None)
    counting.pop("posted_movements", None)
    counting.update(reopened_by=user.id, reopened_by_name=user.name,
                    reopened_at=now().isoformat(), reopen_reason=data.reason)
    s.count_data = json.dumps(counting, ensure_ascii=False)
    db.commit()
    return serial(s)


@app.post("/api/sessions/{session_id}/handover")
def handover(
    session_id: str,
    data: Handover,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    s, r = cash(db, user, session_id, True)
    operator = db.get(User, data.operator_id)
    if not operator or not operator.active or operator.role == "consulta":
        raise HTTPException(422, "Operador inválido.")
    authorize(operator, db, r.store_id)
    record(
        db,
        user,
        "passagem_caixa",
        s.id,
        {
            "from": s.operator_id,
            "to": operator.id,
            "reason": data.reason,
            "balance_cents": balance(db, s),
        },
        r.store_id,
    )
    s.operator_id = operator.id
    db.commit()
    return serial(s)


@app.get("/api/movements")
def movements(all_records: bool = False, user=Depends(current), db: Session = Depends(db_session)):
    rows = db.execute(
        select(Movement, func.coalesce(CashSession.register_id, BankDeposit.register_id))
        .outerjoin(CashSession, CashSession.id == Movement.session_id)
        .outerjoin(BankDeposit, (BankDeposit.movement_id == Movement.id) | (BankDeposit.reversal_id == Movement.id))
        .outerjoin(Register, Register.id == func.coalesce(CashSession.register_id, BankDeposit.register_id))
        .where(Register.store_id.in_(scope(user, db)) | Movement.bank_account_id.in_(select(BankAccount.id).where(BankAccount.store_id.in_(scope(user, db)))))
        .where(
            # Reopening cancels generated closing entries as a linked pair.
            # Keep that audit trail in the ledger, but list only current close
            # entries; manual reversals retain their existing visible history.
            Movement.origin.notin_(("fecho_caixa", cash_posting.ORIGIN))
            | (Movement.reversed_by.is_(None) & Movement.reversal_of.is_(None))
        )
        .order_by(Movement.created_at.desc())
        .limit(None if all_records else 500)
    )
    payment_names = cash_local.methods(db)
    if integration_policy(db)["mode"] != "local":
        from .cash_sheet import LABELS
        payment_names.update(LABELS)
    accounts = {account.id: account for account in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(scope(user, db))))}
    rows = list(rows)
    supplier_views = cash_posting.supplier_views(db, [m for m, _ in rows])
    result = [{**serial(m), **supplier_views.get(m.id, {}), "register_id": register_id, "payment_name": payment_names.get(m.payment_method_id or m.payment),
               "bank_store_id": accounts[m.bank_account_id].store_id if m.bank_account_id in accounts else None,
               "bank_account_name": accounts[m.bank_account_id].name if m.bank_account_id in accounts else None,
               "bank_name": accounts[m.bank_account_id].bank if m.bank_account_id in accounts else None} for m, register_id in rows]
    return result + deposit_bank_rows(db, accounts, payment_names)


def deposit_bank_rows(db, accounts, payment_names):
    """
    Entrada na conta bancária de cada depósito registado, ao lado da saída da caixa. É derivada do
    próprio depósito (não é um segundo registo): os saldos bancários já a contam a partir do depósito.
    Um depósito cancelado depois do registo mostra também o estorno no banco.
    """
    from datetime import date, datetime, time
    from zoneinfo import ZoneInfo
    rows = []
    deposits = db.scalars(select(BankDeposit).where(BankDeposit.account_id.in_(list(accounts)), BankDeposit.delivery_date.is_not(None),
                                                    BankDeposit.movement_id.is_not(None)))
    for deposit in deposits:
        cash_out = db.get(Movement, deposit.movement_id)
        reversal = db.get(Movement, deposit.reversal_id) if deposit.reversal_id else None
        if not cash_out or deposit.status == "cancelado" and not reversal:
            continue
        account = accounts[deposit.account_id]
        base = {"kind": "deposito", "amount_cents": cash_out.amount_cents, "payment": cash_out.payment,
                "payment_name": payment_names.get(cash_out.payment), "category_id": cash_out.category_id,
                "document": deposit.number, "reference": deposit.id, "entity": "", "origin": "deposito_banco",
                "register_id": None, "session_id": None, "deposit_id": deposit.id, "transfer_id": None,
                "bank_account_id": account.id, "bank_account_name": account.name, "bank_name": account.bank,
                "bank_store_id": account.store_id}
        entry_id, reversal_id = "deposit-bank:" + deposit.id, "deposit-bank-reversal:" + deposit.id
        rows.append({**base, "id": entry_id, "direction": 1, "occurred_at": datetime.combine(date.fromisoformat(deposit.delivery_date), time(12), ZoneInfo("Europe/Lisbon")).isoformat(),
                     "created_at": cash_out.created_at, "notes": "Entrada no banco do depósito " + deposit.number,
                     "reversed_by": reversal_id if reversal else None, "reversal_of": None})
        if reversal:
            rows.append({**base, "id": reversal_id, "direction": -1, "occurred_at": serial(reversal)["occurred_at"],
                         "created_at": reversal.created_at, "notes": "Estorno do depósito " + deposit.number,
                         "reversed_by": None, "reversal_of": entry_id})
    return rows


@app.post("/api/movements")
def create_movement(data: MovementCreate, user=Depends(current), db: Session = Depends(db_session)):
    m = add_movement(data, user, db)
    db.commit()
    return serial(m)


def add_movement(data, user, db):
    s, r = cash(db, user, data.session_id, True)
    allowed = cash_local.methods(db, active_only=True) if integration_policy(db)["mode"] == "local" else {"numerario", "cartao", "transferencia", "outro"}
    if data.payment not in allowed:
        raise HTTPException(422, "Escolha um meio de pagamento ativo e configurado nesta empresa.")
    if not ensure_usable(db, Category, data.category_id):
        raise HTTPException(422, "Categoria inválida.")
    amount = cents(data.amount)
    direction = 1 if data.kind in IN else -1
    configured = db.get(LocalPaymentMethod, data.payment)
    preferences = json.loads(configured.settings or "{}") if configured else {}
    if not preferences.get("receipts" if direction > 0 else "payments", True):
        raise HTTPException(422, "Este meio de pagamento não está disponível para este tipo de movimento.")
    terminal = db.get(PaymentTerminal, data.terminal_id) if data.terminal_id else None
    if data.terminal_id and (not terminal or not terminal.active or terminal.store_id != r.store_id or terminal.register_id and terminal.register_id != r.id
                             or data.payment not in {"cartao", "mbway"} or data.payment == "mbway" and not terminal.mbway):
        raise HTTPException(422, "Escolha um terminal ativo desta caixa e deste meio de pagamento.")
    if data.kind in {"deposito", "reforco"} and data.payment != "numerario" and not preferences.get("cash"):
        raise HTTPException(422, "Este movimento deve ser em numerário.")
    timestamp = data.occurred_at
    if timestamp.tzinfo is None:
        raise HTTPException(422, "A data deve incluir o fuso horário.")
    opened = s.opened_at.replace(tzinfo=timezone.utc) if s.opened_at.tzinfo is None else s.opened_at
    if timestamp < opened or timestamp > now() + timedelta(minutes=5):
        raise HTTPException(422, "A data deve estar entre a abertura e a hora atual.")
    if data.payment == "numerario" and direction < 0 and amount > balance(db, s):
        raise HTTPException(409, "Numerário insuficiente nesta caixa.")
    values = data.model_dump(exclude={"amount"})
    values["amount_cents"] = amount
    values["direction"] = direction
    values["user_id"] = user.id
    values["bank_account_id"] = terminal.account_id if terminal else cash_posting.default_bank(db, r.store_id, data.payment)
    if preferences.get("cash") and data.payment != "numerario":
        values["payment_method_id"], values["payment"] = data.payment, "numerario"
        values["bank_account_id"] = None
        if direction < 0 and amount > balance(db, s):
            raise HTTPException(409, "Numerário insuficiente nesta caixa.")
    m = Movement(**values)
    db.add(m)
    db.flush()
    record(
        db,
        user,
        "movimento",
        m.id,
        {"amount_cents": amount, "kind": m.kind, "payment": m.payment},
        r.store_id,
    )
    return m


def planned_summary(db, planned):
    movements = list(
        db.scalars(
            select(Movement)
            .join(PlannedSettlement, PlannedSettlement.movement_id == Movement.id)
            .where(PlannedSettlement.planned_id == planned.id)
        )
    )
    paid = sum(m.amount_cents for m in movements if not m.reversed_by)
    from .liquidity import active_local_settlements
    paid += sum(payment.amount_cents for link, payment in active_local_settlements(db) if link.source_key == "planned:" + planned.id)
    if planned.purchase_id:
        from .procurement import paid_total
        paid = paid_total(db, planned.purchase_id)
    result = serial(planned)
    split = [
        {"category_id": item.category_id, "category_name": name, "amount_cents": item.amount_cents}
        for item, name in db.execute(
            select(PlannedAllocation, Category.name)
            .join(Category, Category.id == PlannedAllocation.category_id)
            .where(PlannedAllocation.planned_id == planned.id)
            .order_by(PlannedAllocation.amount_cents.desc())
        )
    ]
    result.update(
        allocations=split,
        paid_cents=paid,
        remaining_cents=planned.amount_cents - paid,
        status="Liquidado" if paid == planned.amount_cents else "Parcial" if paid else "Pendente",
        settlements=[serial(m) for m in movements],
    )
    return result


@app.get("/api/planned-movements")
def planned_list(user=Depends(current), db: Session = Depends(db_session)):
    rows = db.scalars(
        select(PlannedMovement)
        .where(PlannedMovement.store_id.in_(scope(user, db)))
        .order_by(PlannedMovement.due_date, PlannedMovement.created_at)
    )
    return [planned_summary(db, row) for row in rows]


@app.post("/api/planned-movements")
def planned_create(data: PlannedCreate, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, data.store_id, ["administrador", "gestor", "operador"])
    if not db.get(Store, data.store_id) or not ensure_usable(db, Category, data.category_id):
        raise HTTPException(422, "Loja ou categoria inválida.")
    row = PlannedMovement(
        **data.model_dump(exclude={"amount", "due_date"}),
        due_date=data.due_date.isoformat(),
        amount_cents=cents(data.amount),
    )
    db.add(row)
    db.flush()
    record(db, user, "movimento_previsto", row.id, {"amount_cents": row.amount_cents}, row.store_id)
    db.commit()
    return planned_summary(db, row)


@app.post("/api/planned-movements/{planned_id}/edit")
def planned_edit(planned_id: str, data: PlannedCreate, user=Depends(current), db: Session = Depends(db_session)):
    row = db.scalar(select(PlannedMovement).where(PlannedMovement.id == planned_id).with_for_update())
    if not row:
        raise HTTPException(404, "Movimento previsto não encontrado.")
    authorize(user, db, row.store_id, ["administrador", "gestor"])
    if data.store_id != row.store_id or not ensure_usable(db, Category, data.category_id, row.category_id):
        raise HTTPException(422, "Mantenha a loja original e escolha uma rubrica válida.")
    paid = planned_summary(db, row)["paid_cents"]
    amount = cents(data.amount)
    split = list(db.scalars(select(PlannedAllocation).where(PlannedAllocation.planned_id == row.id)))
    if row.purchase_id:
        from .models import Purchase
        purchase = db.get(Purchase, row.purchase_id)
        if amount != abs(purchase.amount_cents) or data.direction != row.direction or data.document != row.document or data.entity != row.entity:
            raise HTTPException(409, "O valor e a identidade deste previsto são definidos pela compra de origem. Pode ajustar o vencimento e a rubrica.")
    if len(split) > 1 and data.category_id != row.category_id:
        raise HTTPException(409, "Esta compra tem linhas de várias rubricas; a repartição é definida pelas linhas em Compras. Pode ajustar o vencimento.")
    for item in split:
        item.category_id = data.category_id
    if amount < paid or paid and data.direction != row.direction:
        raise HTTPException(409, "A alteração não pode invalidar liquidações já registadas.")
    before = {key: value for key, value in serial(row).items() if key != "created_at"}
    for name, value in data.model_dump(exclude={"amount", "due_date"}).items():
        setattr(row, name, value)
    row.amount_cents, row.due_date = amount, data.due_date.isoformat()
    record(db, user, "movimento_previsto_alterado", row.id, {"before": before, "after": data.model_dump(mode="json")}, row.store_id)
    db.commit()
    return planned_summary(db, row)


@app.post("/api/planned-movements/{planned_id}/settle")
def planned_settle(
    planned_id: str, data: PlannedPay, user=Depends(current), db: Session = Depends(db_session)
):
    row = db.scalar(
        select(PlannedMovement).where(PlannedMovement.id == planned_id).with_for_update()
    )
    if not row:
        raise HTTPException(404, "Movimento previsto não encontrado.")
    authorize(user, db, row.store_id, ["administrador", "gestor", "operador"])
    previous = db.scalar(select(Movement).where(Movement.request_key == data.request_key))
    if previous:
        link = db.scalar(
            select(PlannedSettlement).where(PlannedSettlement.movement_id == previous.id)
        )
        if (
            not link
            or link.planned_id != row.id
            or previous.amount_cents != cents(data.amount)
            or previous.payment != data.payment
            or previous.session_id != data.session_id
        ):
            raise HTTPException(409, "Referência de pagamento já utilizada.")
        return planned_summary(db, row)
    _, register = cash(db, user, data.session_id, True)
    if register.store_id != row.store_id:
        raise HTTPException(422, "Selecione uma caixa da loja deste movimento.")
    if cents(data.amount) > planned_summary(db, row)["remaining_cents"]:
        raise HTTPException(409, "O valor excede o saldo por liquidar.")
    movement = add_movement(
        MovementCreate(
            **data.model_dump(),
            category_id=row.category_id,
            kind="cliente" if row.direction == 1 else "fornecedor",
            entity=row.entity,
            document=row.document,
            reference=row.id,
        ),
        user,
        db,
    )
    movement.origin = "previsto"
    db.add(PlannedSettlement(planned_id=row.id, movement_id=movement.id))
    if row.purchase_id:
        from .models import DocumentPayment, PaymentDocumentLine
        payment = DocumentPayment(store_id=row.store_id, payment_date=movement.occurred_at.date().isoformat(), amount_cents=movement.amount_cents,
            direction=row.direction, entity=row.entity, reference=row.document, kind="pagamento", method=data.payment,
            source="movement", source_id=movement.id, request_key="procurement:"+movement.id)
        db.add(payment); db.flush()
        db.add(PaymentDocumentLine(payment_id=payment.id, purchase_id=row.purchase_id, amount_cents=movement.amount_cents))
    record(db, user, "liquidacao_previsto", row.id, {"movement_id": movement.id}, row.store_id)
    db.commit()
    return planned_summary(db, row)


@app.post("/api/movements/{movement_id}/reverse")
def reverse(
    movement_id: str,
    data: Reason,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    original = db.get(Movement, movement_id)
    if not original:
        raise HTTPException(404, "Movimento não encontrado.")
    if original.origin == "deposito_modulo":
        raise HTTPException(409, "Use o cancelamento justificado na ficha do depósito para devolver os valores à caixa.")
    s, r = cash(db, user, original.session_id, True)
    authorize(user, db, r.store_id, ["administrador", "gestor"])
    original = db.scalar(
        select(Movement)
        .where(Movement.id == movement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    from .reconciliation import identified, used

    if used(db, "movement", original.id):
        raise HTTPException(
            409, "Desfaça a reconciliação bancária antes de estornar este movimento."
        )
    if identified(db, "movement", original.id):
        raise HTTPException(
            409,
            "Desfaça a distribuição do pagamento pelos documentos antes de estornar este movimento.",
        )
    from .billing_conference import linked

    if linked(db, "movement", original.id):
        raise HTTPException(409, "Desfaça a conferência de faturação deste recebimento antes de o estornar.")
    if original.origin == "fecho_caixa":
        raise HTTPException(409, "Reabra a folha de caixa para rever a diferença do fecho.")
    if original.origin == cash_posting.ORIGIN:
        raise HTTPException(409, "Reabra a folha de caixa para rever os movimentos gerados no fecho.")
    if original.reversed_by or original.reversal_of:
        raise HTTPException(409, "O movimento já foi estornado ou é um estorno.")
    if original.transfer_id:
        raise HTTPException(
            409,
            "Transferências não podem ser estornadas isoladamente. Registe uma transferência inversa, indicando o motivo.",
        )
    if (
        original.payment == "numerario"
        and original.direction > 0
        and original.amount_cents > balance(db, s)
    ):
        raise HTTPException(409, "Numerário insuficiente para estornar esta entrada.")
    m = Movement(
        session_id=s.id,
        user_id=user.id,
        category_id=original.category_id,
        bank_account_id=original.bank_account_id,
        terminal_id=original.terminal_id,
        payment_method_id=original.payment_method_id,
        kind=original.kind,
        payment=original.payment,
        amount_cents=original.amount_cents,
        direction=-original.direction,
        occurred_at=now(),
        notes=data.reason,
        request_key=uid(),
        reversal_of=original.id,
    )
    db.add(m)
    db.flush()
    original.reversed_by = m.id
    record(
        db,
        user,
        "estorno",
        original.id,
        {"reversal": m.id, "reason": data.reason},
        r.store_id,
    )
    db.commit()
    return serial(m)


@app.post("/api/transfers")
def transfer(data: Transfer, user=Depends(current), db: Session = Depends(db_session)):
    if data.source_id == data.destination_id:
        raise HTTPException(422, "Escolha duas caixas diferentes.")
    # Ordem determinística de bloqueio impede deadlocks entre transferências opostas.
    pairs = {i: cash(db, user, i, True) for i in sorted([data.source_id, data.destination_id])}
    s, r = pairs[data.source_id]
    destination, dr = pairs[data.destination_id]
    authorize(user, db, roles=["administrador", "gestor"])
    if not ensure_usable(db, Category, data.category_id):
        raise HTTPException(422, "Categoria inválida.")
    amount = cents(data.amount)
    if amount > balance(db, s):
        raise HTTPException(409, "Numerário insuficiente.")
    transfer_id = uid()
    for session, register, direction, suffix in [
        (s, r, -1, "out"),
        (destination, dr, 1, "in"),
    ]:
        m = Movement(
            session_id=session.id,
            user_id=user.id,
            category_id=data.category_id,
            kind="transferencia_caixa",
            payment="numerario",
            amount_cents=amount,
            direction=direction,
            occurred_at=now(),
            notes=data.reason,
            request_key=data.request_key + suffix,
            transfer_id=transfer_id,
        )
        db.add(m)
        db.flush()
        record(
            db,
            user,
            "transferencia",
            m.id,
            {
                "transfer_id": transfer_id,
                "amount_cents": amount,
                "direction": direction,
            },
            register.store_id,
        )
    db.commit()
    return {"id": transfer_id}


@app.get("/api/dashboard")
def dashboard(user=Depends(current), db: Session = Depends(db_session)):
    opened = list(
        db.scalars(
            select(CashSession)
            .join(Register)
            .where(Register.store_id.in_(scope(user, db)), CashSession.closed_at.is_(None))
        )
    )
    return {
        "cash_cents": sum(balance(db, s) for s in opened),
        "open_sessions": len(opened),
        "bank_balance": None,
        "management_result": None,
    }


@app.get("/api/audit")
def audit(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor", "consulta"])
    q = select(Audit)
    if user.role != "administrador":
        q = q.where(Audit.store_id.in_(scope(user, db)))
    return [serial(a) for a in db.scalars(q.order_by(Audit.created_at.desc()).limit(500))]


@app.post("/api/movements/{movement_id}/attachments")
def upload(
    movement_id: str,
    file: UploadFile = File(...),
    user=Depends(current),
    db: Session = Depends(db_session),
):
    m = db.get(Movement, movement_id)
    if not m:
        raise HTTPException(404, "Movimento não encontrado.")
    s, r = cash(db, user, m.session_id, True)
    from .models import DocumentScan
    if (
        db.scalar(
            select(func.count()).select_from(Attachment).where(Attachment.movement_id == m.id)
        )
        + db.scalar(select(func.count()).select_from(DocumentScan).where(DocumentScan.movement_id == m.id, DocumentScan.status == "cloud_saved")) >= 3
    ):
        raise HTTPException(422, "Máximo de três anexos por movimento.")
    raw = file.file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024 or not raw:
        raise HTTPException(422, "O ficheiro deve ter até 5 MB.")
    mime = (
        "application/pdf"
        if raw.startswith(b"%PDF-")
        else "image/png"
        if raw.startswith(b"\x89PNG\r\n\x1a\n")
        else "image/jpeg"
        if raw.startswith(b"\xff\xd8\xff")
        else None
    )
    if not mime or mime != file.content_type:
        raise HTTPException(422, "São aceites apenas PDF, JPG e PNG válidos.")
    name = (file.filename or "anexo").replace("\\", "/").split("/")[-1][:200]
    a = Attachment(movement_id=m.id, name=name, mime=mime, data=raw)
    db.add(a)
    db.flush()
    record(
        db,
        user,
        "anexo_adicionado",
        a.id,
        {"movement_id": m.id, "name": name},
        r.store_id,
    )
    db.commit()
    return {"id": a.id, "name": a.name}


@app.get("/api/movements/{movement_id}/attachments")
def attachments(movement_id: str, user=Depends(current), db: Session = Depends(db_session)):
    m = db.get(Movement, movement_id)
    if not m:
        raise HTTPException(404, "Movimento não encontrado.")
    cash(db, user, m.session_id)
    local = [
        {"id": a.id, "name": a.name, "size": len(a.data)}
        for a in db.scalars(select(Attachment).where(Attachment.movement_id == m.id))
    ]
    from .models import DocumentScan
    local += [{"id": "scan:"+a.id, "name": a.name, "size": a.size, "view_url": "/api/scans/"+a.id+"/pdf", "scan_id": a.id, "validation_status": a.validation_status} for a in db.scalars(select(DocumentScan).where(DocumentScan.movement_id == m.id, DocumentScan.status == "cloud_saved"))]
    return local


@app.get("/api/attachments/{attachment_id}")
def download(attachment_id: str, user=Depends(current), db: Session = Depends(db_session)):
    from urllib.parse import quote

    a = db.get(Attachment, attachment_id)
    if not a:
        raise HTTPException(404, "Anexo não encontrado.")
    m = db.get(Movement, a.movement_id)
    cash(db, user, m.session_id)
    return Response(
        a.data,
        media_type=a.mime,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(a.name),
            "Content-Security-Policy": "sandbox",
        },
    )


@app.get("/api/sessions/{session_id}/report")
def close_report(session_id: str, user=Depends(current), db: Session = Depends(db_session)):
    from io import BytesIO
    from zoneinfo import ZoneInfo

    from reportlab.pdfgen import canvas

    s, r = cash(db, user, session_id)
    if not s.closed_at:
        raise HTTPException(409, "Feche a caixa antes de gerar o relatório.")
    file = BytesIO()
    pdf = canvas.Canvas(file, invariant=1)
    y = 800

    def line(value):
        nonlocal y
        if y < 55:
            pdf.showPage()
            y = 800
        pdf.drawString(45, y, str(value))
        y -= 23

    def euro(v):
        return f"{Decimal(v) / 100:,.2f}".replace(",", " ").replace(".", ",") + " EUR"

    line("IAZELA - Folha de caixa / relatório interno de fecho")
    line("NÃO É UM DOCUMENTO FISCAL")
    if os.getenv("APP_MODE") == "demonstracao":
        line("DEMONSTRAÇÃO - dados de teste")
    line("Loja: " + db.get(Store, r.store_id).name)
    line("Caixa: " + r.name)
    line("Operador: " + db.get(User, s.operator_id).name)
    line("Sessão: " + s.id)
    line("Fecho: " + s.closed_at.astimezone(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M"))
    line("Dia da folha: " + (s.sheet_day or s.opened_at.date().isoformat()))
    line("Fundo de maneio inicial: " + euro(s.opening_cents))
    line("Esperado: " + euro(s.expected_cents))
    line("Contado: " + (euro(s.counted_cents) if s.counted_cents is not None else "Não contado"))
    line("Diferença: " + (euro(s.counted_cents - s.expected_cents) if s.counted_cents is not None else "Por conferir"))
    counting = json.loads(s.count_data or "{}")
    local_reconciliation = counting.get("local_reconciliation")
    declared_basis = (local_reconciliation or {}).get("expectation_basis") == "declared_sales"
    if local_reconciliation:
        line("Faturado do dia - origem Manual:" if declared_basis else "Realizado do dia - origem Manual:")
        line("Referência POS: " + local_reconciliation["realized"].get("pos_reference", ""))
        for row in local_reconciliation["rows"]:
            if declared_basis:
                counted_value = row["counted_cents"]
                line(row["name"] + " - Faturado: " + euro(row["declared_cents"])
                     + " / Esperado: " + euro(row["expected_cents"]))
                line("  Contado / conferido: " + (euro(counted_value) if counted_value is not None else "Por conferir")
                     + " / Diferença: " + (euro(row["count_difference_cents"]) if counted_value is not None else "Por conferir"))
                line("  Fundo: " + euro(row["fund_cents"]) + " / Outros movimentos: " + euro(row["other_movements_cents"]))
                if "debits_cents" in row:
                    line("  Débitos: " + euro(row["debits_cents"]) + " / Dos movimentos: " + euro(row["registered_debits_cents"]))
                if "registered_credits_cents" in row:
                    line("  Créditos: " + euro(row["other_credits_cents"]) + " / Dos movimentos: " + euro(row["registered_credits_cents"]))
                continue
            line(row["name"] + " - Declarado: " + euro(row["declared_cents"])
                 + " / Registado: " + euro(row["registered_cents"])
                 + " / Diferença: " + euro(row["difference_cents"]))
        line("Total faturado: " + euro(local_reconciliation["declared_total_cents"]))
        line("Consulta dos recebimentos registados (registado - faturado):")
        line("Total registado: " + euro(local_reconciliation["registered_total_cents"]))
        line("Diferença dos registos: " + euro(local_reconciliation["difference_cents"]))
        line("Conferido por: " + counting.get("reconciliation_check", {}).get("checked_by_name", ""))
        line("Fechado por: " + counting.get("closed_by_name", ""))
    counted_values = counting.get("values", {"numerario": s.counted_cents})
    line("Meios de pagamento (valores líquidos):")
    for row in sheet_summary(db, s):
        line(
            row["name"]
            + " - IAZELA: "
            + euro(row["local_cents"])
            + " / Wintouch: "
            + euro(row["wintouch_cents"])
        )
        line(
            "  Créditos: " + euro(row["credits_cents"]) + " / Débitos: " + euro(row["debits_cents"])
        )
        if declared_basis:
            continue
        counted_value = counted_values.get(row["key"])
        line(
            "  Esperado: "
            + euro(row["expected_cents"])
            + " / Conferido: "
            + (euro(counted_value) if counted_value is not None else "Não contado")
        )
        if counted_value is not None:
            line("  Diferença: " + euro(counted_value - row["expected_cents"]))
    line("Contagem de moedas e notas:")
    for value, count in json.loads(s.denominations).items():
        if count:
            line(euro(int(value)) + " x " + str(count))
    from textwrap import wrap

    supplier = counting.get("debit_supplier", {})
    if supplier.get("answer") == "indicado":
        if supplier.get("allocations"):
            line("Fornecedores dos débitos:")
            for allocation in supplier["allocations"]:
                payment_label = next((row["name"] for row in counting.get("summary", []) if row["key"] == allocation.get("payment")), "")
                for fragment in wrap("  " + allocation["name"] + (" · " + payment_label if payment_label else "") + ": " + euro(allocation["amount_cents"]), 90):
                    line(fragment)
            line("Total atribuído: " + euro(supplier["debits_cents"]))
            if supplier.get("unassigned_cents", 0):
                line("Por atribuir: " + euro(supplier["unassigned_cents"]))
        else:
            for fragment in wrap("Fornecedor dos débitos: " + supplier["name"], 90):
                line(fragment)
            if supplier.get("tax_number"):
                line("NIF do fornecedor: " + supplier["tax_number"])
            line("Débitos no fecho: " + euro(supplier["debits_cents"]))

    for fragment in wrap("Justificação: " + s.close_reason, 90):
        line(fragment)
    pdf.save()
    return Response(
        file.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="fecho-caixa.pdf"'},
    )


@app.get("/api/integrations/wintouch")
def company_wintouch_status(user=Depends(current), db: Session = Depends(db_session)):
    with database.next_platform_session() as core:
        company = core.get(Company, db.info.get("company", {}).get("id"))
        if not company:
            raise HTTPException(404, "Empresa indisponivel.")
        config = integration_policy(db)
        return {
            "id": company.id,
            "mode": company.mode,
            "active": bool(company.active),
            "api_active": config["active"],
            "operating_mode": config["mode"],
            "capabilities": config["capabilities"],
            "resources": config["resources"],
        }


@app.get("/api/integrations/wintouch/sales")
def company_sales_list(user=Depends(current), db: Session = Depends(db_session)):
    from .deposits import cash_origin_resolver
    resolve_origin = cash_origin_resolver(db)
    supplier_overrides = {}
    for closed in db.scalars(select(CashSession).where(CashSession.closed_at.is_not(None)).order_by(CashSession.closed_at)):
        association = json.loads(closed.count_data or "{}").get("debit_supplier", {})
        if association.get("answer") == "indicado":
            for movement_id in association.get("imported_movement_ids", []):
                supplier_overrides[movement_id] = association["name"]
    accounts = {
        (a.enterprise_id, a.external_id): a for a in db.scalars(select(WintouchCashAccount))
    }
    query = select(WintouchSaleRecord).where(
        WintouchSaleRecord.store_id.in_(scope(user, db)), WintouchSaleRecord.active.is_(True)
    )
    company_id = db.info.get("company", {}).get("id")
    enterprise = ""
    if company_id:
        with database.next_platform_session() as core:
            company = core.get(Company, company_id)
            if not company:
                raise HTTPException(404, "Empresa indisponível.")
            enterprise = company.api_enterprise
            query = query.where(WintouchSaleRecord.enterprise_id == enterprise)
    result = []
    from .models import IntegrationBinding
    local_owned = set(db.scalars(select(IntegrationBinding.external_id).where(IntegrationBinding.connector == "wintouch", IntegrationBinding.external_kind == "pending", IntegrationBinding.status == "local_owned")))
    for r in db.scalars(query.order_by(WintouchSaleRecord.document_date.desc())):
        if r.kind == "previsto" and r.external_id in local_owned:
            continue
        data = json.loads(r.data)
        if r.kind == "real" and not is_real_cash_movement(data):
            continue
        if r.id in supplier_overrides and (data.get("entity") or "").strip() in ("", "Entidade não indicada"):
            data["entity"] = supplier_overrides[r.id]
        account = accounts.get((r.enterprise_id, data.get("account_id")))
        result.append(
            {
                "id": r.id,
                "kind": r.kind,
                "store_id": r.store_id,
                "synced_at": r.synced_at,
                **data,
                "register_id": resolve_origin(r.store_id, r.enterprise_id, data.get("account_id"), sheet_day_of(data.get("occurred_at") or r.document_date))[0] if r.kind == "real" else account.register_id if account else None,
                "account_name": account.name if account else None,
            }
        )
    # Compras por pagar do ERP: rubrica do documento ou do fornecedor (definida localmente).
    from .rubric_expenses import erp_rubric_labels
    payables = [row for row in result if row["kind"] == "previsto" and row.get("document_group") == "compras" and not row.get("category_name")]
    for row, label in zip(payables, erp_rubric_labels(db, enterprise, [(row.get("source_document_id"), row.get("entity_nif")) for row in payables])):
        row["category_name"] = label
    return result


class CashSheetERPImport(Strict):
    confirm_demo: bool = False


@app.post("/api/sessions/{session_id}/import-erp")
def import_cash_sheet_erp(session_id: str, data: CashSheetERPImport, user=Depends(current), db: Session = Depends(db_session)):
    from datetime import date
    session, register = cash(db, user, session_id)
    authorize(user, db, register.store_id, ["administrador"])
    if session.closed_at:
        raise HTTPException(409, "Reabra a folha antes de importar para atualizar a conferência.")
    company_id = db.info.get("company", {}).get("id")
    with database.next_platform_session() as core:
        company = core.get(Company, company_id) if company_id else None
        provider = company.erp_provider if company else "none"
    if provider != "wintouch":
        raise HTTPException(409, "Configure um ERP com importação de movimentos disponível nas configurações da empresa.")
    selected_day = date.fromisoformat(session.sheet_day or sheet_day_of(session.opened_at))
    result = company_sales_import(SalesImport(store_id=register.store_id, start_date=selected_day, end_date=selected_day, confirm_demo=data.confirm_demo), user, db)
    if result.get("status") != "concluida":
        raise HTTPException(409, result.get("error_message") or "Não foi possível concluir a importação dos movimentos do ERP.")
    db.expire_all()
    session, register = cash(db, user, session_id)
    if not session.closed_at:
        token, enterprise = sheet_source(db, register.store_id, session.sheet_day or sheet_day_of(session.opened_at), True, session.id)
        session.wintouch_scope, session.wintouch_enterprise = token, enterprise
        counting = json.loads(session.count_data or "{}")
        counting["wintouch_excluded"] = False
        session.count_data = json.dumps(counting, ensure_ascii=False)
        record(db, user, "movimentos_erp_importados_folha", session.id, {"provider": provider, "day": selected_day.isoformat()}, register.store_id)
        db.commit()
    return {"import": result, "closed": bool(session.closed_at), "sheet": session_result(db, session)}


@app.post("/api/integrations/wintouch/sales")
def company_sales_import(
    data: SalesImport, user=Depends(current), db: Session = Depends(db_session)
):
    from cryptography.fernet import InvalidToken

    from .platform import cipher, validate_url
    from .wintouch_sales import synchronize_sales
    from .wintouch_sync import SyncError

    authorize(user, db, data.store_id, ["administrador"])
    if not db.get(Store, data.store_id):
        raise HTTPException(422, "Loja inválida.")
    if data.start_date > data.end_date or (data.end_date - data.start_date).days > 366:
        raise HTTPException(422, "Escolha um período válido de até um ano.")
    company_id = db.info.get("company", {}).get("id")
    with database.next_platform_session() as core:
        company = core.get(Company, company_id)
        if not company or not company.active or not company.api_active:
            raise HTTPException(409, "Ative a ligação Wintouch desta empresa antes de importar.")
        if company.erp_provider != "wintouch":
            raise HTTPException(409, "O ERP configurado não dispõe deste adaptador de importação de movimentos.")
        if company.mode == "demonstracao" and not data.confirm_demo:
            raise HTTPException(422, "Confirme a importação para esta empresa de demonstração.")
        url = validate_url(company.api_url)
        try:
            key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
        except (InvalidToken, AttributeError):
            raise HTTPException(503, "Não foi possível decifrar a chave API.") from None
        enterprise = company.api_enterprise
    record(
        db,
        user,
        "importacao_vendas_wintouch",
        company_id,
        {"start_date": data.start_date.isoformat(), "end_date": data.end_date.isoformat()},
        data.store_id,
    )
    db.commit()
    try:
        return synchronize_sales(
            db.get_bind(), enterprise, data.store_id, data.start_date, data.end_date, url, key
        )
    except SyncError as exc:
        raise HTTPException(409, str(exc)) from None


def settlement_context(db, user, record_id):
    from .platform import cipher, validate_url

    row = db.get(WintouchSaleRecord, record_id)
    if not row or row.kind != "previsto" or not row.active:
        raise HTTPException(404, "Saldo Wintouch em aberto não encontrado.")
    authorize(user, db, row.store_id, ["administrador", "gestor"])
    from .liquidity import active_local_settlements
    if any(link.source_key == "wintouch:" + record_id for link, payment in active_local_settlements(db)):
        raise HTTPException(409, "Existem liquidações locais deste documento. Confira-as antes de liquidar novamente no ERP.")
    with database.next_platform_session() as core:
        company = core.get(Company, db.info.get("company", {}).get("id"))
        if (
            not company
            or not company.active
            or not company.api_active
            or company.api_enterprise != row.enterprise_id
        ):
            raise HTTPException(409, "Ligação Wintouch indisponível para este saldo.")
        url = validate_url(company.api_url)
        key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
    return row, url, key


@app.get("/api/integrations/wintouch/sales/{record_id}/settlement-options")
def wintouch_settlement_options(
    record_id: str, user=Depends(current), db: Session = Depends(db_session)
):
    import httpx

    from .wintouch_sales import Reader
    from .wintouch_settlement import options
    from .wintouch_sync import SyncError

    row, url, key = settlement_context(db, user, record_id)
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            return options(
                Reader(client, url, row.enterprise_id, key), json.loads(row.data)["direction"]
            )
    except (httpx.HTTPError, SyncError):
        raise HTTPException(
            502, "Não foi possível consultar as opções de liquidação Wintouch."
        ) from None


@app.post("/api/integrations/wintouch/sales/{record_id}/settle")
def wintouch_settle(
    record_id: str,
    data: WintouchSettlement,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    from uuid import UUID

    import httpx

    from .wintouch_sales import Reader, amount
    from .wintouch_settlement import encode_payload, payload, rejection_message
    from .wintouch_sync import SyncError

    row, url, key = settlement_context(db, user, record_id)
    try:
        settlement_id = str(UUID(data.request_key))
    except ValueError:
        raise HTTPException(422, "Identificador de operação inválido.") from None
    if data.occurred_at.tzinfo is None or data.occurred_at > now() + timedelta(minutes=5):
        raise HTTPException(422, "Data de liquidação inválida.")
    # Serialize operations for this pending, and persist intent before the remote write.
    row = db.scalar(
        select(WintouchSaleRecord).where(WintouchSaleRecord.id == record_id).with_for_update()
    )
    previous = db.scalar(
        select(Audit)
        .where(Audit.target_id == record_id, Audit.action == "liquidacao_wintouch_iniciada")
        .order_by(Audit.created_at.desc())
        .limit(1)
    )
    if previous:
        previous_id = json.loads(previous.detail)["settlement_id"]
        done = db.scalar(
            select(Audit).where(
                Audit.target_id == previous_id, Audit.action == "liquidacao_wintouch_concluida"
            )
        )
        rejected = db.scalar(
            select(Audit).where(
                Audit.target_id == previous_id, Audit.action == "liquidacao_wintouch_recusada"
            )
        )
        if not done and not rejected:
            raise HTTPException(
                409,
                "Existe uma liquidação sem confirmação. Verifique-a na Wintouch antes de repetir.",
            )
        if done and previous_id == settlement_id:
            return {"settlement_id": settlement_id, "message": "Liquidação já confirmada."}
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            reader = Reader(client, url, row.enterprise_id, key)
            pending_id = row.external_id.removeprefix("pending:")
            pending = reader.get(
                "payablesreceivables/" + pending_id, {"embed": "CreditAccountState"}
            )
            state = pending.get("CreditAccountState") or reader.get(
                "credit_account_states/" + pending["CreditAccountStateID"]
            )
            if state.get("IsFinalStateEnabled") is not False:
                raise HTTPException(409, "O documento já não tem saldo pendente confirmado.")
            body = payload(reader, pending, data, json.loads(row.data)["direction"], settlement_id)
            record(
                db,
                user,
                "liquidacao_wintouch_iniciada",
                row.id,
                {
                    "settlement_id": settlement_id,
                    "amount_cents": amount(data.amount.replace(",", ".")),
                    "pending_value_cents": amount(pending["StateValue"]),
                    "pending_version": pending.get("Version"),
                },
                row.store_id,
            )
            db.commit()
            response = client.post(
                url.rstrip("/") + "/settlement_documents",
                headers={**reader.headers, "Content-Type": "application/json"},
                content=encode_payload(body).encode("utf-8"),
            )
            # Verify the assigned ID at the origin, rather than trusting an HTTP success alone.
            if response.status_code in (400, 401, 403, 422):
                message = rejection_message(response)
                record(
                    db,
                    user,
                    "liquidacao_wintouch_recusada",
                    settlement_id,
                    {"record_id": row.id, "http_status": response.status_code, "message": message},
                    row.store_id,
                )
                db.commit()
                raise HTTPException(422, f"A Wintouch recusou a liquidação: {message}")
            if response.status_code not in (200, 201):
                raise HTTPException(
                    502,
                    "A Wintouch não confirmou a liquidação. Verifique o documento na origem antes de repetir.",
                )
            try:
                result = response.json()
            except ValueError:
                result = None
            assigned_id = None
            if isinstance(result, str):
                assigned_id = result
            elif isinstance(result, dict):
                assigned_id = result.get("ID") or result.get("id")
            if not assigned_id and response.headers.get("Location"):
                assigned_id = (
                    response.headers["Location"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
                )
            try:
                confirmed_id = str(UUID(assigned_id)) if assigned_id else settlement_id
            except (ValueError, TypeError, AttributeError):
                confirmed_id = settlement_id
            record(db, user, "liquidacao_wintouch_enviada", settlement_id, {"record_id": row.id, "wintouch_settlement_id": confirmed_id}, row.store_id)
            db.commit()
            confirmed = reader.get("settlement_documents/" + confirmed_id)
            if confirmed.get("ID") != confirmed_id or amount(confirmed.get("Amount")) != amount(
                data.amount.replace(",", ".")
            ):
                raise HTTPException(
                    502, "A liquidação enviada ainda não pôde ser confirmada na Wintouch."
                )
            record(
                db,
                user,
                "liquidacao_wintouch_concluida",
                settlement_id,
                {"record_id": row.id, "wintouch_settlement_id": confirmed_id},
                row.store_id,
            )
            db.commit()
            # Refresh the actual outstanding balance; never subtract it locally by assumption.
            refreshed = next(
                (
                    item
                    for item in reader.collection("payablesreceivables", "CreditAccountState")
                    if item.get("ID") == pending_id
                ),
                None,
            )
            values = json.loads(row.data)
            values["amount_cents"] = abs(amount(refreshed["StateValue"])) if refreshed else 0
            row.data = json.dumps(values, ensure_ascii=False)
            row.active = (
                values["amount_cents"] != 0
                and (refreshed.get("CreditAccountState") or {}).get("IsFinalStateEnabled")
                is not True
            )
            row.synced_at = now()
            db.commit()
            return {"settlement_id": confirmed_id, "message": "Liquidação confirmada na Wintouch."}
    except (httpx.HTTPError, SyncError, KeyError, ValueError):
        raise HTTPException(
            502,
            "Não foi possível confirmar a operação. Verifique a liquidação na Wintouch antes de repetir.",
        ) from None


@app.post("/api/integrations/wintouch/sync")
def company_wintouch_sync(
    data: SyncRequest, user=Depends(current), db: Session = Depends(db_session)
):
    authorize(user, db, roles=["administrador"])
    from .wintouch_sync import RESOURCES

    if data.resource not in RESOURCES:
        raise HTTPException(422, "Escolha artigos, entidades ou caixas.")
    company_id = db.info.get("company", {}).get("id")
    record(db, user, "sincronizacao_" + data.resource, company_id or "", {})
    db.commit()
    with database.next_platform_session() as core:
        return sync_company(company_id, data, admin=None, db=core)


@app.get("/api/integrations/wintouch/sync-history")
def company_wintouch_history(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    with database.next_platform_session() as core:
        return sync_history(db.info.get("company", {}).get("id"), admin=None, db=core)


from .purchases import register_routes as register_purchase_routes

register_purchase_routes(app, current, authorize, scope, record, serial, cents)
from .reconciliation import register_routes as register_reconciliation_routes

register_reconciliation_routes(app, current, authorize, scope, record, serial)
from .documents import register_routes as register_document_routes

register_document_routes(app, current, authorize, scope, record, serial)


from .entities import router as entities_router

app.include_router(entities_router)


from .settings import router as settings_router

app.include_router(settings_router)

from .drive_imports import router as drive_imports_router

app.include_router(drive_imports_router)

from .billing_conference import router as billing_conference_router

app.include_router(billing_conference_router)


from .google_integration import register_routes as register_google_routes
register_google_routes(app, current, authorize, record)


from .google_invoices import register_routes as register_google_invoice_routes
register_google_invoice_routes(app, current, authorize, scope, record)

from .drive_documents import register_routes as register_drive_document_routes
register_drive_document_routes(app, current, authorize, record)

from .rubrics import register_routes as register_rubric_routes
register_rubric_routes(app, current, authorize, record, serial)

from .deposits import register_routes as register_deposit_routes
register_deposit_routes(app, current, authorize, scope, record)

from .liquidity import register_routes as register_liquidity_routes
register_liquidity_routes(app, current, authorize, scope, record, serial, planned_summary, company_sales_list)

from .operating_modes import register_routes as register_operating_mode_routes
register_operating_mode_routes(app, current, authorize, scope, record, serial)

from .document_scanning import register_routes as register_scanning_routes
register_scanning_routes(app, current, authorize, scope, record)
from .document_scanning import ScanBodyLimit
app.add_middleware(ScanBodyLimit)

from .invoice_workflow import register_routes as register_invoice_workflow_routes
register_invoice_workflow_routes(app, current, authorize, scope, record)
from .invoice_workflow import register_conference as register_invoice_conference_routes
register_invoice_conference_routes(app, current, authorize, scope, record)

from .employees import register_routes as register_employee_routes
register_employee_routes(app, current, authorize, scope, record, serial)

from .payment_methods import register_routes as register_payment_method_routes
register_payment_method_routes(app, current, authorize, record)

from .company_setup import register_routes as register_setup_routes
register_setup_routes(app, current, authorize, record, serial, scope)

from .register_management import register_routes as register_register_routes
register_register_routes(app, current, authorize, record, serial)

from .saft_sales import register_routes as register_saft_routes
register_saft_routes(app, current, authorize, scope, record)
from .saft_sales import SaftBodyLimit
app.add_middleware(SaftBodyLimit)

from .inventory import register_routes as register_inventory_routes
register_inventory_routes(app, current, authorize, scope, record)

from .procurement import register_routes as register_procurement_routes
register_procurement_routes(app, current, authorize, scope, record)

from .payment_terms import router as payment_terms_router
app.include_router(payment_terms_router)

# Comportamento comum das tabelas: as definições registam-se ao importar table_specs.
from . import table_specs  # noqa: E402,F401
from .table_framework import router as tables_router
app.include_router(tables_router)

from .bank_balances import router as bank_balances_router
app.include_router(bank_balances_router)

from .rubric_expenses import router as rubric_expenses_router
app.include_router(rubric_expenses_router)

from .management_maps import router as management_maps_router
app.include_router(management_maps_router)

from .dashboard import router as dashboard_router
app.include_router(dashboard_router)

from .quick_scan import router as quick_scan_router
app.include_router(quick_scan_router)

from .archive_api import router as archive_router
app.include_router(archive_router)
from .archive_assistant import router as archive_assistant_router
app.include_router(archive_assistant_router)

from .customer_orders import router as customer_orders_router
app.include_router(customer_orders_router)
from .order_cycle import router as order_cycle_router
app.include_router(order_cycle_router)
