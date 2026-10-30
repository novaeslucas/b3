"""Falhas visíveis sem interromper a execução inteira.

Cada ticker ou símbolo que falha entra numa lista. No fim do script, ``resumo_falhas``
imprime o que ficou de fora e por quê. O logger também escreve um aviso na hora.
"""
import logging

logger = logging.getLogger("b3")
_falhas = []


def configurar():
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def registrar_falha(ticker, etapa, exc):
    msg = f"{type(exc).__name__}: {exc}"
    logger.warning("falha em %s [%s]: %s", ticker or "-", etapa, msg)
    _falhas.append((str(ticker or "-"), etapa, msg))


def falhas():
    return list(_falhas)


def limpar_falhas():
    _falhas.clear()


def resumo_falhas():
    if not _falhas:
        return
    print(f"\nFalhas durante a execução ({len(_falhas)}); a análise seguiu sem esses dados:")
    por_etapa = {}
    for ticker, etapa, msg in _falhas:
        por_etapa.setdefault(etapa, []).append(f"{ticker} ({msg})")
    for etapa, itens in por_etapa.items():
        amostra = "; ".join(itens[:12])
        resto = f" … +{len(itens) - 12}" if len(itens) > 12 else ""
        print(f"  {etapa}: {amostra}{resto}")
