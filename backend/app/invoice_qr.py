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
    return {"nif":fields["A"],"document_type":fields["D"],"document_date":day,"document":fields["G"],"amount_cents":int(amount*100),"atcud":fields.get("H", ""),"status":fields.get("E", ""),"tax_breakdown":tax_breakdown(fields)}

# Campos do QR fiscal com bases e IVA por taxa (I = continente/espaço fiscal indicado em I1).
QR_RATES = (("I2", None, "isenta"), ("I3", "I4", "reduzida"), ("I5", "I6", "intermedia"), ("I7", "I8", "normal"))
MAINLAND_RATES = {"isenta": 0, "reduzida": 6, "intermedia": 13, "normal": 23}

def tax_breakdown(fields):
    """Bases e IVA por taxa indicados no QR. A taxa só é deduzida no continente (I1=PT) e quando
    o IVA declarado corresponde a essa taxa; nos restantes casos fica por indicar."""
    region = fields.get("I1", "")
    result = []
    for base_key, tax_key, kind in QR_RATES:
        try:
            base = int(Decimal(fields[base_key]) * 100) if fields.get(base_key) else 0
            tax = int(Decimal(fields[tax_key]) * 100) if tax_key and fields.get(tax_key) else 0
        except InvalidOperation:
            return []
        if not base and not tax:
            continue
        rate = MAINLAND_RATES[kind] if region == "PT" else None
        if rate is not None and abs(round(base * rate / 100) - tax) > 1:
            rate = None
        result.append({"region": region, "kind": kind, "base_cents": base, "tax_cents": tax, "rate": rate})
    return result

def read_images_qr(raws):
    """QR fiscal (código AT) nas fotografias das páginas, localmente; o primeiro válido ou `None`."""
    import io

    from PIL import Image, ImageOps

    for raw in raws:
        try:
            with Image.open(io.BytesIO(raw)) as opened:
                image = ImageOps.exif_transpose(opened).convert("L")
        except (OSError, ValueError):
            continue
        for side in (2400, 1400):
            sample = image.copy()
            sample.thumbnail((side, side))
            for found in zxingcpp.read_barcodes(sample):
                parsed = parse_fiscal_qr(found.text or "")
                if parsed:
                    return parsed
    return None


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
                                if data: found.setdefault(tuple((k, v) for k, v in data.items() if k != "tax_breakdown"), {**data,"page":index+1})
                        finally: image.close()
                    finally: bitmap.close()
                finally: page.close()
        finally: pdf.close()
    documents=list(found.values())
    return {"status":"found" if len(documents)==1 else "multiple" if documents else "not_found", "documents":documents}
