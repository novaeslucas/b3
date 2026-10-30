"""Parsers de HTML do Fundamentus usados pelo script de ações. Sem rede."""
import html
import re

from b3lib.numeros import num

COLS_RESULTADO = ["ticker", "cotacao", "pl", "pvp", "psr", "dy", "p_ativo", "p_capgiro", "p_ebit", "p_acl",
                  "ev_ebit", "ev_ebitda", "mrg_bruta", "mrg_ebit", "mrg_liq", "liq_corr", "roic", "roe",
                  "liq_2m", "patrim_liq", "div_liq_patrim", "cresc_rec_5a"]


def parse_resultado_acoes(page):
    """Uma linha por ticker da tabela resultado.php."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page or "", re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) != len(COLS_RESULTADO):
            continue
        ticker = re.sub(r"<[^>]+>", "", tds[0]).strip()
        rows.append({"ticker": ticker, **{c: num(re.sub(r"<[^>]+>", "", v)) for c, v in zip(COLS_RESULTADO[1:], tds[1:])}})
    return rows


def parse_campos_detalhe(page):
    """Rótulo -> primeiro valor em detalhes.php (a primeira ocorrência é a de 12 meses)."""
    campos = {}
    pares = re.findall(r'<span class="txt">([^<]+)</span></td>\s*<td class="data[^"]*">\s*'
                       r'<span class="(?:txt|oscil)">(?:<a[^>]*>|<font[^>]*>)?\s*([^<]*)', page or "", re.S)
    for label, valor in pares:
        campos.setdefault(html.unescape(label).strip(), html.unescape(valor).strip())
    return campos
