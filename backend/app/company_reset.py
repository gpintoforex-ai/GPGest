"""Operational/catalog reset; original schemas remain archived, never deleted here."""

from sqlalchemy import text

from .models import Base

CONFIGURATION_TABLES = (
    "users",
    "stores",
    "store_access",
    "registers",
    "categories",
    "employees",
    "employee_stores",
)
CATALOG_TABLES = frozenset(
    {
        *CONFIGURATION_TABLES,
        "bank_institutions",
        "bank_accounts",
        "business_entities",
        "local_payment_methods",
        "payment_terminals",
        "payment_method_bank_defaults",
        "efatura_config",
        "company_setup",
        "company_fiscal_settings",
        "liquidity_settings",
        "attachment_integration_config",
        "document_scan_settings",
        "scan_cloud_folders",
        "google_connections",
        "invoice_workflow_settings",
        "company_email_settings",
        "wt_categories",
        "wt_products",
        "stock_articles",
        "stock_warehouses",
        "stock_settings",
        "wt_entities",
        "wt_cash_accounts",
        "wt_payment_methods",
        "category_integration_links",
        "integration_bindings",
    }
)


def copy_company_data(connection, original, replacement, movements_only=False):
    """Copy in FK order; the operational reset keeps only catalogs and banks.

    Schema names are generated/validated by the platform route. Everything runs
    on that route's transaction, including CREATE SCHEMA and the company switch.
    """
    for table in Base.metadata.sorted_tables:
        retained = CATALOG_TABLES if movements_only else CONFIGURATION_TABLES
        if table.name not in retained:
            continue
        columns, values = [], []
        for column in table.columns:
            quoted = '"' + column.name + '"'
            columns.append(quoted)
            removed_reference = movements_only and any(
                fk.column.table.name not in retained for fk in column.foreign_keys
            )
            if removed_reference and not column.nullable:
                raise RuntimeError("A nova relação exige revisão da reinicialização de movimentos.")
            values.append("NULL" if removed_reference else quoted)
        # Keep catalog mappings, never links to removed purchases/credit documents.
        condition = (
            " WHERE \"internal_kind\" IN ('entity', 'register')"
            if movements_only and table.name == "integration_bindings"
            else ""
        )
        connection.execute(
            text(
                f'INSERT INTO "{replacement}"."{table.name}" ({", ".join(columns)}) '
                f'SELECT {", ".join(values)} FROM "{original}"."{table.name}"{condition}'
            )
        )
