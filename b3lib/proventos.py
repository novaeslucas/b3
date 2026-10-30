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
# Pico de provento por ação contra a mediana dos anos fechados, na mesma série.
FATOR_PICO_PROVENTOS = 1.8
FATOR_MISTO_PROVENTOS = 1.3
ANOS_MEDIANA_PROVENTOS = 5


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
        if e["classe"] in ("outro", "ajuste") and not incluir_outros:
            continue
        if e["classe"] == "ajuste":
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


def _fatores_ajuste(eventos):
    """(data-com, ações novas / ações antigas) de desdobramento, grupamento e bonificação."""
    fatores = []
    for e in eventos:
        if e.get("classe") != "ajuste" and not e.get("fator"):
            continue
        tipo = sem_acento(e.get("tipo") or "").lower()
        if e.get("classe") != "ajuste" and not any(k in tipo for k in ("desdobr", "grupament", "bonific")):
            continue
        fator = e.get("fator")
        if not fator or fator <= 0:
            continue
        fatores.append((e["data_com"], float(fator)))
    return fatores


def _valor_ajustado(valor, data, fatores):
    """Traz o provento por ação para a quantidade de ações de hoje."""
    mult = 1.0
    for data_fator, fator in fatores:
        if data_fator > data:
            mult *= fator
    return valor / mult if mult else valor


def _provento_caixa(e):
    return e.get("classe") in ("dividendo", "jcp")


def classificar_natureza(eventos, hoje, _anos_completos=None, pago_de_reserva=False):
    """Recorrente, misto, extraordinário ou histórico insuficiente.

    O pico compara proventos por ação brutos (data-com) dos 12 meses com a mediana
    dos proventos por ação dos anos-calendário já fechados, no máximo cinco, da
    mesma série, ajustados por desdobramento, grupamento e bonificação. Não usa
    lucro nem DY × P/L. Razão acima de 1,8 é extraordinário. Entre 1,3 e 1,8 só
    vira misto se algum evento da janela vier rotulado; senão, recorrente.
    Menos de três anos fechados não dispara pico (histórico insuficiente), salvo
    rótulo da companhia ou débito em reserva de lucros de exercícios anteriores.
    """
    inicio = hoje - dt.timedelta(days=365)
    fatores = _fatores_ajuste(eventos)
    caixa = [e for e in eventos if _provento_caixa(e)]

    def bruto(e):
        return _valor_ajustado(e["valor_bruto"], e["data_com"], fatores)

    por_ano = {}
    for e in caixa:
        por_ano[e["data_com"].year] = por_ano.get(e["data_com"].year, 0.0) + bruto(e)
    primeiro = min(por_ano) if por_ano else None
    fechados = []
    if primeiro is not None:
        for ano in range(hoje.year - ANOS_MEDIANA_PROVENTOS, hoje.year):
            if ano >= primeiro:
                fechados.append(por_ano.get(ano, 0.0))
    mediana = statistics.median(fechados) if len(fechados) >= 3 else None
    janela = [e for e in caixa if inicio < e["data_com"] <= hoje]
    ttm = sum(bruto(e) for e in janela)
    rotulados = [e for e in janela if e.get("extraordinario")]
    razao = None
    if mediana is not None and mediana > 0:
        razao = ttm / mediana
    elif mediana == 0 and ttm > 0:
        razao = float("inf")

    gatilhos = []
    if pago_de_reserva:
        gatilhos.append("reserva")
    if razao is not None and razao > FATOR_PICO_PROVENTOS:
        gatilhos.append("pico_proventos")
    if rotulados:
        gatilhos.append("rotulado")

    if "reserva" in gatilhos or "pico_proventos" in gatilhos:
        natureza = "extraordinario"
    elif "rotulado" in gatilhos and razao is not None and FATOR_MISTO_PROVENTOS <= razao <= FATOR_PICO_PROVENTOS:
        natureza = "misto"
    elif "rotulado" in gatilhos:
        natureza = "extraordinario"
    elif mediana is None:
        natureza = "historico_insuficiente"
    else:
        natureza = "recorrente"
    return {
        "natureza_proventos": natureza,
        "gatilho_natureza": "+".join(gatilhos),
        "parcela_extraordinaria": 0.0 if not ttm or razao is None or razao <= 1 else min(1.0, (razao - 1) / razao),
        "proventos_12m_bruto": ttm,
        "mediana_proventos_acao": mediana,
        "razao_proventos": None if razao is None or razao == float("inf") else razao,
        "proventos_anuais_bruto": por_ano,
        "eventos_rotulados_12m": [
            {"date": e["data_com"].isoformat(), "type": e.get("tipo"), "gross_per_share": round(bruto(e), 4)}
            for e in rotulados
        ],
        "pago_de_reserva": bool(pago_de_reserva),
    }


def aplicar_dy_liquido(dy_bruto, eventos, hoje):
    """DY do Fundamentus (bruto) × líquido/bruto dos eventos de 12 meses. O preço já está no DY."""
    fator = fator_liquido_12m(eventos, hoje)
    if dy_bruto is None or fator is None:
        return dy_bruto, fator
    return dy_bruto * fator, fator
