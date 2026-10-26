"""Selective catalog reset. Referenced records survive; original schema is archived."""
import hashlib
import json
import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Base, BusinessEntity, IntegrationBinding, LoginSession, StockArticle, WintouchEntity, WintouchProduct

LABELS = {"articles": "Artigos", "customers": "Clientes", "suppliers": "Fornecedores"}
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def tokens(value):
    if isinstance(value, str):
        yield value.strip().casefold()
        yield from (v.casefold() for v in UUID.findall(value))
        try:
            nested = json.loads(value)
        except (ValueError, TypeError):
            return
        if not isinstance(nested, str):
            yield from tokens(nested)
    elif isinstance(value, dict):
        for item in value.values():
            yield from tokens(item)
    elif isinstance(value, list):
        for item in value:
            yield from tokens(item)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)


def plan_catalog_reset(db, scope):
    """Protect SQL references and text/JSON references used by existing ledgers.

    Names/codes are intentionally conservative: ambiguous historical references
    protect all matching cards rather than silently break a document.
    """
    excluded = {"audit", "integration_bindings", "login_sessions"}
    excluded.update({"stock_articles", "wt_products"} if scope == "articles" else {"business_entities", "wt_entities"})
    referenced = set()
    for table in Base.metadata.sorted_tables:
        if table.name in excluded or "audit" in table.name or "sync" in table.name:
            continue
        columns = [c for c in table.columns if getattr(c.type, "python_type", None) is str]
        if columns:
            for row in db.execute(select(*columns)):
                for value in row:
                    referenced.update(tokens(value))
    actions, protected, snapshots = [], [], []
    if scope == "articles":
        mirrors = {r.id: r for r in db.scalars(select(WintouchProduct))}
        cards = list(db.scalars(select(StockArticle).order_by(StockArticle.id)))
        records = [(r, mirrors.pop(r.erp_id, None)) for r in cards]
        records.extend((None, r) for r in mirrors.values())
    else:
        mirrors = {r.id: r for r in db.scalars(select(WintouchEntity))}
        cards = list(db.scalars(select(BusinessEntity).order_by(BusinessEntity.id)))
        records = [(r, mirrors.pop(r.wintouch_id, None)) for r in cards]
        records.extend((None, r) for r in mirrors.values())
    role = "is_customer" if scope == "customers" else "is_supplier"
    other_role = "is_supplier" if scope == "customers" else "is_customer"
    for local, remote in records:
        effective = (local or remote) if scope == "articles" else local if not remote or local and local.flags_local else remote
        if scope != "articles" and (local and local.removed or not getattr(effective, role)):
            continue
        identifiers = {v for row in (local, remote) if row for v in [row.id, getattr(row, "external_id", ""), getattr(row, "name", ""), getattr(row, "code", ""), getattr(row, "tax_number", "")] if v}
        row = local or remote
        item = {"id": row.id, "name": row.name, "local_id": local.id if local else None, "erp_id": remote.id if remote else None}
        snapshots.extend({c.name: getattr(record, c.name) for c in record.__table__.columns} for record in (local, remote) if record)
        if any(v.strip().casefold() in referenced for v in identifiers):
            protected.append(item)
        else:
            item["keep_other_role"] = scope != "articles" and bool(getattr(effective, other_role))
            actions.append(item)
    payload = json.dumps({"scope": scope, "records": snapshots, "actions": actions, "protected": protected}, sort_keys=True, default=str)
    return {"scope": scope, "label": LABELS[scope], "total": len(actions) + len(protected), "removable": len(actions), "protected": len(protected),
            "shared": sum(bool(a["keep_other_role"]) for a in actions), "token": hashlib.sha256(payload.encode()).hexdigest(), "actions": actions,
            "protected_examples": [{"id": r["id"], "name": r["name"]} for r in protected[:10]]}


def copy_and_reset_catalog(connection, original, replacement, plan):
    """Full FK-ordered copy, then change only the selected catalog in that copy."""
    for table in Base.metadata.sorted_tables:
        if table.name == LoginSession.__tablename__:
            continue
        columns = ", ".join('"' + c.name + '"' for c in table.columns)
        connection.execute(text(f'INSERT INTO "{replacement}"."{table.name}" ({columns}) SELECT {columns} FROM "{original}"."{table.name}"'))
    with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
        scope = plan["scope"]
        for item in plan["actions"]:
            local_model, remote_model = (StockArticle, WintouchProduct) if scope == "articles" else (BusinessEntity, WintouchEntity)
            local = db.get(local_model, item["local_id"]) if item["local_id"] else None
            remote = db.get(remote_model, item["erp_id"]) if item["erp_id"] else None
            if item["keep_other_role"]:
                role = "is_customer" if scope == "customers" else "is_supplier"
                for record in (local, remote):
                    if record:
                        setattr(record, role, False)
            else:
                ids = {item["local_id"], item["erp_id"]} - {None}
                internal_kinds = ["article", "stock_article", "product"] if scope == "articles" else ["entity"]
                external_kinds = ["article", "product", "products"] if scope == "articles" else ["entity", "entities"]
                for binding in db.scalars(select(IntegrationBinding).where(
                    (IntegrationBinding.internal_id.in_(ids) & IntegrationBinding.internal_kind.in_(internal_kinds))
                    | (IntegrationBinding.external_id.in_(ids) & IntegrationBinding.external_kind.in_(external_kinds))
                )):
                    db.delete(binding)
                if local:
                    db.delete(local)
                    db.flush()
                if remote:
                    db.delete(remote)
        db.flush()
        db.commit()
