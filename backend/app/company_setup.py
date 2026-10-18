"""Company-scoped onboarding; drafts and final application are durable and audited."""
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException
from sqlalchemy import MetaData, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import db as database
from .cash_local import BASE_METHODS, method_catalog
from .db import db_session
from .models import (
    Access,
    AccountBalanceReference,
    AttachmentIntegrationConfig,
    BankAccount,
    BankInstitution,
    CashSession,
    Category,
    CompanySetup,
    Employee,
    EmployeeStore,
    GoogleConnection,
    IntegrationBinding,
    LocalPaymentMethod,
    PaymentMethodBankDefault,
    PaymentTerminal,
    Register,
    Store,
    User,
    WintouchCashAccount,
    WintouchPaymentMethod,
    WintouchSyncRun,
    now,
)
from .operating_modes import CONNECTORS, RESOURCES, pending_operations
from .platform import cipher, validate_url
from .platform_models import Company
from .setup_schemas import STEPS, DraftSave, Finish, ImportPreview, Revision, SetupData
from .wintouch_sync import RESOURCES as CATALOGS
from .wintouch_sync import SyncError, normalize, string

SETUP_CATALOGS = {**CATALOGS, "payment_methods": WintouchPaymentMethod}


def unpack(value):
    return json.loads(value or "{}")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def euros(value):
    return f"{Decimal(value) / 100:.2f}".replace(".", ",")


def amount(value, signed=False):
    try:
        number = Decimal(str(value).strip().replace(",", "."))
        if not number.is_finite() or abs(number) > 999999999 or number * 100 != (number * 100).to_integral_value() or (not signed and number < 0):
            raise ValueError()
        return int(number * 100)
    except (ValueError, InvalidOperation):
        raise HTTPException(422, "Indique um montante válido com, no máximo, duas casas decimais.") from None


def iban(value):
    cleaned = "".join(value.split()).upper()
    if not cleaned:
        return ""
    import re
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", cleaned):
        raise HTTPException(422, "O IBAN introduzido não é válido.")
    # ISO 13616: checksum and known national lengths; unknown countries remain invalid.
    lengths = {"PT":25,"ES":24,"FR":27,"DE":22,"IT":27,"NL":18,"BE":16,"GB":22,"IE":22,"LU":20,"CH":21,"AT":20,"DK":18,"FI":18,"NO":15,"SE":24,"PL":28,"RO":24,"CZ":24,"HU":28,"GR":27,"SK":24,"SI":19,"HR":21,"BG":22,"CY":28,"EE":20,"LT":20,"LV":21,"MT":31,"IS":26,"LI":21,"BR":29,"AO":25,"CV":25,"ST":25,"TL":23,"AE":23,"SA":24,"TR":26,"UA":29,"MD":24,"AL":28,"BA":20,"RS":22,"ME":22,"MK":19,"XK":20,"GE":22,"AZ":28,"BH":22,"IL":23,"IQ":23,"JO":30,"KW":30,"LB":28,"PS":29,"QA":29,"CR":22,"DO":28,"GT":28,"SV":28,"SC":31,"LC":32,"VA":22,"SM":27,"BY":28,"PK":24,"KZ":20,"TN":24,"MR":27,"MU":30,"BI":27,"DJ":27,"EG":29,"LY":25,"SO":23,"SD":18,"RU":33,"OM":23,"FK":18,"NI":28,"MN":20}
    if lengths.get(cleaned[:2]) != len(cleaned):
        raise HTTPException(422, "O comprimento do IBAN não corresponde ao país indicado.")
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in cleaned[4:] + cleaned[:4])
    if int(digits) % 97 != 1:
        raise HTTPException(422, "O IBAN introduzido tem dígitos de controlo inválidos.")
    return cleaned


def settings(db):
    row = db.get(CompanySetup, "default")
    return unpack(row.settings) if row else {}


def register_settings(register):
    return unpack(register.settings)


def company_table():
    # Explicit central schema on the SAME transaction as tenant writes. Using a
    # separate platform commit here would leave half-applied configuration.
    with database.next_platform_session() as core:
        schema = (core.get_bind().get_execution_options().get("schema_translate_map") or {}).get(None, "public")
    return Company.__table__.to_metadata(MetaData(), schema=schema)


def central(db, lock=False):
    table = company_table()
    query = select(table).where(table.c.id == db.info.get("company", {}).get("id"), table.c.active == 1)
    result = db.execute(query.with_for_update() if lock else query).mappings().first()
    if not result:
        raise HTTPException(404, "Empresa ativa indisponível.")
    return table, dict(result)


def initial(db, company):
    config = settings(db)
    stores = list(db.scalars(select(Store).order_by(Store.name)))
    primary = config.get("company", {}).get("store_id") or (stores[0].id if stores else "primary")
    profile = {"name": company["name"], "trade_name": company["name"], "nif": company["nif"] or "",
               "store_id": primary, "store_name": next((s.name for s in stores if s.id == primary), "Loja principal"),
               **config.get("company", {})}
    registers = []
    for row in db.scalars(select(Register).order_by(Register.name)):
        ref = db.get(AccountBalanceReference, "cash:" + row.id)
        preferences = register_settings(row)
        last = db.scalar(select(CashSession).where(CashSession.register_id == row.id).order_by(CashSession.opened_at.desc()).limit(1))
        registers.append({"key": row.id, "id": row.id, "name": row.name, "store_id": row.store_id,
                          "fund": euros(preferences.get("fund_cents", ref.fund_cents if ref else (last.retained_fund_cents if last.retained_fund_cents is not None else last.opening_cents) if last else 0)),
                          "opening": euros(ref.amount_cents) if ref else None,
                          **{k: v for k, v in preferences.items() if k in ("responsible", "start_date", "count_required", "balance_mode")}})
    accounts = []
    for row in db.scalars(select(BankAccount).order_by(BankAccount.name)):
        ref = db.get(AccountBalanceReference, "bank:" + row.id)
        accounts.append({"key": row.id, "id": row.id, "bank": row.bank, "name": row.name, "currency": row.currency,
                         "store_id": row.store_id, "opening": euros(ref.amount_cents) if ref else None,
                         "opening_date": ref.reference_date if ref else None, "principal": unpack(row.settings).get("principal", False)})
    methods = []
    for item in method_catalog(db):
        configured = db.get(LocalPaymentMethod, item["id"])
        detail = unpack(configured.settings) if configured else {}
        defaults = list(db.scalars(select(PaymentMethodBankDefault).where(PaymentMethodBankDefault.method_id == item["id"], PaymentMethodBankDefault.store_id == primary)))
        methods.append({"key": item["id"], "id": item["id"], "name": item["name"], "selected": item["active"],
                        "cash": item["id"] == "numerario", "bank_receivable": item["id"] in ("cartao", "mbway", "transferencia"),
                        "account": defaults[0].account_id if defaults else None, **detail})
    if not any(p["key"] == "cheque" for p in methods):
        methods.append({"key": "cheque", "name": "Cheque", "selected": False})
    terminals = []
    for row in db.scalars(select(PaymentTerminal).where(PaymentTerminal.active.is_(True))):
        terminals.append({"key": row.id, "id": row.id, "name": row.name, "identifier": row.identifier,
                          "store_id": row.store_id, "register_id": row.register_id, "provider": row.provider, "account": row.account_id,
                          "settlement_days": row.settlement_days, "settlement_mode": row.settlement_mode,
                          "commission_percent": euros(row.commission_bps) if row.commission_bps is not None else None,
                          "commission_fixed": euros(row.commission_cents) if row.commission_cents is not None else None,
                          "statement_reference": row.statement_reference, "mbway": row.mbway})
    return SetupData(company=profile, mode={"mode": company["operating_mode"], "provider": company["erp_provider"] if CONNECTORS.get(company["erp_provider"], {}).get("operational") else "wintouch",
                     "api_url": company["api_url"], "enterprise": company["api_enterprise"], "resources": unpack(company["sync_resources"])},
                     registers=registers or [{"key": "main", "name": "Caixa principal", "store_id": primary}],
                     accounts=accounts, payments=methods, terminals=terminals, preferences=config.get("preferences", {})).model_dump(mode="json")


def public_draft(raw):
    result = json.loads(json.dumps(raw))
    result.setdefault("mode", {}).pop("api_key_ciphertext", None)
    result["mode"]["api_key"] = ""
    for account in result.get("accounts", []):
        account.pop("iban_ciphertext", None)
        account["iban"] = ""
    return result


def credentials(raw, company):
    config = raw["mode"]
    return config.get("api_key_ciphertext") or company["api_key_ciphertext"]


def connection_fingerprint(raw, company):
    config = raw["mode"]
    return digest({"provider": config["provider"], "url": config["api_url"], "enterprise": config["enterprise"], "key": credentials(raw, company)})


def tested(row, raw, company):
    test = unpack(row.erp_test) if row else {}
    return bool(test.get("success") and test.get("fingerprint") == connection_fingerprint(raw, company))


def state_row(db, revision):
    db.execute(insert(CompanySetup).values(id="default").on_conflict_do_nothing(index_elements=[CompanySetup.id]))
    row = db.scalar(select(CompanySetup).where(CompanySetup.id == "default").with_for_update())
    if row.revision != revision:
        raise HTTPException(409, "A configuração foi atualizada noutra sessão. Reabra o assistente para continuar com a versão guardada.")
    return row


def touch(row, user):
    row.revision += 1
    row.updated_at, row.updated_by = now(), user.id


def pending(db, raw, row, company):
    data = SetupData(**public_draft(raw))
    result = []
    if data.mode.mode == "integrated" and (data.mode.defer or not tested(row, raw, company)):
        result.append("Configurar e testar a ligação ao ERP")
    terminals = [t for t in data.terminals if "terminals" not in data.skipped or t.id]
    accounts = [a for a in data.accounts if "banks" not in data.skipped or a.id]
    if any(p.selected and p.key == "cartao" for p in data.payments) and not terminals:
        result.append("Configurar os terminais TPA e as contas de liquidação")
    if not accounts:
        result.append("Adicionar contas bancárias, quando necessário")
    if data.preferences.archive == "later":
        result.append("Escolher o local de arquivo dos documentos")
    if data.preferences.archive == "drive":
        google = db.get(GoogleConnection, "default")
        config = db.get(AttachmentIntegrationConfig, "default")
        if not google or not google.token_ciphertext or not config or not config.drive_folder:
            result.append("Ligar o Google Drive e escolher a pasta de arquivo")
    return result


def overview(db, company=None):
    row = db.get(CompanySetup, "default")
    if not row:
        return {"started": False, "completed": False, "revision": 0, "step": "company", "progress": 0, "pending": [], "updated_at": None}
    company = company or central(db)[1]
    raw = unpack(row.draft) if row and row.draft != "{}" else initial(db, company)
    return {"started": bool(row), "completed": bool(row and row.completed_at), "revision": row.revision if row else 0,
            "step": row.step if row else "company", "progress": 100 if row and row.completed_at else round(STEPS.index(row.step) / len(STEPS) * 100) if row else 0,
            "pending": pending(db, raw, row, company), "updated_at": row.updated_at.isoformat() if row else None}


def view(db, company, serial):
    row = db.get(CompanySetup, "default")
    raw = unpack(row.draft) if row and row.draft != "{}" else initial(db, company)
    data = public_draft(raw)
    return {**overview(db, company), "data": data, "connection_tested": tested(row, raw, company),
            "has_api_key": bool(credentials(raw, company)),
            "iban_configured": [a["key"] for a in raw.get("accounts", []) if a.get("iban_ciphertext") or a.get("id") and db.get(BankAccount, a["id"]) and db.get(BankAccount, a["id"]).iban_ciphertext],
            "connectors": [{"id": key, "name": value["name"], "capabilities": value["capabilities"]} for key, value in CONNECTORS.items() if value["operational"]],
            "resource_labels": RESOURCES, "catalog_labels": {**RESOURCES, "payment_methods": "Meios de pagamento"}, "stores": [serial(s) for s in db.scalars(select(Store).order_by(Store.name))],
            "banks": [serial(b) for b in db.scalars(select(BankInstitution).where(BankInstitution.active.is_(True)).order_by(BankInstitution.name))],
            "responsibles": [{"id": "user:" + u.id, "name": u.name} for u in db.scalars(select(User).where(User.active == 1, User.role != "consulta"))]
                + [{"id": "employee:" + e.id, "name": e.name} for e in db.scalars(select(Employee).where(Employee.active.is_(True)))],
            "erp_accounts": [serial(a) for a in db.scalars(select(WintouchCashAccount).where(WintouchCashAccount.enterprise_id == data["mode"]["enterprise"], WintouchCashAccount.active.is_(True)))],
            "erp_methods": [{"id": p.id, "name": p.name} for p in db.scalars(select(WintouchPaymentMethod).where(WintouchPaymentMethod.enterprise_id == data["mode"]["enterprise"]))]}


def validate_final(db, raw, company, row):
    data = SetupData(**public_draft(raw))
    if "banks" in data.skipped:
        data.accounts = [a for a in data.accounts if a.id]
    if "terminals" in data.skipped:
        data.terminals = [t for t in data.terminals if t.id]
    if "preferences" in data.skipped:
        data.preferences = type(data.preferences).model_validate(settings(db).get("preferences", {}))
    if len(data.company.name.strip()) < 2 or len(data.company.store_name.strip()) < 2 or not data.company.nif.isdigit() or len(data.company.nif) != 9:
        raise HTTPException(422, "Preencha o nome da empresa, o NIF de nove dígitos e o estabelecimento principal.")
    if len(data.company.trade_name.strip()) < 2:
        raise HTTPException(422, "Indique o nome comercial da empresa.")
    if data.mode.mode == "integrated" and not CONNECTORS.get(data.mode.provider, {}).get("operational"):
        raise HTTPException(422, "Escolha um conector efetivamente disponível.")
    if data.mode.mode == "integrated" and not data.mode.defer and not tested(row, raw, company):
        raise HTTPException(422, "Teste a ligação ao ERP ou escolha concluir com a ligação pendente.")
    if pending_operations(db) or db.scalar(select(WintouchSyncRun.id).where(WintouchSyncRun.status == "em_curso")):
        if data.mode.mode != company["operating_mode"] or data.mode.enterprise != company["api_enterprise"]:
            raise HTTPException(409, "Existem operações ERP pendentes ou sincronizações em curso. Reveja-as em Ligação ERP antes de mudar a origem.")
    if not data.registers and not data.accounts:
        raise HTTPException(422, "Adicione ou associe pelo menos uma caixa ou conta bancária para começar.")
    for group in (data.registers, data.accounts, data.payments, data.terminals):
        keys = [item.key for item in group]
        ids = [item.id for item in group if item.id]
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise HTTPException(422, "Cada registo só pode aparecer uma vez na configuração.")
    if len({(r.store_id, r.name.strip().casefold()) for r in data.registers}) != len(data.registers):
        raise HTTPException(422, "Use nomes de caixa diferentes no mesmo estabelecimento.")
    if sum(a.principal for a in data.accounts) > 1:
        raise HTTPException(422, "Escolha apenas uma conta bancária principal.")
    for item in [*data.registers, *data.accounts, *data.terminals]:
        if len(item.name.strip()) < 2:
            raise HTTPException(422, "Preencha os nomes das caixas, contas e terminais que pretende guardar.")
    for register in data.registers:
        amount(register.fund)
        if register.opening is not None and register.opening.strip():
            amount(register.opening)
            if not register.start_date:
                raise HTTPException(422, "Indique a data de início do saldo inicial de cada caixa.")
    for account in data.accounts:
        if len(account.bank.strip()) < 2 and not account.bank_id:
            raise HTTPException(422, "Indique o banco de cada conta bancária.")
        if account.opening is not None and account.opening.strip():
            amount(account.opening, signed=True)
            if not account.opening_date:
                raise HTTPException(422, "Indique a data do saldo inicial bancário.")
    selected = [p for p in data.payments if p.selected]
    if not any(p.key == "numerario" and p.cash and p.receipts and p.payments for p in selected):
        raise HTTPException(422, "Confirme o numerário para gerir e contar a gaveta.")
    if any(len(p.name.strip()) < 2 or not (p.receipts or p.payments) or p.cash and p.bank_receivable for p in selected):
        raise HTTPException(422, "Reveja o nome, a utilização e o destino de cada meio de pagamento.")
    if len({p.name.strip().casefold() for p in selected}) != len(selected):
        raise HTTPException(422, "Use um nome diferente para cada meio de pagamento.")
    if any(p.key in {"numerario", "cartao", "mbway", "transferencia"} and p.cash != (p.key == "numerario") for p in selected):
        raise HTTPException(422, "O numerário movimenta a gaveta; os meios bancários são conferidos no banco ou terminal.")
    if len({p.erp_id for p in selected if p.erp_id}) != len([p for p in selected if p.erp_id]):
        raise HTTPException(422, "Associe cada meio ERP a um único meio de pagamento local.")
    for item in data.terminals:
        if not item.account:
            raise HTTPException(422, "Escolha a conta bancária onde recebe os pagamentos de cada terminal TPA.")
        if item.commission_percent is not None and item.commission_percent.strip() and amount(item.commission_percent) > 10000:
            raise HTTPException(422, "A comissão percentual deve estar entre 0 e 100.")
        if item.commission_fixed is not None and item.commission_fixed.strip():
            amount(item.commission_fixed)
    return data


def preview_result(db, raw, row, company):
    data = validate_final(db, raw, company, row)
    return {"token": digest({"draft": raw, "revision": row.revision, "company_revision": company["integration_revision"]}),
            "mode": data.mode.mode, "company": data.company.model_dump(),
            "registers": [r.model_dump(mode="json") for r in data.registers],
            "accounts": [{k: v for k, v in a.model_dump(mode="json").items() if k != "iban"} for a in data.accounts],
            "payments": [p.model_dump() for p in data.payments if p.selected],
            "terminals": [t.model_dump() for t in data.terminals], "pending": pending(db, raw, row, company)}


def stable(company, kind, key):
    return str(uuid5(NAMESPACE_URL, "gpgest:" + company["id"] + ":" + kind + ":" + key))


def apply_configuration(db, data, raw, row, company, table, user, audit):
    primary = db.get(Store, data.company.store_id)
    if primary is None:
        if data.company.store_id != "primary":
            raise HTTPException(422, "Escolha um estabelecimento desta empresa.")
        primary = db.scalar(select(Store).where(func.lower(Store.name) == data.company.store_name.strip().lower()))
        if primary is None:
            primary = Store(id=stable(company, "store", "primary"), name=data.company.store_name.strip())
            db.add(primary)
            db.flush()
    primary.name = data.company.store_name.strip()
    def store(identifier):
        found = primary if identifier in ("primary", data.company.store_id) else db.get(Store, identifier)
        if not found:
            raise HTTPException(422, "O estabelecimento não pertence a esta empresa.")
        return found.id

    def reference(target, store_id, text, fund, stamp, currency):
        if text is None or not text.strip():
            return
        value = amount(text, signed=target.startswith("bank:"))
        if stamp > now().astimezone(ZoneInfo(data.company.timezone)).date():
            raise HTTPException(422, "O saldo inicial não pode ter uma data futura.")
        current = db.get(AccountBalanceReference, target)
        if current:
            if (current.amount_cents, current.fund_cents, current.reference_date, current.currency) != (value, fund, stamp.isoformat(), currency):
                raise HTTPException(409, "Já existe um saldo inicial diferente. Reveja-o em Ligação ERP > Saldos iniciais antes de o substituir.")
            return
        if target.startswith("cash:") and db.scalar(select(CashSession.id).where(CashSession.register_id == target[5:])):
            raise HTTPException(409, "Esta caixa já tem folhas. O assistente não altera o seu saldo inicial histórico.")
        db.add(AccountBalanceReference(target=target, store_id=store_id, amount_cents=value, fund_cents=fund,
                                      currency=currency, reference_date=stamp.isoformat(), user_id=user.id))
        audit(db, user, "saldo_inicial_configurado", target, {"amount_cents": value, "fund_cents": fund, "date": stamp.isoformat()}, store_id)

    registers, accounts = {}, {}
    for item in data.registers:
        store_id = store(item.store_id)
        register = db.get(Register, item.id) if item.id else db.scalar(select(Register).where(Register.store_id == store_id, func.lower(Register.name) == item.name.strip().lower()))
        if item.id and (not register or register.store_id != store_id):
            raise HTTPException(422, "Escolha uma caixa deste estabelecimento e desta empresa.")
        if register is None:
            register = Register(id=stable(company, "register", item.key), store_id=store_id, name=item.name.strip(), currency=data.company.currency)
            db.add(register)
            db.flush()
        if item.responsible:
            kind, _, identifier = item.responsible.partition(":")
            person = db.get(User if kind == "user" else Employee, identifier) if kind in ("user", "employee") else None
            permitted = person and person.active and (kind == "user" and person.role == "administrador" or db.get(Access if kind == "user" else EmployeeStore, (identifier, store_id)))
            if not permitted:
                raise HTTPException(422, "Escolha um responsável ativo com acesso a este estabelecimento.")
        register.name = item.name.strip()
        register.settings = json.dumps({"fund_cents": amount(item.fund), "start_date": item.start_date.isoformat() if item.start_date else None,
                                       "responsible": item.responsible, "count_required": item.count_required, "balance_mode": item.balance_mode, "timezone": data.company.timezone})
        reference("cash:" + register.id, store_id, item.opening, min(amount(item.fund), amount(item.opening)) if item.opening else amount(item.fund), item.start_date, register.currency)
        item.id = register.id
        registers[item.key] = register
        registers[register.id] = register
        if item.erp_id:
            remote = db.scalar(select(WintouchCashAccount).where(WintouchCashAccount.enterprise_id == data.mode.enterprise, WintouchCashAccount.external_id == item.erp_id))
            if not remote or remote.register_id and remote.register_id != register.id:
                raise HTTPException(409, "Importe a caixa ERP ou reveja a associação existente antes de continuar.")
            binding = db.scalar(select(IntegrationBinding).where(IntegrationBinding.company_id == company["id"], IntegrationBinding.connector == data.mode.provider,
                IntegrationBinding.external_kind == "register", IntegrationBinding.external_id == remote.external_id))
            reverse = db.scalar(select(IntegrationBinding).where(IntegrationBinding.company_id == company["id"], IntegrationBinding.connector == data.mode.provider,
                IntegrationBinding.internal_kind == "register", IntegrationBinding.internal_id == register.id))
            if binding and binding.internal_id != register.id or reverse and reverse.external_id != remote.external_id:
                raise HTTPException(409, "A caixa já possui outra associação ERP.")
            remote.register_id, remote.store_id = register.id, store_id
            if not binding:
                db.add(IntegrationBinding(company_id=company["id"], connector=data.mode.provider, external_kind="register", external_id=remote.external_id, internal_kind="register", internal_id=register.id))
    known_ibans = {}
    for stored in db.scalars(select(BankAccount).where(BankAccount.iban_ciphertext != "")):
        try:
            clear = cipher().decrypt(stored.iban_ciphertext.encode()).decode()
        except InvalidToken:
            raise HTTPException(409, "Não foi possível ler um IBAN existente. Verifique a chave de cifra antes de alterar as contas.") from None
        known_ibans.setdefault(digest(clear), []).append(stored)
    for item in data.accounts:
        store_id = store(item.store_id)
        bank = db.get(BankInstitution, item.bank_id) if item.bank_id else db.scalar(select(BankInstitution).where(func.lower(BankInstitution.name) == item.bank.strip().lower()))
        if item.bank_id and (not bank or not bank.active):
            raise HTTPException(422, "Escolha um banco ativo desta empresa.")
        if bank is None:
            bank = BankInstitution(id=stable(company, "bank", item.bank.strip().casefold()), name=item.bank.strip())
            db.add(bank)
            db.flush()
        encrypted = next(a.get("iban_ciphertext", "") for a in raw["accounts"] if a["key"] == item.key)
        account = db.get(BankAccount, item.id) if item.id else db.scalar(select(BankAccount).where(BankAccount.store_id == store_id, func.lower(BankAccount.name) == item.name.strip().lower(), BankAccount.bank == bank.name))
        fingerprint = digest(cipher().decrypt(encrypted.encode()).decode()) if encrypted else None
        same_iban = known_ibans.get(fingerprint, []) if fingerprint else []
        if not item.id and same_iban:
            if len(same_iban) != 1 or same_iban[0].store_id != store_id:
                raise HTTPException(409, "O IBAN já existe na empresa. Selecione a conta existente e o respetivo estabelecimento.")
            account = same_iban[0]
        if account and any(a.id == account.id for a in accounts.values()):
            raise HTTPException(422, "A mesma conta bancária ou IBAN aparece duas vezes. Retire a duplicação da configuração.")
        if item.id and any(a.id != item.id for a in same_iban):
            raise HTTPException(409, "O IBAN já pertence a outra conta desta empresa. Reutilize essa conta.")
        if item.id and (not account or account.store_id != store_id):
            raise HTTPException(422, "Escolha uma conta bancária deste estabelecimento e desta empresa.")
        if account is None:
            account = BankAccount(id=stable(company, "account", item.key), store_id=store_id, name=item.name.strip(), bank=bank.name, currency=item.currency, iban_last4="")
            db.add(account)
        if account.currency != item.currency:
            raise HTTPException(409, "A moeda de uma conta existente é preservada. Crie outra conta para uma moeda diferente.")
        account.name, account.bank = item.name.strip(), bank.name
        if encrypted:
            account.iban_ciphertext = encrypted
            account.iban_last4 = cipher().decrypt(encrypted.encode()).decode()[-4:]
            known_ibans[fingerprint] = [account]
        account.settings = json.dumps({"principal": item.principal, "bank_id": bank.id})
        db.flush()
        reference("bank:" + account.id, store_id, item.opening, 0, item.opening_date, item.currency)
        item.id, item.bank_id, item.bank = account.id, bank.id, bank.name
        accounts[item.key], accounts[account.id] = account, account
    if any(a.principal for a in data.accounts):
        submitted = {a.id for a in data.accounts}
        for existing in db.scalars(select(BankAccount).where(BankAccount.id.notin_(submitted))):
            detail = unpack(existing.settings)
            if detail.get("principal"):
                existing.settings = json.dumps({**detail, "principal": False})
    for item in data.terminals:
        account = accounts.get(item.account) or db.get(BankAccount, item.account)
        store_id = store(item.store_id)
        register = registers.get(item.register_id) if item.register_id else None
        if not account or account.store_id != store_id or item.register_id and (not register or register.store_id != store_id):
            raise HTTPException(422, "O terminal, a caixa e a conta de destino têm de pertencer ao mesmo estabelecimento.")
        terminal = db.get(PaymentTerminal, item.id) if item.id else db.scalar(select(PaymentTerminal).where(PaymentTerminal.store_id == store_id, func.lower(PaymentTerminal.name) == item.name.strip().lower()))
        if item.id and (not terminal or terminal.store_id != store_id):
            raise HTTPException(422, "Escolha um terminal desta empresa.")
        terminal = terminal or PaymentTerminal(id=stable(company, "terminal", item.key), store_id=store_id)
        db.add(terminal)
        terminal.name, terminal.identifier = item.name.strip(), item.identifier.strip()
        terminal.account_id, terminal.register_id, terminal.provider = account.id, register.id if register else None, item.provider.strip()
        terminal.settlement_days, terminal.settlement_mode = item.settlement_days, item.settlement_mode
        terminal.commission_bps = amount(item.commission_percent) if item.commission_percent and item.commission_percent.strip() else None
        terminal.commission_cents = amount(item.commission_fixed) if item.commission_fixed and item.commission_fixed.strip() else None
        terminal.statement_reference, terminal.mbway, terminal.active = item.statement_reference.strip(), item.mbway, True
        item.id = terminal.id
    terminal_ids = {t.id for t in data.terminals}
    for existing in db.scalars(select(PaymentTerminal).where(PaymentTerminal.active.is_(True), PaymentTerminal.id.notin_(terminal_ids))):
        existing.active = False
    disabled_base = []
    for item in data.payments:
        method_id = item.id or (item.key if item.key in BASE_METHODS else "pm_" + stable(company, "method", item.key).replace("-", "")[:16])
        method = db.get(LocalPaymentMethod, method_id)
        if item.id and item.id not in BASE_METHODS and not method:
            raise HTTPException(422, "Escolha um meio de pagamento desta empresa.")
        if not item.selected:
            if method:
                method.active = False
            elif method_id in BASE_METHODS:
                disabled_base.append(method_id)
            continue
        method = method or LocalPaymentMethod(id=method_id, name=item.name.strip())
        db.add(method)
        method.name, method.active = item.name.strip(), True
        method.settings = json.dumps({"cash": item.cash, "bank_receivable": item.bank_receivable, "receipts": item.receipts, "payments": item.payments,
                                      "erp_id": item.erp_id, "mbway_via": item.mbway_via})
        db.flush()
        destinations = set()
        if item.account:
            account = accounts.get(item.account) or db.get(BankAccount, item.account)
            if not account or account.store_id != primary.id or item.cash:
                raise HTTPException(422, "Escolha uma conta de destino do estabelecimento principal para o meio de pagamento.")
            destinations.add(account.id)
        if method_id == "cartao" or method_id == "mbway" and item.mbway_via == "terminal":
            destinations.update(t.account_id for t in db.scalars(select(PaymentTerminal).where(PaymentTerminal.store_id == primary.id, PaymentTerminal.active.is_(True), *([PaymentTerminal.mbway.is_(True)] if method_id == "mbway" else []))))
        previous = db.get(PaymentMethodBankDefault, (method_id, primary.id))
        if len(destinations) == 1:
            if not previous:
                previous = PaymentMethodBankDefault(method_id=method_id, store_id=primary.id)
                db.add(previous)
            previous.account_id = next(iter(destinations))
        elif previous:
            db.delete(previous)
        if item.erp_id:
            remote = db.get(WintouchPaymentMethod, item.erp_id)
            if not remote or remote.enterprise_id != data.mode.enterprise:
                raise HTTPException(422, "O meio ERP não pertence à ligação desta empresa.")
            if remote.local_payment and remote.local_payment != method_id:
                raise HTTPException(409, "O meio ERP já tem outra associação.")
            remote.local_payment = method_id
        item.id = method_id
    for name in data.preferences.categories:
        clean = name.strip()
        if not clean:
            continue
        if len(clean) < 2 or len(clean) > 100:
            raise HTTPException(422, "Use nomes de categorias com 2 a 100 caracteres.")
        if not db.scalar(select(Category.id).where(func.lower(Category.name) == clean.lower())):
            db.add(Category(name=clean))
    profile = data.company.model_dump()
    profile["store_id"] = primary.id
    row.settings = json.dumps({"company": profile, "preferences": data.preferences.model_dump(), "disabled_base_methods": disabled_base})
    active = data.mode.mode == "integrated" and not data.mode.defer and tested(row, raw, company)
    values = {"name": data.company.name.strip(), "nif": data.company.nif, "operating_mode": data.mode.mode,
              "api_active": int(active), "integration_revision": company["integration_revision"] + 1,
              "connector_writes": company["connector_writes"] if data.mode.mode == company["operating_mode"] and data.mode.enterprise == company["api_enterprise"] else 0,
              "sync_interval_minutes": company["sync_interval_minutes"] if active and data.mode.mode == company["operating_mode"] and data.mode.enterprise == company["api_enterprise"] else 0}
    if data.mode.mode == "integrated":
        values.update(erp_provider=data.mode.provider, api_url=validate_url(data.mode.api_url), api_enterprise=data.mode.enterprise.strip(),
                      api_key_ciphertext=credentials(raw, company), sync_resources=json.dumps(data.mode.resources))
    db.execute(update(table).where(table.c.id == company["id"]).values(**values))
    normalized = data.model_dump(mode="json")
    normalized["mode"]["api_key_ciphertext"] = raw["mode"].get("api_key_ciphertext", "")
    for account in normalized["accounts"]:
        account["iban_ciphertext"] = db.get(BankAccount, account["id"]).iban_ciphertext
    for step, section in (("banks", "accounts"), ("terminals", "terminals")):
        if step in data.skipped:
            applied = {entry["key"] for entry in normalized[section]}
            normalized[section].extend(entry for entry in raw[section] if entry["key"] not in applied)
    if "preferences" in data.skipped:
        normalized["preferences"] = raw["preferences"]
    row.draft = json.dumps(normalized)
    db.flush()


def fetch_catalogs(raw, company, resources):
    config = raw["mode"]
    try:
        key = cipher().decrypt(credentials(raw, company).encode()).decode()
        url = validate_url(config["api_url"])
        result, deadline = {}, monotonic() + 90
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            for resource in resources:
                rows, seen, reported = [], set(), None
                while True:
                    if monotonic() > deadline or len(rows) >= 5000:
                        raise HTTPException(422, "A pré-visualização atingiu o limite. Importe este catálogo pela área Integrações.")
                    response = client.get(url + "/" + resource, params={"limit": 200, "skip": len(rows)}, headers={"Authorization": "ApiKey " + key, "Accept": "application/json", "x-current-enterprise": config["enterprise"]})
                    if response.status_code != 200:
                        raise ValueError()
                    payload = response.json()
                    page = payload
                    if isinstance(payload, dict):
                        if payload.get("success") is False or payload.get("Success") is False:
                            raise ValueError()
                        total = next((payload[k] for k in ("total", "Total", "totalCount", "TotalCount") if k in payload), None)
                        if total is not None:
                            if type(total) is not int or total < 0 or reported is not None and reported != total:
                                raise ValueError()
                            reported = total
                        page = next((payload[k] for k in ("items", "Items", "data", "Data") if isinstance(payload.get(k), list)), None)
                    if not isinstance(page, list) or len(page) > 200:
                        raise ValueError()
                    if not page:
                        if reported is not None and len(rows) != reported:
                            raise ValueError()
                        break
                    for source in page:
                        if not isinstance(source, dict):
                            raise ValueError()
                        item = {"external_id": string(source.get("ID"), 200, True), "name": string(source.get("Name"), 200, True)} if resource == "payment_methods" else normalize(resource, source)
                        if item["external_id"] in seen:
                            raise ValueError()
                        seen.add(item["external_id"])
                        rows.append(item)
                result[resource] = rows
        return result
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, SyncError, InvalidToken):
        raise HTTPException(422, "Não foi possível validar a resposta do ERP. Confira a ligação e as permissões; nenhum catálogo foi importado.") from None


def register_routes(app, current, authorize, record, serial, scope):
    @app.get("/api/setup/status")
    def status(user=Depends(current), db: Session = Depends(db_session)):
        config = settings(db)
        preferences = config.get("preferences", {})
        unclosed = 0
        if preferences.get("daily_close", True) and preferences.get("unclosed_warning", True):
            selected_day = now().astimezone(ZoneInfo(config.get("company", {}).get("timezone", "Europe/Lisbon"))).date().isoformat()
            unclosed = db.scalar(select(func.count()).select_from(CashSession).join(Register).where(Register.store_id.in_(scope(user, db)), CashSession.closed_at.is_(None), CashSession.sheet_day < selected_day))
        return {**overview(db), "can_edit": user.role == "administrador", "unclosed_count": unclosed}

    @app.get("/api/setup")
    def get_setup(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        return view(db, central(db)[1], serial)

    @app.post("/api/setup/draft")
    def save_draft(data: DraftSave, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        _, company = central(db)
        row = state_row(db, data.revision)
        previous = unpack(row.draft)
        raw = data.data.model_dump(mode="json")
        supplied = raw["mode"].pop("api_key", "")
        raw["mode"]["api_key_ciphertext"] = cipher().encrypt(supplied.encode()).decode() if supplied else previous.get("mode", {}).get("api_key_ciphertext", "")
        for account in raw["accounts"]:
            clean = iban(account.pop("iban", ""))
            prior = next((a for a in previous.get("accounts", []) if a["key"] == account["key"]), {})
            account["iban_ciphertext"] = cipher().encrypt(clean.encode()).decode() if clean else prior.get("iban_ciphertext", "")
        row.draft, row.step = json.dumps(raw), data.step
        touch(row, user)
        record(db, user, "configuracao_inicial_rascunho", row.id, {"step": row.step, "revision": row.revision})
        db.commit()
        return view(db, company, serial)

    @app.post("/api/setup/preview")
    def preview(data: Revision, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        row = db.get(CompanySetup, "default")
        if not row or row.revision != data.revision:
            raise HTTPException(409, "Guarde a configuração e atualize o resumo antes de concluir.")
        return preview_result(db, unpack(row.draft), row, central(db)[1])

    @app.post("/api/setup/finish")
    def finish(data: Finish, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        table, company = central(db, lock=True)
        row = db.get(CompanySetup, "default")
        if row and unpack(row.settings).get("last_finish_token") == data.token:
            return view(db, company, serial)
        row = state_row(db, data.revision)
        raw = unpack(row.draft)
        summary = preview_result(db, raw, row, company)
        if summary["token"] != data.token:
            raise HTTPException(409, "A configuração ou a ligação mudou. Atualize o resumo antes de confirmar.")
        try:
            apply_configuration(db, validate_final(db, raw, company, row), raw, row, company, table, user, record)
            row.settings = json.dumps({**unpack(row.settings), "last_finish_token": data.token})
            row.completed_at, row.completed_by, row.step = now(), user.id, "summary"
            touch(row, user)
            record(db, user, "configuracao_inicial_concluida", row.id, {"revision": row.revision, "mode": summary["mode"], "registers": len(summary["registers"]), "accounts": len(summary["accounts"]), "terminals": len(summary["terminals"]), "pending": summary["pending"]})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Já existe um registo com estes dados. Reveja as associações; o rascunho foi preservado.") from None
        return view(db, central(db)[1], serial)

    @app.post("/api/setup/erp/test")
    def test(data: Revision, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        _, company = central(db)
        row = state_row(db, data.revision)
        raw = unpack(row.draft)
        if raw["mode"]["mode"] != "integrated" or raw["mode"]["provider"] != "wintouch" or not raw["mode"]["enterprise"].strip() or not credentials(raw, company):
            raise HTTPException(422, "Escolha o conector, a empresa ERP e a chave para testar a ligação.")
        success = False
        try:
            key = cipher().decrypt(credentials(raw, company).encode()).decode()
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.get(validate_url(raw["mode"]["api_url"]) + "/products", params={"limit": 1, "skip": 0}, headers={"Authorization": "ApiKey " + key, "Accept": "application/json", "x-current-enterprise": raw["mode"]["enterprise"]})
                payload = response.json() if response.status_code == 200 else None
                success = isinstance(payload, list) or isinstance(payload, dict) and payload.get("success") is not False and payload.get("Success") is not False and any(isinstance(payload.get(k), list) for k in ("items", "Items", "data", "Data"))
        except (httpx.HTTPError, ValueError, InvalidToken):
            pass
        row.erp_test = json.dumps({"success": success, "fingerprint": connection_fingerprint(raw, company), "checked_at": now().isoformat()})
        touch(row, user)
        record(db, user, "configuracao_inicial_teste_erp", row.id, {"success": success, "provider": raw["mode"]["provider"]})
        db.commit()
        return {**view(db, company, serial), "test_message": "Ligação testada com sucesso. Nenhum dado foi importado." if success else "Ligação pendente: não foi possível confirmar a resposta do ERP."}

    @app.post("/api/setup/erp/preview")
    def erp_preview(data: ImportPreview, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        _, company = central(db)
        row = state_row(db, data.revision)
        raw = unpack(row.draft)
        if raw["mode"]["mode"] != "integrated" or not tested(row, raw, company):
            raise HTTPException(409, "Teste esta ligação antes de pré-visualizar uma importação.")
        if company["mode"] == "demonstracao" and not data.confirm_demo:
            raise HTTPException(422, "Confirme a leitura de dados reais do ERP nesta empresa de demonstração.")
        catalogs = fetch_catalogs(raw, company, list(dict.fromkeys(data.resources)))
        info = {"catalogs": catalogs, "fingerprint": connection_fingerprint(raw, company), "created_at": now().isoformat()}
        info["token"] = digest(info)
        row.erp_preview = json.dumps(info, default=str)
        touch(row, user)
        record(db, user, "configuracao_inicial_previsualizacao_erp", row.id, {"resources": data.resources, "counts": {key: len(rows) for key, rows in catalogs.items()}})
        db.commit()
        return {"revision": row.revision, "token": info["token"], "resources": {key: {"count": len(rows), "examples": [{"id": r["external_id"], "name": r["name"]} for r in rows[:10]]} for key, rows in catalogs.items()}}

    @app.post("/api/setup/erp/import")
    def erp_import(data: Finish, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        _, company = central(db, lock=True)
        current_row = db.get(CompanySetup, "default")
        if current_row and unpack(current_row.erp_preview).get("token") == data.token and unpack(current_row.erp_preview).get("imported"):
            return view(db, company, serial)
        row = state_row(db, data.revision)
        snapshot = unpack(row.erp_preview)
        raw = unpack(row.draft)
        if not snapshot or snapshot.get("token") != data.token or snapshot["fingerprint"] != connection_fingerprint(raw, company) or not tested(row, raw, company) or datetime.fromisoformat(snapshot["created_at"]) < now() - timedelta(minutes=10):
            raise HTTPException(409, "A pré-visualização expirou ou a ligação mudou. Atualize-a antes de importar.")
        for resource, rows in snapshot["catalogs"].items():
            model = SETUP_CATALOGS[resource]
            for item in rows:
                values = dict(item)
                for key in ("source_updated_at",):
                    if values.get(key):
                        values[key] = datetime.fromisoformat(values[key])
                if "last_cost" in values and values["last_cost"] is not None:
                    values["last_cost"] = Decimal(values["last_cost"])
                native = db.scalar(select(model).where(model.enterprise_id == raw["mode"]["enterprise"], model.external_id == item["external_id"]))
                native = native or model(enterprise_id=raw["mode"]["enterprise"], external_id=item["external_id"])
                db.add(native)
                for key, value in values.items():
                    setattr(native, key, value)
                native.synced_at = now()
        snapshot["imported"] = True
        row.erp_preview = json.dumps(snapshot)
        touch(row, user)
        record(db, user, "configuracao_inicial_importacao_erp", row.id, {"counts": {key: len(rows) for key, rows in snapshot["catalogs"].items()}})
        db.commit()
        return view(db, company, serial)
