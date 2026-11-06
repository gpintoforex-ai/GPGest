from collections import Counter
from copy import deepcopy
from io import BytesIO
import re

import pytest
from pypdf import PdfReader

from app.reconciliation_report import build_model, render_pdf
from test_bank_balances import case, bank, movement, match


@pytest.fixture
def snapshot():
    return {'period_start': '2026-01-01', 'period_end': '2026-01-31', 'bank_date': '2026-01-31', 'currency': 'EUR',
        'bank_rows': [
            {'id': '11111111-1111-4111-8111-111111111111', 'date': '2026-01-01', 'description': 'Recebimento de teste', 'amount_cents': 10000, 'associated_cents': 10000, 'remaining_cents': 0},
            {'id': '22222222-2222-4222-8222-222222222222', 'date': '2026-01-02', 'description': 'Pagamento parcial de teste', 'amount_cents': -6000, 'associated_cents': 2000, 'remaining_cents': 4000},
            {'id': '33333333-3333-4333-8333-333333333333', 'date': '2026-01-03', 'description': 'Pagamento pendente de teste', 'amount_cents': -15000, 'associated_cents': 0, 'remaining_cents': 15000}],
        'application_rows': [{'source': 'movement', 'id': '44444444-4444-4444-8444-444444444444', 'date': '2026-01-04', 'description': 'Transferência de teste', 'reference': 'TRF 123', 'amount_cents': 2000, 'associated_cents': 500, 'remaining_cents': 1500}],
        'statement_incoming_cents': 10000, 'statement_outgoing_cents': 21000, 'statement_opening_cents': 100000,
        'bank_balance_cents': 89000, 'book_balance_cents': None, 'difference_cents': None, 'unresolved_difference_cents': None,
        'justified_difference_cents': 0, 'comparable': False, 'comparison_reason': 'Falta saldo de abertura da aplicação.',
        'pending_bank': {'count': 2}, 'pending_application': {'count': 1}, 'closing_blockers': ['Faltam dados comparáveis'],
        'signature': 'a'*64, 'reconciliations': [{'id': '55555555-5555-4555-8555-555555555555', 'reason': 'Associação de teste', 'difference_cents': 0, 'commission_cents': 0,
            'lines': [{'source': 'bank', 'source_id': '11111111-1111-4111-8111-111111111111', 'amount_cents': 10000}]}]}


def contents(raw):
    return '\n'.join(p.extract_text() for p in PdfReader(BytesIO(raw)).pages)


def test_projection_preserves_every_amount_and_does_not_mutate_snapshot(snapshot):
    before = deepcopy(snapshot)
    model = build_model(snapshot)
    assert snapshot == before
    assert model['totals'] == {'count': 3, 'incoming_cents': 10000, 'outgoing_cents': 21000, 'associated_cents': 12000, 'remaining_cents': 19000}
    assert model['reconciled']['count'] == 1 and model['reconciled']['associated_cents'] == 10000
    assert model['pending']['count'] == 2 and model['pending']['remaining_cents'] == 19000
    assert model['transit_totals']['remaining_cents'] == 1500
    assert model['partial_count'] == 1
    assert model['priorities'][0]['remaining_cents'] == 15000
    assert Counter(r['id'] for rows in model['groups'].values() for r in rows) == Counter(r['id'] for r in snapshot['bank_rows'])


def test_sorting_keeps_references_stable_and_pending_is_not_an_error(snapshot):
    by_date = build_model(snapshot)
    by_amount = build_model(snapshot, sort_by='amount', descending=True)
    assert [r['amount_cents'] for r in by_amount['bank_rows']] == [-15000, 10000, -6000]
    assert {r['id']:r['report_ref'] for r in by_date['bank_rows']} == {r['id']:r['report_ref'] for r in by_amount['bank_rows']}
    assert by_date['status'] == 'Em análise' and by_date['status_tone'] != 'red'
    with pytest.raises(ValueError):
        build_model(snapshot, sort_by='invalid')


def test_missing_or_changed_balances_never_appear_as_complete(snapshot):
    closing = {'id': 'close', 'created_at': '2026-02-01', 'stale': False, 'reopened_at': None}
    assert build_model(snapshot, closing=closing)['status'] == 'Em análise'
    snapshot.update(comparable=True, book_balance_cents=90000, difference_cents=1000, unresolved_difference_cents=1000)
    assert build_model(snapshot, closing=closing)['status'] == 'Com divergências'
    snapshot.update(book_balance_cents=89000, difference_cents=0, unresolved_difference_cents=0, closing_blockers=[])
    assert build_model(snapshot, closing=closing)['status'] == 'Fechada com ressalvas'
    for row in snapshot['bank_rows']:
        row.update(associated_cents=abs(row['amount_cents']), remaining_cents=0)
    snapshot['pending_bank']['count'] = 0
    assert build_model(snapshot, closing=closing)['status'] == 'Fechada'
    assert build_model(snapshot, closing={**closing, 'stale': True})['status'] == 'Em análise'
    assert build_model(snapshot, closing={**closing, 'reopened_at': '2026-02-02'})['status'] == 'Em análise'


def test_inconsistent_totals_raise_visible_alert_instead_of_silently_changing_values(snapshot):
    snapshot['statement_incoming_cents'] = 999
    model = build_model(snapshot)
    assert model['integrity_issues'] and model['status'] == 'Com divergências'
    assert model['state']['statement_incoming_cents'] == 999 and model['totals']['incoming_cents'] == 10000


def test_main_pdf_has_readable_references_and_audit_is_optional(snapshot):
    model = build_model(snapshot, company={'name': 'Empresa de teste', 'nif': '500000000'}, account={'bank': 'Banco de teste', 'name': 'Conta de teste', 'currency': 'EUR', 'iban_last4': '1234'})
    plain = contents(render_pdf(model))
    assert 'B0001' in plain and 'T0001' in plain and 'R0001' in plain
    assert not re.search(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', plain)
    assert 'Em análise' in plain and 'Não disponível' in plain
    assert 'Parcial' in plain and 'Em trânsito' in plain
    assert 'Empresa de teste' in plain and 'Banco de teste' in plain
    technical = contents(render_pdf(model, audit=True))
    assert 'Anexo técnico opcional' in technical and snapshot['bank_rows'][0]['id'] in technical


def test_pagination_repeats_headers_preserves_rows_and_avoids_nearly_blank_pages(snapshot):
    snapshot['bank_rows'] = [{**snapshot['bank_rows'][2], 'id': str(i), 'description': f'Movimento de teste {i} - descrição extensa para verificar a tabela e a continuidade entre páginas.'} for i in range(100)]
    snapshot.update(statement_incoming_cents=0, statement_outgoing_cents=1500000, pending_bank={'count': 100})
    model = build_model(snapshot)
    reader = PdfReader(BytesIO(render_pdf(model)))
    assert len(reader.pages) >= 4
    for i, page in enumerate(reader.pages, 1):
        content = page.extract_text()
        assert 'RELATÓRIO DE RECONCILIAÇÃO BANCÁRIA' in content
        assert f'Página {i} de {len(reader.pages)}' in content
        assert float(page.mediabox.width) == pytest.approx(595.2756, abs=.1)
        if re.search(r'B\d{4}', content) and 'Intervenção sugerida' not in content:
            assert 'Data' in content and 'Valor' in content and 'Estado' in content
        assert len(content) > 350, f'Unexpected nearly blank page {i}'
    content = '\n'.join(p.extract_text() for p in reader.pages)
    for row in model['bank_rows']:
        assert row['report_ref'] in content


def test_huge_description_and_markup_are_printable_without_loss(snapshot):
    snapshot['bank_rows'][0]['description'] = '<invoice & company> ' + ('Descrição longa. ' * 500)
    content = contents(render_pdf(build_model(snapshot)))
    assert '<invoice & company>' in content
    # A repeated phrase can straddle pages, with the footer/header between its words.
    # Count both words to verify that the text was preserved across the page break.
    assert len(re.findall(r'\bDescrição\b', content)) == 500
    assert len(re.findall(r'\blonga\.', content)) == 500


def test_stale_current_report_is_not_described_as_the_original_snapshot(snapshot):
    closing = {'id':'test-close', 'stale':True, 'created_at':'2026-02-01', 'reopened_at':None}
    current = contents(render_pdf(build_model(snapshot, closing={**closing, 'is_snapshot':False})))
    historical = contents(render_pdf(build_model(snapshot, closing={**closing, 'is_snapshot':True})))
    assert 'Relatório de consulta' in current and 'apresenta os dados atuais' in current
    assert 'Relatório do fecho registado' in historical and 'preserva os valores do snapshot original' in historical


def test_report_api_sort_options_and_read_only_behavior(case):
    c, ids, factory = case
    m = movement(case, 25000); b = bank(case, 25000)
    match(case, b, m, 25000)
    bank(case, -12345, description='Pendente com valor distinto')
    path = f'/api/reconciliation/accounts/{ids["account"]}/report'
    params = {'start':'2026-01-01', 'end':'2026-01-31', 'format':'pdf', 'sort_by':'amount', 'descending':True, 'audit':True}
    before = c.get(f'/api/accounts/{ids["account"]}/transactions').json()
    result = c.get(path, params=params)
    assert result.status_code == 200, result.text
    assert 'Anexo técnico opcional' in contents(result.content)
    assert 'Ordenação por valor absoluto, decrescente' in contents(result.content)
    assert c.get(f'/api/accounts/{ids["account"]}/transactions').json() == before
    assert c.get(path, params={**params, 'sort_by':'invalid'}).status_code == 422
