import hashlib
import json
import os
import secrets
from collections import defaultdict, deque
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
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
from .db import db_session
from .models import (
    Access,
    Attachment,
    Audit,
    BankAccount,
    CashSession,
    Category,
    LoginSession,
    Movement,
    PlannedMovement,
    PlannedSettlement,
    Register,
    Store,
    User,
    now,
    uid,
)
from .platform import SyncRequest, selection_router, sync_company, sync_history
from .platform import router as platform_router
from .platform_models import Company
from .schemas import (
    BankCreate,
    CloseCash,
    Handover,
    Login,
    MovementCreate,
    Name,
    OpenCash,
    PasswordChange,
    PlannedCreate,
    PlannedPay,
    Reason,
    RegisterCreate,
    Transfer,
    UserCreate,
)

app = FastAPI(
    title="GPGest",
    version="0.1.0",
    description="Gestão interna. Não emite documentos fiscais.",
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
    return session, register


def balance(db, session):
    return session.opening_cents + int(
        db.scalar(
            select(func.sum(Movement.direction * Movement.amount_cents)).where(
                Movement.session_id == session.id, Movement.payment == "numerario"
            )
        )
        or 0
    )


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
    users = (
        list(db.scalars(select(User)))
        if user.role == "administrador"
        else list(db.scalars(select(User).join(Access).where(Access.store_id.in_(ids)).distinct()))
    )
    return {
        "user": serial(user),
        "mode": db.info.get("company", {}).get("mode", os.getenv("APP_MODE", "real")),
        "company": db.info.get("company", {}),
        "stores": [serial(s) for s in db.scalars(select(Store).where(Store.id.in_(ids)))],
        "registers": [
            serial(r) for r in db.scalars(select(Register).where(Register.store_id.in_(ids)))
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


@app.post("/api/categories")
def create_category(data: Name, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    obj = Category(name=data.name.strip())
    db.add(obj)
    db.flush()
    record(db, user, "categoria_criada", obj.id, {"name": obj.name})
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
    result = []
    for s in rows:
        totals = {
            payment: int(total)
            for payment, total in db.execute(
                select(
                    Movement.payment,
                    func.sum(Movement.direction * Movement.amount_cents),
                )
                .where(Movement.session_id == s.id)
                .group_by(Movement.payment)
            ).all()
        }
        result.append({**serial(s), "balance_cents": balance(db, s), "totals": totals})
    return result


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
    s = CashSession(register_id=r.id, operator_id=user.id, opening_cents=cents(data.opening, True))
    db.add(s)
    db.flush()
    record(db, user, "abertura", s.id, {"opening_cents": s.opening_cents}, r.store_id)
    db.commit()
    return serial(s)


@app.post("/api/sessions/{session_id}/close")
def close_session(
    session_id: str,
    data: CloseCash,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    s, r = cash(db, user, session_id, True)
    if any(
        not k.isdigit() or int(k) not in DENOMINATIONS or v < 0 or v > 100000
        for k, v in data.counts.items()
    ):
        raise HTTPException(422, "Contagem de notas ou moedas inválida.")
    counted = sum(int(k) * v for k, v in data.counts.items())
    expected = balance(db, s)
    if counted != expected and len(data.reason.strip()) < 5:
        raise HTTPException(422, "Justifique a diferença antes de fechar a caixa.")
    s.counted_cents = counted
    s.expected_cents = expected
    s.closed_at = now()
    s.close_reason = data.reason
    s.denominations = json.dumps(data.counts)
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
        },
        r.store_id,
    )
    s.closed_at = None
    s.counted_cents = None
    s.expected_cents = None
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
def movements(user=Depends(current), db: Session = Depends(db_session)):
    rows = db.scalars(
        select(Movement)
        .join(CashSession)
        .join(Register)
        .where(Register.store_id.in_(scope(user, db)))
        .order_by(Movement.created_at.desc())
        .limit(500)
    )
    return [serial(m) for m in rows]


@app.post("/api/movements")
def create_movement(data: MovementCreate, user=Depends(current), db: Session = Depends(db_session)):
    m = add_movement(data, user, db)
    db.commit()
    return serial(m)


def add_movement(data, user, db):
    s, r = cash(db, user, data.session_id, True)
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
    s, r = cash(db, user, original.session_id, True)
    authorize(user, db, r.store_id, ["administrador", "gestor"])
    db.refresh(original)
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
    if (
        db.scalar(
            select(func.count()).select_from(Attachment).where(Attachment.movement_id == m.id)
        )
        >= 3
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
    return [
        {"id": a.id, "name": a.name, "size": len(a.data)}
        for a in db.scalars(select(Attachment).where(Attachment.movement_id == m.id))
    ]


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
        return f"{Decimal(v) / 100:,.2f} EUR"

    line("GPGest - Relatório interno de fecho de caixa")
    line("NÃO É UM DOCUMENTO FISCAL")
    if os.getenv("APP_MODE") == "demonstracao":
        line("DEMONSTRAÇÃO - dados de teste")
    line("Loja: " + db.get(Store, r.store_id).name)
    line("Caixa: " + r.name)
    line("Operador: " + db.get(User, s.operator_id).name)
    line("Sessão: " + s.id)
    line("Fecho: " + s.closed_at.astimezone(ZoneInfo("Europe/Lisbon")).strftime("%d/%m/%Y %H:%M"))
    line("Fundo inicial: " + euro(s.opening_cents))
    line("Esperado: " + euro(s.expected_cents))
    line("Contado: " + euro(s.counted_cents))
    line("Diferença: " + euro(s.counted_cents - s.expected_cents))
    totals = db.execute(
        select(Movement.payment, func.sum(Movement.amount_cents * Movement.direction))
        .where(Movement.session_id == s.id)
        .group_by(Movement.payment)
    ).all()
    for payment, total in totals:
        line(payment + ": " + euro(total))
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
        return {
            "id": company.id,
            "mode": company.mode,
            "active": bool(company.active),
            "api_active": bool(company.api_active),
        }


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
