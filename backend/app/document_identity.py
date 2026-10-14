import re
import unicodedata


def number_key(value):
    # Keep slashes, hyphens, punctuation and leading zeros significant.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).upper()


def identity(nif, kind, number):
    return nif.removeprefix("PT").strip(), kind.upper(), number_key(number)
