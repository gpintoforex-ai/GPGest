"""Local, conservative extraction. File names never establish invoice identity."""
import io
import os
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from time import monotonic
from PIL import Image, ImageOps
import pypdfium2 as pdfium
import zxingcpp
from .invoice_qr import PDF_LOCK, extract_pdf_qr, parse_fiscal_qr
from .google_invoices import pdf_text

def nif_key(value):
    return re.sub(r"\s+", "", (value or "").strip().upper().removeprefix("PT"))

def invoice_key(nif, kind, number):
    kind=(kind or "").strip().upper()
    number=unicodedata.normalize("NFKC",number or "").upper().strip()
    number=number.translate(str.maketrans({"–":"-","—":"-","−":"-","／":"/"}))
    number=re.sub(r"\s*([/.-])\s*",r"\1",number)
    number=re.sub(r"\s+"," ",number)
    if number.startswith(kind+" "): number=number[len(kind)+1:]
    return nif_key(nif),kind,number

def ocr_command():
    command=os.getenv("GPGEST_OCR_COMMAND","") or shutil.which("tesseract")
    return command if command and os.path.isfile(command) else None

def ocr_image(image):
    command=ocr_command()
    if not command: return ""
    output=io.BytesIO(); image.save(output,"PNG")
    # GPGEST_OCR_TESSDATA permite idiomas numa pasta própria (por exemplo, .local/tessdata), sem instalar no sistema.
    tessdata=os.getenv("GPGEST_OCR_TESSDATA","")
    extra=["--tessdata-dir",tessdata] if tessdata and os.path.isdir(tessdata) else []
    result=subprocess.run([command,*extra,"stdin","stdout","-l",os.getenv("GPGEST_OCR_LANG","eng"),"--psm","6"],input=output.getvalue(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
    if result.returncode: raise ValueError("O OCR local não conseguiu ler o documento. Verifique o executável e os idiomas instalados.")
    return result.stdout.decode("utf-8",errors="replace")[:60000]

def money(text):
    value=text.replace(" ","").replace("\u00a0","")
    if "," in value: value=value.replace(".","").replace(",",".")
    elif value.count(".")>1: return None
    try:
        amount=Decimal(value)
        if not amount.is_finite() or abs(amount)>Decimal("100000000000") or amount!=amount.quantize(Decimal(".01")): return None
        return int(amount*100)
    except InvalidOperation: return None

def text_fields(text):
    nifs=set(re.findall(r"(?i)\b(?:NIF|NIPC|VAT(?:\s*NUMBER)?)\s*[:#.-]?\s*(?:PT\s*)?(\d{9})\b",text))
    issuer=set(re.findall(r"(?i)(?:NIF\s*(?:do\s*)?(?:emitente|fornecedor)|(?:emitente|fornecedor)\s*NIF)\s*[:#.-]?\s*(?:PT\s*)?(\d{9})\b",text))
    if issuer: nifs=issuer
    numbers=set((kind.upper(),number.strip()) for kind,number in re.findall(r"\b(FT|FS|FR|NC|ND)\s+([A-Za-z0-9_.-]+\s*/\s*[0-9]+)\b",text,re.I))
    dates=set()
    for raw in re.findall(r"(?i)(?:data\s*(?:de\s*)?(?:emiss[aã]o|documento)|data)\s*[:.-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})",text):
        try: dates.add(datetime.strptime(raw,"%Y-%m-%d" if raw[4:5]=="-" else "%d/%m/%Y" if "/" in raw else "%d-%m-%Y").date().isoformat())
        except ValueError: pass
    totals=set()
    for raw in re.findall(r"(?i)(?:total\s*(?:do\s*documento|a\s*pagar|da\s*fatura|com\s*(?:iva|impostos))?|valor\s*total)\s*[:=]?\s*(?:EUR|€)?\s*([0-9][0-9 .,]*[.,][0-9]{2})(?!\d)",text):
        value=money(raw)
        if value is not None: totals.add(abs(value))
    currencies=set(re.findall(r"\b(EUR|USD|GBP|CHF|BRL|AOA)\b",text.upper()))
    # Vencimento indicado no documento (texto ou OCR); nunca é deduzido de outras datas.
    dues=set()
    for raw in re.findall(r"(?i)(?:data\s*(?:de\s*)?vencimento|vencimento|data\s*limite(?:\s*(?:de|para)\s*pagamento)?|(?:pagamento|pagar)\s*at[eé])\s*[:.-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}[/.-]\d{2}[/.-]\d{4})",text):
        try: dues.add(datetime.strptime(raw.replace(".","-").replace("/","-"),"%Y-%m-%d" if raw[4:5]=="-" else "%d-%m-%Y").date().isoformat())
        except ValueError: pass
    return {"due_dates":sorted(dues),"nifs":sorted(nifs),"numbers":[{"kind":kind,"number":number} for kind,number in sorted(numbers)],"dates":sorted(dates),"totals":sorted(totals),"currencies":sorted(currencies)}

def extract(raw, mime, use_ocr=False, keep_text=False):
    documents=[]; warnings=[]; source="text"; text=pdf_text(raw,mime); incomplete=False
    if mime=="application/pdf":
        result=extract_pdf_qr(raw,{"max_pages":40,"max_bytes":5*1024*1024})
        documents=result["documents"]
        if not documents:
            from pypdf import PdfReader
            incomplete=len(PdfReader(io.BytesIO(raw)).pages)>10
    else:
        with Image.open(io.BytesIO(raw)) as original:
            if original.width*original.height>40_000_000: raise ValueError("Imagem demasiado grande.")
            image=ImageOps.exif_transpose(original).convert("RGB")
            try:
                image.thumbnail((3500,3500))
                for code in zxingcpp.read_barcodes(image,formats=zxingcpp.BarcodeFormat.QRCode):
                    fields=parse_fiscal_qr(code.text)
                    if fields: documents.append(fields)
                if not documents and use_ocr and ocr_command(): text=ocr_image(image); source="ocr"
            finally: image.close()
    fields=text_fields(text)
    insufficient=not all(fields[key] for key in ("nifs","numbers","dates","totals"))
    if not documents and insufficient and use_ocr:
        if not ocr_command(): warnings.append("OCR indisponível: configure Tesseract no servidor.")
        elif mime=="application/pdf":
            parts=[]; started=monotonic()
            with PDF_LOCK:
                pdf=pdfium.PdfDocument(raw)
                try:
                    for index in range(min(len(pdf),10)):
                        if monotonic()-started>60:
                            incomplete=True; warnings.append("OCR parcial: atingiu o limite de tempo. Reveja o anexo manualmente."); break
                        page=pdf[index]
                        try:
                            width,height=page.get_size(); bitmap=page.render(scale=min(3,3500/max(width,height)))
                            try:
                                image=bitmap.to_pil().convert("RGB")
                                try: parts.append(ocr_image(image))
                                finally: image.close()
                            finally: bitmap.close()
                        finally: page.close()
                finally: pdf.close()
            text="\n".join(parts); fields=text_fields(text); source="ocr"
    result={**fields,"documents":documents,"source":"qr" if documents else source,"warnings":warnings,"incomplete":incomplete}
    # O arquivo digital classifica o tipo pelo texto; os restantes chamadores não guardam o texto.
    if keep_text: result["text"]=text[:20000]
    return result

def compare_evidence(evidence, purchase, tolerance=2):
    target=invoice_key(purchase.supplier_nif,purchase.document_type,purchase.document)
    documents=evidence.get("documents",[])
    if documents:
        matching=[item for item in documents if invoice_key(item["nif"],item["document_type"],item["document"])==target]
        if not matching: return {"safe":False,"candidate":False,"reasons":["Identidade do QR diferente"]}
        dates={item["document_date"] for item in matching}; totals={abs(item["amount_cents"]) for item in matching}
        safe=len({invoice_key(item["nif"],item["document_type"],item["document"]) for item in documents})==1
        if any(item.get("status") not in (None,"N") for item in matching): safe=False
    else:
        nifs=evidence.get("nifs",[]); numbers=evidence.get("numbers",[])
        identity_matches=any(invoice_key(purchase.supplier_nif,item["kind"],item["number"])==target for item in numbers)
        candidate=nif_key(purchase.supplier_nif) in nifs and identity_matches
        if not candidate: return {"safe":False,"candidate":identity_matches,"reasons":["NIF e número não confirmados no conteúdo"]}
        safe=len(nifs)==1 and len(numbers)==1
        dates=set(evidence.get("dates",[])); totals=set(evidence.get("totals",[]))
    reasons=[]
    if evidence.get("incomplete"): safe=False; reasons.append("Extração parcial; requer revisão manual")
    if dates!={purchase.document_date}: safe=False; reasons.append("Data ausente, ambígua ou divergente")
    if len(totals)!=1 or abs(next(iter(totals),-(10**15))-abs(purchase.amount_cents))>tolerance: safe=False; reasons.append("Total ausente, ambíguo ou divergente")
    currencies=set(evidence.get("currencies",[]))
    if currencies and currencies!={"EUR"}: safe=False; reasons.append("Moeda divergente ou ambígua")
    if not safe and not reasons: reasons.append("Mais de um documento ou identidade insuficiente")
    return {"safe":safe,"candidate":True,"reasons":reasons or ["NIF, tipo, número, data e total confirmados no conteúdo"]}
