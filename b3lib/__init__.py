"""Funções compartilhadas dos scripts atuais (ações, FIIs e painéis).

As pastas em ``leituras/`` guardam a cópia do script que rodou naquele pregão e não
importam este pacote: são um registro congelado. Código novo deve usar ``b3lib``.
"""

from b3lib.http import B3, fetch, load_env
from b3lib.numeros import lin, media, num

__all__ = ["B3", "fetch", "lin", "load_env", "media", "num"]
