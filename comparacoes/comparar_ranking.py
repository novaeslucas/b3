"""Compara o ranking publicado de 26/10/2026 com o score desta metodologia.

Não grava em resultados/. Usa o CSV antigo como base de preço, Jev e pilares que não
dependem de provento (rentabilidade e valuation). Recalcula payout, histórico e
crescimento. Exige rede (Fundamentus, B3 e CVM).

Uso, na raiz do repositório:  python comparacoes/comparar_ranking.py
"""
import csv
import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import dividendos as acoes  # noqa: E402
from b3lib.cvm import norm_cd, payout_por_cd, resumir, setor_financeiro  # noqa: E402
from b3lib.numeros import lin, media  # noqa: E402
from b3lib.proventos import agregar_por_ano, parse_proventos_html, tabela_anual  # noqa: E402

ANTIGO = os.path.join(RAIZ, "resultados", "resultado_dividendos.csv")
SAIDA_CSV = os.path.join(RAIZ, "comparacoes", "ranking_2026_10_26.csv")
SAIDA_MD = os.path.join(RAIZ, "comparacoes", "ranking_2026_10_26.md")
PESOS = acoes.PESOS


def num(s):
    if s is None or s == "":
        return None
    return float(s)


def pilar_novo(m, liq, cresc):
    s_payout = 0.0 if m.get("sem_cobertura") else acoes._nota_payout(m["payout"])
    if s_payout is not None and m.get("anos_prejuizo_com_provento"):
        s_payout = max(0.0, s_payout - acoes.PENALIDADE_PREJUIZO_COM_PROVENTO * m["anos_prejuizo_com_provento"])
    sust = media([s_payout, lin(m["div_liq_ebit"], 4.0, 1.0), lin(liq, 0.8, 1.5)])
    hist = media([lin(m["anos_pagos"], acoes.ANOS_HIST - 3, acoes.ANOS_HIST), lin(m["cortes"], 3, 0)])
    cresc_p = media([lin(cresc, 0.0, 0.15), lin(m["cagr_div"], -0.05, 0.10)])
    return sust, hist, cresc_p


def score_misturado(sust, hist, cresc, rent_antigo, val_antigo):
    pilares = {
        "sustentabilidade": None if sust is None else sust,
        "historico": None if hist is None else hist,
        "rentabilidade": None if rent_antigo is None else rent_antigo / 100,
        "crescimento": None if cresc is None else cresc,
        "valuation": None if val_antigo is None else val_antigo / 100,
    }
    disp = {k: v for k, v in pilares.items() if v is not None}
    if not disp:
        return None
    total = sum(PESOS[k] * v for k, v in disp.items()) / sum(PESOS[k] for k in disp)
    return round(100 * total, 1)


def decisao(jev_classe, p_extra, score):
    if not jev_classe or score is None:
        return "-"
    if jev_classe == "DIVIDENDO_FRAGIL" or (p_extra or 0) >= 0.7:
        return "descartar"
    if jev_classe == "DIVIDENDO_SUSTENTAVEL" and score >= 60 and (p_extra or 0) < 0.5:
        return "estudar"
    return "monitorar"


def proventos(ticker):
    try:
        page = acoes.fetch(f"/proventos.php?papel={ticker}&tipo=2")
        eventos = parse_proventos_html(page)
        bruto = tabela_anual(page) or agregar_por_ano(eventos, liquido=False)
        liquido = agregar_por_ano(eventos, liquido=True, incluir_outros=False)
        return bruto, liquido
    except Exception as exc:
        print(f"  proventos {ticker}: {type(exc).__name__}: {exc}")
        return {}, {}


def codigo(ticker):
    try:
        return acoes.codigo_cvm(ticker)
    except Exception as exc:
        print(f"  cvm {ticker}: {type(exc).__name__}: {exc}")
        return None


def main():
    antigos = list(csv.DictReader(open(ANTIGO, encoding="utf-8-sig"), delimiter=";"))
    mercado = {r["ticker"]: r for r in acoes.carregar_mercado()}
    print(f"{len(antigos)} empresas no CSV de 26/10")

    with ThreadPoolExecutor(max_workers=4) as pool:
        series = list(pool.map(lambda r: proventos(r["ticker"]), antigos))
        codigos = list(pool.map(codigo, [r["ticker"] for r in antigos]))
    cds = {c for c in codigos if c}
    por_cd = payout_por_cd(cds, dt.date(2026, 10, 26), os.path.join(acoes.CACHE_DIR, "cvm"),
                           anos=[2021, 2022, 2023, 2024, 2025])

    linhas = []
    for antigo, (bruto, liquido), cd in zip(antigos, series, codigos):
        base = mercado.get(antigo["ticker"], {})
        r = {
            "dy": num(antigo["dy"]), "dy_bruto": num(antigo["dy"]), "pl": num(antigo["pl"]),
            "proventos_ano": bruto, "proventos_ano_liquido": liquido,
            "div_liquida": None, "ebit_12m": None,
        }
        cvm = por_cd.get(norm_cd(cd)) if cd else None
        if cvm:
            r["cvm"] = resumir(cvm["por_ano"], financeiro=setor_financeiro(antigo.get("setor")))
        m = acoes.metricas(r)
        m["div_liq_ebit"] = num(antigo["div_liq_ebit"])
        sust, hist, cresc = pilar_novo(m, base.get("liq_corr"), base.get("cresc_rec_5a"))
        novo = score_misturado(sust, hist, cresc, num(antigo["rentabilidade"]), num(antigo["valuation"]))
        p_extra = num(antigo["jev_p_extraordinario"])
        dec_nova = decisao(antigo["jev_classe"], p_extra, novo)
        linhas.append({
            "ticker": antigo["ticker"], "setor": antigo.get("setor"),
            "score_antigo": num(antigo["score"]), "score_novo": novo,
            "delta": None if novo is None else round(novo - num(antigo["score"]), 1),
            "payout_antigo": num(antigo["payout"]), "payout_novo": m["payout"],
            "payout_fonte": m["payout_fonte"],
            "decisao_antiga": antigo["decisao"], "decisao_nova": dec_nova,
            "jev_classe": antigo["jev_classe"], "jev_p_extraordinario": p_extra,
            "anos_prejuizo_com_provento": m["anos_prejuizo_com_provento"],
            "cagr_novo": m["cagr_div"],
        })

    os.makedirs(os.path.dirname(SAIDA_CSV), exist_ok=True)
    with open(SAIDA_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(linhas[0]), delimiter=";")
        w.writeheader()
        w.writerows(linhas)

    mudou = [x for x in linhas if x["decisao_antiga"] != x["decisao_nova"]]
    fontes = {}
    for x in linhas:
        fontes[x["payout_fonte"]] = fontes.get(x["payout_fonte"], 0) + 1
    deltas = sorted(x["delta"] for x in linhas if x["delta"] is not None)
    mediana = deltas[len(deltas) // 2] if deltas else None

    def cruzou_60(x):
        a, b = x["score_antigo"], x["score_novo"]
        return a is not None and b is not None and (a >= 60) != (b >= 60)

    travessias = [x for x in linhas if cruzou_60(x)]
    maiores = sorted(linhas, key=lambda z: abs(z["delta"] or 0), reverse=True)[:12]
    texto = [
        "# Ranking antigo × novo — pregão de 26/10/2026",
        "",
        "Base antiga: `resultados/resultado_dividendos.csv` (DY bruto × P/L, série bruta, Jev daquela execução).",
        "Score novo: payout bruto (CVM = soma dos proventos ÷ soma dos lucros em 5 anos, ou DY bruto × P/L),",
        "série líquida de JCP e penalidade de provento em ano de prejuízo.",
        "Liquidez corrente e crescimento de receita saem do Fundamentus baixado nesta execução (30/10),",
        "não do pregão de 26/10: um pedaço do delta pode ser atualização desses dois indicadores,",
        "que não estavam no CSV antigo. Rentabilidade e valuation ficaram nos pilares já gravados.",
        "O Jev não foi chamado de novo. A decisão nova combina a classe e o P(extra) antigos com o score novo.",
        "",
        "A faixa do payout (nota cheia até 90%, zero acima de 120%) **não foi recalibrada**.",
        "O payout voltou a ser bruto, a mesma escala da faixa. Nenhuma decisão mudou com o Jev antigo.",
        "Quem cruzou 60 continuou na mesma decisão porque o Jev (frágil, ou P(extra) alto) já segurava.",
        "",
        f"Empresas: {len(linhas)}. Mediana do delta de score: {mediana}.",
        f"Fonte do payout: {fontes}.",
        f"Decisão diferente: {len(mudou)}. Cruzaram o score 60: {len(travessias)}.",
        "",
        "## Decisão diferente",
        "",
        "| Ticker | Score antigo | Score novo | Delta | Decisão antiga | Decisão nova | Fonte |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    if mudou:
        for x in sorted(mudou, key=lambda z: z["ticker"]):
            texto.append(
                f"| {x['ticker']} | {x['score_antigo']} | {x['score_novo']} | {x['delta']} | "
                f"{x['decisao_antiga']} | {x['decisao_nova']} | {x['payout_fonte']} |"
            )
    else:
        texto.append("| — | | | | nenhuma | | |")
    texto += [
        "",
        "## Cruzaram 60, decisão igual",
        "",
        "| Ticker | Score antigo | Score novo | Decisão | Jev | Fonte |",
        "|---|---:|---:|---|---|---|",
    ]
    for x in sorted(travessias, key=lambda z: z["ticker"]):
        texto.append(
            f"| {x['ticker']} | {x['score_antigo']} | {x['score_novo']} | {x['decisao_antiga']} | "
            f"{x['jev_classe']} | {x['payout_fonte']} |"
        )
    texto += ["", "## Maiores variações de score", "",
              "| Ticker | Score antigo | Score novo | Delta | Payout antigo | Payout novo | Fonte | Anos com prejuízo e provento |",
              "|---|---:|---:|---:|---:|---:|---|---:|"]
    for x in maiores:
        texto.append(
            f"| {x['ticker']} | {x['score_antigo']} | {x['score_novo']} | {x['delta']} | "
            f"{x['payout_antigo']} | {x['payout_novo']} | {x['payout_fonte']} | {x['anos_prejuizo_com_provento']} |"
        )
    texto.append("")
    with open(SAIDA_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(texto) + "\n")
    print("\n".join(texto[-12:]))
    print(f"CSV em {SAIDA_CSV}")


if __name__ == "__main__":
    main()
