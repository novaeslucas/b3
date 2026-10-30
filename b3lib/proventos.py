"""Proventos do Fundamentus: parse do HTML e série anual por ação.

Cada evento traz o valor bruto e o líquido de IR sobre JCP. O score e o Jev usam o bruto;
o líquido fica disponível para quem quiser o rendimento do acionista.

O IRRF do JCP era 15% (Lei 9.249/1995, art. 9º, § 2º). A Lei Complementar 224/2025,
art. 8º, fixou 17,5% «na data do pagamento ou do crédito ao beneficiário». O art. 14,
III, produz efeito em 1º/1/2026. Não é a data da deliberação. Sem data de pagamento
no Fundamentus, a data-com é o substituto (limitação: não é o crédito individualizado).

Dividendos entram pelo valor cheio. A retenção de 10% sobre dividendos acima de
R$ 50 mil/mês (Lei 15.270/2025) depende do investidor, não do papel.
"""
import datetime as dt
import re

from b3lib.numeros import num
from b3lib.texto import sem_acento

# Lei 9.249/1995, art. 9º, § 2º, na redação anterior à LC 224/2025.
ALIQUOTA_JCP_ATE_2025 = 0.15
# LC 224/2025, art. 8º, c/c art. 14, III. Não há, nesse artigo, alta posterior para 20%
# (os 17,5% até 2027 e 20% em 2028 do art. 7º são de CSLL de capitalização, não de JCP).
ALIQUOTA_JCP_DESDE_2026 = 0.175
DATA_NOVA_ALIQUOTA_JCP = dt.date(2026, 1, 1)

_DATA = re.compile(r"\d{2}/\d{2}/\d{4}")
_EXTRA = ("extraordin", "especial", "nao recorrente")


def aliquota_jcp(data_ref):
    """IRRF do JCP. ``data_ref`` é o pagamento ou, na falta dele, a data-com."""
    if data_ref and data_ref >= DATA_NOVA_ALIQUOTA_JCP:
        return ALIQUOTA_JCP_DESDE_2026
    return ALIQUOTA_JCP_ATE_2025


def classe_tipo(tipo):
    t = sem_acento(tipo or "").upper()
    if "DESDOBR" in t or "GRUPAMENT" in t or "BONIFIC" in t:
        return "ajuste"
    if "JRS CAP" in t or "JUROS SOBRE" in t or t.strip() == "JCP":
        return "jcp"
    if "DIVID" in t:
        return "dividendo"
    return "outro"


def evento_rotulado_extraordinario(tipo):
    t = sem_acento(tipo or "").lower()
    return any(k in t for k in _EXTRA)


def _data(s):
    s = (s or "").strip()
    if not _DATA.fullmatch(s):
        return None
    return dt.datetime.strptime(s, "%d/%m/%Y").date()


def parse_proventos_html(page):
    """Eventos da tabela completa de proventos.php (data-com, valor, tipo, pagamento, por quantas ações)."""
    eventos = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page or "", re.S):
        tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) != 5:
            continue
        data_com = _data(tds[0])
        valor = num(tds[1])
        if data_com is None or valor is None:
            continue
        por = num(tds[4]) or 1.0
        if por == 0:
            por = 1.0
        bruto = valor / por
        tipo = tds[2].strip()
        pagamento = _data(tds[3])
        classe = classe_tipo(tipo)
        taxa = aliquota_jcp(pagamento or data_com) if classe == "jcp" else 0.0
        eventos.append({
            "data_com": data_com,
            "data_pagamento": pagamento,
            "tipo": tipo,
            "classe": classe,
            "valor_bruto": bruto,
            "valor_liquido": bruto * (1 - taxa),
            "extraordinario": classe == "dividendo" and evento_rotulado_extraordinario(tipo),
            # Desdobramento, grupamento e bonificação: ações novas / ações antigas.
            # O valor da linha, já dividido por «por quantas ações», é esse fator.
            "fator": bruto if classe == "ajuste" and bruto > 0 else None,
        })
    return deduplicar_eventos(eventos)


def deduplicar_eventos(eventos):
    """Tira só a linha repetida: data-com, valor, tipo e data de pagamento iguais.

    Mesma data-com, valor e tipo com pagamentos diferentes são parcelas e as duas ficam.
    """
    vistos = set()
    saida = []
    for e in eventos:
        chave = (e.get("data_com"), e.get("valor_bruto"), e.get("tipo"), e.get("data_pagamento"))
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(e)
    return saida


def tabela_anual(page):
    """Série bruta 'por ano' (R$ por ação), como o Fundamentus já soma. {} se a tabela não existir."""
    anos = {}
    for tbody in re.findall(r"<tbody>(.*?)</tbody>", page or "", re.S):
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.S):
            tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(tds) == 2 and re.fullmatch(r"\d{4}", tds[0]):
                anos[int(tds[0])] = num(tds[1]) or 0.0
    return anos


def agregar_por_ano(eventos, liquido=False, incluir_outros=True):
    """Soma R$ por ação pela data-com. ``incluir_outros`` mantém restituição etc. no bruto de conferência."""
    chave = "valor_liquido" if liquido else "valor_bruto"
    anos = {}
    for e in eventos:
        if e["classe"] in ("outro", "ajuste") and not incluir_outros:
            continue
        if e["classe"] == "ajuste":
            continue
        if liquido and e["classe"] == "outro":
            continue
        ano = e["data_com"].year
        anos[ano] = anos.get(ano, 0.0) + e[chave]
    return anos
