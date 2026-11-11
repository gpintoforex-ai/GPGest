"""Motor de recorrências comum (obrigações e prazos, contratos, viaturas e, depois, orçamento).

Gera as datas de uma regra sem gravar nada: quem a usa guarda cada ocorrência uma vez, com uma
chave estável (a data da regra), para nunca duplicar registos. Meses mais curtos limitam o dia ao
último dia do mês, sempre a partir do dia original (31 jan → 28 fev → 31 mar).
"""

import calendar
from datetime import date, timedelta

PERIODICITIES = {
    "unica": "Única",
    "semanal": "Semanal",
    "mensal": "Mensal",
    "bimestral": "Bimestral",
    "trimestral": "Trimestral",
    "semestral": "Semestral",
    "anual": "Anual",
    "personalizada": "Personalizada (de N em N meses)",
}
MONTHS = {"mensal": 1, "bimestral": 2, "trimestral": 3, "semestral": 6, "anual": 12}
LIMIT = 2000  # Segurança: nunca gera mais do que este número de datas numa chamada.


def add_months(anchor: date, months: int) -> date:
    number = anchor.year * 12 + anchor.month - 1 + months
    year, month = divmod(number, 12)
    month += 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def step_months(periodicity: str, every_months: int | None = None) -> int | None:
    if periodicity == "personalizada":
        if not every_months or not 1 <= every_months <= 120:
            raise ValueError("Indique de quantos em quantos meses (1 a 120).")
        return every_months
    return MONTHS.get(periodicity)


def dates(start: date, periodicity: str, until: date, *, end: date | None = None, every_months: int | None = None, since: date | None = None):
    """Datas da regra entre `since` (inclusive; por omissão o início) e min(`until`, `end`)."""
    if periodicity not in PERIODICITIES:
        raise ValueError("Periodicidade desconhecida.")
    stop = min(until, end) if end else until
    since = since or start
    if periodicity == "unica":
        return [start] if since <= start <= stop else []
    result = []
    if periodicity == "semanal":
        # Salta diretamente para a primeira semana relevante (sem percorrer anos de semanas).
        skip = max(0, (since - start).days // 7)
        current = start + timedelta(days=7 * skip)
        while current <= stop and len(result) < LIMIT:
            if current >= since:
                result.append(current)
            current += timedelta(days=7)
        return result
    months = step_months(periodicity, every_months)
    elapsed = (since.year - start.year) * 12 + since.month - start.month
    index = max(0, elapsed // months - 1)
    while len(result) < LIMIT:
        current = add_months(start, index * months)
        if current > stop:
            break
        if current >= since:
            result.append(current)
        index += 1
    return result


def describe(periodicity: str, every_months: int | None = None) -> str:
    if periodicity == "personalizada" and every_months:
        return f"De {every_months} em {every_months} meses"
    return PERIODICITIES.get(periodicity, periodicity)
