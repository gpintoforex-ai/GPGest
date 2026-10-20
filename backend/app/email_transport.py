"""Shared SMTP transport: measured MIME payloads and all-or-none RCPT checks."""

import hashlib
import mimetypes
import smtplib
import socket
import ssl
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr

from fastapi import HTTPException

from .accounting_delivery import csv_file
from .dossier_package import zip_bytes
from .email_settings import smtp_security_error


class DeliveryFailure(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def connect(config, password):
    if error := smtp_security_error(config["port"], config["security"]):
        raise DeliveryFailure(error)
    client = None
    try:
        context = ssl.create_default_context()
        # SMTP commands are ASCII, including EHLO on computers with accented hostnames.
        try:
            local_hostname = socket.getfqdn().encode("idna").decode("ascii")
        except UnicodeError:
            local_hostname = "[127.0.0.1]"
        if config["security"] == "ssl":
            client = smtplib.SMTP_SSL(
                config["host"], config["port"], timeout=15,
                context=context, local_hostname=local_hostname,
            )
        else:
            client = smtplib.SMTP(
                config["host"], config["port"], timeout=15, local_hostname=local_hostname
            )
            client.ehlo()
            client.starttls(context=context)
        client.ehlo()
        if config["username"]:
            client.login(config["username"], password)
        advertised = str(client.esmtp_features.get("size", "")).split()
        limit = int(advertised[0]) if advertised and advertised[0].isdigit() else None
        return client, limit
    except Exception as exc:
        if client:
            try:
                client.close()
            except Exception:
                pass
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            message = "O servidor SMTP recusou a autenticação. Reveja o utilizador e a palavra-passe."
            if config["host"].lower() == "smtp.gmail.com":
                message += " No Gmail, utilize uma palavra-passe de aplicação da conta Google."
        elif isinstance(exc, TimeoutError):
            message = "O servidor SMTP não respondeu a tempo. Reveja a porta, a segurança e a ligação de rede."
        elif isinstance(exc, ssl.SSLError):
            message = "Não foi possível estabelecer uma ligação TLS segura. Utilize STARTTLS na porta 587 ou SSL / TLS na porta 465 e verifique o certificado do servidor."
        else:
            message = "Não foi possível ligar ou autenticar no servidor SMTP. Reveja o servidor, TLS e as credenciais."
        raise DeliveryFailure(message) from None


def disconnect(client):
    try:
        client.quit()
    except Exception:
        try:
            client.close()
        except Exception:
            pass


def submit(client, sender, recipients, raw, before_data):
    data_started = False
    try:
        code, _ = client.mail(
            sender, options=[f"SIZE={len(raw)}"] if client.has_extn("size") else []
        )
        if code != 250:
            raise DeliveryFailure("O servidor SMTP recusou o remetente ou o tamanho da mensagem.")
        refused = []
        for recipient in recipients:
            code, _ = client.rcpt(recipient)
            if code not in (250, 251):
                refused.append(recipient)
        if refused:
            client.rset()
            raise DeliveryFailure(
                "O servidor recusou destinatários. Nenhum conteúdo foi enviado nesta mensagem: "
                + ", ".join(refused)
            )
        before_data()
        data_started = True
        code, _ = client.data(raw)
        if code != 250:
            raise DeliveryFailure("O servidor SMTP recusou o conteúdo da mensagem.")
    except smtplib.SMTPDataError:
        raise DeliveryFailure("O servidor SMTP recusou o conteúdo da mensagem.") from None
    except DeliveryFailure:
        raise
    except Exception:
        raise DeliveryFailure(
            "A ligação SMTP foi interrompida. Confirme a entrega antes de reenviar."
            if data_started
            else "A ligação SMTP foi interrompida antes do envio do conteúdo. Pode preparar uma nova tentativa.",
            uncertain=data_started,
        ) from None


def make_message(config, dispatch, draft, files, metadata, number=1, total=1):
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = formataddr((config["sender_name"], config["sender_email"]))
    message["To"] = ", ".join(draft["to"])
    if draft["cc"]:
        message["Cc"] = ", ".join(draft["cc"])
    message["Subject"] = draft["subject"] + (f" [{number}/{total}]" if total > 1 else "")
    message["Message-ID"] = f"<zela.{dispatch.id}.{number}@{config['sender_email'].split('@')[1]}>"
    message["Date"] = format_datetime(dispatch.created_at)
    message.set_content(
        draft["message"]
        + f"\n\nFicheiros anexados nesta mensagem: {len(files)}. Consulte o índice para identificar os originais e respetivas versões."
        + (
            f"\n\nMensagem {number} de {total}. O índice identifica os ficheiros desta mensagem."
            if total > 1
            else ""
        ),
        cte="quoted-printable",
    )
    prefix = f"{dispatch.start_date}_{dispatch.end_date}"
    index = csv_file(
        [
            "Fornecedor",
            "NIF",
            "Documento",
            "Data",
            "Valor cêntimos",
            "Ficheiro original",
            "Ficheiro enviado",
            "SHA-256",
        ],
        [
            [
                metadata[key].get(field, "")
                for field in (
                    "supplier",
                    "nif",
                    "document",
                    "date",
                    "amount_cents",
                    "original_name",
                )
            ]
            + [
                key if draft["format"] == "zip" else key.replace("/", "__"),
                hashlib.sha256(raw).hexdigest(),
            ]
            for key, raw in files.items()
        ],
    )
    if draft["format"] == "zip":
        package = {f"{prefix}/{key}": raw for key, raw in files.items()}
        package[prefix + "/indice-envio.csv"] = index
        message.add_attachment(
            zip_bytes(package),
            maintype="application",
            subtype="zip",
            filename=f"dossie-{prefix}-{number:02}.zip",
        )
    else:
        for key, raw in files.items():
            mime = mimetypes.guess_type(key)[0] or "application/octet-stream"
            maintype, subtype = mime.split("/", 1)
            message.add_attachment(
                raw, maintype=maintype, subtype=subtype, filename=key.replace("/", "__")
            )
        message.add_attachment(index, maintype="text", subtype="csv", filename="indice-envio.csv")
    message.set_boundary(f"zela_{dispatch.id}_{number}")
    return message.as_bytes()


def plan_messages(config, dispatch, draft, files, metadata, limit):
    whole = make_message(config, dispatch, draft, files, metadata)
    if len(whole) <= limit:
        return [(list(files), whole)]
    if len(files) == 1:
        raise HTTPException(
            413,
            "Um ficheiro excede o limite SMTP, incluindo a codificação MIME. Reduza a seleção ou utilize a partilha privada já autorizada no Drive, fora deste envio. Não foi criada uma ligação pública.",
        )
    # Bound work by raw byte size, then validate every actual MIME message.
    bins, current, size = [], {}, 0
    budget = max(1, int(limit * 0.65) - 10_000)
    for key, raw in files.items():
        if current and size + len(raw) > budget:
            bins.append(current)
            current, size = {}, 0
        current[key] = raw
        size += len(raw)
    if current:
        bins.append(current)
    if len(bins) == 1:
        entries = list(files.items())
        middle = len(entries) // 2
        bins = [dict(entries[:middle]), dict(entries[middle:])]
    while True:
        if len(bins) > 50:
            raise HTTPException(
                413, "O envio exigiria mais de 50 mensagens. Reduza o período ou a seleção."
            )
        result, revised = [], []
        for index, group in enumerate(bins, 1):
            raw = make_message(config, dispatch, draft, group, metadata, index, len(bins))
            if len(raw) > limit:
                if len(group) == 1:
                    raise HTTPException(
                        413,
                        "Um ficheiro excede o limite SMTP mesmo enviado individualmente. Reduza a seleção; nenhum email foi enviado.",
                    )
                entries = list(group.items())
                middle = len(entries) // 2
                revised.extend([dict(entries[:middle]), dict(entries[middle:])])
            else:
                revised.append(group)
            result.append((list(group), raw))
        if len(revised) == len(bins):
            return result
        bins = revised
