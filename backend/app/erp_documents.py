"""Compatibility entry point for the current Wintouch connection.

Application routes keep their existing signature; provider-specific mapping lives
inside integrations and all results pass through the common ERP contract.
"""

from datetime import date

from .integrations.erp import ERPIntegrationError, ERPRegistry
from .integrations.wintouch_documents import WintouchPurchaseAdapter
from .wintouch_sync import SyncError


def snapshot(reader, start, end):
    registry = ERPRegistry()
    registry.register("wintouch", lambda: WintouchPurchaseAdapter(reader))
    try:
        return [
            item.model_dump()
            for item in registry.purchase_documents(
                "wintouch", date.fromisoformat(start), date.fromisoformat(end)
            )
        ]
    except ERPIntegrationError as exc:
        raise SyncError(str(exc)) from None
