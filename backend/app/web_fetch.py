"""Leitura segura de endereços indicados pelos utilizadores (imagens de produtos, páginas de lojas).

O servidor só segue HTTPS na porta normal, nunca para a rede interna (verificado em cada redirecionamento, no máximo
três), com tempo e tamanho limitados. Usado pelas fotografias dos artigos e pelos preços de mercado.
"""

import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from fastapi import HTTPException

# Nome com que os sites podem bloquear a IAZELA no robots.txt («User-agent: IAZELA»).
ROBOT_AGENT = "IAZELA"
USER_AGENT = f"Mozilla/5.0 (compatible; {ROBOT_AGENT}/1.0)"
ROBOTS_LIMIT = 512 * 1024


def fail(message, status=422):
    raise HTTPException(status, message)


def public_address(url, what="da imagem"):
    """Só HTTPS, sem credenciais, porta 443, e anfitrião com endereços públicos (nunca a rede interna do servidor)."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        fail("Indique um endereço HTTPS válido, sem credenciais.")
    if parsed.port not in (None, 443):
        fail(f"O endereço {what} tem de usar a porta HTTPS normal.")
    try:
        found = socket.getaddrinfo(parsed.hostname, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        fail(f"Não foi possível encontrar o site {what}. Confirme o endereço.")
    if not found or any(not ipaddress.ip_address(item[4][0].split("%")[0]).is_global for item in found):
        fail(f"O endereço {what} aponta para uma rede interna e não é permitido.")


def get(url, *, accept, limit, what, too_big, hint="", timeout=20):
    """Conteúdo do endereço (bytes) e o endereço final, depois dos redirecionamentos verificados."""
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "pt-PT,pt;q=0.9"}
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _ in range(4):
            public_address(url, what)
            try:
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = str(response.url.join(response.headers.get("location", "")))
                        continue
                    if response.status_code != 200:
                        fail(f"O site {what} respondeu HTTP {response.status_code}: pode não permitir a leitura "
                             f"automática.{hint}", 502)
                    raw = b""
                    for chunk in response.iter_bytes():
                        raw += chunk
                        if len(raw) > limit:
                            fail(too_big)
                    if not raw:
                        fail(f"O site {what} não devolveu conteúdo.", 502)
                    return raw, url
            except httpx.HTTPError:
                fail(f"Não foi possível ler o endereço {what}. Confirme o endereço.{hint}", 502)
        fail(f"O endereço {what} redireciona demasiadas vezes.", 502)


def robots_allow(url, timeout=10):
    """O robots.txt do site permite à IAZELA ler este endereço? (RFC 9309: sem ficheiro, ou 4xx, permite; site em
    erro ou inacessível, não permite; redirecionamentos verificados como os restantes pedidos, no máximo três)."""
    parsed = urlsplit(url)
    target = f"https://{parsed.hostname}/robots.txt"
    headers = {"User-Agent": USER_AGENT, "Accept": "text/plain"}
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _ in range(4):
            public_address(target, "da loja")
            try:
                with client.stream("GET", target) as response:
                    if response.is_redirect:
                        target = str(response.url.join(response.headers.get("location", "")))
                        continue
                    if 400 <= response.status_code < 500:
                        return True
                    if response.status_code != 200:
                        return False
                    raw = b""
                    for chunk in response.iter_bytes():
                        raw += chunk
                        if len(raw) >= ROBOTS_LIMIT:
                            break
            except httpx.HTTPError:
                return False
            rules = RobotFileParser()
            rules.parse(raw[:ROBOTS_LIMIT].decode("utf-8", errors="replace").splitlines())
            return rules.can_fetch(ROBOT_AGENT, url)
    return False
