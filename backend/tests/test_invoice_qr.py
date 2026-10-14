import io
import pytest
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from app.invoice_qr import extract_pdf_qr, parse_fiscal_qr

PAYLOAD="A:500000000*B:999999990*C:PT*D:FT*E:N*F:20261014*G:FT DEMO/12*H:DEMO1234-12*N:23.00*O:123.45*Q:ABCD*R:9999"
LIMITS={"max_pages":20,"max_bytes":5*1024*1024}

def document(*payloads):
    output=io.BytesIO(); pdf=canvas.Canvas(output)
    for payload in payloads:
        if payload:
            qr=QrCodeWidget(payload); x,y,right,top=qr.getBounds()
            drawing=Drawing(130,130,transform=[130/(right-x),0,0,130/(top-y),0,0]); drawing.add(qr)
            renderPDF.draw(drawing,pdf,350,100)
        pdf.showPage()
    pdf.save(); return output.getvalue()

def test_rendered_qr_later_page_and_repeated_same_document():
    result=extract_pdf_qr(document(None,PAYLOAD,PAYLOAD),LIMITS)
    assert result["status"]=="found" and len(result["documents"])==1
    assert result["documents"][0]=={"nif":"500000000","document_type":"FT","document_date":"2026-10-14","document":"FT DEMO/12","amount_cents":12345,"atcud":"DEMO1234-12","page":2,"status":"N"}

def test_different_documents_require_selection_and_non_fiscal_is_ignored():
    other=PAYLOAD.replace("DEMO/12","DEMO/13")
    assert extract_pdf_qr(document(PAYLOAD,other),LIMITS)["status"]=="multiple"
    assert extract_pdf_qr(document("https://example.test/invoice"),LIMITS)["status"]=="not_found"

@pytest.mark.parametrize("raw",[PAYLOAD.replace("123.45","NaN"),PAYLOAD.replace("20261014","20260230"),PAYLOAD+"*A:500000001",PAYLOAD.replace("123.45","123.456"),"https://example.test"])
def test_invalid_fields_never_fill_financial_metadata(raw):
    assert parse_fiscal_qr(raw) is None
