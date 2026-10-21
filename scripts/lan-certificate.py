"""Certificado autoassinado para o acesso HTTPS pela rede local (Iniciar-Rede.ps1 -Https).

Os navegadores só permitem a câmara em direto em contexto seguro (HTTPS ou localhost). Este
certificado serve apenas o servidor de desenvolvimento na rede de confiança: no telemóvel aceita-se
o aviso de segurança uma vez. A chave privada fica em .local/https (excluída do Git) e nunca é
instalada como autoridade de certificação.
"""
import argparse
import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

VALID_DAYS = 365  # os navegadores Apple recusam certificados TLS com validade superior a 398 dias


def current(cert_path, address):
    """Reutiliza o certificado se já incluir este IP e ainda for válido por mais de 30 dias."""
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        addresses = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)
        return address in addresses and cert.not_valid_after_utc > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True)
    parser.add_argument("--out", required=True)
    options = parser.parse_args()
    address = ipaddress.IPv4Address(options.ip)
    if not address.is_private or address.is_loopback or address.is_link_local:
        parser.error("Indique o IPv4 privado do computador na rede local.")
    folder = Path(options.out)
    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / "lan-cert.pem", folder / "lan-key.pem"
    if key_path.exists() and current(cert_path, address):
        print(f"Certificado local válido para {address}.")
        return
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "IAZELA - rede local"), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "IAZELA (desenvolvimento)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address), x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")), x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Certificado local criado para {address} (válido {VALID_DAYS} dias).")


if __name__ == "__main__":
    main()
