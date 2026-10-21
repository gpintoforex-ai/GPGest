"""Assistente do arquivo digital (Clara): pedidos em linguagem natural sobre dados reais.

Interpretação local e determinística (sem serviços externos): reconhece a intenção, o período, o
tipo e a entidade, consulta o arquivo e devolve as fontes usadas. Operações de escrita nunca são
executadas na pergunta: devolvem um plano que exige permissão e confirmação explícita.
"""
import json
import re
from calendar import monthrange
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import access
from .archive import label, match_document, money, normalized, settings, today
from .archive_api import MANAGE, doc_view, link_view, store_names, visible
from .db import db_session
from .models import (
    ArchiveDocument,
    ArchiveLink,
    BankAccount,
    BankTransaction,
    DriveDocumentAttachment,
    Purchase,
    PurchaseAllocation,
    PurchaseAttachment,
    now,
)
from .platform import cipher
from .schemas import Strict

router = APIRouter()
MONTHS = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
          "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
TYPE_WORDS = [
    (r"notas? de credito", {"nota_credito"}, "notas de crédito"),
    (r"faturas?[ -]recibos?", {"fatura_recibo"}, "faturas-recibo"),
    (r"\bfaturas?\b|\bfacturas?\b", {"fatura", "fatura_recibo", "fatura_simplificada"}, "faturas"),
    (r"\brecibos?\b", {"recibo", "fatura_recibo"}, "recibos"),
    (r"\bcomprovativos?\b", {"comprovativo_pagamento"}, "comprovativos de pagamento"),
    (r"\bextratos?\b", {"extrato_bancario"}, "extratos bancários"),
    (r"\bdespesas?\b", {"despesa"}, "despesas"),
    (r"\bcontratos?\b", {"contrato"}, "contratos"),
]
STOP = set("""a o as os da do das dos de e em no na nos nas um uma uns umas que quais qual quanto quantos com sem para por
encontra encontrar encontre mostra mostrar mostre procura procurar procure lista listar liste ve ver quero preciso
documentos documento ficheiros ficheiro todos todas este esta mes ano passado ultimo ultima trimestre primeiro segundo terceiro
quarto 1 2 3 4 faturas fatura facturas factura recibos recibo comprovativos comprovativo extratos extrato despesas despesa
notas nota credito contratos contrato pagamentos pagamento falta faltam em_falta contabilidade relaciona relacionar associa
associar liga ligar cruza cruzar anexos anexo disponiveis compras compra erp prepara preparar dossie dossier hoje semana
me mostra-me sao estao ha tem""".split())


def period(text):
    """Período pedido: mês (com ou sem ano), trimestre, «este mês», «mês passado», ano."""
    current = today()
    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else None
    if "mes passado" in text or "ultimo mes" in text:
        end = current.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, f"{end.month:02d}/{end.year}"
    if "este mes" in text:
        return current.replace(day=1), current, f"{current.month:02d}/{current.year}"
    quarter = re.search(r"\b([1-4])\s*(?:o|º)?\s*trimestre|\b(primeiro|segundo|terceiro|quarto)\s+trimestre", text)
    if quarter:
        number = int(quarter.group(1)) if quarter.group(1) else ["primeiro", "segundo", "terceiro", "quarto"].index(quarter.group(2)) + 1
        year = year or current.year
        start = date(year, 3 * number - 2, 1)
        return start, date(year, 3 * number, monthrange(year, 3 * number)[1]), f"{number}.º trimestre de {year}"
    for name, number in MONTHS.items():
        if re.search(r"\b" + name + r"\b", text):
            year = year or (current.year if number <= current.month else current.year - 1)
            return date(year, number, 1), date(year, number, monthrange(year, number)[1]), f"{name} de {year}"
    if year:
        return date(year, 1, 1), date(year, 12, 31), str(year)
    if "este ano" in text:
        return date(current.year, 1, 1), current, str(current.year)
    return None


def kinds(text):
    for pattern, values, name in TYPE_WORDS:
        if re.search(pattern, text):
            return values, name
    return None, ""


def entity_terms(question):
    words = [w.strip(".-&") for w in re.findall(r"[\w.&-]+", normalized(question))]
    return [w for w in words if w not in STOP and w not in MONTHS and not re.fullmatch(r"20\d{2}", w) and len(w) >= 2][:3]


def date_filter(start, end):
    reference = ArchiveDocument.issue_date
    return [reference >= start.isoformat(), reference <= end.isoformat()]


class Question(Strict):
    question: str = Field(min_length=3, max_length=500)


def describe(start, end):
    return f"{start.strftime('%d/%m/%Y')} a {end.strftime('%d/%m/%Y')}"


@router.post("/api/archive/assistant")
def assistant(data: Question, user=Depends(access.current), db: Session = Depends(db_session)):
    text = normalized(data.question)
    found_period = period(text)
    types, type_label = kinds(text)
    terms = entity_terms(data.question)
    stores, condition = store_names(db), visible(user, db)
    interpretation = {"periodo": found_period[2] if found_period else "", "tipo": type_label, "entidade": " ".join(terms)}
    base = {"interpretation": interpretation, "documents": [], "relations": [], "items": [], "plan": None, "navigate": None}

    def entity_condition():
        conditions = []
        for term in terms:
            like = "%" + term + "%"
            digits = re.sub(r"\D", "", term)
            conditions.append(or_(ArchiveDocument.entity_name.ilike(like), ArchiveDocument.original_name.ilike(like),
                                  ArchiveDocument.description.ilike(like), *([ArchiveDocument.entity_nif.ilike("%" + digits + "%")] if len(digits) >= 5 else [])))
        return conditions

    # Escrita: só um plano, com confirmação.
    if re.search(r"\b(relaciona|relacionar|associa|associar|liga|ligar|cruza|cruzar)\b", text):
        if user.role not in MANAGE:
            raise HTTPException(403, "Só administradores e gestores podem pedir associações automáticas.")
        query = select(ArchiveDocument.id).where(condition, ArchiveDocument.duplicate_of.is_(None),
                                                 ArchiveDocument.id.not_in(select(ArchiveLink.document_id).where(
                                                     ArchiveLink.status == "confirmada", ArchiveLink.target_kind.in_(("purchase", "erp_document")))))
        if found_period:
            query = query.where(*date_filter(found_period[0], found_period[1]))
        if terms:
            query = query.where(*entity_condition())
        ids = list(db.scalars(query.limit(300)))
        token = cipher().encrypt(json.dumps({"user": user.id, "action": "relacionar", "ids": ids,
                                             "expires": (now() + timedelta(minutes=15)).isoformat()}).encode()).decode()
        target = "compras do ERP e do e-Fatura" if "erp" in text else "compras, ERP, movimentos bancários e pagamentos"
        return {**base, "intent": "relacionar", "mode": "escrita",
                "answer": f"Posso calcular propostas de associação entre {len(ids)} documento(s) sem compra associada e as {target}. "
                          "Nada fica confirmado: as propostas aparecem em Associações, com a justificação, para validação.",
                "sources": ["Arquivo digital: documentos sem associação confirmada a compra/ERP" + (f", período {interpretation['periodo']}" if found_period else ""),
                            "Motor de relações: NIF, número, total, datas e descrições bancárias"],
                "plan": {"token": token, "count": len(ids), "summary": f"Calcular propostas para {len(ids)} documento(s)"} if ids else None}

    if re.search(r"dossi[eê]|dossier", text):
        start, end, label = found_period or (lambda e: (e.replace(day=1), e, f"{e.month:02d}/{e.year}"))(today().replace(day=1) - timedelta(days=1))
        docs = list(db.scalars(select(ArchiveDocument).where(condition, ArchiveDocument.duplicate_of.is_(None), *date_filter(start, end))))
        missing = missing_purchases(db, user, start, end)
        review = [d for d in docs if d.review_status == "por_rever"]
        return {**base, "intent": "dossie", "mode": "leitura", "navigate": "Entrega à contabilidade",
                "answer": f"Dossiê de {label}: {len(docs)} documento(s) no arquivo, {len(review)} por rever e {len(missing)} compra(s) sem documento. "
                          "Para fechar o período e exportar ou enviar o dossiê, use «Entrega à contabilidade» (pede confirmação).",
                "sources": [f"Arquivo digital: documentos emitidos de {describe(start, end)}", "Compras/e-Fatura sem anexo nem associação no mesmo período"],
                "documents": [doc_view(d, stores) for d in review[:30]], "items": missing[:50]}

    if re.search(r"sem (comprovativo|documento|fatura|recibo)", text) and re.search(r"pagamento|movimento|debito|transferencia", text):
        start, end = (found_period[0], found_period[1]) if found_period else (today() - timedelta(days=90), today())
        accounts = select(BankAccount.id).where(BankAccount.store_id.in_(access.scope(user, db)))
        rows = list(db.scalars(select(BankTransaction).where(
            BankTransaction.account_id.in_(accounts), BankTransaction.amount_cents < 0,
            BankTransaction.movement_date >= start.isoformat(), BankTransaction.movement_date <= end.isoformat(),
            BankTransaction.id.not_in(select(ArchiveLink.target_id).where(ArchiveLink.status == "confirmada", ArchiveLink.target_kind == "bank_transaction")),
            BankTransaction.id.not_in(select(PurchaseAllocation.transaction_id))).order_by(BankTransaction.movement_date.desc()).limit(200)))
        total = sum(-r.amount_cents for r in rows)
        return {**base, "intent": "pagamentos_sem_comprovativo", "mode": "leitura",
                "answer": f"Encontrei {len(rows)} pagamento(s) bancário(s) sem comprovativo nem compra associada entre {describe(start, end)}, no total de {money(total)}." if rows
                          else f"Não encontrei pagamentos sem comprovativo entre {describe(start, end)}.",
                "sources": ["Extratos bancários: débitos do período", "Arquivo digital: associações confirmadas a movimentos bancários", "Compras: alocações a movimentos bancários"],
                "items": [{"kind": "bank_transaction", "id": r.id, "label": r.description[:140], "date": r.movement_date, "amount_cents": r.amount_cents} for r in rows]}

    if re.search(r"em falta|faltam|falta|pendentes?", text):
        start, end = (found_period[0], found_period[1]) if found_period else (today() - timedelta(days=90), today())
        missing = missing_purchases(db, user, start, end)
        review = list(db.scalars(select(ArchiveDocument).where(condition, ArchiveDocument.duplicate_of.is_(None), ArchiveDocument.review_status == "por_rever").limit(50)))
        return {**base, "intent": "em_falta", "mode": "leitura", "navigate": "Pendências",
                "answer": f"Para a contabilidade ({describe(start, end)}): {len(missing)} compra(s) sem documento digital e {len(review)} documento(s) do arquivo por rever.",
                "sources": [f"Compras/e-Fatura emitidas de {describe(start, end)} sem anexo nem associação confirmada", "Arquivo digital: caixa de entrada por rever"],
                "documents": [doc_view(d, stores) for d in review], "items": missing}

    query = select(ArchiveDocument).where(condition, ArchiveDocument.duplicate_of.is_(None))
    if found_period:
        query = query.where(*date_filter(found_period[0], found_period[1]))
    if types:
        query = query.where(ArchiveDocument.doc_type.in_(types))
    if terms:
        query = query.where(*entity_condition())
    docs = list(db.scalars(query.order_by(ArchiveDocument.issue_date.desc().nulls_last()).limit(100)))
    links = list(db.scalars(select(ArchiveLink).where(ArchiveLink.document_id.in_([d.id for d in docs]), ArchiveLink.status == "confirmada"))) if docs else []
    total = sum(d.total_cents or 0 for d in docs)
    what = type_label or "documentos"
    who = f" de «{' '.join(terms).upper()}»" if terms else ""
    when = f" em {interpretation['periodo']}" if found_period else ""
    return {**base, "intent": "pesquisa", "mode": "leitura",
            "answer": f"Encontrei {len(docs)} {what}{who}{when}" + (f", no total de {money(total)}." if total else ".") if docs
                      else f"Não encontrei {what}{who}{when} no arquivo. Confirme se os ficheiros já foram carregados ou sincronizados.",
            "sources": ["Arquivo digital: " + ", ".join(filter(None, [f"tipo {type_label}" if types else "", f"entidade, nome ou descrição com «{' '.join(terms)}»" if terms else "",
                                                                       f"emissão de {describe(found_period[0], found_period[1])}" if found_period else ""])) or "Arquivo digital: todos os documentos"],
            "documents": [doc_view(d, stores) for d in docs], "relations": [link_view(db, link) for link in links]}


def missing_purchases(db, user, start, end):
    confirmed = select(ArchiveLink.target_id).where(ArchiveLink.status == "confirmada", ArchiveLink.target_kind == "purchase")
    attached = select(PurchaseAttachment.purchase_id).union(select(DriveDocumentAttachment.purchase_id))
    stores = store_names(db)
    rows = db.scalars(select(Purchase).where(Purchase.store_id.in_(access.scope(user, db)), Purchase.document_date >= start.isoformat(),
                                             Purchase.document_date <= end.isoformat(), Purchase.id.not_in(confirmed), Purchase.id.not_in(attached))
                      .order_by(Purchase.document_date.desc()).limit(200))
    return [{"kind": "purchase", "id": p.id, "label": label(p.document_type, p.document), "entity": p.supplier_name, "date": p.document_date,
             "amount_cents": p.amount_cents, "store": stores.get(p.store_id, "")} for p in rows]


class PlanInput(Strict):
    token: str = Field(min_length=10, max_length=20000)


@router.post("/api/archive/assistant/confirm")
def run_plan(data: PlanInput, user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    try:
        plan = json.loads(cipher().decrypt(data.token.encode()))
        expired = now().isoformat() > plan["expires"]
    except Exception:
        raise HTTPException(422, "Plano inválido. Repita o pedido ao assistente.") from None
    if plan.get("user") != user.id or plan.get("action") != "relacionar":
        raise HTTPException(403, "Este plano pertence a outro utilizador.")
    if expired:
        raise HTTPException(410, "O plano expirou. Repita o pedido ao assistente.")
    config, proposals, documents = settings(db), 0, 0
    for identifier in plan["ids"]:
        doc = db.scalar(select(ArchiveDocument).where(ArchiveDocument.id == identifier, visible(user, db)).with_for_update())
        if not doc:
            continue
        documents += 1
        proposals += len(match_document(db, doc, config))
    access.record(db, user, "arquivo_assistente_relacionar", "arquivo", {"documentos": documents, "propostas": proposals})
    db.commit()
    pending = db.scalar(select(ArchiveLink.id).where(ArchiveLink.status == "proposta").limit(1))
    return {"documents": documents, "proposals": proposals, "navigate": "Associações" if pending else None,
            "answer": f"Analisei {documents} documento(s) e encontrei {proposals} candidato(s). As propostas ficam em Associações para validação; nada foi confirmado."}
