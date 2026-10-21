"""Opt-in periodic catalog reads. Never sends ERP payments or documents."""
from threading import Event, Thread
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import db as database
from .models import WintouchSyncRun, now
from .platform_models import Company
from .operating_modes import company_policy


def tick():
    from .platform import cipher, validate_url
    from .wintouch_sync import synchronize
    for identifier in company_ids():
        with database.next_platform_session() as core:
            company = core.scalar(select(Company).where(Company.id == identifier).with_for_update())
            config = company_policy(company)
            if not config["active"] or not company.sync_interval_minutes:
                continue
            stamp = now()
            previous = company.last_scheduled_at
            if previous and (stamp - (previous if previous.tzinfo else previous.replace(tzinfo=stamp.tzinfo))).total_seconds() < company.sync_interval_minutes * 60:
                continue
            company.last_scheduled_at = stamp
            resources = [r for r in config["resources"] if r in ("products", "entities", "cash_accounts")]
            enterprise, schema = company.api_enterprise, company.schema_name
            try:
                key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
                url, enterprise, schema = validate_url(company.api_url), company.api_enterprise, company.schema_name
            except Exception:
                core.commit()
                with Session(database.engine.execution_options(schema_translate_map={None: schema})) as tenant:
                    for resource in resources:
                        tenant.add(WintouchSyncRun(enterprise_id=enterprise, resource=resource, status="falhou", finished_at=stamp, error_message="Não foi possível abrir a configuração da ligação. Reveja os parâmetros e a chave guardada."))
                    tenant.commit()
                continue
            core.commit()
        for resource in resources:
            # Recheck between calls so explicit deactivation stops subsequent resources.
            with database.next_platform_session() as core:
                current = core.get(Company, identifier)
                if not current or not company_policy(current)["active"] or not current.sync_interval_minutes:
                    break
            try:
                synchronize(database.engine.execution_options(schema_translate_map={None: schema}), enterprise, resource, url, key)
            except Exception:
                # Synchronize persists its own execution status. Never expose credentials.
                continue


def company_ids():
    with database.next_platform_session() as core:
        return list(core.scalars(select(Company.id).where(Company.active == 1, Company.operating_mode == "integrated", Company.api_active == 1, Company.sync_interval_minutes > 0)))


def start_scheduler():
    stop = Event()
    def run():
        while not stop.wait(30):
            try:
                from .document_scanning import cleanup_all
                cleanup_all()
            except Exception:
                pass
            try:
                tick()
            except Exception:
                pass
            try:
                from .archive import scheduled_tick as archive_tick
                archive_tick()
            except Exception:
                pass
            try:
                from .invoice_workflow import scheduled_tick
                scheduled_tick()
            except Exception:
                continue
    worker = Thread(target=run, name="gpgest-integration-catalogs", daemon=True)
    worker.start()
    return stop
