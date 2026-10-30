"""Índices da B3: em quais índices cada papel está e o peso na carteira do dia. Sem rede: só o parse.

Fontes (``indexProxy/indexCall`` da B3, JSON com parâmetros em base64, como o resto de ``b3lib``):
  - ``GetStockIndex``: uma linha por papel listado com a lista de índices de que participa (mercado inteiro numa
    chamada; conferido em 30/10/2026: 466 papéis);
  - ``GetPortfolioDay``: carteira do dia de um índice, com a participação (%) de cada papel.
"""
import json

from b3lib.numeros import num

# Nomes só dos índices conferidos; os demais aparecem pela sigla.
NOMES = {
    "IBOV": "Ibovespa", "IBXX": "IBrX 100", "IBXL": "IBrX 50", "IBRA": "IBrA (Brasil Amplo)",
    "IDIV": "Dividendos (IDIV)", "SMLL": "Small Cap", "MLCX": "MidLarge Cap", "IVBX": "IVBX 2 (Valor)",
    "IFNC": "Financeiro", "UTIL": "Utilidade Pública", "IMOB": "Imobiliário", "IMAT": "Materiais Básicos",
    "INDX": "Industrial", "ICON": "Consumo", "IEEX": "Energia Elétrica", "AGFS": "Agronegócio",
    "IGCX": "Governança Corporativa Diferenciada", "IGCT": "Governança Corporativa Trade",
    "IGNM": "Governança Novo Mercado", "ITAG": "Tag Along Diferenciado", "ICO2": "Carbono Eficiente",
    "ISEE": "Sustentabilidade Empresarial", "IFIX": "Fundos Imobiliários", "IFIL": "IFIX Alta Liquidez",
    "BDRX": "BDRs", "GPTW": "GPTW",
}
# Ordem de exibição: amplos e de tamanho, depois setoriais, depois governança/sustentabilidade; o resto no fim.
PRIORIDADE = ["IBOV", "IBXX", "IBXL", "IBRA", "IDIV", "SMLL", "MLCX", "IVBX", "IFIX", "IFIL",
              "IFNC", "UTIL", "IEEX", "IMOB", "IMAT", "INDX", "ICON", "AGFS",
              "ISEE", "ICO2", "IGNM", "ITAG", "IGCX", "IGCT"]


def ordenar(codigos):
    pos = {c: i for i, c in enumerate(PRIORIDADE)}
    return sorted(dict.fromkeys(codigos), key=lambda c: (pos.get(c, len(PRIORIDADE)), c))


def indices_por_papel(texto):
    """{ticker: [índices, na ordem de exibição]} do JSON de GetStockIndex."""
    out = {}
    for r in json.loads(texto).get("results") or []:
        codigos = [c.strip() for c in (r.get("indexes") or "").split(",") if c.strip()]
        if r.get("code") and codigos:
            out[r["code"].strip()] = ordenar(codigos)
    return out


def carteira(texto):
    """{ticker: participação em fração (0,476% -> 0.00476)} do JSON de GetPortfolioDay."""
    out = {}
    for r in json.loads(texto).get("results") or []:
        part = num(r.get("part"))
        if r.get("cod") and part is not None:
            out[r["cod"].strip()] = part / 100
    return out
