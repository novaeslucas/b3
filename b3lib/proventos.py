"""Proventos do Fundamentus: parse do HTML, JCP líquido e natureza recorrente/extraordinária.

O IRRF do JCP era 15% (Lei 9.249/1995, art. 9º, § 2º). A Lei Complementar 224/2025,
art. 8º, fixou 17,5% «na data do pagamento ou do crédito ao beneficiário». O art. 14,
III, produz efeito em 1º/1/2026. Não é a data da deliberação. Sem data de pagamento
no Fundamentus, a data-com é o substituto (limitação: não é o crédito individualizado).

Dividendos entram pelo valor cheio. A retenção de 10% sobre dividendos acima de
R$ 50 mil/mês (Lei 15.270/2025) depende do investidor, não do papel.
"""
import datetime as dt
import re
import statistics

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
        })
    return eventos


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
        if e["classe"] == "outro" and not incluir_outros:
            continue
        if liquido and e["classe"] == "outro":
            continue
        ano = e["data_com"].year
        anos[ano] = anos.get(ano, 0.0) + e[chave]
    return anos


def fator_liquido_12m(eventos, hoje):
    """líquido ÷ bruto na janela de 12 meses até ``hoje`` (data-com). None se não houver provento."""
    inicio = hoje - dt.timedelta(days=365)
    bruto = liquido = 0.0
    for e in eventos:
        if e["classe"] == "outro":
            continue
        if inicio < e["data_com"] <= hoje:
            bruto += e["valor_bruto"]
            liquido += e["valor_liquido"]
    if bruto <= 0:
        return None
    return liquido / bruto


def classificar_natureza(eventos, hoje, anos_completos):
    """Recorrente, misto ou extraordinário na mesma janela de 12 meses do DY.

    A base é a mediana dos anos civis inteiros anteriores ao início da janela
    (pelo menos três com pagamento). Um dividendo extraordinário de 2026 entra
    mesmo que 2025 tenha sido um ano normal. JCP não é extraordinário só por ser JCP.
    A parcela é o maior entre o valor rotulado e o excesso sobre a mediana, para
    não contar o mesmo pico duas vezes.
    """
    inicio = hoje - dt.timedelta(days=365)

    def da_janela(e):
        return e["classe"] != "outro" and inicio < e["data_com"] <= hoje

    def total_ano(ano, so_extra=False):
        s = 0.0
        for e in eventos:
            if e["classe"] == "outro" or e["data_com"].year != ano:
                continue
            if so_extra and not e["extraordinario"]:
                continue
            s += e["valor_liquido"]
        return s

    janela = [e for e in eventos if da_janela(e)]
    tot = sum(e["valor_liquido"] for e in janela)
    rotulado = sum(e["valor_liquido"] for e in janela if e["extraordinario"])
    anteriores = [total_ano(a) for a in anos_completos
                  if dt.date(a, 12, 31) < inicio and total_ano(a) > 0]
    mediana = statistics.median(anteriores) if len(anteriores) >= 3 else None
    excesso = tot - mediana if mediana is not None and tot > 1.8 * mediana else 0.0
    parcela = max(rotulado, excesso)
    share = parcela / tot if tot > 0 else 0.0
    if tot <= 0 or share <= 0:
        natureza = "recorrente"
    elif share > 0.5:
        natureza = "extraordinario"
    else:
        natureza = "misto"
    return {
        "natureza_proventos": natureza,
        "parcela_extraordinaria": share,
        "proventos_janela_liquido": tot,
        "mediana_anterior_liquida": mediana,
    }


def aplicar_dy_liquido(dy_bruto, eventos, hoje):
    """DY do Fundamentus (bruto) × líquido/bruto dos eventos de 12 meses. O preço já está no DY."""
    fator = fator_liquido_12m(eventos, hoje)
    if dy_bruto is None or fator is None:
        return dy_bruto, fator
    return dy_bruto * fator, fator
