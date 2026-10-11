"""Read-only financial snapshot: source payments never alter local cash sessions."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from time import monotonic

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import WintouchSaleRecord, WintouchSyncRun, now
from .wintouch_sync import SyncError, string


class Reader:
    def __init__(self, client, url, enterprise, key):
        self.client = client
        self.url = url.rstrip("/")
        self.headers = {
            "Authorization": "ApiKey " + key,
            "Accept": "application/json",
            "x-current-enterprise": enterprise,
        }
        self.deadline = monotonic() + 180

    def get(self, resource, params=None):
        if monotonic() >= self.deadline:
            raise SyncError("A importação excedeu três minutos. Escolha um período menor e repita.")
        response = self.client.get(self.url + "/" + resource, params=params, headers=self.headers)
        if response.status_code != 200:
            raise SyncError(
                f"A API respondeu com HTTP {response.status_code}. Nenhum movimento foi importado."
            )
        try:
            data = json.loads(response.text, parse_float=Decimal)
        except ValueError:
            raise SyncError("A API não devolveu JSON válido.") from None
        if isinstance(data, dict) and (
            data.get("success") is False or data.get("Success") is False or "Message" in data
        ):
            raise SyncError(
                "A API recusou a consulta de vendas. Verifique as permissões da ligação."
            )
        return data

    def collection(self, resource, embed=None):
        seen = set()
        total = None
        while True:
            params = {"limit": 100, "skip": len(seen)}
            if embed:
                params["embed"] = embed
            data = self.get(resource, params)
            rows = data
            if isinstance(data, dict):
                for key in ("total", "Total", "totalCount", "TotalCount"):
                    if key in data:
                        value = data[key]
                        if (
                            not isinstance(value, int)
                            or isinstance(value, bool)
                            or value < 0
                            or (total is not None and value != total)
                        ):
                            raise SyncError(
                                "O total da coleção mudou ou é inválido. Repita a importação."
                            )
                        total = value
                rows = next(
                    (data[k] for k in ("items", "Items", "data", "Data") if k in data), None
                )
            if not isinstance(rows, list):
                raise SyncError("A API devolveu uma coleção de vendas não suportada.")
            if not rows:
                if total is not None and total != len(seen):
                    raise SyncError("A API devolveu uma coleção incompleta.")
                return
            for row in rows:
                if not isinstance(row, dict):
                    raise SyncError("A API devolveu um documento inválido.")
                external_id = string(row.get("ID"), 150, True)
                if external_id in seen:
                    raise SyncError("A API repetiu IDs entre páginas. A importação foi anulada.")
                seen.add(external_id)
                if len(seen) > 50000:
                    raise SyncError(
                        "A coleção excede 50 000 registos. Nenhum movimento foi importado."
                    )
                yield row


def source_date(value):
    if not isinstance(value, str):
        raise SyncError("Um documento não tem data válida.")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.year < 1900:
            raise ValueError()
        return stamp.date().isoformat()
    except ValueError:
        raise SyncError("Um documento não tem data válida.") from None


def amount(value):
    try:
        if value is None or isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        if (
            not number.is_finite()
            or abs(number) >= Decimal("1000000000")
            or number != number.quantize(Decimal(".01"))
        ):
            raise ValueError()
        return int(number * 100)
    except (ArithmeticError, ValueError):
        raise SyncError("A API devolveu um montante monetário inválido.") from None


def document_total(row):
    if row.get("NetValueTotal") is not None:
        return amount(row["NetValueTotal"])
    if not any(f"BaseValue{i}" in row for i in range(1, 5)):
        raise SyncError("O documento não tem valores líquidos e IVA para calcular o total.")
    return sum(
        amount(row.get(f"{field}{i}") or 0)
        for field in ("BaseValue", "VATValue", "RechargeValue")
        for i in range(1, 5)
    ) - amount(row.get("WithHoldedValue") or 0)


def document_label(row, types, series):
    doc_type = types.get(row.get("DocumentTypeID"), {})
    serie = series.get(row.get("DocumentSerieID"), {})
    number = string(row.get("DocumentNumber"), 40, True)
    return " ".join(
        x
        for x in [string(doc_type.get("Code"), 40, True), string(serie.get("Code"), 100), number]
        if x
    )


def payment_record(payment, common, methods, states):
    method = methods.get(payment.get("PaymentMethodID"))
    if not method:
        raise SyncError("Um pagamento usa um meio de pagamento desconhecido.")
    if method.get("PaymentMethodType") in (4, 8) or method.get("Code") == "DESCONTO":
        return None
    state = states.get(method.get("TargetCreditAccountStateID"))
    # A crédito/pendente não é recebimento; não inferir liquidação do nome ou da fatura.
    if method.get("TargetCreditAccountStateID"):
        if not state or not isinstance(state.get("IsFinalStateEnabled"), bool):
            raise SyncError("Não foi possível validar se o meio de pagamento liquida a dívida.")
        if state.get("IsFinalStateEnabled") is not True:
            return None
    else:
        typ = method.get("PaymentMethodType")
        mechanism = method.get("SaftPaymentMechanism")
        # Pares observados na API configurada. Conta corrente e consumo interno
        # não são recebimentos; descontos também não são um pagamento em dinheiro.
        if typ in (4, 8) or method.get("Code") == "DESCONTO":
            return None
        if (typ, mechanism) not in {
            (0, "NU"),
            (2, "TB"),
            (3, "CD"),
            (5, "CC"),
            (7, "CH"),
            (9, "TR"),
        }:
            raise SyncError(
                "Meio de pagamento não validado. Configure o estado de liquidação na Wintouch."
            )
    value = amount(payment.get("Amount"))
    if value < 0:
        raise SyncError("Pagamento negativo exige validação própria; importação anulada.")
    if value == 0:
        return None
    return {
        **common,
        "amount_cents": value,
        "payment": string(method.get("Name"), 200, True),
        "payment_code": string(method.get("SaftPaymentMechanism"), 40),
        "occurred_at": payment.get("PaymentMethodDate") or common["occurred_at"],
    }


def sales_snapshot(reader, start, end):
    catalogs = {}
    for resource in (
        "document_types",
        "document_series",
        "currencies",
        "payment_methods",
        "credit_account_states",
    ):
        catalogs[resource] = {row["ID"]: row for row in reader.collection(resource)}
    types, series = catalogs["document_types"], catalogs["document_series"]
    methods, states = catalogs["payment_methods"], catalogs["credit_account_states"]
    result = {}
    selected = set()
    scanned = 0

    def save(prefix, external_id, kind, day, values):
        key = prefix + ":" + string(external_id, 150, True)
        if key in result:
            raise SyncError("A origem repetiu um identificador financeiro.")
        result[key] = {
            "external_id": key,
            "kind": kind,
            "document_date": day,
            "data": json.dumps(values, ensure_ascii=False),
        }

    def common(row, direction):
        currency = catalogs["currencies"].get(row.get("CurrencyID"), {}).get("Code")
        if currency != "EUR":
            raise SyncError("A importação financeira suporta apenas documentos em EUR.")
        entity = (
            row.get("EntityName")
            or (row.get("CEntity") or {}).get("Name")
            or "Entidade não indicada"
        )
        return {
            "document": document_label(row, types, series),
            "document_group": "compras" if types.get(row.get("DocumentTypeID"), {}).get("Class") in (32, 33, 34, 35) else "vendas",
            "entity": string(entity, 300, True),
            "occurred_at": row["Date"],
            "currency": "EUR",
            "direction": direction,
            "source_document_id": row["ID"],
            "account_id": string(row.get("AccountID"), 150),
            "workshift": string(row.get("WorkShift"), 150),
        }

    for row in reader.collection(
        "product_documents", "Payments,DocumentType,DocumentSerie,CEntity"
    ):
        scanned += 1
        typ = types.get(row.get("DocumentTypeID"), {})
        if typ.get("Class") not in (3, 5, 6, 40):
            continue  # Orçamentos, encomendas, guias e compras não são vendas.
        day = source_date(row.get("Date"))
        if not start <= day <= end:
            continue
        if "Payments" not in row:
            row = reader.get(
                "product_documents/" + string(row["ID"], 150, True),
                {"embed": "Payments,DocumentType,DocumentSerie,CEntity"},
            )
        if not isinstance(row, dict) or not isinstance(row.get("Payments"), list):
            raise SyncError("A API não disponibilizou os pagamentos do documento.")
        direction = -1 if typ.get("Class") == 6 else 1
        values = common(row, direction)
        cancelled = bool(row.get("DeletedDate"))
        values.update(
            amount_cents=abs(document_total(row)),
            cancelled=cancelled,
            type=string(typ.get("Code"), 40, True),
        )
        save("document", row["ID"], "documento", day, values)
        if cancelled:
            continue
        selected.add(row["ID"])
        for payment in row["Payments"]:
            data = payment_record(payment, values, methods, states)
            if data:
                source_date(data["occurred_at"])
                save("payment", payment.get("ID"), "real", day, data)

    # Saldos em aberto de vendas e compras, incluindo documentos anteriores ao período.
    for pending in reader.collection("payablesreceivables", "CreditAccountState"):
        source_id = pending.get("SourceProductDocumentMasterID")
        if not source_id:
            continue
        state = pending.get("CreditAccountState") or states.get(pending.get("CreditAccountStateID"))
        if not state or not isinstance(state.get("IsFinalStateEnabled"), bool):
            raise SyncError("Não foi possível validar o estado da conta corrente.")
        if state["IsFinalStateEnabled"]:
            continue
        value = amount(pending.get("StateValue"))
        if value == 0:
            continue
        if source_id in selected:
            values = json.loads(result["document:" + source_id]["data"])
        else:
            document = reader.get(
                "product_documents/" + string(source_id, 150, True),
                {"embed": "DocumentType,DocumentSerie,CEntity"},
            )
            typ = types.get(document.get("DocumentTypeID"), {})
            directions = {3: 1, 5: 1, 6: -1, 40: 1, 32: -1, 33: 1, 34: -1, 35: 1}
            if typ.get("Class") not in directions or document.get("DeletedDate"):
                continue
            direction = directions[typ["Class"]]
            if typ.get("CashFlowMode") not in (None, direction):
                raise SyncError("O sentido financeiro do documento de conta corrente não é suportado.")
            values = common(document, direction)
        values.update(
            amount_cents=abs(value),
            due_date=source_date(pending.get("DueDate")),
            payment="A crédito",
        )
        save("pending", pending["ID"], "previsto", source_date(values["occurred_at"]), values)

    # Recibos posteriores: pagamentos por método, não o total da fatura de novo.
    for row in reader.collection("settlement_documents"):
        day = source_date(row.get("Date"))
        if not start <= day <= end:
            continue
        typ = types.get(row.get("DocumentTypeID"), {})
        if typ.get("CashFlowMode") != 1:
            continue  # Pagamentos a fornecedores não são recebimentos de vendas.
        row = reader.get(
            "settlement_documents/" + string(row["ID"], 150, True),
            {"embed": "Payments,SettlementDetails,CEntity,DocumentType"},
        )
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("Payments"), list)
            or not isinstance(row.get("SettlementDetails"), list)
        ):
            raise SyncError("A API não disponibilizou os pagamentos e documentos do recibo.")
        if row.get("ProcessStatus") not in (None, ""):
            raise SyncError(
                "O recibo tem um estado de processamento não validado. Nenhum movimento foi importado."
            )
        sources = [
            x.get("SourceProductDocumentMasterID")
            for x in row["SettlementDetails"]
            if x.get("SourceProductDocumentMasterID")
        ]
        if not sources:
            continue
        values = common(row, 1)
        values["source_invoice_ids"] = sources
        for payment in row["Payments"]:
            data = payment_record(payment, values, methods, states)
            if data:
                source_date(data["occurred_at"])
                save("payment", payment.get("ID"), "real", day, data)
    return list(result.values()), scanned


def synchronize_sales(bind, enterprise, store_id, start: date, end: date, url, key):
    with Session(bind, expire_on_commit=False) as db:
        db.execute(
            update(WintouchSyncRun)
            .where(
                WintouchSyncRun.enterprise_id == enterprise,
                WintouchSyncRun.resource == "sales",
                WintouchSyncRun.status == "em_curso",
                WintouchSyncRun.started_at < now() - timedelta(minutes=10),
            )
            .values(
                status="falhou",
                finished_at=now(),
                error_message="Importação interrompida. Pode repetir.",
            )
        )
        db.commit()
        run = WintouchSyncRun(enterprise_id=enterprise, resource="sales", status="em_curso")
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise SyncError("Já existe uma importação de vendas em curso.") from None
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                values, scanned = sales_snapshot(
                    Reader(client, url, enterprise, key), start.isoformat(), end.isoformat()
                )
            for values_row in values:
                row = db.scalar(
                    select(WintouchSaleRecord).where(
                        WintouchSaleRecord.enterprise_id == enterprise,
                        WintouchSaleRecord.external_id == values_row["external_id"],
                    )
                )
                if row:
                    if row.store_id != store_id:
                        raise SyncError(
                            "Existem documentos já associados a outra loja. Use a loja da primeira importação."
                        )
                    run.updated_count += 1
                    for field, value in values_row.items():
                        setattr(row, field, value)
                else:
                    row = WintouchSaleRecord(
                        enterprise_id=enterprise, store_id=store_id, **values_row
                    )
                    db.add(row)
                    run.inserted_count += 1
                row.active = True
                row.sync_run_id = run.id
                row.synced_at = now()
            db.flush()
            # Desaparecimento/anulação/liquidação confirmado por snapshot completo do período.
            db.execute(
                update(WintouchSaleRecord)
                .where(
                    WintouchSaleRecord.enterprise_id == enterprise,
                    WintouchSaleRecord.store_id == store_id,
                    or_(
                        WintouchSaleRecord.kind == "previsto",
                        and_(
                            WintouchSaleRecord.document_date >= start.isoformat(),
                            WintouchSaleRecord.document_date <= end.isoformat(),
                        ),
                    ),
                    WintouchSaleRecord.sync_run_id != run.id,
                )
                .values(active=False)
            )
            run.status = "concluida"
            run.read_count = scanned
            run.finished_at = now()
            db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(WintouchSyncRun, run.id)
            run.status = "falhou"
            run.finished_at = now()
            run.inserted_count = run.updated_count = 0
            run.error_message = (
                str(exc)
                if isinstance(exc, SyncError)
                else "Falha de comunicação ou gravação. Nenhum movimento foi importado."
            )
            db.commit()
        return {
            "id": run.id,
            "status": run.status,
            "read_count": run.read_count,
            "inserted_count": run.inserted_count,
            "updated_count": run.updated_count,
            "error_message": run.error_message,
        }
