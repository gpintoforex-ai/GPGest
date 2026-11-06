"""Instituições financeiras, contas bancárias e importação de extratos (BPI, Viva Wallet)."""

import hashlib
import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .access import authorize, current, record, serial
from .db import db_session
from .drive_imports import download, drive_configured, list_files, mark_imported
from .models import BankAccount, BankInstitution, BankTransaction, DocumentImport, Store
from .schemas import BankAccountDetailsEdit, BankCreate, BankInstitutionEdit, Name, Strict

router = APIRouter()
STATEMENT_MAX_BYTES = 5_000_000


class DriveStatement(Strict):
    drive_id: str = Field(min_length=1, max_length=200)
    mapping: str = Field(default="", max_length=20000)


def bank_accounts(db, bank, lock=False):
    """Contas bancárias ligadas à instituição (pelo identificador ou, nas antigas, pelo nome)."""
    query = select(BankAccount).with_for_update() if lock else select(BankAccount)
    result = []
    for account in db.scalars(query):
        try:
            settings = json.loads(account.settings)
        except (TypeError, ValueError):
            settings = {}
        linked_id = settings.get("bank_id") if isinstance(settings, dict) else None
        if linked_id == bank.id or (not linked_id and account.bank.casefold() == bank.name.casefold()):
            result.append(account)
    return result


def rename_bank(db, bank, name):
    """Muda o nome da instituição e das contas que a usam; devolve o número de contas."""
    accounts = bank_accounts(db, bank, lock=True) if name != bank.name else []
    for account in accounts:
        account.bank = name
    bank.name = name
    return len(accounts)


@router.get("/api/banks")
def list_banks(user=Depends(current), db: Session = Depends(db_session)):
    rows = db.scalars(select(BankInstitution).order_by(BankInstitution.name)).all()
    return [serial(row) for row in rows]


@router.post("/api/banks")
def create_bank(data: Name, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    if db.scalar(select(BankInstitution.id).where(func.lower(BankInstitution.name) == data.name.lower())):
        raise HTTPException(409, "Esta instituição financeira já está registada.")
    obj = BankInstitution(name=data.name)
    db.add(obj)
    db.flush()
    record(db, user, "banco_criado", obj.id, {"name": obj.name})
    db.commit()
    return serial(obj)


@router.post("/api/banks/{bank_id}")
def edit_bank(bank_id: str, data: BankInstitutionEdit, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    obj = db.get(BankInstitution, bank_id)
    if not obj:
        raise HTTPException(404, "Instituição financeira não encontrada.")
    duplicate = db.scalar(
        select(BankInstitution.id).where(
            func.lower(BankInstitution.name) == data.name.lower(),
            BankInstitution.id != bank_id,
        )
    )
    if duplicate:
        raise HTTPException(409, "Esta instituição financeira já está registada.")
    previous_name = obj.name
    renamed_accounts = rename_bank(db, obj, data.name)
    obj.active = data.active
    record(db, user, "banco_editado", obj.id, {"name": obj.name, "previous_name": previous_name, "active": obj.active, "accounts_updated": renamed_accounts})
    db.commit()
    return serial(obj)


@router.post("/api/accounts")
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


@router.post("/api/accounts/{account_id}")
def edit_account(account_id: str, data: BankAccountDetailsEdit, user=Depends(current), db: Session = Depends(db_session)):
    account = db.scalar(select(BankAccount).where(BankAccount.id == account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Conta bancária não encontrada.")
    authorize(user, db, account.store_id, ["administrador", "gestor"])
    from .company_setup import amount as setup_amount
    from .company_setup import iban as validate_iban
    from .company_setup import unpack
    from .models import AccountBalanceReference
    from .platform import cipher
    if data.store_id and data.store_id != account.store_id:
        raise HTTPException(409, "O estabelecimento é preservado para manter o histórico da conta.")
    if data.currency and data.currency != account.currency:
        raise HTTPException(409, "A moeda de uma conta existente é preservada. Crie outra conta para uma moeda diferente.")
    if account.iban_ciphertext and not data.iban.strip() and data.iban_last4 != account.iban_last4:
        raise HTTPException(409, "Esta conta tem um IBAN completo guardado. Altere o IBAN na configuração inicial; os últimos quatro algarismos não podem ser alterados isoladamente.")
    if data.bank != account.bank:
        try:
            settings = json.loads(account.settings)
        except (TypeError, ValueError):
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        bank = db.scalar(select(BankInstitution).where(func.lower(BankInstitution.name) == data.bank.lower()))
        if bank:
            settings["bank_id"] = bank.id
        else:
            settings.pop("bank_id", None)
        account.settings = json.dumps(settings)
    if data.iban.strip():
        clear = validate_iban(data.iban)
        for other in db.scalars(select(BankAccount).where(BankAccount.id != account.id, BankAccount.iban_ciphertext != "")):
            if cipher().decrypt(other.iban_ciphertext.encode()).decode() == clear:
                raise HTTPException(409, "O IBAN já pertence a outra conta desta empresa.")
        account.iban_ciphertext = cipher().encrypt(clear.encode()).decode()
        data.iban_last4 = clear[-4:]
    if data.opening.strip():
        from datetime import date
        if not data.opening_date or data.opening_date > date.today():
            raise HTTPException(422, "Indique uma data de saldo inicial válida e não futura.")
        value = setup_amount(data.opening, signed=True)
        ref = db.get(AccountBalanceReference, "bank:" + account.id)
        if ref and (ref.amount_cents != value or ref.reference_date != data.opening_date.isoformat()):
            raise HTTPException(409, "Já existe um saldo inicial diferente. Reveja-o em Ligação ERP > Saldos iniciais.")
        if not ref:
            db.add(AccountBalanceReference(target="bank:" + account.id, store_id=account.store_id, amount_cents=value, currency=account.currency, reference_date=data.opening_date.isoformat(), user_id=user.id))
    if data.principal is not None:
        for other in db.scalars(select(BankAccount)):
            config = unpack(other.settings)
            if other.id == account.id or data.principal:
                other.settings = json.dumps({**config, "principal": data.principal if other.id == account.id else False})
    before = {key: getattr(account, key) for key in ("name", "bank", "iban_last4")}
    account.name, account.bank, account.iban_last4 = data.name, data.bank, data.iban_last4
    record(db, user, "conta_editada", account.id, {"before": before, "after": data.model_dump(mode="json", exclude={"iban"}), "iban_changed": bool(data.iban.strip())}, account.store_id)
    db.commit()
    return serial(account)


@router.get("/api/accounts/{account_id}/transactions")
def bank_transactions(account_id: str, user=Depends(current), db: Session = Depends(db_session)):
    account = db.get(BankAccount, account_id)
    if not account:
        raise HTTPException(404, "Conta não encontrada.")
    authorize(user, db, account.store_id)
    rows = db.scalars(
        select(BankTransaction)
        .where(BankTransaction.account_id == account_id)
        .order_by(
            BankTransaction.movement_date.desc(),
            BankTransaction.exported_at.desc(),
            BankTransaction.source_position.asc(),
            BankTransaction.created_at.desc(),
        )
    ).all()
    available = dict(db.execute(select(DocumentImport.id, DocumentImport.file_ciphertext.is_not(None)).where(DocumentImport.id.in_({row.import_id for row in rows if row.import_id}))).all())
    return [{**serial(row), "original_available": bool(available.get(row.import_id))} for row in rows]


@router.post("/api/accounts/{account_id}/import-bpi")
@router.post("/api/accounts/{account_id}/import-statement")
def import_bpi(
    account_id: str,
    confirm: bool = False,
    file: UploadFile = File(...),
    mapping: str = Form(default=""),
    user=Depends(current),
    db: Session = Depends(db_session),
):
    account = statement_account(db, user, account_id)
    data = file.file.read(STATEMENT_MAX_BYTES + 1)
    if len(data) > STATEMENT_MAX_BYTES:
        raise HTTPException(413, "O limite do ficheiro é 5 MB.")
    return import_statement(db, user, account, data, file.filename, confirm, mapping)


@router.get("/api/accounts/{account_id}/drive-statements")
def drive_statements(account_id: str, user=Depends(current), db: Session = Depends(db_session)):
    statement_account(db, user, account_id)
    if not drive_configured(db):
        return {"configured": False, "files": []}
    return {"configured": True, "files": mark_imported(db, "statement", list_files(db, "statement"))}


@router.post("/api/accounts/{account_id}/import-statement/drive")
def import_drive_statement(
    account_id: str,
    data: DriveStatement,
    confirm: bool = False,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    statement_account(db, user, account_id)
    chunks = []
    name, _ = download(db, "statement", data.drive_id, STATEMENT_MAX_BYTES, chunks.append)
    # authorized_client pode ter confirmado a sessão ao renovar o token: voltar a bloquear a conta.
    account = statement_account(db, user, account_id)
    return import_statement(db, user, account, b"".join(chunks), name, confirm, data.mapping)


def statement_account(db, user, account_id):
    account = db.scalar(select(BankAccount).where(BankAccount.id == account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Conta não encontrada.")
    authorize(user, db, account.store_id, ["administrador", "gestor"])
    return account


def import_statement(db, user, account, data, filename, confirm, mapping=""):
    from .bpi_import import parse_bpi
    from .viva_import import parse_viva

    account_id = account.id
    details = {}
    generic = False
    if len(mapping) > 20000:
        raise HTTPException(422, "Mapeamento demasiado grande.")
    try:
        name = (filename or "").lower()
        is_viva = not data.startswith(b"PK")
        if mapping or name.endswith((".csv", ".txt", ".xml")):
            generic = True
        if not generic:
            try:
                source, rows = parse_viva(data) if is_viva else parse_bpi(data)
            except ValueError:
                if data.startswith(b"PK"):
                    generic = True
                else:
                    raise
        if generic:
            from .statement_import import Mapping, MappingError, parse_mapped, parse_camt
            from .platform import cipher
            try:
                details = parse_camt(data) if name.endswith(".xml") else parse_mapped(data, filename or "", Mapping.model_validate_json(mapping) if mapping else None)
            except MappingError as exc:
                if confirm:
                    raise
                details = exc.preview
            if details.get("mapping_required"):
                if confirm:
                    raise ValueError("Configure o mapeamento e pré-visualize antes de confirmar.")
                return {**details, "imported": False}
            rows = details["rows"]
            expected_iban = cipher().decrypt(account.iban_ciphertext.encode()).decode() if account.iban_ciphertext else ""
            for row in rows:
                identifier = re.sub(r"\s", "", row.pop("mapped_account", "")).upper()
                currency = row.pop("mapped_currency", "").upper()
                if currency and currency != account.currency:
                    raise ValueError("A moeda do extrato não corresponde à conta selecionada.")
                if identifier and identifier not in {account.id.upper(), expected_iban.upper()}:
                    raise ValueError("A conta/IBAN do extrato não corresponde à conta selecionada. Configure o IBAN completo para validar ficheiros identificados por IBAN.")
            source = "mapped:" + account.id
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    format_name = details.get("format") if generic else "Viva Wallet" if is_viva else "BPI Net Empresas"
    existing = db.scalars(
        select(BankTransaction).where(BankTransaction.account_id == account_id)
    ).all()
    if not generic and existing and any(row.source_account != source and not row.source_account.startswith("mapped:") for row in existing):
        raise HTTPException(
            409, "Este extrato pertence a outra conta ou banco. Selecione a conta bancária correta."
        )
    if not generic and account.currency != "EUR":
        raise HTTPException(422, "Este formato de extrato só pode ser importado numa conta em euros.")
    seen = {row.fingerprint for row in existing}
    def economic(row):
        get = (lambda key: row[key]) if isinstance(row, dict) else (lambda key: getattr(row, key))
        return (get("movement_date"), get("value_date"), " ".join(get("description").casefold().split()), get("amount_cents"), get("balance_cents"))
    pool = {}
    for previous in existing:
        if previous.balance_cents is not None:
            pool.setdefault(economic(previous), []).append(previous.reference or "")
    new = []
    for row in rows:
        row["duplicate"] = row["fingerprint"] in seen
        references = pool.get(economic(row), []) if row["balance_cents"] is not None else []
        reference = row.get("reference", "")
        candidate = next((i for i, value in enumerate(references) if value == reference or not value or not reference), None)
        if candidate is not None:
            row["duplicate"] = True
            references.pop(candidate)
        if not row["duplicate"]:
            new.append(row)
            seen.add(row["fingerprint"])
    if confirm:
        from .reconciliation_center import assert_open
        from .platform import cipher
        for row in new:
            assert_open(db, account.id, row["movement_date"])
        batch = DocumentImport(
            store_id=account.store_id,
            user_id=user.id,
            filename=(filename or ("extrato.xls" if is_viva else "extrato.xlsx")).replace("\\", "/").split("/")[-1][:200],
            fingerprint=hashlib.sha256(data).hexdigest(),
            source="bank_mapped" if generic else "viva_wallet_xls" if is_viva else "bpi_xlsx",
            file_ciphertext=cipher().encrypt(data),
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
            "extrato_mapeado_importado" if generic else "extrato_viva_importado" if is_viva else "extrato_bpi_importado",
            account_id,
            {"novos": len(new), "duplicados": len(rows) - len(new)},
            account.store_id,
        )
        db.commit()
    return {
        **{k: v for k, v in details.items() if k != "rows"},
        "rows": rows,
        "new_count": len(new),
        "duplicate_count": len(rows) - len(new),
        "imported": confirm,
        "import_id": batch.id if confirm else None,
        "original_url": f"/api/reconciliation/accounts/{account.id}/imports/{batch.id}/original" if confirm and new else None,
        "format": format_name,
        "warnings": ["Confirme a conta, moeda, separador decimal e ordem das linhas. Os saldos ausentes permanecem desconhecidos; não são substituídos por zero."] if generic else [
            "O extrato Viva Wallet não identifica a conta de origem nem a moeda. "
            "Confirme que pertence à conta selecionada e que os valores estão em euros."
        ] if is_viva else [],
    }
