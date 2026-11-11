"""Read-only A4 presentation of the existing reconciliation snapshot. Amounts remain integer cents."""
from datetime import datetime
from html import escape
import io
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import CondPageBreak, KeepTogether, LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BLUE = colors.HexColor('#1854A0')
INK = colors.HexColor('#162A43')
MUTED = colors.HexColor('#627286')
EDGE = colors.HexColor('#DFE7F0')
GREEN = colors.HexColor('#18714A')
AMBER = colors.HexColor('#94600B')
RED = colors.HexColor('#B43235')
PALE = {'blue': '#EFF5FD', 'green': '#EDF8F1', 'amber': '#FFF7E7', 'red': '#FDEFF0', 'gray': '#F2F4F7'}
TONES = {'blue': BLUE, 'green': GREEN, 'amber': AMBER, 'red': RED, 'gray': MUTED}
UUID = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)
WIDTH = A4[0] - 64


def text(value, technical=False):
    value = str(value if value is not None else '')
    if not technical:
        value = UUID.sub('[referência no anexo técnico]', value)
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)
    return escape(value.translate(str.maketrans({'\u2011': '-', '\u2013': '-', '\u2014': '-'}))).replace('\n', '<br/>')


def money(value):
    if value is None:
        return 'Não disponível'
    sign = '-' if value < 0 else ''
    units, cents = divmod(abs(int(value)), 100)
    return sign + f'{units:,}'.replace(',', ' ') + f',{cents:02d}'


def day(value):
    if not value:
        return 'Não disponível'
    raw = str(value)[:10]
    return '/'.join(reversed(raw.split('-'))) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw) else raw


def build_model(state, *, company=None, account=None, closing=None, sort_by='date', descending=False):
    """Project presentation groups without mutating a row, snapshot, balance or association."""
    if sort_by not in {'date', 'amount'}:
        raise ValueError('Ordenação inválida.')
    company, account = company or {}, account or {}
    bank_rows = [dict(r) for r in state['bank_rows']]
    transit = [dict(r) for r in state['application_rows'] if r['remaining_cents']]
    # Stable, human references do not change when the user switches the report ordering.
    stable_banks = sorted(bank_rows, key=lambda r: (r['date'], str(r['id'])))
    references = {r['id']: f'B{i:04d}' for i, r in enumerate(stable_banks, 1)}
    for row in bank_rows:
        row['report_ref'] = references[row['id']]
        row['report_status'] = 'Conciliado' if not row['remaining_cents'] else 'Parcial' if row['associated_cents'] else 'Pendente'
    stable_transit = sorted(transit, key=lambda r: (r['date'], r['source'], str(r['id'])))
    for i, row in enumerate(stable_transit, 1):
        row['report_ref'] = f'T{i:04d}'
        row['report_status'] = 'Em trânsito'
    sort_key = (lambda r: (abs(r['amount_cents']), r['date'], r['report_ref'])) if sort_by == 'amount' else (lambda r: (r['date'], r['report_ref']))
    bank_rows.sort(key=sort_key, reverse=descending)
    transit.sort(key=sort_key, reverse=descending)
    groups = {name: [r for r in bank_rows if r['report_status'] == name] for name in ['Pendente', 'Parcial', 'Conciliado']}
    def totals(rows):
        return {'count': len(rows), 'incoming_cents': sum(max(r['amount_cents'], 0) for r in rows),
            'outgoing_cents': sum(max(-r['amount_cents'], 0) for r in rows),
            'associated_cents': sum(r['associated_cents'] for r in rows), 'remaining_cents': sum(r['remaining_cents'] for r in rows)}
    full = totals(bank_rows)
    pending = totals(groups['Pendente'] + groups['Parcial'])
    issues = []
    for key in ['incoming_cents', 'outgoing_cents']:
        if state.get('statement_' + key) != full[key]:
            issues.append(f'Total de {"entradas" if key == "incoming_cents" else "saídas"} do resumo difere da soma das linhas: resumo {money(state.get("statement_" + key))}; linhas {money(full[key])}.')
    if state.get('pending_bank', {}).get('count') != pending['count']:
        issues.append('A quantidade de movimentos pendentes do resumo difere das linhas do extrato.')
    comparable = bool(state.get('comparable') and state.get('book_balance_cents') is not None
        and state.get('bank_balance_cents') is not None and state.get('unresolved_difference_cents') is not None)
    difference = state.get('unresolved_difference_cents') if comparable else None
    confirmed_differences = [g for g in state.get('reconciliations', []) if g['difference_cents']]
    active_close = bool(closing and not closing.get('reopened_at') and not closing.get('stale'))
    if issues or difference:
        status, tone = 'Com divergências', 'red'
    elif active_close and comparable:
        status, tone = ('Fechada com ressalvas', 'amber') if state.get('closing_blockers') or pending['count'] else ('Fechada', 'green')
    else:
        status, tone = 'Em análise', 'blue'
    pending_rows = groups['Pendente'] + groups['Parcial']
    priorities = sorted(pending_rows, key=lambda r: (-r['remaining_cents'], r['date'], r['report_ref']))[:5]
    recs = [{**g, 'report_ref': f'R{i:04d}'} for i, g in enumerate(state.get('reconciliations', []), 1)]
    return {'state': state, 'company': dict(company), 'account': dict(account), 'closing': closing,
        'bank_rows': bank_rows, 'transit': transit, 'groups': groups, 'totals': full,
        'pending': pending, 'reconciled': totals(groups['Conciliado']), 'transit_totals': totals(transit),
        'partial_count': len(groups['Parcial']), 'comparable': comparable, 'difference_cents': difference,
        'status': status, 'status_tone': tone, 'priorities': priorities, 'integrity_issues': issues,
        'reconciliations': recs, 'confirmed_differences': confirmed_differences,
        'sort_by': sort_by, 'descending': descending}


def _fonts():
    if 'IAZELA' not in pdfmetrics.getRegisteredFontNames():
        directory = Path(rl_config.__file__).parent / 'fonts'
        pdfmetrics.registerFont(TTFont('IAZELA', str(directory / 'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('IAZELA-Bold', str(directory / 'VeraBd.ttf')))
        pdfmetrics.registerFontFamily('IAZELA', normal='IAZELA', bold='IAZELA-Bold', italic='IAZELA', boldItalic='IAZELA-Bold')


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        count = len(self.pages)
        for state in self.pages:
            self.__dict__.update(state)
            self.setStrokeColor(EDGE); self.line(32, 39, A4[0] - 32, 39)
            self.setFont('IAZELA', 7); self.setFillColor(MUTED)
            self.drawString(32, 25, 'IAZELA | Reconciliação bancária | Uso interno')
            self.drawRightString(A4[0] - 32, 25, f'Página {self._pageNumber} de {count}')
            super().showPage()
        super().save()


def render_pdf(model, *, audit=False, generated_at=None):
    _fonts()
    generated_at = generated_at or datetime.now(ZoneInfo('Europe/Lisbon'))
    state, account, company = model['state'], model['account'], model['company']
    currency = account.get('currency') or state.get('currency', 'EUR')
    styles = {
        'body': ParagraphStyle('body', fontName='IAZELA', fontSize=8, leading=12, textColor=INK, spaceAfter=5, splitLongWords=True),
        'small': ParagraphStyle('small', fontName='IAZELA', fontSize=7, leading=10, textColor=MUTED, spaceAfter=3),
        'cell': ParagraphStyle('cell', fontName='IAZELA', fontSize=7.8, leading=11, textColor=INK),
        'number': ParagraphStyle('number', fontName='IAZELA', fontSize=7.8, leading=11, textColor=INK, alignment=TA_RIGHT),
        'heading': ParagraphStyle('heading', fontName='IAZELA-Bold', fontSize=12, leading=16, textColor=BLUE, spaceBefore=15, spaceAfter=8, keepWithNext=True),
        'title': ParagraphStyle('title', fontName='IAZELA-Bold', fontSize=22, leading=27, textColor=INK, spaceAfter=6),
    }
    def p(value, style='body', technical=False):
        return Paragraph(text(value, technical), styles[style])
    def rich(value, style='body'):
        return Paragraph(value, styles[style])
    def section(title):
        return p(title, 'heading')
    def callout(title, message, tone='blue'):
        box = Table([[rich(f'<font color="{TONES[tone].hexval()}"><b>{text(title)}</b></font><br/>{text(message)}')]], colWidths=[WIDTH])
        box.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor(PALE[tone])), ('BOX', (0,0), (-1,-1), .5, EDGE), ('LEFTPADDING', (0,0), (-1,-1), 11), ('RIGHTPADDING', (0,0), (-1,-1), 11), ('TOPPADDING', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 7)]))
        return box
    def kpi(label, value, note='', tone='blue'):
        content = rich(f'<font size="7" color="{MUTED.hexval()}">{text(label)}</font><br/><font size="13" color="{TONES[tone].hexval()}"><b>{text(value)}</b></font><br/><font size="6.5" color="{MUTED.hexval()}">{text(note)}</font>')
        table = Table([[content]], colWidths=[(WIDTH - 16)/3])
        table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor(PALE[tone])), ('BOX', (0,0), (-1,-1), .4, EDGE), ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10), ('TOPPADDING', (0,0), (-1,-1), 9), ('BOTTOMPADDING', (0,0), (-1,-1), 8)]))
        return table
    def card_row(cards):
        t = Table([[cards[0], '', cards[1], '', cards[2]]], colWidths=[(WIDTH-16)/3, 8, (WIDTH-16)/3, 8, (WIDTH-16)/3])
        t.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING', (0,0), (-1,-1), 0), ('RIGHTPADDING', (0,0), (-1,-1), 0), ('TOPPADDING', (0,0), (-1,-1), 0), ('BOTTOMPADDING', (0,0), (-1,-1), 0)]))
        return t
    def movement_table(rows, label, tone):
        headers = ['Data', 'Referência / descrição', 'Entrada', 'Saída', 'Valor\nassociado', 'Valor\nremanescente', 'Estado']
        body = [[p(h, 'cell') for h in headers]]
        for row in rows:
            description = f'<b>{text(row["report_ref"])}</b> · {text(row["description"])}'
            if row.get('reference'):
                description += '<br/>' + text(row['reference'])
            body.append([p(day(row['date']), 'cell'), rich(description, 'cell'),
                p(money(row['amount_cents']) if row['amount_cents'] > 0 else '-', 'number'),
                p(money(-row['amount_cents']) if row['amount_cents'] < 0 else '-', 'number'),
                p(money(row['associated_cents']), 'number'), p(money(row['remaining_cents']), 'number'),
                rich(f'<font color="{TONES[tone].hexval()}"><b>{text(row["report_status"])}</b></font>', 'cell')])
        totals = {'incoming': sum(max(r['amount_cents'], 0) for r in rows), 'outgoing': sum(max(-r['amount_cents'], 0) for r in rows),
            'associated': sum(r['associated_cents'] for r in rows), 'remaining': sum(r['remaining_cents'] for r in rows)}
        body.append([p('Subtotal', 'cell'), p(f'{len(rows)} movimento(s)', 'cell'), *[p(money(totals[k]), 'number') for k in ['incoming','outgoing','associated','remaining']], p('', 'cell')])
        widths = [58, WIDTH-58-47-47-60-68-60, 47, 47, 60, 68, 60]
        table = LongTable(body, colWidths=widths, repeatRows=1, splitByRow=1, splitInRow=1, hAlign='CENTER')
        table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'), ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#EAF1FB')),
            ('BACKGROUND', (0,1), (-1,-2), colors.HexColor(PALE[tone])), ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#F0F4F9')),
            ('LINEBELOW', (0,0), (-1,0), .7, BLUE), ('LINEBELOW', (0,1), (-1,-2), .35, EDGE), ('LINEABOVE', (0,-1), (-1,-1), .7, EDGE),
            ('LEFTPADDING', (0,0), (-1,-1), 5), ('RIGHTPADDING', (0,0), (-1,-1), 5), ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7)]))
        heading = section(f'{label} ({len(rows)})')
        heading.keepWithNext = False
        return [CondPageBreak(105), heading, table]
    story = []
    story += [p('Reconciliação bancária', 'title'), p('RESUMO EXECUTIVO | ' + ('Relatório do fecho registado' if model['closing'] and model['closing'].get('is_snapshot') else 'Relatório de consulta'), 'small')]
    identity = Table([[rich(f'<b>{text(company.get("name") or "Empresa não identificada")}</b><br/>NIF: {text(company.get("nif") or "Não disponível")}'),
        rich(f'<b>{text(account.get("bank") or state.get("bank") or "Banco não identificado")}</b><br/>{text(account.get("name") or state.get("name"))}<br/>{text("Conta com terminação " + account["iban_last4"] if account.get("iban_last4") else "Terminação da conta não disponível")}'),
        rich(f'<b>{day(state["period_start"])} a {day(state["period_end"])}</b><br/>Montantes em {text(currency)}<br/>Emitido em {generated_at.strftime("%d/%m/%Y %H:%M")} (Lisboa)')]], colWidths=[WIDTH*.34, WIDTH*.34, WIDTH*.32])
    identity.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'), ('LINEBELOW',(0,0),(-1,-1),.5,EDGE), ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),10), ('BOTTOMPADDING',(0,0),(-1,-1),10)]))
    story += [identity, Spacer(1,10), callout(model['status'],
        'Saldo contabilístico e extrato sem informação suficiente para validar a reconciliação.' if not model['comparable'] else
        'Existe uma diferença calculável ou uma inconsistência identificada. Consulte os alertas e as justificações.' if model['status_tone']=='red' else
        'Existem pendências ou condições a rever. A confirmação de movimentos mantém a sua operação própria.' if model['status']!='Fechada' else
        'Fecho registado, com saldos comparáveis e sem divergências por resolver no snapshot.', model['status_tone']), Spacer(1,10)]
    story += [card_row([kpi('Saldo inicial do extrato', money(state.get('statement_opening_cents')), 'Inferido das linhas com saldos contínuos'),
        kpi('Saldo final do extrato', money(state.get('bank_balance_cents')), 'Referência: '+day(state.get('bank_date'))),
        kpi('Saldo contabilístico disponível', money(state.get('book_balance_cents')), 'Calculado pelos registos da aplicação')]), Spacer(1,8),
        card_row([kpi('Total de entradas', money(state.get('statement_incoming_cents')), currency, 'green'),
            kpi('Total de saídas', money(state.get('statement_outgoing_cents')), currency),
            kpi('Diferença por resolver', money(model['difference_cents']), 'Após diferenças justificadas' if model['comparable'] else 'Comparação indisponível', 'red' if model['difference_cents'] else 'gray')]), Spacer(1,8),
        card_row([kpi('Conciliados na íntegra', str(model['reconciled']['count'])+' movimento(s)', money(model['reconciled']['associated_cents'])+' '+currency, 'green'),
            kpi('Pendentes (inclui parciais)', str(model['pending']['count'])+' movimento(s)', money(model['pending']['remaining_cents'])+' '+currency+' por associar', 'amber'),
            kpi('Em trânsito', str(model['transit_totals']['count'])+' registo(s)', money(model['transit_totals']['remaining_cents'])+' '+currency+' remanescente', 'gray')]), Spacer(1,8)]
    story.append(p('O saldo contabilístico apresentado é calculado pelos registos da aplicação e pela referência inicial configurada. Não é um saldo oficial fornecido pelo ERP. O saldo inicial inferido não garante que todos os movimentos do período tenham sido importados.', 'small'))
    story.append(p(f'Diferença bruta dos saldos: {money(state.get("difference_cents") if model["comparable"] else None)} {currency}. Diferenças justificadas no período: {money(state.get("justified_difference_cents"))} {currency}.', 'small'))
    if model['closing']:
        closing = model['closing']
        message = f'Fecho de {day(closing.get("created_at"))}, por {closing.get("created_by") or "utilizador registado"}. {closing.get("reason") or "Sem observações adicionais."}'
        if closing.get('reopened_at'):
            message += ' Este fecho foi reaberto em '+day(closing['reopened_at'])+'.'
        if closing.get('stale'):
            message += (' Os dados atuais mudaram após este fecho; este documento preserva os valores do snapshot original.' if closing.get('is_snapshot')
                else ' Os dados atuais mudaram após este fecho. Este documento apresenta os dados atuais; o relatório original do fecho mantém-se preservado.')
        story += [callout('Registo do fecho', message, 'gray'), Spacer(1,8)]
    story += [section('Alertas e prioridades')]
    if not model['comparable']:
        story += [callout('Validação do saldo por completar', state.get('comparison_reason') or 'Falta informação comparável da mesma conta, moeda e data.', 'amber'), Spacer(1,6)]
    if model['difference_cents']:
        story += [callout('Diferença de saldos identificada', f'{money(model["difference_cents"])} {currency} por resolver. A diferença é calculada; a sua causa necessita de conferência. Não implica que um movimento pendente seja um erro.', 'red'), Spacer(1,6)]
    for issue in model['integrity_issues']:
        story += [callout('Inconsistência nos dados do resumo', issue, 'red'), Spacer(1,6)]
    if model['partial_count']:
        story += [p(f'{model["partial_count"]} movimento(s) parcialmente associado(s). Confira o remanescente nas tabelas seguintes.')]
    if model['priorities']:
        story += [p('Maiores valores pendentes - prioridade por montante remanescente. Pendência não significa erro.', 'small')]
        rows = [[p('Referência / data', 'cell'), p('Intervenção sugerida', 'cell'), p('Remanescente', 'cell')]]
        for r in model['priorities']:
            excerpt = r['description'] if len(r['description']) <= 180 else r['description'][:177] + '...'
            rows.append([p(r['report_ref']+' | '+day(r['date']), 'cell'), p(excerpt+'\n'+('Completar associação parcial' if r['associated_cents'] else 'Conferir a correspondência e associar'), 'cell'), p(money(r['remaining_cents']), 'number')])
        table = LongTable(rows, colWidths=[95, WIDTH-175, 80], repeatRows=1, splitInRow=1)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor(PALE['amber'])),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.4,EDGE),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story += [table]
    elif not model['difference_cents'] and not model['integrity_issues']:
        story += [p('Sem movimentos bancários pendentes no extrato apresentado. A validação do saldo depende sempre dos dados contabilísticos disponíveis.')]
    story += [section('Movimentos bancários'), p(f'{len(model["bank_rows"])} movimento(s). Ordenação por {"valor absoluto" if model["sort_by"] == "amount" else "data"}, {"decrescente" if model["descending"] else "crescente"}. Montantes em {currency}. As referências B identificam as linhas do extrato.', 'small')]
    for status, label, tone in [('Parcial','Parcialmente associados','amber'), ('Pendente','Pendentes de conciliação','amber'), ('Conciliado','Conciliados','green')]:
        if model['groups'][status]:
            story += movement_table(model['groups'][status], label, tone)
    if not model['bank_rows']:
        story += [p('Não existem movimentos bancários no período selecionado.')]
    total = model['totals']
    story += [Spacer(1,9), callout('Controlo de totais do extrato', f'{total["count"]} movimento(s) | Entradas {money(total["incoming_cents"])} | Saídas {money(total["outgoing_cents"])} | Associado {money(total["associated_cents"])} | Remanescente {money(total["remaining_cents"])} ({currency}). Os valores associados incluem pagamentos parciais.', 'blue')]
    if model['transit']:
        story += [section('Movimentos em trânsito'), p('Registos da aplicação com valor remanescente. Não são novas linhas do extrato e não são somados aos totais bancários. As referências T identificam estes registos.', 'small')]
        story += movement_table(model['transit'], 'Em trânsito', 'gray')
    if model['reconciliations']:
        story += [section('Associações e justificações registadas')]
        for g in model['reconciliations']:
            # UUIDs and source identifiers are reserved for the optional technical annex.
            story += [KeepTogether([p(g['report_ref']+' | Diferença '+money(g['difference_cents'])+' | Comissão '+money(g['commission_cents'])+' '+currency),
                p(g.get('reason') or 'Sem observações adicionais.', 'small')])]
            if g.get('attributable') is False:
                story += [p('Este grupo abrange outras contas ou datas. A diferença do grupo não é integralmente atribuída a este período.', 'small')]
    if audit:
        story += [PageBreak(), section('Anexo técnico opcional'), p('Identificadores de origem e informação de auditoria. Este anexo não acrescenta movimentos aos totais.', 'small')]
        identifiers = [['Referência', 'Identificador técnico / origem']]
        identifiers += [[r['report_ref'], f'bank | {r["id"]}'] for r in model['bank_rows']]
        identifiers += [[r['report_ref'], f'{r["source"]} | {r["id"]}'] for r in model['transit']]
        identifiers += [[g['report_ref'], g['id']] for g in model['reconciliations']]
        table = LongTable([[p(c, 'cell', technical=True) for c in row] for row in identifiers], colWidths=[65, WIDTH-65], repeatRows=1, splitInRow=1)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor(PALE['gray'])),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.4,EDGE),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story += [table, Spacer(1,9), p('Assinatura do snapshot: '+str(state.get('signature') or 'Não disponível'), 'small', technical=True)]
        if model['closing']:
            story += [p('Identificador do fecho: '+model['closing']['id'], 'small', technical=True)]
        for g in model['reconciliations']:
            story += [section(g['report_ref']+' - linhas de origem')]
            story += [p(g.get('reason') or 'Sem observações adicionais.', 'small', technical=True)]
            for line in g.get('lines', []):
                story += [p(f'{line["source"]} | {line["source_id"]} | {money(line["amount_cents"])} {currency}', 'small', technical=True)]
        for row in [*model['bank_rows'], *model['transit']]:
            original = row.get('description', '') + '\n' + row.get('reference', '')
            if UUID.search(original):
                story += [p(row['report_ref']+' | Texto de origem: '+original, 'small', technical=True)]
    stream = io.BytesIO()
    document = SimpleDocTemplate(stream, pagesize=A4, rightMargin=32, leftMargin=32, topMargin=79, bottomMargin=54, title='IAZELA - Reconciliação bancária', author='IAZELA')
    logo = Path(__file__).resolve().parents[2] / 'frontend/public/brand/iazela/IAZELA_logo_transparente.png'
    def header(c, doc):
        c.saveState()
        if logo.exists():
            c.drawImage(str(logo), 31, A4[1]-60, width=88, height=36, preserveAspectRatio=True, mask='auto')
        else:
            c.setFillColor(BLUE); c.setFont('IAZELA-Bold',17); c.drawString(32,A4[1]-42,'IAZELA')
        c.setFillColor(BLUE); c.setFont('IAZELA-Bold',8)
        c.drawRightString(A4[0]-32,A4[1]-32,'RELATÓRIO DE RECONCILIAÇÃO BANCÁRIA')
        c.setFont('IAZELA',7); c.setFillColor(MUTED)
        c.drawRightString(A4[0]-32,A4[1]-47,f'{day(state["period_start"])} a {day(state["period_end"])} | {currency}')
        c.setStrokeColor(BLUE); c.setLineWidth(1.2); c.line(32,A4[1]-65,A4[0]-32,A4[1]-65)
        c.restoreState()
    document.build(story, onFirstPage=header, onLaterPages=header, canvasmaker=NumberedCanvas)
    return stream.getvalue()
