"""Alertas e notificações das Obrigações e Prazos.

- Internos: prazos vencidos e urgentes (até 7 dias) nos avisos da barra (só os ainda não lidos pelo utilizador) e
  no painel «O que precisa da sua atenção». Só leitura.
- Email (duas autorizações, ambas desligadas por omissão): o administrador ativa os lembretes para a empresa e cada
  utilizador adere aos seus. Um email por utilizador e por execução com os alertas do dia (dias configurados no
  prazo: 90, 60, 30, 15, 7, no dia) e os vencidos (uma vez). O mesmo alerta nunca segue duas vezes
  (`obligation_notifications`); uma falha antes do envio repete-se na execução seguinte; um envio incerto nunca se
  repete automaticamente (como no resto do email). Destinatário: o responsável do prazo ou, sem responsável, os
  administradores e gestores com acesso ao estabelecimento. Só utilizadores da empresa.
- Execução a pedido (administrador) ou uma vez por dia pelo agendador, a partir das 8 h (Lisboa).
"""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import obligations
from .access import authorize, current, record
from .db import db_session
from .models import (
    Access,
    AutomationSettings,
    NotificationPreference,
    Obligation,
    ObligationNotification,
    ObligationOccurrence,
    ObligationRead,
    User,
)
from .obligations import ROLES, fail
from .schemas import Strict

router = APIRouter()
LISBON = ZoneInfo("Europe/Lisbon")
MANAGERS = ("administrador", "gestor")


def fmt(day):
    return "/".join(reversed(day.split("-")))


# --- Prazos em aberto ------------------------------------------------------------------------------

def open_items(db, stores, reference, horizon=7):
    """[(ocorrência, obrigação)] pendentes de obrigações ativas no âmbito, vencidas ou até `horizon` dias."""
    rows = {o.id: o for o in db.scalars(select(Obligation).where(Obligation.status == "ativa")) if o.store_id is None or o.store_id in stores}
    if not rows:
        return []
    limit = (reference + timedelta(days=horizon)).isoformat()
    occurrences = db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id.in_(rows), ObligationOccurrence.status == "pendente",
                                                                ObligationOccurrence.due_date <= limit).order_by(ObligationOccurrence.due_date))
    return [(o, rows[o.obligation_id]) for o in occurrences]


def alert_group(db, user, stores):
    """Grupo para os avisos da barra: vencidos e urgentes ainda não lidos por este utilizador."""
    if user.role not in MANAGERS:
        return None
    reference = obligations.today()
    items = open_items(db, stores, reference)
    read = set(db.scalars(select(ObligationRead.occurrence_id).where(ObligationRead.user_id == user.id, ObligationRead.occurrence_id.in_([o.id for o, _ in items])))) if items else set()
    unread = [(o, ob) for o, ob in items if o.id not in read]
    if not unread:
        return None
    overdue = any(o.due_date < reference.isoformat() for o, _ in unread)
    labels = [f"{ob.title} · {'vencido a' if o.due_date < reference.isoformat() else 'até'} {fmt(o.due_date)}" for o, ob in unread]
    return {"id": "prazos", "label": "Prazos vencidos ou nos próximos 7 dias", "count": len(labels), "tone": "danger" if overdue else "warning",
            "page": "Obrigações e Prazos", "params": {"area.obrigacoes": "prazos"}, "items": labels}


def attention_notes(db, user, stores):
    """Observações para o painel «O que precisa da sua atenção» (mesmo formato do dashboard)."""
    if user.role not in MANAGERS:
        return []
    reference = obligations.today()
    items = open_items(db, stores, reference)
    overdue = [(o, ob) for o, ob in items if o.due_date < reference.isoformat()]
    soon = [(o, ob) for o, ob in items if o.due_date >= reference.isoformat()]
    notes = []

    def euros(cents):
        return f"{cents / 100:,.2f} €".replace(",", " ").replace(".", ",")
    if overdue:
        notes.append({"severity": "critical", "key": "obligations_overdue", "fact": f"{len(overdue)} prazo(s) vencido(s) por concluir.",
                      "values": ", ".join(ob.title for _, ob in overdue[:3]), "impact": "Coimas, juros ou perda de direitos (por exemplo, denúncia fora de prazo).",
                      "action": "Conclua com o comprovativo ou reagende indicando a origem.", "page": "Obrigações e Prazos", "filters": {"area.obrigacoes": "prazos"}})
    if soon:
        amount = sum(o.expected_cents or 0 for o, _ in soon)
        notes.append({"severity": "warning", "key": "obligations_7_days", "fact": f"{len(soon)} prazo(s) nos próximos 7 dias.",
                      "values": ", ".join(ob.title for _, ob in soon[:3]) + (f" · {euros(amount)} previstos" if amount else ""),
                      "impact": "Obrigações fiscais, renovações, inspeções ou pagamentos a curto prazo.", "action": "Prepare e atribua um responsável.",
                      "page": "Obrigações e Prazos", "filters": {"area.obrigacoes": "visao"}})
    return notes


# --- Email -----------------------------------------------------------------------------------------

def automation_data(db):
    row = db.get(AutomationSettings, "default")
    return (json.loads(row.data or "{}") if row else {}), row


def company_enabled(db):
    return bool(automation_data(db)[0].get("obligation_emails"))


def preference(db, user_id):
    row = db.get(NotificationPreference, user_id)
    return bool(row and row.obligations_email)


def user_stores(db, user):
    if user.role == "administrador":
        from .models import Store
        return set(db.scalars(select(Store.id)))
    return set(db.scalars(select(Access.store_id).where(Access.user_id == user.id)))


def alert_key(occurrence, obligation, reference):
    days = (date.fromisoformat(occurrence.due_date) - reference).days
    if days < 0:
        return "vencido"
    return str(days) if days in json.loads(obligation.alert_days or "[]") else None


def pending_notifications(db, reference):
    """{utilizador: [(ocorrência, obrigação, chave)]} dos alertas de hoje ainda não enviados (nem incertos)."""
    users = {u.id: u for u in db.scalars(select(User).where(User.active == 1))}
    opted = {uid for uid in db.scalars(select(NotificationPreference.user_id).where(NotificationPreference.obligations_email.is_(True)))}
    managers = [u for u in users.values() if u.role in MANAGERS and u.id in opted and u.email]
    stores = {u.id: user_stores(db, u) for u in managers}
    done = {(n.occurrence_id, n.user_id, n.alert_key) for n in db.scalars(select(ObligationNotification).where(ObligationNotification.status.in_(("enviado", "incerto"))))}
    result = defaultdict(list)
    all_stores = set().union(*stores.values()) if stores else set()
    for occurrence, obligation in open_items(db, all_stores | {None}, reference, horizon=366):
        key = alert_key(occurrence, obligation, reference)
        if not key:
            continue
        responsible = occurrence.responsible_id or obligation.responsible_id
        if responsible:
            recipients = [users[responsible]] if responsible in users and responsible in opted and users[responsible].email else []
        else:
            recipients = [u for u in managers if obligation.store_id is None or obligation.store_id in stores[u.id]]
        for person in recipients:
            if (occurrence.id, person.id, key) not in done:
                result[person.id].append((occurrence, obligation, key))
    return result, users


def message_for(config, person, items, reference):
    lines = [f"Olá {person.name},", "", "Prazos a acompanhar na IAZELA:", ""]
    for occurrence, obligation, key in sorted(items, key=lambda item: item[0].due_date):
        when = "vencido desde" if key == "vencido" else "até"
        value = f" · {occurrence.expected_cents / 100:.2f} €".replace(".", ",") if occurrence.expected_cents else ""
        validated = "" if obligation.date_validated else " (data a validar)"
        lines.append(f"- {obligation.title}: {when} {fmt(occurrence.due_date)}{value}{validated}")
    lines += ["", "Abra «Obrigações e Prazos» para concluir (com o comprovativo), reagendar ou confirmar a leitura.",
              "Recebe este email porque ativou os lembretes por email. Pode desativá-los em Obrigações e Prazos → Alertas."]
    message = EmailMessage()
    message["From"] = formataddr((config["sender_name"], config["sender_email"]))
    message["To"] = person.email
    message["Subject"] = f"Prazos a acompanhar ({len(items)}) — {fmt(reference.isoformat())}"
    message["Message-ID"] = make_msgid(domain=(config["sender_email"].split("@")[-1] or "iazela.local"))
    message.set_content("\n".join(lines))
    return message.as_bytes()


def send_notifications(db, actor, reference=None):
    """Envia os alertas por email autorizados; devolve o resumo. Nunca repete enviados nem incertos."""
    from .email_settings import configuration
    from .email_transport import DeliveryFailure, connect, disconnect, submit
    from .platform import cipher
    reference = reference or obligations.today()
    if not company_enabled(db):
        fail("Os lembretes por email estão desativados para a empresa.", 409)
    config_row, config = configuration(db)
    if not config_row or not config["enabled"] or not config["sender_email"]:
        fail("Ative e configure o SMTP em Configurações → Email.", 409)
    pending, users = pending_notifications(db, reference)
    summary = {"emails": 0, "alerts": 0, "failed": 0, "uncertain": 0, "errors": []}
    if not pending:
        return summary
    try:
        password = cipher().decrypt(config_row.password_ciphertext.encode()).decode() if config_row.password_ciphertext else ""
        client, _ = connect(config, password)
    except DeliveryFailure as exc:
        fail(str(exc), 502)
    try:
        for user_id, items in pending.items():
            person = users[user_id]
            raw = message_for(config, person, items, reference)
            rows = []
            for occurrence, _, key in items:
                row = db.scalar(select(ObligationNotification).where(ObligationNotification.occurrence_id == occurrence.id, ObligationNotification.user_id == user_id,
                                                                     ObligationNotification.alert_key == key, ObligationNotification.channel == "email"))
                if not row:
                    row = ObligationNotification(occurrence_id=occurrence.id, user_id=user_id, alert_key=key, channel="email", status="falhou")
                    db.add(row)
                rows.append(row)

            def before_data(rows=rows):
                # A partir daqui a entrega pode ficar incerta: fica registado antes de transmitir o conteúdo.
                for row in rows:
                    row.status, row.error = "incerto", ""
                db.commit()
            try:
                submit(client, config["sender_email"], [person.email], raw, before_data)
                for row in rows:
                    row.status = "enviado"
                summary["emails"] += 1
                summary["alerts"] += len(rows)
            except DeliveryFailure as exc:
                for row in rows:
                    row.status, row.error = ("incerto" if exc.uncertain else "falhou"), str(exc)[:500]
                summary["uncertain" if exc.uncertain else "failed"] += 1
                summary["errors"].append(f"{person.name}: {exc}")
            db.commit()
    finally:
        disconnect(client)
    data, setting = automation_data(db)
    if setting:
        setting.data = json.dumps({**data, "obligation_emails_last_run": reference.isoformat(), "obligation_emails_last_result": summary}, ensure_ascii=False)
    record(db, actor, "alertas_prazos_email", "obligation-emails", summary)
    db.commit()
    return summary


# --- Rotas -----------------------------------------------------------------------------------------

class MyPreference(Strict):
    obligations_email: bool


class CompanyPreference(Strict):
    enabled: bool


@router.get("/api/obligations/notifications")
def notifications(user=Depends(current), db: Session = Depends(db_session)):
    from .email_settings import configuration
    authorize(user, db, roles=ROLES)
    _, config = configuration(db)
    data, _ = automation_data(db)
    reference = obligations.today()
    pending, _ = pending_notifications(db, reference)
    mine = [{"title": ob.title, "due_date": o.due_date, "alert": key} for o, ob, key in pending.get(user.id, [])]
    names = {u.id: u.name for u in db.scalars(select(User))}
    history = []
    if user.role == "administrador":
        for n in db.scalars(select(ObligationNotification).order_by(ObligationNotification.created_at.desc()).limit(30)):
            occurrence = db.get(ObligationOccurrence, n.occurrence_id)
            obligation = db.get(Obligation, occurrence.obligation_id) if occurrence else None
            history.append({"user": names.get(n.user_id), "alert": n.alert_key, "status": n.status, "error": n.error,
                            "created_at": n.created_at.isoformat(), "title": obligation.title if obligation else ""})
    return {"company_enabled": bool(data.get("obligation_emails")), "smtp_enabled": bool(config.get("enabled") and config.get("sender_email")),
            "my_email": user.email, "my_preference": preference(db, user.id), "my_pending": mine,
            "pending_users": len(pending), "pending_alerts": sum(len(v) for v in pending.values()),
            "last_run": data.get("obligation_emails_last_run"), "last_result": data.get("obligation_emails_last_result"), "history": history,
            "rules": ["Avisos internos: prazos vencidos e nos próximos 7 dias, nos avisos da barra (os ainda não lidos) e no painel do dashboard.",
                      "Email: um por dia com os alertas dos dias configurados em cada prazo e os vencidos (uma vez). Nunca repete um alerta enviado.",
                      "Destinatário: o responsável do prazo; sem responsável, os administradores e gestores com acesso ao estabelecimento."]}


@router.put("/api/obligations/notifications/me")
def set_mine(data: MyPreference, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    if data.obligations_email and not user.email:
        fail("O seu utilizador não tem email.")
    row = db.get(NotificationPreference, user.id) or NotificationPreference(user_id=user.id)
    row.obligations_email = data.obligations_email
    db.merge(row)
    record(db, user, "alertas_prazos_preferencia", user.id, {"email": data.obligations_email})
    db.commit()
    return notifications(user, db)


@router.put("/api/obligations/notifications/company")
def set_company(data: CompanyPreference, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    values, row = automation_data(db)
    if not row:
        row = AutomationSettings(id="default", revision=0)
        db.add(row)
    row.data = json.dumps({**values, "obligation_emails": data.enabled}, ensure_ascii=False)
    record(db, user, "alertas_prazos_empresa", "obligation-emails", {"ativo": data.enabled})
    db.commit()
    return notifications(user, db)


@router.post("/api/obligations/notifications/send")
def send_now(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    return send_notifications(db, user)


def scheduled_tick():
    """Uma execução por dia e por empresa com lembretes ativos, a partir das 8 h (Lisboa)."""
    from . import db as database
    from .db import company_db
    from .platform_models import Company
    if datetime.now(LISBON).hour < 8:
        return
    with database.next_platform_session() as core:
        companies = list(core.scalars(select(Company).where(Company.active == 1)))
        for company in companies:
            core.expunge(company)
    for company in companies:
        try:
            with company_db(company) as db:
                data, setting = automation_data(db)
                if not data.get("obligation_emails") or data.get("obligation_emails_last_run") == obligations.today().isoformat():
                    continue
                # Marca o dia antes de enviar: uma falha (SMTP desligado, servidor indisponível) não repete de 30 em 30 s.
                setting.data = json.dumps({**data, "obligation_emails_last_run": obligations.today().isoformat()}, ensure_ascii=False)
                db.commit()
                actor = db.scalar(select(User).where(User.role == "administrador", User.active == 1).order_by(User.name).limit(1))
                if actor:
                    send_notifications(db, actor)
        except Exception:
            continue

