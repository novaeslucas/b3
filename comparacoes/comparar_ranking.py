"""Compara o ranking de 26/10/2026 com o score desta metodologia e, com chave, o Jev novo.

Na raiz do repositório, com `.env` (OPENROUTER_API_KEY ou TYPESAFE_API_KEY):

    python comparacoes/comparar_ranking.py

Reaproveita cache de Fundamentus, B3 e CVM do dia. Chama o Jev no estado novo e
reescreve comparacoes/ranking_2026_10_26.md. Sem chave, não inventa julgamento:
grava a comparação determinística e lista quem mudou de estado.
"""
import csv
import datetime as dt
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import dividendos as acoes  # noqa: E402
from b3lib.cvm import norm_cd, pacotes_por_cd, resumo_empresa, setor_financeiro  # noqa: E402
from b3lib.numeros import lin, media  # noqa: E402
from b3lib.proventos import agregar_por_ano, classificar_natureza, parse_proventos_html, tabela_anual  # noqa: E402

ANTIGO = os.path.join(RAIZ, "resultados", "resultado_dividendos.csv")
SAIDA_CSV = os.path.join(RAIZ, "comparacoes", "ranking_2026_10_26.csv")
SAIDA_MD = os.path.join(RAIZ, "comparacoes", "ranking_2026_10_26.md")
JEV_CACHE = os.path.join(RAIZ, "cache", "jev_ranking_2026_10_26.json")
PREGAO = dt.date(2026, 10, 26)
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
        return bruto, liquido, eventos
    except Exception as exc:
        print(f"  proventos {ticker}: {type(exc).__name__}: {exc}")
        return {}, {}, []


def codigo(ticker):
    try:
        return acoes.codigo_cvm(ticker)
    except Exception as exc:
        print(f"  cvm {ticker}: {type(exc).__name__}: {exc}")
        return None


def chamar_jev(pares):
    """Chama o Jev no estado novo. None se não houver chave — não inventa resposta.

    ``pares`` é uma lista de (linha, estado). O cache fica em cache/jev_ranking_2026_10_26.json.
    """
    acoes.load_env()
    env = next((e for e in acoes.PROVIDERS if os.environ.get(e)), None)
    if not env:
        print("Sem OPENROUTER_API_KEY ou TYPESAFE_API_KEY. O Jev não foi chamado.")
        return None
    cache = json.load(open(JEV_CACHE, encoding="utf-8")) if os.path.exists(JEV_CACHE) else {}
    pendentes = []
    for linha, estado in pares:
        chave = hashlib.sha1(json.dumps([estado, acoes.PERGUNTAS], sort_keys=True).encode()).hexdigest()
        linha["jev_key"] = chave
        if chave not in cache:
            pendentes.append((linha, estado, chave))
    if pendentes:
        url, model = acoes.PROVIDERS[env]
        with ThreadPoolExecutor(max_workers=8) as pool:
            respostas = list(pool.map(lambda p: acoes.ask_jev(p[1], url, model, os.environ[env]), pendentes))
        for (linha, _, chave), resp in zip(pendentes, respostas):
            a = resp["answers"]
            cache[chave] = {
                "ticker": linha["ticker"],
                "classe": a["classe"]["choice"],
                "p_extraordinario": round(a["extraordinario"]["noul"], 3),
            }
        os.makedirs(os.path.dirname(JEV_CACHE), exist_ok=True)
        with open(JEV_CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
        print(f"Jev classificou {len(pendentes)} empresas; {len(pares) - len(pendentes)} vieram do cache.")
    else:
        print(f"Jev: as {len(pares)} empresas já estavam em cache.")
    return {linha["ticker"]: cache[linha["jev_key"]] for linha, _ in pares}


def motivos_materiais(x):
    motivos = []
    antigo, novo = x["payout_antigo"], x["payout_novo"]
    if x["payout_fonte"] != "dy_x_pl":
        motivos.append(f"fonte {x['payout_fonte']}")
    if antigo is not None and novo is not None and abs(novo - antigo) >= 0.10:
        motivos.append("payout mudou ao menos 10 p.p.")
    if x.get("natureza") in ("misto", "extraordinario"):
        motivos.append(x["natureza"])
    if x.get("dividendo_acima_fcf") is True:
        motivos.append("dividendo_acima_fcf")
    if x["anos_prejuizo_com_provento"]:
        motivos.append(f"prejuízo com provento ×{x['anos_prejuizo_com_provento']}")
    return motivos


def main():
    antigos = list(csv.DictReader(open(ANTIGO, encoding="utf-8-sig"), delimiter=";"))
    mercado = {r["ticker"]: r for r in acoes.carregar_mercado()}
    print(f"{len(antigos)} empresas no CSV de 26/10")

    with ThreadPoolExecutor(max_workers=4) as pool:
        series = list(pool.map(lambda r: proventos(r["ticker"]), antigos))
        codigos = list(pool.map(codigo, [r["ticker"] for r in antigos]))
    cds = {c for c in codigos if c}
    anos_cvm, consolidado, individual = pacotes_por_cd(
        cds, PREGAO, os.path.join(acoes.CACHE_DIR, "cvm"), anos=[2021, 2022, 2023, 2024, 2025])

    linhas = []
    estados = []
    for antigo, (bruto, liquido, eventos), cd in zip(antigos, series, codigos):
        base = mercado.get(antigo["ticker"], {})
        financeiro = setor_financeiro(antigo.get("setor"))
        cvm = None
        if cd and (cd in consolidado or cd in individual):
            cvm = resumo_empresa(consolidado.get(norm_cd(cd)), individual.get(norm_cd(cd)),
                                 anos_cvm, financeiro=financeiro)
        nat = classificar_natureza(eventos, PREGAO, [2020, 2021, 2022, 2023, 2024, 2025])
        r = {
            "ticker": antigo["ticker"], "empresa": antigo.get("empresa"), "setor": antigo.get("setor"),
            "subsetor": None, "dy": num(antigo["dy"]), "dy_bruto": num(antigo["dy"]),
            "pl": num(antigo["pl"]), "pvp": num(antigo["pvp"]),
            "roe": num(antigo["roe"]), "roic": num(antigo["roic"]),
            "mrg_liq": base.get("mrg_liq"), "cresc_rec_5a": base.get("cresc_rec_5a"),
            "ev_ebitda": base.get("ev_ebitda"), "div_liq_patrim": base.get("div_liq_patrim"),
            "liq_corr": base.get("liq_corr"), "proventos_ano": bruto, "proventos_ano_liquido": liquido,
            "div_liquida": None, "ebit_12m": None, "lucro_12m": None, "oscil_12m": None,
            **nat,
        }
        if cvm:
            r["cvm"] = cvm
        m = acoes.metricas(r)
        m["div_liq_ebit"] = num(antigo["div_liq_ebit"])
        r["m"] = m
        sust, hist, cresc = pilar_novo(m, base.get("liq_corr"), base.get("cresc_rec_5a"))
        novo = score_misturado(sust, hist, cresc, num(antigo["rentabilidade"]), num(antigo["valuation"]))
        p_extra = num(antigo["jev_p_extraordinario"])
        linha = {
            "ticker": antigo["ticker"], "setor": antigo.get("setor"),
            "score_antigo": num(antigo["score"]), "score_novo": novo,
            "delta": None if novo is None else round(novo - num(antigo["score"]), 1),
            "payout_antigo": num(antigo["payout"]), "payout_novo": m["payout"],
            "payout_fonte": m["payout_fonte"],
            "decisao_antiga": antigo["decisao"],
            "decisao_nova": decisao(antigo["jev_classe"], p_extra, novo),
            "jev_classe": antigo["jev_classe"], "jev_p_extraordinario": p_extra,
            "jev_classe_nova": "", "jev_p_extraordinario_novo": "",
            "anos_prejuizo_com_provento": m["anos_prejuizo_com_provento"],
            "natureza": nat["natureza_proventos"],
            "dividendo_acima_fcf": m.get("dividendo_acima_fcf"),
            "cagr_novo": m["cagr_div"],
        }
        linhas.append(linha)
        estados.append((linha, acoes.estado_jev(r)))

    jev_novo = chamar_jev(estados)
    if jev_novo:
        for linha in linhas:
            j = jev_novo[linha["ticker"]]
            linha["jev_classe_nova"] = j["classe"]
            linha["jev_p_extraordinario_novo"] = j["p_extraordinario"]
            linha["decisao_nova"] = decisao(j["classe"], j["p_extraordinario"], linha["score_novo"])

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
        "O comando, na raiz do repositório, com o `.env` preenchido:",
        "",
        "    python comparacoes/comparar_ranking.py",
        "",
        "Ele reaproveita o cache do dia, chama o Jev no estado novo e reescreve este arquivo.",
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
    if jev_novo:
        mudou_jev = [x for x in linhas if x["decisao_antiga"] != x["decisao_nova"]]
        texto += [
            "## Decisão com o Jev chamado de novo",
            "",
            "Classe e P(extra) saíram do Jev sobre o estado novo (payout, natureza, dividendo acima do FCF).",
            "A decisão nova usa esse julgamento com o score novo. Não há resposta inventada.",
            "",
            f"Decisões diferentes da publicação de 26/10: {len(mudou_jev)}.",
            "",
            "| Ticker | Decisão antiga | Decisão nova | Jev antigo | Jev novo | P(extra) antigo | P(extra) novo | Score novo |",
            "|---|---|---|---|---|---:|---:|---:|",
        ]
        for x in sorted(mudou_jev, key=lambda z: z["ticker"]):
            texto.append(
                f"| {x['ticker']} | {x['decisao_antiga']} | {x['decisao_nova']} | {x['jev_classe']} | "
                f"{x['jev_classe_nova']} | {x['jev_p_extraordinario']} | {x['jev_p_extraordinario_novo']} | "
                f"{x['score_novo']} |"
            )
        if not mudou_jev:
            texto.append("| — | nenhuma | | | | | | |")
    else:
        materiais = [(x, motivos_materiais(x)) for x in linhas]
        materiais = [(x, m) for x, m in materiais if m]
        texto += [
            "## Jev não foi chamado nesta execução",
            "",
            "Não há `OPENROUTER_API_KEY` nem `TYPESAFE_API_KEY` neste ambiente. "
            "Nenhuma classe nova foi inventada. A coluna «decisão nova» acima ainda usa o Jev **antigo** "
            "com o score novo — isso não mede o efeito do estado novo no julgamento.",
            "",
            "Quem tem a chave roda, na raiz:",
            "",
            "    python comparacoes/comparar_ranking.py",
            "",
            "O script reescreve este arquivo com a diferença real de decisão.",
            "",
            "Enquanto isso, estes são os papéis cujo estado mudou de forma material "
            "(fonte ou valor do payout, natureza do provento, dividendo acima do FCF ou provento em ano de prejuízo). "
            "São os mais propensos a mudar de decisão quando o Jev for chamado.",
            "",
            "| Ticker | Decisão antiga | Score antigo | Score novo | Payout antigo | Payout novo | O que mudou |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
        for x, motivos in sorted(materiais, key=lambda z: z[0]["ticker"]):
            texto.append(
                f"| {x['ticker']} | {x['decisao_antiga']} | {x['score_antigo']} | {x['score_novo']} | "
                f"{x['payout_antigo']} | {x['payout_novo']} | {', '.join(motivos)} |"
            )
    texto.append("")
    with open(SAIDA_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(texto) + "\n")
    print("\n".join(texto[-12:]))
    print(f"CSV em {SAIDA_CSV}")


if __name__ == "__main__":
    main()
