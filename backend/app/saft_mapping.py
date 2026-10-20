"""PaymentMechanism recommendations; never infer a settlement account.

Source: Portaria 302/2016, fields 4.1.4.20.6.1 and 4.4.4.10.1.
"""
import unicodedata

DESCRIPTIONS = {
    "CC": "Cartão crédito", "CD": "Cartão débito", "CH": "Cheque bancário",
    "CI": "Crédito documentário internacional", "CO": "Cheque ou cartão oferta",
    "CS": "Compensação de saldos em conta corrente",
    "DE": "Dinheiro eletrónico (ex.: cartões de fidelidade ou pontos)",
    "LC": "Letra comercial", "MB": "Referências de pagamento para Multibanco",
    "NU": "Numerário", "OU": "Outros meios aqui não assinalados", "PR": "Permuta de bens",
    "TB": "Transferência bancária ou débito direto autorizado",
    "TR": "Títulos de compensação extrassalarial (ex.: refeição, educação)",
}


def normalized(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                            if not unicodedata.combining(c)).split())


def recommendations(methods, saved):
    active = {m["id"]: m for m in methods if m.get("active", True)}
    aliases = {
        "NU": {"numerario"}, "CC": {"cartao credito", "cartao de credito", "cartao", "multibanco / tpa", "multibanco / cartao"},
        "CD": {"cartao debito", "cartao de debito", "cartao", "multibanco / tpa", "multibanco / cartao"},
        "TB": {"transferencia", "transferencia bancaria", "debito direto"},
        "CH": {"cheque", "cheque bancario"}, "MB": {"referencia multibanco", "referencias multibanco", "referencias de pagamento para multibanco"},
    }
    result = {}
    for code, description in DESCRIPTIONS.items():
        candidates = [m for m in active.values() if normalized(m["name"]) in
                      (aliases.get(code, set()) | {normalized(description)})]
        previous = active.get(saved.get(code))
        reason = "Correspondência anteriormente guardada nesta empresa para pagamentos SAF-T."
        if previous:
            candidates = [previous]
        else:
            reason = "Significado oficial compatível com um único meio de pagamento ativo da empresa."
        safe = len(candidates) == 1
        result[code] = {
            "id": candidates[0]["id"] if safe else None,
            "name": candidates[0]["name"] if safe else "Requer seleção" if candidates else "Sem sugestão segura",
            "safe": safe,
            "reason": reason if safe else "Existem vários meios compatíveis; escolha o utilizado na origem." if candidates else "Não existe um meio ativo inequivocamente compatível com o significado oficial.",
        }
    return result
