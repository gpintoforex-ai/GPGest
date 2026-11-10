"""Envio de preços de venda (linha de preço 1) para a Wintouch, a partir da revisão de preços.

Contrato v1 (swagger): `PATCH /product_prices_change` com `[{ID, RowPrice, PriceWithoutVAT, PriceWithVAT}]`.
Cada artigo é enviado à parte e só é dado como sincronizado depois de lido de volta
(`GET /products/{id}/prices`, linha 1) com os mesmos preços. Definir um preço é idempotente:
repetir o envio com os mesmos valores não duplica nada. Não altera custos, stock nem documentos.
"""

import json
from decimal import ROUND_HALF_UP
from decimal import Decimal as D

import httpx

from .operating_modes import policy
from .wintouch_order_write import connection
from .wintouch_settlement import encode_payload, rejection_message

NET_TOLERANCE, GROSS_TOLERANCE = D("0.0001"), D("0.005")


def availability(db, user):
    """(pode enviar, motivo quando não pode) para a revisão de preços."""
    from .db import request_company
    company = request_company(db)
    if not company:
        return False, "Empresa não identificada: o envio de preços para a Wintouch não está disponível."
    if company.mode == "demonstracao":
        # A empresa de demonstração pode ter credenciais de uma Wintouch real: nunca se enviam preços daqui.
        return False, "No ambiente de demonstração os preços não são enviados para a Wintouch. Pode simular os preços."
    config = policy(db)
    if config["mode"] != "integrated" or config["provider"] != "wintouch" or not config["active"]:
        return False, "A ligação Wintouch Cloud não está ativa: altere o preço na ficha do artigo na Wintouch."
    if not config["writes"] or not config["capabilities"].get("write_prices"):
        return False, "A escrita na Wintouch não está autorizada nas Integrações: altere o preço na ficha do artigo na Wintouch."
    if "products" not in config.get("resources", []):
        return False, "O recurso Artigos está desativado nas Integrações."
    if user.role != "administrador":
        return False, "Só o administrador pode enviar preços para a Wintouch. Pode simular os preços."
    return True, ""


def remote_prices(reader, external_id):
    """(sem IVA, com IVA) da linha de preço 1 na Wintouch, ou None se não for possível ler."""
    response = reader.client.get(f"{reader.url}/products/{external_id}/prices", headers=reader.headers)
    if response.status_code != 200:
        return None
    rows = json.loads(response.text, parse_float=D)
    main = next((r for r in rows if isinstance(r, dict) and r.get("Row") == 1), None) if isinstance(rows, list) else None
    if not main or main.get("PriceWithoutVAT") is None or main.get("PriceWithVAT") is None:
        return None
    return D(str(main["PriceWithoutVAT"])), D(str(main["PriceWithVAT"]))


def push_prices(db, user, items):
    """Envia e confirma cada preço. items: [{"product": WintouchProduct, "net": D, "gross": D}].

    Devolve, por artigo, {"state": "confirmed" | "failed" | "uncertain", "error", "net", "gross"}.
    «uncertain»: o pedido pode ter sido aplicado mas não foi confirmado; repetir é seguro."""
    results = {}
    # Só empresas reais (availability); a ligação recusa de novo a demonstração.
    with connection(db, user, write=True) as reader:
        for item in items:
            product = item["product"]
            net = item["net"].quantize(D("0.0001"), rounding=ROUND_HALF_UP)
            gross = item["gross"].quantize(D("0.01"), rounding=ROUND_HALF_UP)
            body = [{"ID": product.external_id, "RowPrice": 1, "PriceWithoutVAT": net, "PriceWithVAT": gross}]
            result = {"net": net, "gross": gross, "error": ""}
            try:
                response = reader.client.patch(f"{reader.url}/product_prices_change", content=encode_payload(body).encode(),
                                               headers={**reader.headers, "Content-Type": "application/json"})
                if response.status_code not in (200, 202, 204):
                    result.update(state="failed" if 400 <= response.status_code < 500 else "uncertain",
                                  error=rejection_message(response) or f"A Wintouch respondeu com HTTP {response.status_code}.")
                    results[product.id] = result
                    continue
                remote = remote_prices(reader, product.external_id)
            except (httpx.HTTPError, ValueError):
                result.update(state="uncertain", error="Sem resposta confirmada da Wintouch. Pode repetir o envio com segurança.")
                results[product.id] = result
                continue
            if remote and abs(remote[0] - net) <= NET_TOLERANCE and abs(remote[1] - gross) <= GROSS_TOLERANCE:
                result["state"] = "confirmed"
                product.sale_price, product.sale_price_vat = remote  # Snapshot local = valores lidos da Wintouch.
            else:
                found = f" A Wintouch tem {remote[0]} € sem IVA e {remote[1]} € com IVA." if remote else ""
                result.update(state="uncertain", error="A Wintouch não confirmou o novo preço na linha 1." + found)
            results[product.id] = result
    return results
