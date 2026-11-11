"""Natureza financeira das rubricas e as quatro vistas de saídas: custos operacionais, compras e investimentos,
obrigações fiscais e saídas de tesouraria.

Cada rubrica diz em que análises entra (custos operacionais, análise de compras, saídas de tesouraria, obrigações
fiscais, orçamento de pagamentos). Os documentos e movimentos nunca mudam: a natureza só decide em que vista o
valor aparece. Alterar exige simulação (antes/depois nos últimos 12 meses e registos afetados) e aprovação de um
administrador com motivo; fica na auditoria. As sugestões (por exemplo, «IVA» como obrigação fiscal) nunca se aplicam
sozinhas.

A previsão de liquidez, os extratos, a reconciliação bancária e os dados do ERP não dependem desta configuração.
"""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .schemas import Strict

router = APIRouter()

NATURES = {
    "": "Por definir",
    "operacional": "Custo operacional",
    "pessoal": "Custo com pessoal",
    "mercadorias": "Compra de mercadorias",
    "investimento": "Investimento",
    "fiscal": "Obrigação fiscal",
    "financeiro": "Movimento financeiro",
}
FLAGS = ("in_operating_costs", "in_purchases", "in_treasury", "in_fiscal", "in_payment_budget")
FLAG_LABELS = {
    "in_operating_costs": "Custos e despesas operacionais",
    "in_purchases": "Análise de compras e investimentos",
    "in_treasury": "Saídas de tesouraria",
    "in_fiscal": "Obrigações fiscais",
    "in_payment_budget": "Orçamento de pagamentos",
}
# Rubrica sem configuração (ou parcela sem rubrica): o comportamento anterior.
DEFAULTS = {"in_operating_costs": True, "in_purchases": False, "in_treasury": True, "in_fiscal": False, "in_payment_budget": True}
# Proposta de indicadores ao escolher a natureza (o administrador pode ajustar antes de simular).
PRESETS = {
    "": DEFAULTS,
    "operacional": DEFAULTS,
    "pessoal": DEFAULTS,
    "mercadorias": {**DEFAULTS, "in_operating_costs": False, "in_purchases": True},
    "investimento": {**DEFAULTS, "in_operating_costs": False, "in_purchases": True},
    "fiscal": {**DEFAULTS, "in_operating_costs": False, "in_fiscal": True},
    "financeiro": {**DEFAULTS, "in_operating_costs": False},
}
VIEWS = {
    "operating": ("Custos e despesas operacionais", "in_operating_costs"),
    "purchases": ("Compras e investimentos", "in_purchases"),
    "fiscal": ("Obrigações fiscais", "in_fiscal"),
    "treasury": ("Saídas de tesouraria", "in_treasury"),
}
ORIGINS = {
    "operating": "Documentos de compra e custos sem documento registados no banco, nas rubricas que entram nos custos operacionais. "
                 "Regime normal: base tributável + IVA não dedutível estimado; restantes regimes: total com IVA.",
    "purchases": "Documentos de compra nas rubricas de análise de compras e investimentos (mercadorias, equipamento), com o mesmo "
                 "critério de valor. O custo das mercadorias vendidas continua a vir das margens.",
    "fiscal": "Pagamentos ao Estado pelo valor pago: registos bancários e saídas de caixa nas rubricas de obrigações fiscais "
              "(mesmo os que não contam como custo) e documentos nessas rubricas. "
              "IVA liquidado, suportado e dedutível estimado: os mesmos documentos do Mapa de IVA.",
    "treasury": "Pagamentos efetivos pela data do pagamento: saídas de caixa a fornecedores, despesas e saídas, e pagamentos e "
                "encargos do livro bancário, nas rubricas que entram nas saídas de tesouraria. Exclui transferências internas e estornos.",
}


REASON_WORD = re.compile(r"[^\W\d_]{2,}")


def meaningful(value):
    """Motivo legível: pelo menos duas palavras reais e uma com 4 ou mais letras (recusa «jjjjjkkjk»)."""
    value = " ".join((value or "").split())
    # Uma palavra conta se tiver vogal (ou for uma sigla em maiúsculas, como TSU ou DMR) e não for uma repetição de letras.
    words = [w for w in REASON_WORD.findall(value) if len(set(w.casefold())) >= min(3, len(w))
             and (w.isupper() or any(c in "aeiouy" for c in fold(w)))]
    if len(words) < 2 or not any(len(w) >= 4 for w in words):
        raise ValueError("Indique um motivo com pelo menos duas palavras (por exemplo, «Separar IVA dos custos operacionais»).")
    return value


# Motivo das aprovações, reclassificações, anulações e pedidos: fica na auditoria, por isso tem de ser legível.
Reason = Annotated[str, Field(min_length=5, max_length=500), AfterValidator(meaningful)]


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).casefold().strip()


def suggestion(category):
    """Sugestão de natureza para rubricas por definir (só proposta; nunca aplicada sozinha)."""
    if category.nature:
        return None
    name = fold(category.name)
    words = name.replace("-", " ").replace("/", " ").split()
    if "iva" in words and not any(w in words for w in ("dedutivel", "suportado")):
        return {"nature": "fiscal", **PRESETS["fiscal"],
                "reason": "Pagamentos de IVA ao Estado são uma obrigação fiscal: o IVA liquidado nas vendas já foi recebido do cliente e não é custo."}
    if "mercador" in name:
        return {"nature": "mercadorias", **PRESETS["mercadorias"],
                "reason": "Compras de mercadoria são analisadas como compras; o custo das mercadorias vendidas vem das margens."}
    if any(w.startswith(("imobilizad", "investiment", "equipament")) for w in words) or "obras" in words:
        return {"nature": "investimento", **PRESETS["investimento"],
                "reason": "Equipamento e obras são investimento: pesam no mês da compra se ficarem nos custos operacionais. "
                          "Passam para «Compras e investimentos»; continuam na tesouraria e no orçamento."}
    if any(w.startswith(("transferencia", "suprimento", "emprestimo", "financiament")) for w in words) or "fundos" in words:
        return {"nature": "financeiro", **PRESETS["financeiro"],
                "reason": "Passagens entre contas, entradas e saídas de capital e reembolsos de empréstimos não são custos. "
                          "Os juros devem ter uma rubrica de custo própria."}
    if any(w.startswith(("vencimento", "salario", "ordenado", "remuneraco")) for w in words):
        return {"nature": "pessoal", **PRESETS["pessoal"],
                "reason": "Identifica a rubrica como custo com pessoal; continua nos custos operacionais (os números não mudam)."}
    return None


def flags(db, overrides=None):
    """{rubrica: {indicador: bool}} com as alterações em simulação sobrepostas."""
    from .models import Category
    table = {row.id: {name: getattr(row, name) for name in FLAGS} for row in db.scalars(select(Category))}
    for key, values in (overrides or {}).items():
        table[key] = {**table.get(key, DEFAULTS), **{name: values[name] for name in FLAGS if name in values}}
    return table


def flag(table, category, name):
    return table.get(category or "", DEFAULTS)[name]


def treasury_outflows(db, stores, start, end):
    """[(data, id, rubrica, valor)] dos pagamentos efetivos (os mesmos de billing_conference.payment_list), com a
    rubrica de análise reclassificada quando existe."""
    from .fiscal_payments import reclassified
    return [(day, identifier, category, amount) for day, identifier, category, amount, _ in outflow_records(db, stores, start, end, reclassified(db))]


def outflow_records(db, stores, start, end, moved=None):
    """[(data, id, rubrica de análise, valor, registo)] dos pagamentos efetivos: saídas de caixa (fornecedores,
    despesas, saídas) e pagamentos/encargos do livro bancário, sem transferências nem estornos."""
    from .billing_conference import bounds, local_day, movement_store
    from .models import BankAccount, BankBookEntry, Movement, Register
    moved = moved or {}
    first, last = bounds(str(start), str(end))
    registers = {row.id: row for row in db.scalars(select(Register))}
    result = []
    for move in db.scalars(select(Movement).where(
            Movement.direction == -1, Movement.kind.in_(("fornecedor", "despesa", "saida_caixa")), Movement.reversed_by.is_(None),
            Movement.reversal_of.is_(None), Movement.transfer_id.is_(None), Movement.occurred_at >= first, Movement.occurred_at < last)):
        if movement_store(db, move, registers)[0] in stores:
            key = "move:" + move.id
            result.append((local_day(move.occurred_at), key, moved.get(key, move.category_id or ""), move.amount_cents, move))
    accounts = [a.id for a in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores)))]
    reversed_ids = set(db.scalars(select(BankBookEntry.original_id).where(BankBookEntry.original_id.is_not(None))))
    for entry in db.scalars(select(BankBookEntry).where(
            BankBookEntry.account_id.in_(accounts), BankBookEntry.kind.in_(("payment", "fee")), BankBookEntry.transfer_id.is_(None),
            BankBookEntry.movement_date >= str(start), BankBookEntry.movement_date <= str(end))) if accounts else []:
        if entry.id not in reversed_ids:
            key = "bank:" + entry.id
            result.append((entry.movement_date, key, moved.get(key, entry.category_id or ""), abs(entry.amount_cents), entry))
    return result


def collect(db, stores, start, end, treasury=True):
    """Dados de origem de um período, lidos uma vez e reutilizados pelas várias vistas."""
    from .management_maps import cost_documents
    documents = [(day, identifier, category or "", base, tax) for day, identifier, shares in cost_documents(db, stores, start, end)
                 for category, base, tax in shares]
    return {"documents": documents, "treasury": treasury_outflows(db, stores, start, end) if treasury else []}


def cost_value(category, base, tax, regime, percents):
    from .management_maps import deductible
    return base + (tax - deductible(tax, category, percents) if regime == "normal" else tax)


def contributions(data, view, regime, percents, table):
    """(data, id, rubrica, base, IVA, valor) de uma vista. «costs» = custos operacionais ou compras (mapas de
    custos e proveitos); «budget» = orçamento de pagamentos; «all» = sem filtro."""
    if view == "treasury":
        for day, identifier, category, amount in data["treasury"]:
            if flag(table, category, "in_treasury"):
                yield day, identifier, category, amount, 0, amount
        return
    if view == "fiscal":
        # Pagamentos ao Estado pelo valor pago: registos bancários e saídas de caixa nas rubricas fiscais (mesmo os
        # que não contam como custo) e documentos de outras origens nessas rubricas. Os custos sem documento do
        # banco já estão nos pagamentos: não se contam duas vezes.
        for day, identifier, category, base, tax in data["documents"]:
            if not identifier.startswith("bank:") and flag(table, category, "in_fiscal"):
                yield day, identifier, category, base, tax, base + tax
        for day, identifier, category, amount in data["treasury"]:
            if flag(table, category, "in_fiscal"):
                yield day, identifier, category, amount, 0, amount
        return
    for day, identifier, category, base, tax in data["documents"]:
        if view == "costs":
            included = flag(table, category, "in_operating_costs") or flag(table, category, "in_purchases")
        elif view == "budget":
            included = flag(table, category, "in_payment_budget")
        elif view == "all":
            included = True
        else:
            included = flag(table, category, VIEWS[view][1])
        if included:
            yield day, identifier, category, base, tax, cost_value(category, base, tax, regime, percents)


def filter_shares(documents, table, name):
    """Filtra [(data, id, [(rubrica, base, IVA)])] (formato de cost_documents) por um indicador."""
    result = []
    for day, identifier, shares in documents:
        kept = [share for share in shares if flag(table, share[0], name)]
        if kept:
            result.append((day, identifier, kept))
    return result


def cost_documents(db, stores, start, end, name):
    """cost_documents restrito às rubricas com o indicador `name` (ex.: orçamento de pagamentos)."""
    from .management_maps import cost_documents as everything
    return filter_shares(everything(db, stores, start, end), flags(db), name)


# -------------------------------------------------------------------------- configuração


class Change(Strict):
    category_id: str = Field(min_length=1, max_length=36)
    nature: str = Field(default="", max_length=20)
    in_operating_costs: bool
    in_purchases: bool
    in_treasury: bool
    in_fiscal: bool
    in_payment_budget: bool


class Simulation(Strict):
    changes: list[Change] = Field(min_length=1, max_length=200)


class Approval(Simulation):
    fingerprint: str = Field(min_length=64, max_length=64)
    reason: Reason
    # Pedido de um gestor que esta aplicação aprova (fica aprovado e ligado na auditoria).
    request_id: str | None = Field(default=None, max_length=36)


def describe(category):
    return {"id": category.id, "name": category.name, "active": category.active, "nature": category.nature,
            "nature_label": NATURES.get(category.nature, category.nature), **{name: getattr(category, name) for name in FLAGS},
            "suggestion": suggestion(category)}


def validated(db, changes):
    from .models import Category
    result = {}
    for change in changes:
        category = db.get(Category, change.category_id)
        if not category:
            raise HTTPException(404, "Rubrica inexistente.")
        if change.nature not in NATURES:
            raise HTTPException(422, "Natureza inválida.")
        if change.category_id in result:
            raise HTTPException(422, f"A rubrica «{category.name}» aparece mais de uma vez.")
        values = change.model_dump(exclude={"category_id"})
        if all(getattr(category, key) == value for key, value in values.items()):
            continue
        result[change.category_id] = (category, values)
    if not result:
        raise HTTPException(422, "Não há alterações em relação à configuração atual.")
    return result


def fingerprint(changes):
    """Identifica a simulação: alterações pedidas e configuração atual das rubricas afetadas."""
    payload = sorted((key, values, {name: getattr(category, name) for name in ("nature", *FLAGS)}) for key, (category, values) in changes.items())
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def window():
    from .dashboard import today
    end = today()
    first = end.replace(day=1)
    for _ in range(11):
        first = (first - timedelta(days=1)).replace(day=1)
    return first, end


def view_totals(data, regime, percents, table, budget_data):
    totals = {key: 0 for key in (*VIEWS, "costs", "budget")}
    for view in (*VIEWS, "costs"):
        totals[view] = sum(item[5] for item in contributions(data, view, regime, percents, table))
    totals["budget"] = sum(item[5] for item in contributions(budget_data, "budget", regime, percents, table))
    return totals


def review(db, changes, data):
    """Avisos sobre alterações que provavelmente não são intencionais (não bloqueiam a aplicação)."""
    from .models import BankBookEntry, Movement
    used = {item[2] for item in data["documents"]} | {item[2] for item in data["treasury"]}
    ids = list(changes)
    inflows = set(db.scalars(select(Movement.category_id).where(Movement.category_id.in_(ids), Movement.direction == 1).distinct()))
    inflows |= set(db.scalars(select(BankBookEntry.category_id).where(BankBookEntry.category_id.in_(ids), BankBookEntry.amount_cents > 0).distinct()))
    warnings = []
    for key, (category, values) in changes.items():
        name, nature = category.name, values["nature"]
        changed = [FLAG_LABELS[f].lower() for f in FLAGS if getattr(category, f) != values[f]]
        if not any(values[f] for f in ("in_operating_costs", "in_purchases", "in_fiscal", "in_treasury")):
            warnings.append({"category_id": key, "text": f"«{name}» deixa de aparecer em todas as vistas (custos operacionais, compras e "
                                                          "investimentos, obrigações fiscais e saídas de tesouraria)."})
        elif (category.in_operating_costs and not values["in_operating_costs"] and not values["in_purchases"]
              and not values["in_fiscal"] and nature != "financeiro"):
            warnings.append({"category_id": key, "text": f"«{name}» sai dos custos operacionais sem entrar em compras e investimentos nem em "
                                                          "obrigações fiscais: os valores só ficam nas saídas de tesouraria."})
        if key not in used and changed:
            usage = " É usada em entradas (recebimentos), que não aparecem nestas análises." if key in inflows else ""
            warnings.append({"category_id": key, "text": f"«{name}» não tem pagamentos nem documentos de custo no período: a alteração de "
                                                          f"{', '.join(changed)} não muda nenhum valor.{usage}"})
        expected = {"fiscal": "in_fiscal", "mercadorias": "in_purchases", "investimento": "in_purchases"}.get(nature)
        if expected and not values[expected]:
            warnings.append({"category_id": key, "text": f"A natureza «{NATURES[nature]}» costuma entrar em «{FLAG_LABELS[expected]}», "
                                                          f"mas o indicador está desmarcado em «{name}»."})
        if nature in ("fiscal", "mercadorias", "investimento", "financeiro") and values["in_operating_costs"]:
            warnings.append({"category_id": key, "text": f"«{name}» tem a natureza «{NATURES[nature]}» mas continua nos custos operacionais."})
    return warnings


def simulate(db, user, changes):
    from .dashboard import describe_sources
    from .models import Category
    from .settings import vat_settings
    stores = scope(user, db)
    start, end = window()
    regime = vat_settings(db)["vat_regime"]
    percents = {c.id: c.vat_deduction_percent for c in db.scalars(select(Category))}
    data = collect(db, stores, start, end)
    # Orçamento: o realizado é anual (ano corrente).
    year = end.year
    budget_data = data if start <= date(year, 1, 1) else collect(db, stores, date(year, 1, 1), end, treasury=False)
    before_table = flags(db)
    after_table = flags(db, {key: values for key, (_, values) in changes.items()})
    before = view_totals(data, regime, percents, before_table, budget_data)
    after = view_totals(data, regime, percents, after_table, budget_data)
    labels = {**{key: label for key, (label, _) in VIEWS.items()}, "costs": "Mapa de custos e proveitos (custos)",
              "budget": f"Orçamento: realizado de {year}"}
    affected = defaultdict(lambda: {"amount_cents": 0, "label": "", "date": "", "categories": set()})
    for day, identifier, category, base, tax in data["documents"]:
        if category in changes:
            row = affected[identifier]
            row.update(date=day)
            row["amount_cents"] += base + tax
            row["categories"].add(changes[category][0].name)
    counted = set(affected)
    for day, identifier, category, amount in data["treasury"]:
        if category in changes:
            row = affected[identifier]
            row.update(date=day)
            # O mesmo registo bancário pode ser custo sem documento e pagamento: conta uma vez.
            row["amount_cents"] += 0 if identifier in counted else amount
            row["categories"].add(changes[category][0].name)
    rows = sorted(({"id": key, **value, "categories": sorted(value["categories"])} for key, value in affected.items()),
                  key=lambda row: (row["date"], row["id"]), reverse=True)
    sample = rows[:30]
    describe_sources(db, sample)
    return {
        "fingerprint": fingerprint(changes), "start": str(start), "end": str(end),
        "changes": [{"category_id": key, "name": category.name,
                     "before": {"nature": category.nature, **{name: getattr(category, name) for name in FLAGS}}, "after": values}
                    for key, (category, values) in changes.items()],
        "views": [{"key": key, "label": labels[key], "before_cents": before[key], "after_cents": after[key],
                   "difference_cents": after[key] - before[key]} for key in (*VIEWS, "costs", "budget")],
        "affected": {"count": len(rows), "rows": sample},
        "warnings": review(db, changes, data),
        "preserved": ["Previsão de liquidez e prazos de pagamento", "Extratos, livro bancário e reconciliação",
                      "Documentos de compra, movimentos e dados do ERP (nenhum registo é alterado)",
                      "Mapa de IVA e previsão de IVA (calculados pelos documentos)"],
    }


@router.get("/api/rubric-nature")
def listing(user=Depends(current), db: Session = Depends(db_session)):
    from .models import Category
    authorize(user, db, roles=["administrador", "gestor"])
    return {"natures": [{"key": key, "label": label, "flags": PRESETS[key]} for key, label in NATURES.items()],
            "flags": [{"key": key, "label": label} for key, label in FLAG_LABELS.items()],
            "can_apply": user.role == "administrador",
            "rubrics": [describe(row) for row in db.scalars(select(Category).order_by(Category.active.desc(), Category.name))],
            "history": history(db)}


def history(db, limit=30):
    """Alterações de natureza aplicadas, agrupadas por aplicação (lote), mais recentes primeiro."""
    from .models import Audit, User
    users = {u.id: u.name for u in db.scalars(select(User))}
    batches = {}
    for row in db.scalars(select(Audit).where(Audit.action == "rubrica_natureza_alterada").order_by(Audit.created_at.desc()).limit(400)):
        detail = json.loads(row.detail or "{}")
        # Aplicações anteriores ao lote: o mesmo utilizador, motivo e segundo.
        key = detail.get("batch") or f"{row.user_id}|{detail.get('reason', '')}|{row.created_at.replace(microsecond=0).isoformat()}"
        batch = batches.get(key)
        if batch is None:
            if len(batches) >= limit:
                break
            batch = batches[key] = {"id": key, "date": row.created_at.isoformat(), "user": users.get(row.user_id, ""),
                                    "reason": detail.get("reason", ""), "request_id": detail.get("request_id"), "changes": []}
        batch["changes"].append({"category_id": row.target_id, "name": detail.get("name", ""), "before": detail.get("before", {}),
                                 "after": detail.get("after", {})})
    return list(batches.values())


def recent_changes(db, days=180):
    """Notas para o Dashboard: mudanças recentes de natureza e reclassificações que explicam os totais."""
    from .dashboard import today
    from .models import Category, RubricReclassification
    since = today() - timedelta(days=days)
    notes = []
    for batch in history(db, limit=10):
        day = date.fromisoformat(batch["date"][:10])
        if day < since:
            continue
        out = [c["name"] for c in batch["changes"] if c["before"].get("in_operating_costs") and not c["after"].get("in_operating_costs")]
        back = [c["name"] for c in batch["changes"] if not c["before"].get("in_operating_costs") and c["after"].get("in_operating_costs")]
        parts = []
        if out:
            parts.append(f"{', '.join(out)} deixaram de contar nos custos operacionais" if len(out) > 1 else f"{out[0]} deixou de contar nos custos operacionais")
        if back:
            parts.append(f"{', '.join(back)} voltaram aos custos operacionais" if len(back) > 1 else f"{back[0]} voltou aos custos operacionais")
        if not parts:
            parts.append("mudou a natureza de " + ", ".join(c["name"] for c in batch["changes"]))
        notes.append({"date": str(day), "text": f"Desde {day.strftime('%d/%m/%Y')}, {'; '.join(parts)}."})
    names = {c.id: c.name for c in db.scalars(select(Category))}
    moved = defaultdict(lambda: [0, None])
    for row in db.scalars(select(RubricReclassification).where(RubricReclassification.undone_at.is_(None))):
        day = row.created_at.date()
        if day >= since:
            entry = moved[row.to_category_id]
            entry[0] += 1
            entry[1] = max(entry[1] or day, day)
    for category, (count, day) in moved.items():
        notes.append({"date": str(day), "text": f"{count} pagamento(s) reclassificado(s) para «{names.get(category, '')}» (último em "
                                                 f"{day.strftime('%d/%m/%Y')}); os registos no banco e na caixa não mudaram."})
    notes.sort(key=lambda n: n["date"], reverse=True)
    if notes:
        notes.append({"date": "", "text": "Os dois períodos comparados usam a configuração atual, por isso a comparação continua coerente; "
                                          "relatórios exportados antes destas datas podem mostrar outros totais."})
    return notes


@router.post("/api/rubric-nature/simulate")
def simulation(data: Simulation, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    return simulate(db, user, validated(db, data.changes))


@router.post("/api/rubric-nature/apply")
def apply(data: Approval, user=Depends(current), db: Session = Depends(db_session)):
    from .models import Category
    authorize(user, db, roles=["administrador"])
    ids = sorted({change.category_id for change in data.changes})
    list(db.scalars(select(Category).where(Category.id.in_(ids)).with_for_update()))
    changes = validated(db, data.changes)
    if fingerprint(changes) != data.fingerprint:
        raise HTTPException(409, "A configuração das rubricas mudou desde a simulação. Simule novamente antes de aplicar.")
    result = simulate(db, user, changes)
    summary = {view["key"]: [view["before_cents"], view["after_cents"]] for view in result["views"]}
    from .rubric_requests import settle
    settle(db, user, data.request_id, "nature", [change.model_dump() for change in data.changes], data.reason)
    batch = str(uuid4())
    for key, (category, values) in changes.items():
        before = {"nature": category.nature, **{name: getattr(category, name) for name in FLAGS}}
        for name, value in values.items():
            setattr(category, name, value)
        record(db, user, "rubrica_natureza_alterada", key, {"name": category.name, "before": before, "after": values,
                                                             "reason": data.reason, "simulation": summary,
                                                             "period": [result["start"], result["end"]],
                                                             "affected_records": result["affected"]["count"],
                                                             "batch": batch, "request_id": data.request_id})
    db.commit()
    return {"applied": len(changes), "views": result["views"]}
