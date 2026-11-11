"""Modelos de obrigações fiscais: propostas pré-preenchidas a partir do regime da empresa, sempre «a validar».

Nenhuma data fiscal é fixada como verdade: cada modelo indica a regra habitual e a fonte a confirmar (calendário
fiscal da AT, Segurança Social, contabilista). As obrigações criadas ficam com origem «regra legal» e por validar;
qualquer data concreta pode ser corrigida na ocorrência (prorrogações, dias não úteis). Nada é criado sem escolha
do utilizador; um modelo já criado não se duplica (`Obligation.data.template`).
"""

import calendar
import json
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import obligations, recurrence
from .access import authorize, current, record
from .db import db_session
from .models import Employee, Obligation, User
from .obligations import ROLES, fail, sync
from .schemas import Strict

router = APIRouter()
AT = "Calendário fiscal da AT (Portal das Finanças) e contabilista certificado"
SS = "Segurança Social Direta e contabilista certificado"
WORKDAY = " Se calhar em dia não útil, passa para o dia útil seguinte: confirme e reagende a ocorrência."


def monthly(day, offset):
    """Prazo do mês M+offset, no dia indicado (limitado ao fim do mês), mensalmente."""
    return {"periodicity": "mensal", "months": 1, "day": day, "offset": offset}


def quarterly(day, offset):
    """Prazo no dia indicado do `offset`-ésimo mês depois do fim de cada trimestre."""
    return {"periodicity": "trimestral", "months": 3, "day": day, "offset": offset}


def yearly(month, day):
    return {"periodicity": "anual", "month": month, "day": day}


TEMPLATES = [
    {"key": "iva_mensal_declaracao", "title": "IVA — declaração periódica (mensal)", "kind": "IVA — declaração periódica", "rule": monthly(20, 2),
     "text": "Até ao dia 20 do 2.º mês seguinte ao mês a que respeita.", "when": "iva_mensal"},
    {"key": "iva_mensal_pagamento", "title": "IVA — pagamento (mensal)", "kind": "IVA — pagamento", "rule": monthly(25, 2),
     "text": "Até ao dia 25 do 2.º mês seguinte ao mês a que respeita.", "when": "iva_mensal"},
    {"key": "iva_trimestral_declaracao", "title": "IVA — declaração periódica (trimestral)", "kind": "IVA — declaração periódica", "rule": quarterly(20, 2),
     "text": "Até ao dia 20 do 2.º mês seguinte ao trimestre a que respeita.", "when": "iva_trimestral"},
    {"key": "iva_trimestral_pagamento", "title": "IVA — pagamento (trimestral)", "kind": "IVA — pagamento", "rule": quarterly(25, 2),
     "text": "Até ao dia 25 do 2.º mês seguinte ao trimestre a que respeita.", "when": "iva_trimestral"},
    {"key": "iva_pequenos_retalhistas", "title": "IVA — regime dos pequenos retalhistas", "kind": "IVA — pagamento", "rule": quarterly(20, 1),
     "text": "Entrega trimestral do imposto apurado no regime especial (prazo a confirmar com o contabilista).", "when": "iva_retalhistas"},
    {"key": "efatura_comunicacao", "title": "Comunicação das faturas à AT (e-Fatura / SAF-T)", "kind": "Comunicação de faturas", "rule": monthly(5, 1),
     "text": "Até ao dia 5 do mês seguinte ao da emissão (dispensado se o programa comunica em tempo real).", "when": "always", "default": False},
    {"key": "retencoes_fonte", "title": "Retenções na fonte (IRS/IRC) — pagamento", "kind": "Retenções na fonte", "rule": monthly(20, 1),
     "text": "Até ao dia 20 do mês seguinte ao da retenção.", "when": "always", "default": False},
    {"key": "dmr", "title": "Declaração Mensal de Remunerações (AT)", "kind": "Declaração Mensal de Remunerações", "rule": monthly(10, 1),
     "text": "Até ao dia 10 do mês seguinte ao do pagamento das remunerações.", "when": "employees"},
    {"key": "ss_declaracao", "title": "Segurança Social — declaração de remunerações", "kind": "Segurança Social", "rule": monthly(10, 1),
     "text": "Até ao dia 10 do mês seguinte àquele a que respeitam.", "when": "employees", "source": SS},
    {"key": "ss_pagamento", "title": "Segurança Social — pagamento das contribuições", "kind": "Segurança Social", "rule": monthly(20, 1),
     "text": "Entre os dias 10 e 20 do mês seguinte àquele a que respeitam.", "when": "employees", "source": SS},
    {"key": "fundos_compensacao", "title": "Fundos de compensação do trabalho — pagamento", "kind": "Segurança Social", "rule": monthly(20, 1),
     "text": "Com as contribuições do mês seguinte, quando aplicável (regime em revisão: confirme se ainda se aplica).", "when": "employees", "default": False, "source": SS},
    {"key": "irc_modelo22", "title": "IRC — Modelo 22", "kind": "IRC — Modelo 22", "rule": yearly(5, 31),
     "text": "Até ao último dia útil do mês de maio.", "when": "always"},
    {"key": "irc_ppc_1", "title": "IRC — 1.º pagamento por conta", "kind": "IRC — pagamento por conta", "rule": yearly(7, 31),
     "text": "Até ao fim de julho (se houver pagamentos por conta a efetuar).", "when": "always"},
    {"key": "irc_ppc_2", "title": "IRC — 2.º pagamento por conta", "kind": "IRC — pagamento por conta", "rule": yearly(9, 30),
     "text": "Até ao fim de setembro (se houver pagamentos por conta a efetuar).", "when": "always"},
    {"key": "irc_ppc_3", "title": "IRC — 3.º pagamento por conta", "kind": "IRC — pagamento por conta", "rule": yearly(12, 15),
     "text": "Até 15 de dezembro (se houver pagamentos por conta a efetuar).", "when": "always"},
    {"key": "ies", "title": "IES — Informação Empresarial Simplificada", "kind": "IES", "rule": yearly(7, 15),
     "text": "Até 15 de julho.", "when": "always"},
    {"key": "inventario", "title": "Comunicação de inventários à AT", "kind": "Comunicação de inventários", "rule": yearly(1, 31),
     "text": "Até 31 de janeiro, com o inventário de 31 de dezembro (verifique a dispensa pelo volume de negócios).", "when": "always"},
    {"key": "modelo10", "title": "Declaração Modelo 10", "kind": "Modelo 10", "rule": yearly(2, 10),
     "text": "Até 10 de fevereiro, se pagou rendimentos sujeitos a declaração (por exemplo, a profissionais independentes).", "when": "always", "default": False},
    {"key": "imi", "title": "IMI — pagamento (1.ª ou única prestação)", "kind": "IMI", "rule": yearly(5, 31),
     "text": "Em maio; acima de 100 € há prestações também em agosto e/ou novembro. Só se a empresa tiver imóveis.", "when": "always", "default": False},
]


def first_due(rule, reference):
    """Próxima data-limite a partir de hoje (inclusive)."""
    if rule["periodicity"] == "anual":
        for year in (reference.year, reference.year + 1):
            due = date(year, rule["month"], min(rule["day"], calendar.monthrange(year, rule["month"])[1]))
            if due >= reference:
                return due
    step = rule["months"]
    # Fim do período mais recente (mês ou trimestre civil) e prazos dos períodos seguintes.
    anchor = date(reference.year, ((reference.month - 1) // step) * step + 1, 1)
    for index in range(-4, 6):
        period_start = recurrence.add_months(anchor, index * step)
        period_end_month = recurrence.add_months(period_start, step - 1)
        target = recurrence.add_months(period_end_month, rule["offset"])
        due = date(target.year, target.month, min(rule["day"], calendar.monthrange(target.year, target.month)[1]))
        if due >= reference:
            return due
    return reference


def context(db):
    from .settings import vat_settings
    vat = vat_settings(db)
    employees = db.scalar(select(func.count()).select_from(Employee).where(Employee.active.is_(True))) or 0
    return vat["vat_regime"], vat["vat_period"], employees


def applicability(template, regime, period, employees):
    when = template["when"]
    if when == "iva_mensal":
        return regime == "normal" and period == "mensal", "Regime normal com periodicidade mensal" if regime == "normal" and period == "mensal" else "Só no regime normal mensal"
    if when == "iva_trimestral":
        ok = regime == "normal" and period in ("trimestral", "")
        return ok, "Regime normal trimestral" if ok else "Só no regime normal trimestral"
    if when == "iva_retalhistas":
        return regime == "pequenos_retalhistas", "Regime dos pequenos retalhistas" if regime == "pequenos_retalhistas" else "Só no regime dos pequenos retalhistas"
    if when == "employees":
        return employees > 0, f"{employees} funcionário(s) ativo(s)" if employees else "Sem funcionários registados"
    return True, "Habitual em sociedades: confirme se se aplica"


def existing_templates(db):
    found = set()
    for row in db.scalars(select(Obligation).where(Obligation.category == "fiscal", Obligation.status == "ativa")):
        key = json.loads(row.data or "{}").get("template")
        if key:
            found.add(key)
    return found


@router.get("/api/obligations/fiscal-templates")
def templates(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    regime, period, employees = context(db)
    reference = obligations.today()
    created = existing_templates(db)
    items = []
    for template in TEMPLATES:
        applies, reason = applicability(template, regime, period, employees)
        rule = template["rule"]
        items.append({"key": template["key"], "title": template["title"], "kind": template["kind"], "rule": template["text"],
                      "periodicity": recurrence.describe(rule["periodicity"]), "first_due": first_due(rule, reference).isoformat(),
                      "source": template.get("source", AT), "applicable": applies, "reason": reason, "created": template["key"] in created,
                      "selected": applies and template.get("default", True) and template["key"] not in created})
    return {"regime": regime, "period": period, "employees": employees, "templates": items,
            "notes": ["Modelos com a regra habitual, para validar com o calendário fiscal oficial e o contabilista. As obrigações criadas ficam «a validar».",
                      "O IUC gere-se na ficha de cada viatura (separador Veículos).",
                      "Prorrogações e dias não úteis: reagende a ocorrência indicando a origem (despacho, calendário)."]}


class CreateInput(Strict):
    keys: list[str] = Field(min_length=1, max_length=len(TEMPLATES))
    responsible_id: str | None = None
    store_id: str | None = None


@router.post("/api/obligations/fiscal-templates")
def create(data: CreateInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    obligations.check_scope(user, db, data.store_id)
    by_key = {t["key"]: t for t in TEMPLATES}
    if any(key not in by_key for key in data.keys) or len(set(data.keys)) != len(data.keys):
        fail("Modelo fiscal desconhecido ou repetido.")
    if data.responsible_id:
        person = db.get(User, data.responsible_id)
        if not person or not person.active:
            fail("Responsável inexistente ou inativo.")
    reference = obligations.today()
    created, skipped = [], []
    existing = existing_templates(db)
    for key in data.keys:
        if key in existing:
            skipped.append(key)
            continue
        template = by_key[key]
        rule = template["rule"]
        row = Obligation(store_id=data.store_id, category="fiscal", kind=template["kind"], title=template["title"], description="",
                         entity_name="Segurança Social" if template.get("source") == SS else "Autoridade Tributária", responsible_id=data.responsible_id,
                         periodicity=rule["periodicity"], start_date=first_due(rule, reference).isoformat(), alert_days=json.dumps([30, 15, 7, 0]),
                         date_source="regra_legal", date_note=f"{template['text']}{WORKDAY} Fonte a confirmar: {template.get('source', AT)}.",
                         date_validated=False, status="ativa", data=json.dumps({"template": key}), revision=0, created_by=user.id)
        db.add(row)
        db.flush()
        sync(db, row, reference)
        created.append({"id": row.id, "key": key, "title": row.title, "first_due": row.start_date})
    record(db, user, "obrigacoes_fiscais_modelos", "fiscal-templates", {"criadas": [c["key"] for c in created], "ignoradas": skipped}, data.store_id)
    db.commit()
    return {"created": created, "skipped": skipped}
