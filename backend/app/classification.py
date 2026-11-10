"""Classificação inteligente de rubricas (financeira): sugestões, repartição validada e regras aprovadas.

- Sugestões, por ordem de confiança: regras aprovadas (mais específicas primeiro), histórico de
  classificações do mesmo fornecedor (documentos ERP, compras classificadas e compras com linhas) e a
  rubrica da ficha do fornecedor. Nada é aplicado sem confirmação; o lote só aceita sugestões que o
  utilizador selecionou, e o servidor volta a validar cada uma.
- A repartição soma sempre o total do documento (o valor classificável, como nas Despesas por rubrica);
  por percentagem, os cêntimos são distribuídos sem perdas. Base e IVA são repartidos na mesma proporção
  pelos mapas de gestão.
- Onde fica gravada: num documento ERP (ou compra ligada a um) em `erp_document_rubrics`; numa compra sem
  linhas nem ERP em `purchase_rubrics`. Compras com linhas classificam-se nas linhas, em Compras.
- Classificação financeira (rubricas de gestão). A dedução do IVA vem da % da rubrica e a classificação
  contabilística (SNC) continua a ser do contabilista ou do ERP: nada aqui a altera.
"""

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import ROUND_DOWN
from decimal import Decimal as D
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .invoice_evidence import nif_key
from .management_maps import purchase_documents
from .models import (
    BusinessEntity,
    Category,
    ClassificationRule,
    DocumentERPLink,
    ERPDocument,
    ERPDocumentRubric,
    ProcurementDocument,
    Purchase,
    PurchaseRubric,
    Store,
    now,
)
from .schemas import Strict

router = APIRouter()
ROLES = ["administrador", "gestor"]
SAFE = 85  # Confiança mínima para a sugestão poder ser aceite em lote.
HISTORY = 24  # Documentos do fornecedor considerados no histórico (os mais recentes).


def fail(message, status=422):
    raise HTTPException(status, message)


class ShareInput(Strict):
    category_id: str = Field(min_length=1, max_length=36)
    value: D = Field(gt=0, le=10**10, decimal_places=2)


class RuleCriteria(Strict):
    supplier: bool = True
    document_type: bool = False
    store: bool = False
    keyword: str = Field(default="", max_length=80)


class ApplyInput(Strict):
    kind: Literal["purchase", "erp"]
    id: str = Field(min_length=1, max_length=36)
    mode: Literal["amount", "percent"] = "amount"
    shares: list[ShareInput] = Field(min_length=1, max_length=20)
    rule_id: str | None = None
    save_rule: RuleCriteria | None = None
    rule_name: str = Field(default="", max_length=120)


class BatchInput(Strict):
    items: list[ApplyInput] = Field(min_length=1, max_length=100)


class RuleInput(Strict):
    revision: int = Field(default=0, ge=0)
    name: str = Field(min_length=2, max_length=120)
    supplier_nif: str = Field(default="", max_length=30)
    document_type: str = Field(default="", max_length=10)
    keyword: str = Field(default="", max_length=80)
    store_id: str | None = None
    shares: list[ShareInput] = Field(min_length=1, max_length=20)
    active: bool = True

    @model_validator(mode="after")
    def valid(self):
        if not (self.supplier_nif.strip() or self.document_type.strip() or self.keyword.strip() or self.store_id):
            raise ValueError("Indique pelo menos um critério (fornecedor, tipo de documento, palavra-chave ou estabelecimento).")
        if sum(s.value for s in self.shares) != 100:
            raise ValueError("As percentagens da regra têm de somar 100 %.")
        return self


# --- Documento a classificar -----------------------------------------------------------------------

def load(db, user, kind, identifier, lock=False):
    """(documento, total classificável, destino) com verificação de acesso e de onde se grava."""
    if kind == "erp":
        row = db.get(ERPDocument, identifier, with_for_update=lock)
        if not row:
            fail("Documento do ERP não encontrado.", 404)
        target = ("erp", row.id)
    else:
        row = db.get(Purchase, identifier, with_for_update=lock)
        if not row:
            fail("Documento não encontrado.", 404)
        lines = db.scalar(select(ProcurementDocument.id).where(
            ProcurementDocument.purchase_id == row.id, ProcurementDocument.kind.in_(("invoice", "credit")),
            ProcurementDocument.confirmed_at.is_not(None), ProcurementDocument.status != "cancelled").limit(1))
        if lines:
            target = ("lines", lines)
        else:
            link = db.scalar(select(DocumentERPLink.erp_id).where(DocumentERPLink.purchase_id == row.id, DocumentERPLink.undone_at.is_(None)))
            target = ("erp", link) if link else ("purchase", row.id)
    if row.store_id not in scope(user, db):
        fail("Estabelecimento sem acesso.", 403)
    return row, abs(row.amount_cents), target


def current_split(db, target):
    kind, identifier = target
    if kind == "erp":
        return [(r.category_id, r.amount_cents) for r in db.scalars(select(ERPDocumentRubric).where(ERPDocumentRubric.erp_document_id == identifier))]
    if kind == "purchase":
        return [(r.category_id, r.amount_cents) for r in db.scalars(select(PurchaseRubric).where(PurchaseRubric.purchase_id == identifier))]
    return []


def to_amounts(mode, shares, total):
    """[(rubrica, cêntimos)] somando exatamente o total; por percentagem, maiores restos primeiro."""
    merged = defaultdict(D)
    for share in shares:
        merged[share.category_id] += D(share.value)
    if mode == "amount":
        amounts = {category: int((value * 100).to_integral_value()) for category, value in merged.items()}
        if any(value <= 0 for value in amounts.values()):
            fail("Indique valores positivos em cada rubrica.")
        if sum(amounts.values()) != total:
            fail(f"A repartição ({sum(amounts.values()) / 100:.2f} €) tem de somar o total do documento ({total / 100:.2f} €).")
        return list(amounts.items())
    if sum(merged.values()) != 100:
        fail("As percentagens têm de somar 100 %.")
    exact = {category: D(total) * value / 100 for category, value in merged.items()}
    floors = {category: int(value.to_integral_value(rounding=ROUND_DOWN)) for category, value in exact.items()}
    left = total - sum(floors.values())
    for category in sorted(exact, key=lambda c: exact[c] - floors[c], reverse=True)[:left]:
        floors[category] += 1
    if any(value <= 0 for value in floors.values()):
        fail("Cada rubrica tem de ficar com um valor positivo.")
    return list(floors.items())


def percents(pairs):
    total = sum(amount for _, amount in pairs) or 1
    raw = [(category, (D(amount) * 100 / total).quantize(D("0.01"))) for category, amount in pairs]
    if raw:
        raw[-1] = (raw[-1][0], D(100) - sum(p for _, p in raw[:-1]))
    return raw


# --- Sugestões ---------------------------------------------------------------------------------------

class Suggester:
    """Lê uma vez por pedido as rubricas, as regras ativas, as entidades e o histórico de cada fornecedor."""

    def __init__(self, db):
        self.db = db
        self.names = {c.id: c.name for c in db.scalars(select(Category)) if c.active and not c.cash_difference}
        self.rules = list(db.scalars(select(ClassificationRule).where(ClassificationRule.active.is_(True))))
        self.entities = {}
        for entity in db.scalars(select(BusinessEntity).where(BusinessEntity.category_id.is_not(None), BusinessEntity.removed.is_(False))):
            if nif_key(entity.tax_number or ""):
                self.entities.setdefault(nif_key(entity.tax_number), set()).add(entity.category_id)
        self._lines = None
        self._history = {}

    def lines(self):
        """Compras com linhas (Compras): [(NIF, data, id da compra, repartição)]."""
        if self._lines is None:
            self._lines = []
            for row in self.db.scalars(select(ProcurementDocument).where(
                    ProcurementDocument.kind.in_(("invoice", "credit")), ProcurementDocument.confirmed_at.is_not(None),
                    ProcurementDocument.status != "cancelled").order_by(ProcurementDocument.created_at.desc()).limit(2000)):
                payload = json.loads(row.data)
                split = [(x.get("category_id"), int(x.get("amount_cents") or 0)) for x in payload.get("allocations") or [] if x.get("category_id")]
                if not split and payload.get("category_id"):
                    split = [(payload["category_id"], int(payload.get("total_cents") or 1))]
                if split:
                    nif = nif_key((payload.get("supplier_snapshot") or {}).get("nif", ""))
                    self._lines.append((nif, payload.get("document_date", ""), row.purchase_id, split))
        return self._lines

    def history(self, nif):
        """[(data, id, repartição)] das classificações confirmadas do fornecedor, recentes primeiro."""
        key = nif_key(nif or "")
        if not key:
            return []
        if key not in self._history:
            db, found = self.db, []
            erp_docs = list(db.scalars(select(ERPDocument).where(ERPDocument.supplier_nif == nif).order_by(ERPDocument.document_date.desc()).limit(200)))
            rubrics = defaultdict(list)
            for row in db.scalars(select(ERPDocumentRubric).where(ERPDocumentRubric.erp_document_id.in_([d.id for d in erp_docs]))) if erp_docs else []:
                rubrics[row.erp_document_id].append((row.category_id, row.amount_cents))
            found += [(d.document_date, d.id, rubrics[d.id]) for d in erp_docs if rubrics[d.id]]
            purchases = list(db.scalars(select(Purchase).where(Purchase.supplier_nif == nif).order_by(Purchase.document_date.desc()).limit(200)))
            own = defaultdict(list)
            for row in db.scalars(select(PurchaseRubric).where(PurchaseRubric.purchase_id.in_([x.id for x in purchases]))) if purchases else []:
                own[row.purchase_id].append((row.category_id, row.amount_cents))
            found += [(x.document_date, x.id, own[x.id]) for x in purchases if own[x.id]]
            found += [(day, identifier, split) for nif_line, day, identifier, split in self.lines() if nif_line == key]
            found.sort(key=lambda item: item[0], reverse=True)
            self._history[key] = found
        return self._history[key]

    def matching_rules(self, doc, text):
        rules = []
        for rule in self.rules:
            checks = [(rule.supplier_nif, nif_key(rule.supplier_nif or "") == nif_key(doc.supplier_nif or "")),
                      (rule.document_type, (rule.document_type or "").upper() == (doc.document_type or "").upper()),
                      (rule.keyword, (rule.keyword or "").lower() in text),
                      (rule.store_id, rule.store_id == doc.store_id)]
            used = [ok for criterion, ok in checks if criterion]
            if used and all(used):
                rules.append((len(used), rule))
        return sorted(rules, key=lambda item: (-item[0], -(item[1].hits or 0)))

    def suggest(self, doc, total, exclude=()):
        """[{key, source, confidence, shares: [{category_id, percent, amount_cents}], reasons, rule_id}] por confiança."""
        names = self.names
        text = " ".join(filter(None, [doc.supplier_name, getattr(doc, "notes", ""), doc.document, getattr(doc, "internal_document", "")])).lower()
        found = []

        def add(source, confidence, pairs, reasons, rule_id=None):
            pairs = [(c, D(str(p))) for c, p in pairs if c in names and D(str(p)) > 0]
            if not pairs or sum(p for _, p in pairs) != 100:
                return
            key = "|".join(f"{c}:{p}" for c, p in sorted(pairs))
            existing = next((x for x in found if x["key"] == key), None)
            if existing:
                existing["reasons"] += [r for r in reasons if r not in existing["reasons"]]
                existing["confidence"] = min(99, max(existing["confidence"], confidence) + 3)
                return
            try:
                by_category = dict(to_amounts("percent", [ShareInput(category_id=c, value=p) for c, p in pairs], total)) if total else {}
            except HTTPException:
                by_category = {}
            found.append({"key": key, "source": source, "confidence": confidence, "rule_id": rule_id, "reasons": reasons,
                          "shares": [{"category_id": c, "category_name": names[c], "percent": str(p), "amount_cents": by_category.get(c, 0)}
                                     for c, p in pairs]})

        for specificity, rule in self.matching_rules(doc, text):
            add("regra", min(98, 82 + 5 * specificity), [(x["category_id"], x["percent"]) for x in json.loads(rule.shares)],
                [f"Regra aprovada «{rule.name}»" + (f" (usada {rule.hits} vez(es))" if rule.hits else "")], rule.id)
        past = [split for _, identifier, split in self.history(doc.supplier_nif) if identifier not in exclude][:HISTORY]
        if past:
            patterns = Counter(tuple(sorted({c for c, _ in split})) for split in past)
            (categories, count), = patterns.most_common(1)
            totals = defaultdict(int)
            for split in past:
                if tuple(sorted({c for c, _ in split})) == categories:
                    amount = sum(a for _, a in split) or 1
                    for category, value in split:
                        totals[category] += value * 10000 // amount
            add("historico", int(45 + 45 * count / len(past)) if len(past) > 1 else 60, percents([(c, totals[c]) for c in categories]),
                [f"Histórico do fornecedor: {count} de {len(past)} documento(s) com esta classificação"])
        defaults = self.entities.get(nif_key(doc.supplier_nif or ""), set())
        if len(defaults) == 1:
            add("fornecedor", 65, [(next(iter(defaults)), 100)], ["Rubrica definida na ficha do fornecedor"])
        return sorted(found, key=lambda x: -x["confidence"])[:4]


def document_view(doc, kind, total, target, split, names, stores):
    return {"kind": kind, "id": doc.id, "store_id": doc.store_id, "store": stores.get(doc.store_id, ""), "supplier_nif": doc.supplier_nif,
            "supplier_name": doc.supplier_name, "document_type": doc.document_type, "document": doc.document or getattr(doc, "internal_document", ""),
            "document_date": doc.document_date, "amount_cents": doc.amount_cents, "total_cents": total, "base_cents": doc.base_cents,
            "tax_cents": doc.tax_cents, "target": target[0],
            "current": [{"category_id": c, "category_name": names.get(c, "—"), "amount_cents": a} for c, a in split]}


def active_categories(db):
    query = select(Category).where(Category.active.is_(True), Category.cash_difference.is_(False)).order_by(Category.name)
    return [{"id": c.id, "name": c.name} for c in db.scalars(query)]


# --- Rotas -----------------------------------------------------------------------------------------

@router.get("/api/classification/pending")
def pending(start: date | None = None, end: date | None = None, store_id: str = "", include_classified: bool = False,
            user=Depends(current), db: Session = Depends(db_session)):
    """Documentos do período sem classificação confirmada (rubrica do fornecedor ou por classificar), com sugestões."""
    from .operation_trace import Context, today
    authorize(user, db, roles=ROLES)
    end = end or today()
    start = start or end - timedelta(days=89)
    if start > end or (end - start).days > 400:
        fail("Selecione um período de até um ano.")
    stores = scope(user, db)
    if store_id:
        if store_id not in stores:
            fail("Estabelecimento sem acesso.", 403)
        stores = [store_id]
    shares = {identifier: split for _, identifier, split in purchase_documents(db, stores, start, end)}
    purchases = list(db.scalars(select(Purchase).where(Purchase.id.in_([k for k in shares if not k.startswith("erp:")]))))
    erp_docs = list(db.scalars(select(ERPDocument).where(ERPDocument.id.in_([k.removeprefix("erp:") for k in shares if k.startswith("erp:")]))))
    ctx = Context(db, purchases, erp_docs)
    from .rubric_expenses import erp_shares
    erp_source = {identifier: source for identifier, (_, source) in erp_shares(db, erp_docs, ctx.by_supplier).items()} if erp_docs else {}
    suggester = Suggester(db)
    names = {c.id: c.name for c in db.scalars(select(Category))}
    stores_names = {s.id: s.name for s in db.scalars(select(Store))}
    rows = []
    for kind, doc, source in [("purchase", p, ctx.rubric_source(p)) for p in purchases] + [("erp", d, erp_source.get(d.id, "")) for d in erp_docs]:
        key = doc.id if kind == "purchase" else "erp:" + doc.id
        unclassified = any(not c for c, _, _ in shares.get(key, []))
        if source in ("linhas", "documento") and not unclassified and not include_classified:
            continue
        _, total, target = load(db, user, kind, doc.id)
        state = "linhas" if target[0] == "lines" else "confirmada" if source in ("linhas", "documento") and not unclassified else "fornecedor" if source == "fornecedor" else "por_classificar"
        view = document_view(doc, kind, total, target, [(c, b) for c, b, _ in shares.get(key, [])], names, stores_names)
        found = [] if target[0] == "lines" else suggester.suggest(doc, total, exclude={doc.id})
        rows.append({**view, "state": state, "suggestions": found, "safe": bool(found and found[0]["confidence"] >= SAFE)})
    rows.sort(key=lambda r: r["document_date"], reverse=True)
    rows.sort(key=lambda r: {"por_classificar": 0, "fornecedor": 1, "linhas": 2, "confirmada": 3}[r["state"]])
    return {
        "start": start.isoformat(), "end": end.isoformat(), "rows": rows, "safe_threshold": SAFE,
        "summary": {"por_classificar": sum(r["state"] == "por_classificar" for r in rows), "fornecedor": sum(r["state"] == "fornecedor" for r in rows),
                    "seguras": sum(r["safe"] for r in rows), "regras": db.scalar(select(func.count()).select_from(ClassificationRule).where(ClassificationRule.active.is_(True)))},
        "categories": active_categories(db),
        "notes": ["Classificação financeira (rubricas de gestão). A dedução do IVA vem da % de cada rubrica; a classificação contabilística continua a ser do contabilista ou do ERP.",
                  "A repartição soma o total do documento; base e IVA são repartidos na mesma proporção nos mapas de gestão."],
    }


@router.get("/api/classification/suggest")
def suggest(kind: Literal["purchase", "erp"], id: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    doc, total, target = load(db, user, kind, id)
    names = {c.id: c.name for c in db.scalars(select(Category))}
    stores = {s.id: s.name for s in db.scalars(select(Store))}
    return {**document_view(doc, kind, total, target, current_split(db, target), names, stores),
            "suggestions": [] if target[0] == "lines" else Suggester(db).suggest(doc, total, exclude={doc.id}), "safe_threshold": SAFE,
            "categories": active_categories(db)}


def apply_one(db, user, data):
    authorize(user, db, roles=ROLES)
    doc, total, target = load(db, user, data.kind, data.id, lock=True)
    if target[0] == "lines":
        fail("Esta compra tem linhas lançadas: classifique as linhas em Compras.", 409)
    if not total:
        fail("Documento sem valor: não há nada a repartir.")
    categories = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_([s.category_id for s in data.shares])))}
    if any(s.category_id not in categories or not categories[s.category_id].active or categories[s.category_id].cash_difference for s in data.shares):
        fail("Rubrica inválida ou inativa.")
    amounts = to_amounts(data.mode, data.shares, total)
    before = current_split(db, target)
    if target[0] == "erp":
        db.execute(delete(ERPDocumentRubric).where(ERPDocumentRubric.erp_document_id == target[1]))
        for category, amount in amounts:
            db.add(ERPDocumentRubric(erp_document_id=target[1], category_id=category, amount_cents=amount))
    else:
        db.execute(delete(PurchaseRubric).where(PurchaseRubric.purchase_id == target[1]))
        for category, amount in amounts:
            db.add(PurchaseRubric(purchase_id=target[1], category_id=category, amount_cents=amount, created_by=user.id))
    rule = db.get(ClassificationRule, data.rule_id) if data.rule_id else None
    if rule:
        rule.hits, rule.last_used_at = (rule.hits or 0) + 1, now()
    record(db, user, "classificacao_rubricas", doc.id, {"documento": data.kind, "destino": target[0], "antes": before,
                                                        "depois": [{"category_id": c, "amount_cents": a} for c, a in amounts],
                                                        "regra": rule.id if rule else None}, doc.store_id)
    created = None
    if data.save_rule:
        criteria = data.save_rule
        values = {"supplier_nif": doc.supplier_nif if criteria.supplier else None, "document_type": doc.document_type if criteria.document_type else None,
                  "keyword": criteria.keyword.strip() or None, "store_id": doc.store_id if criteria.store else None}
        if not any(values.values()):
            fail("Indique pelo menos um critério para a regra.")
        created = ClassificationRule(name=data.rule_name.strip() or f"{doc.supplier_name}"[:120], approved_by=user.id,
                                     shares=json.dumps([{"category_id": c, "percent": str(p)} for c, p in percents(amounts)]), **values)
        db.add(created)
        db.flush()
        record(db, user, "regra_classificacao_criada", created.id, {"name": created.name, **values, "shares": json.loads(created.shares)}, doc.store_id)
    return doc, amounts, created


@router.post("/api/classification/apply")
def apply(data: ApplyInput, user=Depends(current), db: Session = Depends(db_session)):
    doc, amounts, rule = apply_one(db, user, data)
    db.commit()
    return {"ok": True, "shares": [{"category_id": c, "amount_cents": a} for c, a in amounts], "rule_id": rule.id if rule else None}


@router.post("/api/classification/batch")
def batch(data: BatchInput, user=Depends(current), db: Session = Depends(db_session)):
    """Aplica as sugestões selecionadas; tudo ou nada (uma falha não deixa metade aplicada)."""
    if any(item.save_rule for item in data.items):
        fail("No lote não se criam regras: guarde a regra a partir de um documento.")
    if len({(i.kind, i.id) for i in data.items}) != len(data.items):
        fail("Há documentos repetidos no lote.")
    for item in data.items:
        apply_one(db, user, item)
    db.commit()
    return {"ok": True, "applied": len(data.items)}


# --- Regras ----------------------------------------------------------------------------------------

def rule_view(rule, names, stores):
    return {"id": rule.id, "name": rule.name, "kind": rule.kind, "supplier_nif": rule.supplier_nif, "document_type": rule.document_type,
            "keyword": rule.keyword, "store_id": rule.store_id, "store": stores.get(rule.store_id, "") if rule.store_id else "",
            "shares": [{**s, "category_name": names.get(s["category_id"], "—")} for s in json.loads(rule.shares)], "active": rule.active,
            "hits": rule.hits, "last_used_at": rule.last_used_at.isoformat() if rule.last_used_at else None, "revision": rule.revision}


@router.get("/api/classification/rules")
def rules(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    names = {c.id: c.name for c in db.scalars(select(Category))}
    stores = {s.id: s.name for s in db.scalars(select(Store))}
    suppliers = {nif_key(e.tax_number): e.name for e in db.scalars(select(BusinessEntity).where(BusinessEntity.removed.is_(False))) if e.tax_number}
    return [{**rule_view(r, names, stores), "supplier_name": suppliers.get(nif_key(r.supplier_nif or ""), "")}
            for r in db.scalars(select(ClassificationRule).order_by(ClassificationRule.active.desc(), ClassificationRule.name))]


def rule_values(db, user, data):
    if data.store_id and data.store_id not in scope(user, db):
        fail("Estabelecimento sem acesso.", 403)
    categories = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_([s.category_id for s in data.shares])))}
    if any(s.category_id not in categories or not categories[s.category_id].active for s in data.shares):
        fail("Rubrica inválida ou inativa.")
    return {"name": data.name.strip(), "supplier_nif": data.supplier_nif.strip() or None, "document_type": data.document_type.strip().upper() or None,
            "keyword": data.keyword.strip() or None, "store_id": data.store_id or None, "active": data.active,
            "shares": json.dumps([{"category_id": s.category_id, "percent": str(s.value)} for s in data.shares])}


@router.post("/api/classification/rules")
def create_rule(data: RuleInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    values = rule_values(db, user, data)
    rule = ClassificationRule(approved_by=user.id, **values)
    db.add(rule)
    db.flush()
    record(db, user, "regra_classificacao_criada", rule.id, {**values, "shares": json.loads(values["shares"])})
    db.commit()
    return {"id": rule.id}


@router.put("/api/classification/rules/{identifier}")
def update_rule(identifier: str, data: RuleInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    rule = db.get(ClassificationRule, identifier, with_for_update=True)
    if not rule:
        fail("Regra inexistente.", 404)
    if rule.revision != data.revision:
        fail("A regra foi alterada entretanto. Atualize a página.", 409)
    values = rule_values(db, user, data)
    before = {k: getattr(rule, k) for k in values}
    for key, value in values.items():
        setattr(rule, key, value)
    rule.revision += 1
    record(db, user, "regra_classificacao_alterada", rule.id, {"antes": before, "depois": values})
    db.commit()
    return {"id": rule.id}
