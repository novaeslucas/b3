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


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def fetch(path, url=None, encoding="latin-1", nome=None, dia=None):
    """GET (Fundamentus por padrão) com cache diário em disco. SSL sempre verificado.

    ``dia`` entra no nome do arquivo. Quem reaproveita um snapshot passa a data;
    omitido, vale o dia do processo. Não há estado global.
    """
    import datetime as dt
    dia = dia or dt.date.today()
    os.makedirs(CACHE_DIR, exist_ok=True)
    fn = os.path.join(CACHE_DIR, re.sub(r"[^\w]+", "_", nome or path or "url") + f"_{dia:%Y%m%d}.html")
    if os.path.exists(fn):
        return open(fn, encoding="utf-8").read()
    text = baixar(url or "https://www.fundamentus.com.br" + path, encoding)
    with open(fn, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def baixar(url, encoding="utf-8", timeout=30):
    """GET sem cache, com até 3 tentativas. Para cotação ao vivo, em que o cache diário de ``fetch`` não serve."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for tentativa in range(3):
        try:
            return urllib.request.urlopen(req, timeout=timeout).read().decode(encoding)
        except (urllib.error.URLError, TimeoutError) as exc:
            # 404 e afins não melhoram com nova tentativa (ex.: ticker que saiu do Yahoo); 429 e 5xx sim
            definitivo = isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500 and exc.code != 429
            if tentativa == 2 or definitivo:
                logger.warning("download falhou %s: %s", url, exc)
                raise
            time.sleep(2 ** tentativa)
