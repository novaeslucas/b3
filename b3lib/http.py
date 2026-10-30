"""HTTP com cache diário. A verificação SSL fica ligada para todos os hosts.

Em 30/10/2026 o endpoint da B3 (``sistemaswebb3-listados.b3.com.br``), o Yahoo, o
Fundamentus e a CVM responderam com certificado válido no trust store padrão do
Python. O atalho que desligava a verificação para qualquer URL explícita
(inclusive o Yahoo) foi removido. Se a B3 voltar a falhar na cadeia de
certificados, o erro aparece no resumo de falhas em vez de desligar a verificação.
"""
import os
import re
import time
import urllib.error
import urllib.request

from b3lib.log import logger

B3 = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
CACHE_DIR = "cache"
_HOJE = None  # preenchido por definir_hoje; o padrão é a data do processo


def definir_hoje(data):
    """Permite aos scripts fixar o dia do cache (snapshots)."""
    global _HOJE
    _HOJE = data


def hoje():
    import datetime as dt
    return _HOJE or dt.date.today()


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def fetch(path, url=None, encoding="latin-1", nome=None):
    """GET (Fundamentus por padrão) com cache diário em disco. SSL sempre verificado."""
    import datetime as dt
    dia = hoje() if isinstance(hoje(), dt.date) else dt.date.today()
    os.makedirs(CACHE_DIR, exist_ok=True)
    fn = os.path.join(CACHE_DIR, re.sub(r"[^\w]+", "_", nome or path or "url") + f"_{dia:%Y%m%d}.html")
    if os.path.exists(fn):
        return open(fn, encoding="utf-8").read()
    destino = url or "https://www.fundamentus.com.br" + path
    req = urllib.request.Request(destino, headers={"User-Agent": "Mozilla/5.0"})
    for tentativa in range(3):
        try:
            text = urllib.request.urlopen(req, timeout=30).read().decode(encoding)
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            if tentativa == 2:
                logger.warning("download falhou %s: %s", destino, exc)
                raise
            time.sleep(2 ** tentativa)
    with open(fn, "w", encoding="utf-8") as f:
        f.write(text)
    return text
