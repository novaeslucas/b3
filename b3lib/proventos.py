"""Proventos do Fundamentus: parse do HTML, JCP líquido e natureza recorrente/extraordinária.

O IR retido na fonte sobre JCP era 15% até 2025. A Lei Complementar 224/2025 elevou a
alíquota para 17,5% sobre JCP pago ou creditado a partir de 01/01/2026. Sem data de
pagamento, a data-com decide a alíquota. Dividendos entram pelo valor cheio: a
retenção de 10% sobre dividendos acima de R$ 50 mil/mês (Lei 15.270/2025) depende
do investidor, não do papel, e fica de fora desta conta.
"""
import datetime as dt
import re
import statistics

from b3lib.numeros import num
from b3lib.texto import sem_acento

_DATA = re.compile(r"\d{2}/\d{2}/\d{4}")
_EXTRA = ("extraordin", "especial", "nao recorrente")


def aliquota_jcp(data_ref):
    """Alíquota de IRRF do JCP conforme a data de pagamento (ou data-com, se faltar)."""
    if data_ref and data_ref >= dt.date(2026, 1, 1):
        return 0.175
    return 0.15


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


def classificar_natureza(eventos, anos_completos):
    """Recorrente, misto ou extraordinário no último ano completo.

    Um evento de dividendo entra como extraordinário se o tipo traz extraordinário,
    especial ou não recorrente. JCP não é extraordinário só por ser JCP.

    Além do rótulo, o excesso do último ano completo sobre a mediana dos anos
    anteriores com pagamento (quando há pelo menos três e o ano passa de 1,8× a
    mediana) conta como parcela extraordinária. A parcela usada é o maior entre o
    valor rotulado e esse excesso, para não contar o mesmo pico duas vezes.
    """
    if not anos_completos:
        return {"natureza_proventos": "recorrente", "parcela_extraordinaria": 0.0,
                "proventos_ultimo_ano_liquido": 0.0, "mediana_anterior_liquida": None}
    ultimo = anos_completos[-1]

    def total(ano, so_extra=False):
        s = 0.0
        for e in eventos:
            if e["classe"] == "outro" or e["data_com"].year != ano:
                continue
            if so_extra and not e["extraordinario"]:
                continue
            s += e["valor_liquido"]
        return s

    tot = total(ultimo)
    rotulado = total(ultimo, so_extra=True)
    anteriores = [total(a) for a in anos_completos[:-1] if total(a) > 0]
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
        "proventos_ultimo_ano_liquido": tot,
        "mediana_anterior_liquida": mediana,
    }


def aplicar_dy_liquido(dy_bruto, eventos, hoje):
    """DY do Fundamentus (bruto) × líquido/bruto dos eventos de 12 meses. O preço já está no DY."""
    fator = fator_liquido_12m(eventos, hoje)
    if dy_bruto is None or fator is None:
        return dy_bruto, fator
    return dy_bruto * fator, fator
