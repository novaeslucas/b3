"""Normalização de texto usada nos parsers (stdlib apenas)."""
import unicodedata


def sem_acento(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
