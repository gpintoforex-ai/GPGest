"""Última venda de cada cliente e indicador de atividade comercial (tabela de Clientes).

A data é a do documento de venda válido mais recente, em todo o histórico disponível (não o período do Dashboard):
faturas, faturas simplificadas, faturas-recibo e vendas a dinheiro, com estado normal. Ficam de fora anulados,
notas de crédito (não substituem a última venda), notas de débito, orçamentos, encomendas, guias e recibos. Nunca se
usa a data de pagamento nem a de importação/sincronização.

Origem: a mesma da análise comercial (`dashboard.sales_source`): SAF-T de vendas no modo local, vendas Wintouch no
integrado. Uma consulta agregada por origem (DISTINCT ON), com cache invalidada quando muda a sincronização ou a
importação; a ligação ao cliente faz-se pelo NIF (ou, sem NIF, pelo nome).
"""

import json
import time as clock
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import Boolean, Integer, cast, false, func, select
from sqlalchemy.dialects.postgresql import JSONB, distinct_on
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .models import CompanySetup, WintouchSaleRecord
from .schemas import Strict

router = APIRouter()
LISBON = ZoneInfo("Europe/Lisbon")
SALE_TYPES = ("FT", "FS", "FR", "VD")
DEFAULT_DAYS = [30, 90, 180]
CACHE: dict = {}
CACHE_SECONDS = 300
STATES = {"ativo": "Ativo", "acompanhar": "A acompanhar", "risco": "Em risco", "inativo": "Inativo", "sem_historico": "Sem histórico"}


def today():
    return datetime.now(LISBON).date()


def fold(value):
    from .rubric_nature import fold as base
    return base(value or "")


def nif_of(value):
    return str(value or "").upper().removeprefix("PT").replace(" ", "").strip()


def settings(db):
    row = db.get(CompanySetup, "default")
    values = json.loads(row.settings or "{}") if row else {}
    # Revisão própria: a revisão de CompanySetup pertence ao assistente de configuração.
    return {"days": [int(d) for d in values.get("customer_activity_days") or DEFAULT_DAYS], "revision": int(values.get("customer_activity_revision") or 0)}


def latest(db, stores):
    """{"nif": {...}, "name": {...}, "meta": {...}} com a última venda válida de cada cliente (cache por empresa)."""
    from .billing_conference import saft_source
    from .dashboard import sales_source
    mode = sales_source(db)[0]
    company = str(db.info.get("company", {}).get("id") or db.info.get("company", {}).get("nif") or "")
    version = (db.scalar(select(func.count(WintouchSaleRecord.id))), str(db.scalar(select(func.max(WintouchSaleRecord.synced_at)))))
    key = (company, mode, tuple(sorted(stores)))
    cached = CACHE.get(key)
    if cached and cached[0] == version and clock.monotonic() - cached[1] < CACHE_SECONDS:
        return cached[2]
    # Consultas pelo modelo (nunca SQL em texto): o mapa de esquemas da empresa só se aplica às tabelas do modelo.
    record = WintouchSaleRecord
    data = cast(record.data, JSONB)

    def field(name):
        return data[name].astext

    def newest(columns, valid, key):
        """Venda mais recente por chave (DISTINCT ON sobre uma subconsulta, para a ordenação usar as mesmas colunas)."""
        inner = select(key.label("k"), record.id.label("id"), record.store_id.label("store_id"), record.document_date.label("day"),
                       field("document").label("document"), field("type").label("typ"), *columns).where(*valid).subquery()
        return db.execute(select(inner).ext(distinct_on(inner.c.k)).order_by(inner.c.k, inner.c.day.desc(), func.length(inner.c.document).desc(),
                                                                       inner.c.document.desc()))

    by_nif, by_name, bounds = {}, {}, (None, None)
    if stores and mode == "saft" and (source := saft_source(db)):
        raw = data["raw"]
        customers = {code: (nif_of(nif), name) for code, nif, name in db.execute(
            select(raw["CustomerID"].astext, raw["CustomerTaxID"].astext, raw["CompanyName"].astext)
            .where(record.enterprise_id == source, record.kind == "saft_customer", record.active.is_(True)))}
        valid = (record.enterprise_id == source, record.kind == "saft_invoice", record.active.is_(True), record.store_id.in_(stores),
                 field("status") == "N", field("type").in_(SALE_TYPES))
        for row in newest([], valid, field("customer_id")):
            nif, name = customers.get(row.k, ("", row.k))
            add(by_nif, by_name, nif, name, {"date": row.day, "id": row.id, "store_id": row.store_id, "document": row.document, "type": row.typ,
                                             "customer_code": row.k})
        bounds = db.execute(select(func.min(record.document_date), func.max(record.document_date)).where(*valid)).one()
    elif stores and mode == "erp":
        valid = (record.kind == "documento", record.active.is_(True), record.store_id.in_(stores), field("document_group") == "vendas",
                 func.coalesce(field("cancelled").cast(Boolean), false()).is_(false()), func.coalesce(field("direction").cast(Integer), 1) > 0)
        key = func.coalesce(func.nullif(field("entity_nif"), ""), func.concat("nome:", func.lower(field("entity"))))
        for row in newest([field("entity_nif").label("nif"), field("entity").label("name")], valid, key):
            add(by_nif, by_name, nif_of(row.nif), row.name, {"date": row.day, "id": row.id, "store_id": row.store_id, "document": row.document,
                                                             "type": row.typ, "customer_code": ""})
        bounds = db.execute(select(func.min(record.document_date), func.max(record.document_date)).where(*valid)).one()
    value = {"nif": by_nif, "name": by_name, "meta": {"mode": mode or "none", "data_from": bounds[0], "data_until": bounds[1]}}
    if len(CACHE) > 32:
        CACHE.clear()
    CACHE[key] = (version, clock.monotonic(), value)
    return value


def add(by_nif, by_name, nif, name, sale):
    """Guarda a venda mais recente por NIF e por nome (o mesmo cliente pode ter vários códigos no SAF-T)."""
    def newer(current):
        return not current or (sale["date"], len(sale["document"] or ""), sale["document"] or "") > (current["date"], len(current["document"] or ""), current["document"] or "")
    if nif and newer(by_nif.get(nif)):
        by_nif[nif] = sale
    if name and newer(by_name.get(fold(name))):
        by_name[fold(name)] = sale


def classify(sale, days, meta, reference=None):
    """Estado de atividade, com o texto da dica. Dados antigos ou curtos não geram inatividade enganadora."""
    reference = reference or today()
    data_from, data_until = meta.get("data_from"), meta.get("data_until")
    stale = bool(data_until) and date.fromisoformat(data_until) < reference - timedelta(days=7)
    coverage = f"vendas disponíveis de {short(data_from)} a {short(data_until)}" if data_from else "sem vendas importadas ou sincronizadas"
    if not sale:
        partial = not data_from or date.fromisoformat(data_from) > reference - timedelta(days=days[-1])
        return {"state": "sem_historico", "label": STATES["sem_historico"], "days": None,
                "hint": f"Sem vendas nos dados disponíveis ({coverage})." + (" O histórico pode estar incompleto." if partial else "")}
    elapsed = (reference - date.fromisoformat(sale["date"])).days
    state = "ativo" if elapsed <= days[0] else "acompanhar" if elapsed <= days[1] else "risco" if elapsed <= days[2] else "inativo"
    hint = f"Última venda há {elapsed} dia(s): {sale['type']} {sale['document']}. Limites: {days[0]}/{days[1]}/{days[2]} dias."
    if stale:
        hint += f" Atenção: {coverage}; vendas posteriores ainda não estão na IAZELA."
    return {"state": state, "label": STATES[state], "days": elapsed, "hint": hint, "stale": stale}


def short(value):
    return "/".join(reversed(str(value).split("-"))) if value else "—"


def for_entities(ctx, rows):
    """{id da linha: última venda e atividade} para as linhas da tabela de Clientes (uma consulta agregada)."""
    stores = scope(ctx.user, ctx.db)
    data = latest(ctx.db, stores)
    days = settings(ctx.db)["days"]
    result = {}
    for row in rows:
        raw = row.get("raw") or row
        nif = nif_of(raw.get("tax_number"))
        sale = data["nif"].get(nif) if nif else data["name"].get(fold(raw.get("name")))
        result[row["id"]] = {**(sale or {}), "has_sale": bool(sale), **classify(sale, days, data["meta"])}
    return result, data["meta"], days


# Filtros rápidos: «sem vendas há mais de N dias» inclui quem nunca comprou.
FILTERS = {"recent30": lambda a: a["has_sale"] and a["days"] <= 30, "over90": lambda a: not a["has_sale"] or a["days"] > 90,
           "over180": lambda a: not a["has_sale"] or a["days"] > 180, "over365": lambda a: not a["has_sale"] or a["days"] > 365,
           "never": lambda a: not a["has_sale"]}


def filter_rows(rows, params):
    """Filtro rápido (`activity`) e intervalo da última venda (`sale_from`, `sale_to`) sobre as linhas da tabela."""
    wanted, first, last = params.get("activity") or "", params.get("sale_from") or "", params.get("sale_to") or ""
    if wanted in FILTERS:
        rows = [r for r in rows if r.get("extra") and FILTERS[wanted](r["extra"]["last_sale"])]
    if first or last:
        rows = [r for r in rows if r.get("extra") and r["extra"]["last_sale"].get("date")
                and (not first or r["extra"]["last_sale"]["date"] >= first) and (not last or r["extra"]["last_sale"]["date"] <= last)]
    return rows


class ActivityInput(Strict):
    days: list[int] = Field(min_length=3, max_length=3)
    revision: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if not 1 <= self.days[0] < self.days[1] < self.days[2] <= 3650:
            raise ValueError("Indique três limites crescentes, em dias (por exemplo 30, 90 e 180).")
        return self


@router.get("/api/customers/activity-settings")
def get_settings(user=Depends(current), db: Session = Depends(db_session)):
    return {**settings(db), "can_edit": user.role == "administrador"}


@router.put("/api/customers/activity-settings")
def put_settings(data: ActivityInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    row = db.get(CompanySetup, "default", with_for_update=True)
    if not row:
        row = CompanySetup(id="default")
        db.add(row)
        db.flush()
    values = json.loads(row.settings or "{}")
    if int(values.get("customer_activity_revision") or 0) != data.revision:
        raise HTTPException(409, "Os limites foram alterados entretanto. Recarregue e tente de novo.")
    before = values.get("customer_activity_days") or DEFAULT_DAYS
    values["customer_activity_days"] = data.days
    values["customer_activity_revision"] = data.revision + 1
    row.settings = json.dumps(values, ensure_ascii=False)
    record(db, user, "atividade_clientes_limites", "default", {"antes": before, "depois": data.days})
    db.commit()
    return {**settings(db), "can_edit": True}
