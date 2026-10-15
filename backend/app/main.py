import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from time import monotonic

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pwdlib import PasswordHash
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import db as database
from . import cash_local
from .operating_modes import policy as integration_policy
from .cash_sheet import (
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
from .db import db_session
from .models import (
    Access,
    Attachment,
    Audit,
    BankAccount,
    BankTransaction,
    BankDeposit,
    BusinessEntity,
    AttachmentIntegrationConfig,
    CashSession,
    Category,
    LoginSession,
    Movement,
    LocalPaymentMethod,
    PlannedMovement,
    PlannedSettlement,
    Register,
    Store,
    User,
    WintouchCashAccount,
    WintouchEntity,
    WintouchSaleRecord,
    now,
    uid,
)
from .platform import SyncRequest, selection_router, sync_company, sync_history
from .platform import router as platform_router
from .platform_models import Company
from .schemas import (
    BankCreate,
    CashCount,
    CashSources,
    CloseCash,
    Handover,
    Login,
    MovementCreate,
    Name,
    OpenCash,
    PasswordChange,
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
    title="GPGest",
    version="0.1.0",
    description="Gestão interna. Não emite documentos fiscais.",
    lifespan=integration_lifespan,
)
app.include_router(platform_router)
app.include_router(selection_router)


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
            reason = "introduza exatamente 9 d?gitos"
        elif kind == "string_pattern_mismatch" and field == "slug":
            reason = "use entre 3 e 80 caracteres: letras sem acentos, números e hífen; comece com uma letra ou número"
        elif kind == "extra_forbidden":
            reason = "foi enviado um campo não permitido"
        else:
            reason = "valor inválido"
        messages.append(f"{label}: {reason}.")
    # Não devolver valores submetidos, sobretudo palavras-passe e chaves API.
    return JSONResponse({"detail": " ".join(messages)}, status_code=422)


def cents(value: str, zero=False):
    try:
        amount = Decimal(value.replace(",", "."))
        if (
            not amount.is_finite()
            or amount < 0
            or (not zero and amount == 0)
            or amount > Decimal("999999999")
            or amount * 100 != (amount * 100).to_integral_value()
        ):
            raise ValueError()
        return int(amount * 100)
    except (InvalidOperation, ValueError):
        raise HTTPException(
            422, "Introduza um montante positivo com, no máximo, duas casas decimais."
        )


def current(request: Request, db: Session = Depends(db_session)):
    token = request.cookies.get("gpgest_session", "")
    login = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
    if not login:
        raise HTTPException(401, "Inicie sessão para continuar.")
    expires = (
        login.expires_at.replace(tzinfo=timezone.utc)
        if login.expires_at.tzinfo is None
        else login.expires_at
    )
    user = db.get(User, login.user_id)
    if expires <= now() or not user or not user.active:
        raise HTTPException(401, "A sessão expirou.")
    return user


def authorize(user, db, store_id=None, roles=None):
    if roles and user.role not in roles:
        raise HTTPException(403, "O seu perfil não permite esta ação.")
    if store_id and user.role != "administrador" and not db.get(Access, (user.id, store_id)):
        raise HTTPException(403, "Não tem acesso a esta loja.")


def scope(user, db):
    return (
        [s.id for s in db.scalars(select(Store))]
        if user.role == "administrador"
        else list(db.scalars(select(Access.store_id).where(Access.user_id == user.id)))
    )


def record(db, user, action, target, detail, store_id=None):
    db.add(
        Audit(
            user_id=user.id,
            store_id=store_id,
            action=action,
            target_id=target,
            detail=json.dumps(detail, ensure_ascii=False),
        )
    )


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


def serial(obj):
    return {
        c.name: getattr(obj, c.name)
        for c in obj.__table__.columns
        if c.name not in {"password_hash", "token_hash"}
    }


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
    return {
        "integration": integration_policy(db),
        "payment_methods": cash_local.methods(db) if integration_policy(db)["mode"] == "local" else None,
        "user": serial(user),
        "mode": db.info.get("company", {}).get("mode", os.getenv("APP_MODE", "real")),
        "company": db.info.get("company", {}),
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
        "categories": [serial(c) for c in db.scalars(select(Category).order_by(Category.name))],
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "role": u.role,
                "email": u.email,
                "store_ids": list(
                    db.scalars(select(Access.store_id).where(Access.user_id == u.id))
                ),
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


@app.post("/api/accounts")
def create_account(data: BankCreate, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, data.store_id, ["administrador", "gestor"])
    if not db.get(Store, data.store_id):
        raise HTTPException(404, "Loja não encontrada.")
    obj = BankAccount(**data.model_dump())
    db.add(obj)
    db.flush()
    record(db, user, "conta_criada", obj.id, {"bank": obj.bank}, data.store_id)
    db.commit()
    return serial(obj)


@app.get("/api/accounts/{account_id}/transactions")
def bank_transactions(account_id: str, user=Depends(current), db: Session = Depends(db_session)):
    account = db.get(BankAccount, account_id)
    if not account:
        raise HTTPException(404, "Conta não encontrada.")
    authorize(user, db, account.store_id)
    rows = db.scalars(
        select(BankTransaction)
        .where(BankTransaction.account_id == account_id)
        .order_by(BankTransaction.movement_date.desc(), BankTransaction.created_at.desc())
    ).all()
    return [serial(row) for row in rows]


@app.post("/api/accounts/{account_id}/import-bpi")
async def import_bpi(
    account_id: str,
    confirm: bool = False,
    file: UploadFile = File(...),
    user=Depends(current),
    db: Session = Depends(db_session),
):
    from .bpi_import import parse_bpi

    account = db.scalar(select(BankAccount).where(BankAccount.id == account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Conta não encontrada.")
    authorize(user, db, account.store_id, ["administrador", "gestor"])
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(413, "O limite do ficheiro é 5 MB.")
    try:
        source, rows = parse_bpi(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    existing = db.scalars(
        select(BankTransaction).where(BankTransaction.account_id == account_id)
    ).all()
    if existing and any(row.source_account != source for row in existing):
        raise HTTPException(
            409, "Este extrato pertence a outra conta BPI. Selecione a conta bancária correta."
        )
    seen = {row.fingerprint for row in existing}
    new = []
    for row in rows:
        row["duplicate"] = row["fingerprint"] in seen
        if not row["duplicate"]:
            new.append(row)
            seen.add(row["fingerprint"])
    if confirm:
        from .models import DocumentImport

        batch = DocumentImport(
            store_id=account.store_id,
            user_id=user.id,
            filename=(file.filename or "extrato.xlsx").replace("\\", "/").split("/")[-1][:200],
            fingerprint=hashlib.sha256(data).hexdigest(),
            source="bpi_xlsx",
        )
        db.add(batch)
        db.flush()
        for row in new:
            db.add(
                BankTransaction(
                    account_id=account_id,
                    source_account=source,
                    import_id=batch.id,
                    **{k: v for k, v in row.items() if k != "duplicate"},
                )
            )
        record(
            db,
            user,
            "extrato_bpi_importado",
            account_id,
            {"novos": len(new), "duplicados": len(rows) - len(new)},
            account.store_id,
        )
        db.commit()
    return {
        "rows": rows,
        "new_count": len(new),
        "duplicate_count": len(rows) - len(new),
        "imported": confirm,
    }


from .rubrics import CategoryEdit, cash_difference_category, set_cash_difference


@app.post("/api/categories")
def create_category(data: CategoryEdit, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    if len(data.name.strip()) < 2:
        raise HTTPException(422, "Indique o nome da rubrica.")
    obj = Category(name=data.name.strip(), icon=data.icon, color=data.color)
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
    return [session_result(db, s) for s in rows]


def session_result(db, s):
    local = cash_local.is_local(db, s, integration_policy)
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
    return {"candidate": available[0] if len(available) == 1 else None, "ambiguous": len(available) > 1, "last_opening_cents": last}


@app.post("/api/sessions")
def open_session(data: OpenCash, user=Depends(current), db: Session = Depends(db_session)):
    r = db.scalar(select(Register).where(Register.id == data.register_id).with_for_update())
    if not r:
        raise HTTPException(404, "Caixa não encontrada.")
    authorize(user, db, r.store_id, ["administrador", "gestor", "operador"])
    if db.scalar(
        select(CashSession).where(CashSession.register_id == r.id, CashSession.closed_at.is_(None))
    ):
        raise HTTPException(409, "Esta caixa já tem uma sessão aberta.")
    from zoneinfo import ZoneInfo

    sheet_day = (data.sheet_day or now().astimezone(ZoneInfo("Europe/Lisbon")).date()).isoformat()
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
            and any(value is not None for value in saved.get("realized", {}).get("values", {}).values())):
        raise HTTPException(409, "Esta folha já tem realizado manual guardado. Limpe os valores e guarde o rascunho antes de alterar o dia, ou crie outra folha.")
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
    return session_result(db, s)


@app.post("/api/sessions/{session_id}/reconcile")
def reconcile_cash(session_id: str, data: CashCount, user=Depends(current), db: Session = Depends(db_session)):
    s, r = cash(db, user, session_id, True)
    if not cash_local.is_local(db, s, integration_policy):
        raise HTTPException(409, "Use a conferência de valores ERP no modo integrado.")
    counting = cash_local.validate(db, s, data, cash_local.drawer_rows(db, s), cents, user, complete=True)
    s.count_data = json.dumps(counting, ensure_ascii=False)
    record(db, user, "conferencia_folha_caixa", s.id, counting, r.store_id)
    db.commit()
    return session_result(db, s)


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
        counting = {**json.loads(s.count_data or "{}"), **validate_count(data, rows, cents, closing=True)}
    supplier = None
    if data.supplier_id:
        if data.supplier_answer != "indicado":
            raise HTTPException(422, "Confirme que pretende indicar um fornecedor.")
        supplier = next((e for e in list_entities(user, db) if e["id"] == data.supplier_id and e["is_supplier"] and e["active"]), None)
        if not supplier:
            raise HTTPException(404, "Fornecedor ativo não encontrado nesta empresa.")
    supplier_name = supplier["name"] if supplier else data.supplier_name.strip()
    if data.supplier_answer == "indicado" and len(supplier_name) < 2:
        raise HTTPException(422, "Indique o nome do fornecedor dos débitos.")
    if data.supplier_answer:
        counting["debit_supplier"] = {
            "answer": data.supplier_answer,
            "name": supplier_name if data.supplier_answer == "indicado" else "",
            "debits_cents": sum(row["debits_cents"] for row in rows),
        }
        if supplier:
            counting["debit_supplier"].update(id=supplier["id"], tax_number=supplier["tax_number"], source=supplier["source"])
        if data.supplier_answer == "indicado":
            # Associate only unidentified debits covered by this store/day sheet.
            local_debits = db.scalars(select(Movement).join(CashSession).join(Register).where(
                Register.store_id == r.store_id, Movement.direction == -1,
                Movement.reversed_by.is_(None), Movement.reversal_of.is_(None),
                Movement.origin != "fecho_caixa",
                Movement.origin != "deposito_modulo",
            ))
            local_ids = []
            for debit in local_debits:
                if (debit.entity or "").strip() in ("", "Entidade não indicada") and sheet_day_of(debit.occurred_at) == (s.sheet_day or sheet_day_of(s.opened_at)):
                    debit.entity = supplier_name
                    local_ids.append(debit.id)
            imported_ids = [item["_record_id"] for item in real_receipts(
                db, r.store_id, s.sheet_day or sheet_day_of(s.opened_at), daily_enterprise(db, s)
            ) if item.get("source_kind") == "cash_movement" and item.get("direction") == -1
                and (item.get("entity") or "").strip() in ("", "Entidade não indicada")]
            counting["debit_supplier"].update(local_movement_ids=local_ids, imported_movement_ids=imported_ids)
    counting["opening_wintouch_id"] = json.loads(s.count_data or "{}").get("opening_wintouch_id")
    counting["wintouch_excluded"] = json.loads(s.count_data or "{}").get("wintouch_excluded", False)
    counted = counting["values"]["numerario"]
    expected = next(row["expected_cents"] for row in rows if row["key"] == "numerario")
    ledger_expected = expected
    if local:
        local_result = cash_local.reconciliation(db, s, rows, counting)
        expected = next(row["expected_cents"] for row in local_result["rows"] if row["key"] == "numerario")
    counting.update(
        final=True,
        summary=rows,
        updated_at=now().isoformat(),
        updated_by=user.id,
        updated_by_name=user.name,
        closed_by=user.id,
        closed_by_name=user.name,
        workflow_mode="local" if local else "integrated",
        scope="register_day" if local else "store_day",
        ledger_balance_cents=ledger_expected if local else balance(db, s),
        include_wintouch=bool(daily_enterprise(db, s)),
    )
    if local:
        counting["local_reconciliation"] = cash_local.reconciliation(db, s, rows, counting)
    s.count_data = json.dumps(counting, ensure_ascii=False)
    s.counted_cents = counted
    s.expected_cents = expected
    s.closed_at = now()
    s.close_reason = data.reason
    s.denominations = json.dumps(data.counts)
    difference = counted - expected
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
            "difference_cents": counted - expected,
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
        .join(Register, Register.id == func.coalesce(CashSession.register_id, BankDeposit.register_id))
        .where(Register.store_id.in_(scope(user, db)))
        .where(
            (Movement.origin != "fecho_caixa")
            | (Movement.reversed_by.is_(None) & Movement.reversal_of.is_(None))
        )
        .order_by(Movement.created_at.desc())
        .limit(None if all_records else 500)
    )
    payment_names = cash_local.methods(db)
    if integration_policy(db)["mode"] != "local":
        from .cash_sheet import LABELS
        payment_names.update(LABELS)
    return [{**serial(m), "register_id": register_id, "payment_name": payment_names.get(m.payment)} for m, register_id in rows]


@app.post("/api/movements")
def create_movement(data: MovementCreate, user=Depends(current), db: Session = Depends(db_session)):
    m = add_movement(data, user, db)
    db.commit()
    return serial(m)


def add_movement(data, user, db):
    s, r = cash(db, user, data.session_id, True)
    allowed = cash_local.methods(db) if integration_policy(db)["mode"] == "local" else {"numerario", "cartao", "transferencia", "outro"}
    if data.payment not in allowed:
        raise HTTPException(422, "Escolha um meio de pagamento configurado nesta empresa.")
    if not db.get(Category, data.category_id):
        raise HTTPException(422, "Categoria inválida.")
    amount = cents(data.amount)
    direction = 1 if data.kind in IN else -1
    if data.kind in {"deposito", "reforco"} and data.payment != "numerario":
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
    result = serial(planned)
    result.update(
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
    if not db.get(Store, data.store_id) or not db.get(Category, data.category_id):
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
    if data.store_id != row.store_id or not db.get(Category, data.category_id):
        raise HTTPException(422, "Mantenha a loja original e escolha uma rubrica válida.")
    paid = planned_summary(db, row)["paid_cents"]
    amount = cents(data.amount)
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
    if original.origin == "fecho_caixa":
        raise HTTPException(409, "Reabra a folha de caixa para rever a diferença do fecho.")
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
    if not db.get(Category, data.category_id):
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
    pdf = canvas.Canvas(file)
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

    line("GPGest - Folha de caixa / relatório interno de fecho")
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
    line("Contado: " + euro(s.counted_cents))
    line("Diferença: " + euro(s.counted_cents - s.expected_cents))
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
            + " - GPGest: "
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
    if company_id:
        with database.next_platform_session() as core:
            company = core.get(Company, company_id)
            if not company:
                raise HTTPException(404, "Empresa indisponível.")
            query = query.where(WintouchSaleRecord.enterprise_id == company.api_enterprise)
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


class EntityEdit(Strict):
    category_id: str | None = None
    name: str = Field(min_length=2, max_length=300)
    tax_number: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=60)
    is_customer: bool
    is_supplier: bool
    active: bool = True

    @model_validator(mode="after")
    def validate_entity(self):
        self.name = self.name.strip()
        self.tax_number = self.tax_number.strip()
        self.email = self.email.strip().lower()
        if len(self.name) < 2 or not (self.is_customer or self.is_supplier):
            raise ValueError("Indique nome e tipo de entidade.")
        if self.email and ("@" not in self.email or any(c.isspace() for c in self.email)):
            raise ValueError("Email inválido.")
        return self


def entity_view(entity, source, key):
    return {
        "id": key,
        "source": source,
        "category_id": getattr(entity, "category_id", None),
        **{
            name: getattr(entity, name)
            for name in (
                "name",
                "tax_number",
                "email",
                "phone",
                "is_customer",
                "is_supplier",
                "active",
            )
        },
    }


@app.get("/api/entities")
def list_entities(user=Depends(current), db: Session = Depends(db_session)):
    local = list(db.scalars(select(BusinessEntity).order_by(BusinessEntity.name)))
    overrides = {e.wintouch_id: e for e in local if e.wintouch_id}
    config = integration_policy(db)
    external_primary = config["mode"] == "integrated" and config.get("authority", {}).get("entities", "erp") == "erp" and bool(db.info.get("company", {}).get("id"))
    result = [
        entity_view(e, "GPGest", "local:" + e.id)
        for e in local
        if not e.wintouch_id and not e.removed
    ]
    with database.next_platform_session() as core:
        company_id = db.info.get("company", {}).get("id")
        company = core.get(Company, company_id) if company_id else None
        enterprise = company.api_enterprise if company else None
    if not enterprise:
        return sorted(result, key=lambda e: e["name"].casefold())
    for imported in db.scalars(
        select(WintouchEntity).where(WintouchEntity.enterprise_id == enterprise)
    ):
        entity = imported if external_primary else overrides.get(imported.id, imported)
        if isinstance(entity, BusinessEntity) and entity.removed:
            continue
        result.append(
            entity_view(
                entity,
                "Wintouch (edição local)" if imported.id in overrides else "Wintouch",
                "wt:" + imported.id,
            )
        )
        result[-1]["editable"] = not external_primary
        if external_primary and imported.id in overrides:
            result[-1]["category_id"] = overrides[imported.id].category_id
    return sorted(result, key=lambda e: e["name"].casefold())


@app.post("/api/entities")
def create_entity(data: EntityEdit, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    if data.category_id and not db.get(Category, data.category_id):
        raise HTTPException(422, "Rubrica não encontrada.")
    entity = BusinessEntity(**data.model_dump())
    db.add(entity)
    db.flush()
    record(db, user, "entidade_criada", entity.id, data.model_dump())
    db.commit()
    return entity_view(entity, "GPGest", "local:" + entity.id)


@app.post("/api/entities/{entity_key}")
def edit_entity(
    entity_key: str, data: EntityEdit, user=Depends(current), db: Session = Depends(db_session)
):
    authorize(user, db, roles=["administrador", "gestor"])
    if data.category_id and not db.get(Category, data.category_id):
        raise HTTPException(422, "Rubrica não encontrada.")
    source, separator, key = entity_key.partition(":")
    if not separator or source not in {"local", "wt"}:
        raise HTTPException(404, "Entidade não encontrada.")
    if source == "local":
        entity = db.get(BusinessEntity, key)
    else:
        imported = db.get(WintouchEntity, key)
        with database.next_platform_session() as core:
            company = core.get(Company, db.info["company"]["id"])
            enterprise = company.api_enterprise
        if not imported or imported.enterprise_id != enterprise:
            raise HTTPException(404, "Entidade não encontrada.")
        config = integration_policy(db)
        if config["mode"] == "integrated" and config.get("authority", {}).get("entities", "erp") == "erp":
            if any((getattr(data, field) or "") != (getattr(imported, field) or "") for field in ("name", "tax_number", "email", "phone", "is_customer", "is_supplier", "active")):
                raise HTTPException(409, "Os dados principais desta entidade são geridos pelo ERP. Pode manter as informações locais associadas.")
        entity = db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == key))
        if not entity:
            entity = BusinessEntity(wintouch_id=key, **data.model_dump())
            db.add(entity)
    if not entity or entity.removed:
        raise HTTPException(404, "Entidade não encontrada.")
    before = entity_view(entity, source, entity_key) if entity.id else {}
    for name, value in data.model_dump().items():
        setattr(entity, name, value)
    db.flush()
    record(db, user, "entidade_alterada", entity.id, {"antes": before, "depois": data.model_dump()})
    db.commit()
    return entity_view(
        entity, "GPGest" if source == "local" else "Wintouch (edição local)", entity_key
    )


@app.post("/api/entities/{entity_key}/remove")
def remove_entity(entity_key: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    source, _, key = entity_key.partition(":")
    if source == "local":
        entity = db.get(BusinessEntity, key)
    elif source == "wt":
        imported = db.get(WintouchEntity, key)
        with database.next_platform_session() as core:
            company = core.get(Company, db.info["company"]["id"])
            enterprise = company.api_enterprise
        if not imported or imported.enterprise_id != enterprise:
            raise HTTPException(404, "Entidade não encontrada.")
        config = integration_policy(db)
        if config["mode"] == "integrated" and config.get("authority", {}).get("entities", "erp") == "erp":
            raise HTTPException(409, "A entidade pertence ao ERP. Altere o estado na origem ou escolha explicitamente a gest\u00e3o local.")
        entity = db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == key))
        if not entity:
            entity = BusinessEntity(
                wintouch_id=key,
                name=imported.name,
                tax_number=imported.tax_number or "",
                email=imported.email or "",
                phone=imported.phone or "",
                is_customer=bool(imported.is_customer),
                is_supplier=bool(imported.is_supplier),
            )
            db.add(entity)
    else:
        raise HTTPException(404, "Entidade não encontrada.")
    if not entity or entity.removed:
        raise HTTPException(404, "Entidade não encontrada.")
    entity.removed = True
    entity.active = False
    db.flush()
    record(db, user, "entidade_removida", entity.id, {"nome": entity.name, "origem": source})
    db.commit()
    return {"ok": True}


from typing import Literal


class FiscalSettingsEdit(Strict):
    vat_period: Literal["", "mensal", "trimestral"]


@app.get("/api/settings/fiscal")
def fiscal_settings(user=Depends(current), db: Session = Depends(db_session)):
    from .models import CompanyFiscalSettings
    authorize(user, db, roles=["administrador", "gestor"])
    config = db.get(CompanyFiscalSettings, "default")
    return {"vat_period": config.vat_period if config else ""}


@app.post("/api/settings/fiscal")
def save_fiscal_settings(data: FiscalSettingsEdit, user=Depends(current), db: Session = Depends(db_session)):
    from .models import CompanyFiscalSettings
    authorize(user, db, roles=["administrador", "gestor"])
    config = db.get(CompanyFiscalSettings, "default")
    if not config:
        config = CompanyFiscalSettings(id="default")
        db.add(config)
    before = config.vat_period
    config.vat_period = data.vat_period
    record(db, user, "configuracao_fiscal_alterada", "default", {"antes": before, "depois": data.model_dump()})
    db.commit()
    return {"vat_period": config.vat_period}


class AttachmentConfigEdit(Strict):
    outlook_email: str = Field(max_length=200)
    google_email: str = Field(max_length=200)
    drive_folder: str = Field(max_length=300)
    mail_folder: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_settings(self):
        import re

        for field in ("outlook_email", "google_email"):
            value = getattr(self, field).strip().lower()
            if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                raise ValueError("Email invalid")
            setattr(self, field, value)
        folder = self.drive_folder.strip()
        if folder.startswith("https://drive.google.com/drive/folders/"):
            folder = folder.split("/folders/", 1)[1].split("?", 1)[0].strip("/")
        if folder and not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", folder):
            raise ValueError("Invalid Google Drive folder")
        self.drive_folder = folder
        self.mail_folder = self.mail_folder.strip()
        if not self.mail_folder:
            raise ValueError("Mail folder required")
        return self


@app.get("/api/settings/attachments")
def attachment_settings(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    config = db.get(AttachmentIntegrationConfig, "default")
    return {"config": serial(config) if config else {}, "connected": False}


@app.post("/api/settings/attachments")
def save_attachment_settings(
    data: AttachmentConfigEdit, user=Depends(current), db: Session = Depends(db_session)
):
    authorize(user, db, roles=["administrador", "gestor"])
    config = db.get(AttachmentIntegrationConfig, "default")
    if not config:
        config = AttachmentIntegrationConfig(id="default")
        db.add(config)
    for name, value in data.model_dump().items():
        setattr(config, name, value)
    record(db, user, "configuracao_anexos_alterada", "default", data.model_dump())
    db.commit()
    return {"ok": True}


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
