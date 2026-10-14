"""Read Portuguese fiscal QR codes locally; never fetch QR URLs."""
import re
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
import pypdfium2 as pdfium
import zxingcpp

PDF_LOCK = threading.Lock()  # PDFium does not allow concurrent calls in one process.

def parse_fiscal_qr(text):
    fields = {}
    for part in text.strip().split("*"):
        key, separator, value = part.partition(":")
        if not separator or key in fields: return None
        fields[key] = value
    if not re.fullmatch(r"\d{9}", fields.get("A", "")) or fields.get("D") not in {"FT","FS","FR","NC","ND","VD","TV","RC","RG","GT","GR","GA","GC","GD","CM","CC","NE","OU","OR","PF","DC","FC","FO","RP","RE","CS","LD","RA"}:
        return None
    if not fields.get("G") or len(fields["G"]) > 100 or not re.fullmatch(r"\d{8}",fields.get("F","")): return None
    try:
        day = datetime.strptime(fields["F"], "%Y%m%d").date().isoformat()
        amount = Decimal(fields["O"])
        if not amount.is_finite() or amount < 0 or amount > Decimal("100000000000") or amount != amount.quantize(Decimal("0.01")): return None
    except (KeyError, ValueError, InvalidOperation): return None
    return {"nif":fields["A"],"document_type":fields["D"],"document_date":day,"document":fields["G"],"amount_cents":int(amount*100),"atcud":fields.get("H", ""),"status":fields.get("E", "")}

def extract_pdf_qr(raw, limits):
    from .document_scanning import make_pdf
    make_pdf([raw], [], limits)  # Same type, active-content, size and page validation as uploads.
    found = {}
    with PDF_LOCK:
        pdf = pdfium.PdfDocument(raw)
        try:
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    width,height=page.get_size()
                    scale=min(3, 3500/max(width,height))
                    bitmap=page.render(scale=scale)
                    try:
                        image=bitmap.to_pil().convert("RGB")
                        try:
                            codes=zxingcpp.read_barcodes(image,formats=zxingcpp.BarcodeFormat.QRCode)
                            for code in codes:
                                data=parse_fiscal_qr(code.text)
                                if data: found.setdefault(tuple(data.items()), {**data,"page":index+1})
                        finally: image.close()
                    finally: bitmap.close()
                finally: page.close()
        finally: pdf.close()
    documents=list(found.values())
    return {"status":"found" if len(documents)==1 else "multiple" if documents else "not_found", "documents":documents}
