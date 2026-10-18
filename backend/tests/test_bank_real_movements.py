from datetime import datetime, timezone
from app.models import BankAccount, Movement, Register, User, Category
from sqlalchemy import select


def test_bank_movement_without_cash_session_is_listed(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        store_id = db.get(Register, ids["registers"][0]).store_id
        bank = BankAccount(store_id=store_id, name="Conta teste", bank="Banco", iban_last4="1234")
        db.add(bank)
        db.flush()
        row = Movement(request_key="bank-real-test", bank_account_id=bank.id, user_id=db.scalar(select(User.id).where(User.role == "administrador")), category_id=db.scalar(select(Category.id)), kind="receita", payment="transferencia", amount_cents=2500, direction=1, occurred_at=datetime.now(timezone.utc))
        db.add(row)
        db.flush()
        key = row.id
    result = client.get("/api/movements?all_records=true")
    assert result.status_code == 200
    listed = next(r for r in result.json() if r["id"] == key)
    assert listed["register_id"] is None
    assert listed["bank_store_id"] == store_id
    assert listed["bank_account_name"] == "Conta teste"

