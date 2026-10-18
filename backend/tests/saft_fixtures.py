"""Fictitious SAF-T 1.04_01 documents, validated against the bundled AT XSD."""

from xml.sax.saxutils import escape

NS = "urn:OECD:StandardAuditFile-Tax:PT_1.04_01"


def address():
    return "<AddressDetail>Rua de Teste</AddressDetail><City>Lisboa</City><PostalCode>1000-001</PostalCode><Country>PT</Country>"


def customer(code="C1", nif="500000002", name="Cliente fictício"):
    return f"<Customer><CustomerID>{code}</CustomerID><AccountID>Desconhecido</AccountID><CustomerTaxID>{nif}</CustomerTaxID><CompanyName>{escape(name)}</CompanyName><BillingAddress>{address()}</BillingAddress><SelfBillingIndicator>0</SelfBillingIndicator></Customer>"


def payment(value="12.30", code="NU", day="2026-01-10", tag="Payment"):
    return (
        f"<{tag}>"
        + (f"<PaymentMechanism>{code}</PaymentMechanism>" if code else "")
        + f"<PaymentAmount>{value}</PaymentAmount><PaymentDate>{day}</PaymentDate></{tag}>"
    )


def invoice(typ="FT", number=1, value="12.30", status="N", payments="", customer_id="C1"):
    # value=12.30 => net 10, IVA 2.30; amount may be overridden only with same tax ratio.
    from decimal import Decimal

    net = (Decimal(value) / Decimal("1.23")).quantize(Decimal(".01"))
    tax = Decimal(value) - net
    side = "DebitAmount" if typ == "NC" else "CreditAmount"
    return f"""<Invoice><InvoiceNo>{typ} TESTE/{number}</InvoiceNo><ATCUD>0</ATCUD>
    <DocumentStatus><InvoiceStatus>{status}</InvoiceStatus><InvoiceStatusDate>2026-01-10T10:00:00</InvoiceStatusDate><SourceID>TEST</SourceID><SourceBilling>P</SourceBilling></DocumentStatus>
    <Hash>0</Hash><HashControl>0</HashControl><InvoiceDate>2026-01-10</InvoiceDate><InvoiceType>{typ}</InvoiceType>
    <SpecialRegimes><SelfBillingIndicator>0</SelfBillingIndicator><CashVATSchemeIndicator>0</CashVATSchemeIndicator><ThirdPartiesBillingIndicator>0</ThirdPartiesBillingIndicator></SpecialRegimes>
    <SourceID>TEST</SourceID><SystemEntryDate>2026-01-10T10:00:00</SystemEntryDate><CustomerID>{customer_id}</CustomerID>
    <Line><LineNumber>1</LineNumber><ProductCode>P1</ProductCode><ProductDescription>Serviço fictício</ProductDescription><Quantity>1</Quantity><UnitOfMeasure>UN</UnitOfMeasure><UnitPrice>{net}</UnitPrice><TaxPointDate>2026-01-10</TaxPointDate><Description>Teste</Description><{side}>{net}</{side}><Tax><TaxType>IVA</TaxType><TaxCountryRegion>PT</TaxCountryRegion><TaxCode>NOR</TaxCode><TaxPercentage>23</TaxPercentage></Tax></Line>
    <DocumentTotals><TaxPayable>{tax}</TaxPayable><NetTotal>{net}</NetTotal><GrossTotal>{value}</GrossTotal>{payments}</DocumentTotals></Invoice>"""


def receipt(number=1, reference="FT TESTE/1", value="12.30", methods=None, debit=False, status="N"):
    side = "DebitAmount" if debit else "CreditAmount"
    if methods is None:
        methods = payment(value, tag="PaymentMethod", day="2026-01-15")
    return f"""<Payment><PaymentRefNo>RG TESTE/{number}</PaymentRefNo><ATCUD>0</ATCUD><TransactionDate>2026-01-15</TransactionDate><PaymentType>RG</PaymentType>
    <DocumentStatus><PaymentStatus>{status}</PaymentStatus><PaymentStatusDate>2026-01-15T10:00:00</PaymentStatusDate><SourceID>TEST</SourceID><SourcePayment>P</SourcePayment></DocumentStatus>{methods}
    <SourceID>TEST</SourceID><SystemEntryDate>2026-01-15T10:00:00</SystemEntryDate><CustomerID>C1</CustomerID>
    <Line><LineNumber>1</LineNumber><SourceDocumentID><OriginatingON>{reference}</OriginatingON><InvoiceDate>2026-01-10</InvoiceDate></SourceDocumentID><{side}>{value}</{side}></Line>
    <DocumentTotals><TaxPayable>0.00</TaxPayable><NetTotal>{value}</NetTotal><GrossTotal>{value}</GrossTotal></DocumentTotals></Payment>"""


def saft(invoices=None, receipts=None, customers=None, nif="500000001", extra=""):
    invoices = [invoice()] if invoices is None else invoices
    receipts = [] if receipts is None else receipts
    customers = customer() if customers is None else customers
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <AuditFile xmlns="{NS}"><Header><AuditFileVersion>1.04_01</AuditFileVersion><CompanyID>{nif}</CompanyID><TaxRegistrationNumber>{nif}</TaxRegistrationNumber><TaxAccountingBasis>F</TaxAccountingBasis><CompanyName>Empresa fictícia</CompanyName><CompanyAddress>{address()}</CompanyAddress><FiscalYear>2026</FiscalYear><StartDate>2026-01-01</StartDate><EndDate>2026-01-31</EndDate><CurrencyCode>EUR</CurrencyCode><DateCreated>2026-02-01</DateCreated><TaxEntity>Global</TaxEntity><ProductCompanyTaxID>500000001</ProductCompanyTaxID><SoftwareCertificateNumber>0</SoftwareCertificateNumber><ProductID>TESTE/Zela</ProductID><ProductVersion>1</ProductVersion></Header>
    <MasterFiles>{customers}<Product><ProductType>S</ProductType><ProductCode>P1</ProductCode><ProductDescription>Serviço fictício</ProductDescription><ProductNumberCode>P1</ProductNumberCode></Product></MasterFiles>
    <SourceDocuments><SalesInvoices><NumberOfEntries>{len(invoices)}</NumberOfEntries><TotalDebit>0.00</TotalDebit><TotalCredit>0.00</TotalCredit>{"".join(invoices)}</SalesInvoices>{extra}
    {("<Payments><NumberOfEntries>" + str(len(receipts)) + "</NumberOfEntries><TotalDebit>0.00</TotalDebit><TotalCredit>0.00</TotalCredit>" + "".join(receipts) + "</Payments>") if receipts else ""}</SourceDocuments></AuditFile>""".encode()
