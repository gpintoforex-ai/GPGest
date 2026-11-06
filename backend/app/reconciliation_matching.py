"""Deterministic proposals only. Financial writes remain in the existing confirmation API."""
import hashlib
import itertools
import json
from collections import Counter

from .reconciliation import iso_day


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def elapsed(bank, item):
    return (iso_day(bank["date"]) - iso_day(item["date"])).days


def compatible(bank, item, tolerance=10, late=False):
    """`late`: aceita também registos anteriores ao movimento, fora da tolerância e até um ano."""
    return (bank["store_id"] == item["store_id"]
            and bank["amount_cents"] * item["amount_cents"] > 0
            and bank.get("currency", "EUR") == item.get("currency", "EUR")
            and (not item.get("account_id") or item["account_id"] == bank.get("account_id"))
            and (abs(elapsed(bank, item)) <= tolerance or late and 0 < elapsed(bank, item) <= 366))


def grouped(banks, internals, rules, named=None):
    """Bounded sum proposals: never silently treat a bounded search as exhaustive.
    `named`: por movimento, as palavras de entidades conhecidas que a descrição bancária refere."""
    from .reconciliation import entity_tokens, normalized
    named = named or {}
    words = {b["id"]: set(normalized(b["description"] + " " + b.get("reference", "")).split()) for b in banks}

    def same_entity(bank, item):
        # Quando a descrição refere uma entidade conhecida, só registos dessa entidade.
        return not named.get(bank["id"]) or bool(entity_tokens(item.get("entity") or "") & words[bank["id"]])

    def late_invoice(bank, item):
        # Débito direto de várias faturas do fornecedor identificado na descrição, pago depois delas.
        return (item["source"] == "purchase" and bool(named.get(bank["id"]))
                and compatible(bank, item, tolerance, late=True) and same_entity(bank, item))

    def one_payee(batch):
        # Uma transferência paga um só fornecedor: faturas de entidades diferentes nunca se somam.
        purchases = [r for r in batch if r["source"] == "purchase"]
        return not purchases or len(purchases) == len(batch) and len({normalized(r.get("entity") or "") for r in purchases}) == 1
    output = []
    eligible = [r for r in internals if r["available_cents"] and r["status"] != "ignorado"]
    limited = len(banks) > 200 or len(eligible) > 200
    for bank in banks[:200]:
        if not bank["available_cents"] or bank["status"] == "ignorado":
            continue
        tolerance = rules.get(bank.get("account_id"), {}).get("date_tolerance_days", 10)
        candidates = [r for r in eligible if not r.get("transfer")
                      and (compatible(bank, r, tolerance) and same_entity(bank, r) or late_invoice(bank, r))]
        # As mais próximas primeiro: a pesquisa limitada fica com as faturas mais recentes.
        candidates.sort(key=lambda r: abs(elapsed(bank, r)))
        if any(r["available_cents"] == bank["available_cents"] for r in candidates):
            continue
        limited |= len(candidates) > 12
        for count in (2, 3):
            for batch in itertools.combinations(candidates[:12], count):
                if sum(r["available_cents"] for r in batch) != bank["available_cents"] or not one_payee(batch):
                    continue
                late = max(abs(elapsed(bank, r)) for r in batch) > tolerance
                same_day = len({r["date"] for r in batch}) == 1
                dates = "/".join(reversed(min(r["date"] for r in batch).split("-")))
                output.append({"bank_id": bank["id"], "net_cents": bank["available_cents"],
                    "gross_cents": bank["available_cents"], "commission_cents": 0,
                    "kind": "pagamento", "confidence": "Média", "score": 55 + (10 if same_day else 0),
                    "reasons": [f"Soma de {count} registos igual ao remanescente bancário",
                                f"Faturas de {dates} ou posteriores, do fornecedor identificado na descrição" if late
                                else "Mesma loja, moeda e sentido; datas dentro da tolerância",
                                *(["Faturas do mesmo dia"] if same_day else []), "Agrupamento sujeito a confirmação"],
                    "lines": [{"source": r["source"], "source_id": r["id"], "amount_cents": r["available_cents"]} for r in batch]})
                if sum(1 for p in output if p["bank_id"] == bank["id"]) >= 3:
                    break
            if sum(1 for p in output if p["bank_id"] == bank["id"]) >= 3:
                break
    # Several statement lines for one economic record. Never net opposite directions.
    for item in eligible[:200]:
        if item.get("transfer"):
            continue
        possible = [b for b in banks if b["available_cents"] and b["status"] != "ignorado"
                    and compatible(b, item, rules.get(b.get("account_id"), {}).get("date_tolerance_days", 10)) and same_entity(b, item)]
        limited |= len(possible) > 12
        for pair in itertools.combinations(possible[:12], 2):
            if pair[0].get("account_id") != pair[1].get("account_id") or sum(b["available_cents"] for b in pair) != item["available_cents"]:
                continue
            output.append({"bank_id": pair[0]["id"], "bank_lines": [{"source": "bank", "source_id": b["id"], "amount_cents": b["available_cents"]} for b in pair],
                "net_cents": item["available_cents"], "gross_cents": item["available_cents"], "commission_cents": 0, "kind": "pagamento",
                "confidence": "Média", "score": 55, "reasons": ["Soma de dois movimentos da mesma conta igual ao registo interno", "Mesmo sentido, moeda e loja; requer confirmação"],
                "lines": [{"source": item["source"], "source_id": item["id"], "amount_cents": item["available_cents"]}]})
            break
    return output, limited


def classify(banks, internals, suggestions, cards, rejected=()):
    from .reconciliation import normalized
    by_bank = {r["id"]: r for r in banks}
    by_internal = {(r["source"], r["id"]): r for r in internals}
    all_proposals = []
    seen = set()
    for original in [*suggestions, *cards]:
        proposal = dict(original)
        bank = by_bank[proposal["bank_id"]]
        lines = proposal.get("lines") or [{"source": proposal["source"], "source_id": proposal["source_id"], "amount_cents": proposal["amount_cents"]}]
        bank_lines = proposal.get("bank_lines") or [{"source": "bank", "source_id": bank["id"], "amount_cents": proposal.get("net_cents", proposal.get("amount_cents"))}]
        signature = {"banks": [(l, by_bank[l["source_id"]]) for l in bank_lines],
                     "internals": [(l, by_internal[l["source"], l["source_id"]]) for l in lines]}
        proposal["proposal_id"] = fingerprint(signature)
        if proposal["proposal_id"] in rejected or proposal["proposal_id"] in seen:
            continue
        seen.add(proposal["proposal_id"])
        proposal["bank_lines"] = bank_lines
        proposal["classification"] = "provavel"
        if len(lines) == len(bank_lines) == 1:
            internal = by_internal[lines[0]["source"], lines[0]["source_id"]]
            reference = normalized(internal.get("reference", ""))
            identified = len(reference) >= 4 and reference in normalized(bank.get("description", "") + " " + bank.get("reference", ""))
            if (internal["source"] != "purchase" and identified and bank["available_cents"] == internal["available_cents"]
                    and internal.get("account_id") == bank.get("account_id") and internal.get("account_id")):
                proposal["classification"] = "exata"
            if internal.get("associated_cents"):
                proposal["reasons"] = [*proposal["reasons"], "Registo com associações parciais anteriores; apenas o remanescente é proposto"]
        all_proposals.append(proposal)
    bank_counts = Counter(l["source_id"] for p in all_proposals for l in p["bank_lines"])
    source_banks = {}
    for proposal in all_proposals:
        lines = proposal.get("lines") or [proposal]
        for line in lines:
            source_banks.setdefault((line["source"], line["source_id"]), set()).update(b["source_id"] for b in proposal["bank_lines"])
    for proposal in all_proposals:
        lines = proposal.get("lines") or [proposal]
        own_banks = {b["source_id"] for b in proposal["bank_lines"]}
        if any(bank_counts[b] > 1 for b in own_banks) or any(source_banks[(l["source"], l["source_id"])] - own_banks for l in lines):
            proposal["classification"] = "conflito"
            proposal["reasons"] = [*proposal["reasons"], "Existem alternativas concorrentes; escolha manualmente após conferir"]
    for bank in banks:
        related = [p for p in all_proposals if any(l["source_id"] == bank["id"] for l in p["bank_lines"])]
        bank["match_classification"] = ("conciliado" if bank["status"] == "reconciliado" else
            "ignorado" if bank["status"] == "ignorado" else "conflito" if any(p["classification"] == "conflito" for p in related)
            else "exata" if any(p["classification"] == "exata" for p in related) else "provavel" if related else "sem_correspondencia")
    return [p for p in all_proposals if "lines" not in p], [p for p in all_proposals if "lines" in p]
