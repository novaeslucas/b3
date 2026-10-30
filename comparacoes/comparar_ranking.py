"""Compara o ranking de 26/10/2026 com o score desta metodologia e, com chave, o Jev.

Na raiz do repositório:

    python comparacoes/comparar_ranking.py

Com `OPENROUTER_API_KEY` ou `TYPESAFE_API_KEY` no `.env`, chama o Jev três vezes
no estado antigo (mesma entrada, temperature 0) e três vezes no estado novo.
A taxa de virada de base é a fração de papéis em que as três chamadas antigas
não foram unânimes. A decisão nova é a maioria das três chamadas novas.
«Além da base» são as trocas de decisão em papéis que eram unânimes no estado antigo.

    python comparacoes/comparar_ranking.py --repeticoes 1
    python comparacoes/comparar_ranking.py --sem-jev

Sem chave, ou com `--sem-jev`, não inventa julgamento: grava o score e lista
quem mudou de estado. Liquidez corrente e crescimento de receita de 30/10 não
entram no delta: não há cache do Fundamentus de 26/10 para esses dois campos.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import dividendos as acoes  # noqa: E402
from b3lib.cvm import norm_cd, pacotes_por_cd, resumo_empresa, setor_financeiro  # noqa: E402
from b3lib.numeros import lin, media  # noqa: E402
from b3lib.proventos import agregar_por_ano, parse_proventos_html, tabela_anual  # noqa: E402

ANTIGO = os.path.join(RAIZ, "resultados", "resultado_dividendos.csv")
SAIDA_CSV = os.path.join(RAIZ, "comparacoes", "ranking_2026_10_26.csv")
SAIDA_MD = os.path.join(RAIZ, "comparacoes", "ranking_2026_10_26.md")
JEV_CACHE = os.path.join(RAIZ, "cache", "jev_ranking_2026_10_26.json")
PREGAO = dt.date(2026, 10, 26)
PESOS = acoes.PESOS
TEMPERATURA = 0


def num(s):
    if s is None or s == "":
        return None
    return float(s)


def cel(v, casas=None):
    if v in (None, ""):
        return "—"
    if casas is not None and isinstance(v, float):
        return f"{v:.{casas}f}"
    return v


def payout_txt(valor, fonte):
    if fonte == "sem_base_de_lucro":
        return "n/d (sem base de lucro)"
    return cel(valor, 3)


def score_misturado(sust, hist, cresc, rent_antigo, val_antigo):
    pilares = {
        "sustentabilidade": sust,
        "historico": hist,
        "rentabilidade": None if rent_antigo is None else rent_antigo / 100,
        "crescimento": cresc,
        "valuation": None if val_antigo is None else val_antigo / 100,
    }
    disp = {k: v for k, v in pilares.items() if v is not None}
    if not disp:
        return None
    total = sum(PESOS[k] * v for k, v in disp.items()) / sum(PESOS[k] for k in disp)
    return round(100 * total, 1)


def notas_isoladas(payout, sem_cobertura, prejuizo, div_liq, anos_pagos, cortes, cagr, penalizar):
    """Subnotas do score isolado. Sem base de lucro deixa o payout de fora (None, não zero)."""
    if sem_cobertura:
        s_payout = None
    else:
        s_payout = acoes._nota_payout(payout)
        if penalizar and s_payout is not None and prejuizo:
            s_payout = max(0.0, s_payout - acoes.PENALIDADE_PREJUIZO_COM_PROVENTO * prejuizo)
    return {
        "payout": s_payout,
        "div_liq_ebit": lin(div_liq, 4.0, 1.0),
        "anos_pagos": lin(anos_pagos, acoes.ANOS_HIST - 3, acoes.ANOS_HIST),
        "cortes": lin(cortes, 3, 0),
        "cagr_div": lin(cagr, -0.05, 0.10),
    }


def score_isolado(payout, sem_cobertura, prejuizo, div_liq, anos_pagos, cortes, cagr, rent, val, penalizar):
    """Score sem liquidez corrente e sem crescimento de receita, dos dois lados do delta."""
    n = notas_isoladas(payout, sem_cobertura, prejuizo, div_liq, anos_pagos, cortes, cagr, penalizar)
    sust = media([n["payout"], n["div_liq_ebit"]])
    hist = media([n["anos_pagos"], n["cortes"]])
    return score_misturado(sust, hist, n["cagr_div"], rent, val)


def _diferente(a, b):
    if a is None or b is None:
        return a is not b
    return round(a, 4) != round(b, 4)


def causa_decisao(linha):
    """Campos que mudaram a decisão. Vazio quando a decisão não mudou."""
    if linha["decisao_antiga"] == linha["decisao_nova"]:
        return ""
    causas = []
    antigo, novo = linha["_comp_antigo"], linha["_comp_novo"]
    if _diferente(antigo["payout"], novo["payout"]):
        causas.append("payout")
    if _diferente(antigo["anos_pagos"], novo["anos_pagos"]) or _diferente(antigo["cortes"], novo["cortes"]):
        causas.append("historico")
    if _diferente(antigo["cagr_div"], novo["cagr_div"]):
        causas.append("cagr_div")
    if _diferente(antigo["div_liq_ebit"], novo["div_liq_ebit"]):
        causas.append("div_liq_ebit")
    p_novo = linha.get("jev_p_extraordinario_novo")
    if p_novo not in ("", None) and _diferente(linha.get("jev_p_extraordinario"), p_novo):
        causas.append("P(extra)")
    classe_nova = linha.get("jev_classe_nova") or ""
    if classe_nova not in ("", "empate") and classe_nova != linha.get("jev_classe"):
        causas.append("jev_classe")
    if not causas and _diferente(linha.get("score_antigo"), linha.get("score_novo")):
        causas.append("score")
    return ", ".join(causas)


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


def oscilacao(ticker):
    """Oscilação de 12 meses do detalhes.php, para a pergunta de provento extraordinário."""
    try:
        return acoes.detalhes(ticker).get("oscil_12m")
    except Exception as exc:
        print(f"  oscilação {ticker}: {type(exc).__name__}: {exc}")
        return None


def codigo(ticker):
    try:
        return acoes.codigo_cvm(ticker)
    except Exception as exc:
        print(f"  cvm {ticker}: {type(exc).__name__}: {exc}")
        return None


def chave_jev(estado, rotulo, repeticao, temperature):
    pacote = [rotulo, repeticao, temperature, estado, acoes.PERGUNTAS]
    return hashlib.sha1(json.dumps(pacote, sort_keys=True).encode()).hexdigest()


def chamar_jev(pares, repeticoes, rotulo, temperature=TEMPERATURA):
    """N chamadas por empresa. None sem chave — não inventa resposta.

    Cada repetição tem chave própria, para a taxa de virada não colapsar no cache.
    """
    acoes.load_env()
    env = next((e for e in acoes.PROVIDERS if os.environ.get(e)), None)
    if not env:
        print("Sem OPENROUTER_API_KEY ou TYPESAFE_API_KEY. O Jev não foi chamado.")
        return None
    cache = json.load(open(JEV_CACHE, encoding="utf-8")) if os.path.exists(JEV_CACHE) else {}
    url, model = acoes.PROVIDERS[env]
    pendentes = []
    for linha, estado in pares:
        for rep in range(repeticoes):
            chave = chave_jev(estado, rotulo, rep, temperature)
            if chave not in cache:
                pendentes.append((linha, estado, chave, rep))
    if pendentes:
        with ThreadPoolExecutor(max_workers=8) as pool:
            respostas = list(pool.map(
                lambda p: acoes.ask_jev(p[1], url, model, os.environ[env], temperature=temperature),
                pendentes,
            ))
        for (linha, _, chave, rep), resp in zip(pendentes, respostas):
            a = resp["answers"]
            cache[chave] = {
                "ticker": linha["ticker"], "rep": rep, "rotulo": rotulo,
                "classe": a["classe"]["choice"],
                "p_extraordinario": round(a["extraordinario"]["noul"], 3),
            }
        os.makedirs(os.path.dirname(JEV_CACHE), exist_ok=True)
        with open(JEV_CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
        print(f"Jev ({rotulo}): {len(pendentes)} chamadas; o resto veio do cache.")
    else:
        print(f"Jev ({rotulo}): as {len(pares) * repeticoes} chamadas já estavam em cache.")
    por_ticker = {}
    for linha, estado in pares:
        por_ticker[linha["ticker"]] = [
            cache[chave_jev(estado, rotulo, rep, temperature)] for rep in range(repeticoes)
        ]
    return por_ticker


def maioria(amostras):
    """(classe, p mediano, unânime). Sem maioria estrita a classe fica None."""
    cont = Counter(a["classe"] for a in amostras)
    classe, votos = cont.most_common(1)[0]
    p = statistics.median(a["p_extraordinario"] for a in amostras)
    if votos * 2 <= len(amostras):
        return None, p, False
    return classe, p, votos == len(amostras)


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
    if x.get("pago_de_reserva"):
        motivos.append("pago de reserva")
    return motivos


def estado_antigo(antigo, bruto):
    """Estado pré-metodologia: payout do CSV, série bruta, sem natureza nem CVM."""
    r = {
        "ticker": antigo["ticker"], "empresa": antigo.get("empresa"), "setor": antigo.get("setor"),
        "subsetor": None, "dy": num(antigo["dy"]), "dy_bruto": num(antigo["dy"]),
        "pl": num(antigo["pl"]), "pvp": num(antigo["pvp"]),
        "roe": num(antigo["roe"]), "roic": num(antigo["roic"]),
        "mrg_liq": None, "cresc_rec_5a": None, "liq_corr": None,
        "ev_ebitda": None, "div_liq_patrim": None,
        "proventos_ano": bruto, "div_liquida": None, "ebit_12m": None,
        "lucro_12m": None, "oscil_12m": None,
    }
    m = acoes.metricas(r)
    m["div_liq_ebit"] = num(antigo["div_liq_ebit"])
    m["payout"] = num(antigo["payout"])
    m["payout_fonte"] = "dy_x_pl"
    m["anos_pagos"] = num(antigo["anos_pagos"])
    m["cortes"] = num(antigo["cortes"])
    m["cagr_div"] = num(antigo["cagr_div"])
    m["anos_prejuizo_com_provento"] = 0
    m["sem_cobertura"] = False
    r["m"] = m
    return acoes.estado_jev(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeticoes", type=int, default=3,
                    help="chamadas do Jev em cada estado (padrão 3; maioria e taxa de virada)")
    ap.add_argument("--sem-jev", action="store_true", help="não chama o Jev mesmo com chave no .env")
    ap.add_argument("--limpar-cache-jev", action="store_true",
                    help="renomeia o cache do Jev desta comparação para uma rodada nova não reaproveitar as respostas")
    args = ap.parse_args()
    if args.limpar_cache_jev and os.path.exists(JEV_CACHE):
        destino = JEV_CACHE[:-5] + ".anterior.json" if JEV_CACHE.endswith(".json") else JEV_CACHE + ".anterior"
        if os.path.exists(destino):
            destino = destino[:-5] + f".{dt.datetime.now():%Y%m%d%H%M%S}.json"
        os.replace(JEV_CACHE, destino)
        print(f"Cache do Jev renomeado para {destino}")
    if args.repeticoes < 1:
        sys.exit("--repeticoes começa em 1")

    antigos = list(csv.DictReader(open(ANTIGO, encoding="utf-8-sig"), delimiter=";"))
    print(f"{len(antigos)} empresas no CSV de 26/10")

    with ThreadPoolExecutor(max_workers=4) as pool:
        series = list(pool.map(lambda r: proventos(r["ticker"]), antigos))
        codigos = list(pool.map(codigo, [r["ticker"] for r in antigos]))
        oscils = list(pool.map(oscilacao, [r["ticker"] for r in antigos]))
    cds = {c for c in codigos if c}
    anos_cvm, consolidado, individual = pacotes_por_cd(
        cds, PREGAO, os.path.join(acoes.CACHE_DIR, "cvm"), anos=[2021, 2022, 2023, 2024, 2025])

    acoes.HOJE = PREGAO
    linhas = []
    estados_novos = []
    estados_antigos = []
    for antigo, (bruto, liquido, eventos), cd, oscil in zip(antigos, series, codigos, oscils):
        financeiro = setor_financeiro(antigo.get("setor"))
        cvm = None
        if cd and (cd in consolidado or cd in individual):
            cvm = resumo_empresa(consolidado.get(norm_cd(cd)), individual.get(norm_cd(cd)),
                                 anos_cvm, financeiro=financeiro)
        r = {
            "ticker": antigo["ticker"], "empresa": antigo.get("empresa"), "setor": antigo.get("setor"),
            "subsetor": None, "dy": num(antigo["dy"]), "dy_bruto": num(antigo["dy"]),
            "pl": num(antigo["pl"]), "pvp": num(antigo["pvp"]),
            "roe": num(antigo["roe"]), "roic": num(antigo["roic"]),
            "mrg_liq": None, "cresc_rec_5a": None, "liq_corr": None,
            "ev_ebitda": None, "div_liq_patrim": None,
            "proventos_ano": bruto, "proventos_ano_liquido": liquido,
            "eventos_proventos": eventos,
            "div_liquida": None, "ebit_12m": None, "lucro_12m": None, "oscil_12m": oscil,
        }
        if cvm:
            r["cvm"] = cvm
        acoes.atualizar_natureza(r, PREGAO)
        m = acoes.metricas(r)
        m["div_liq_ebit"] = num(antigo["div_liq_ebit"])
        r["m"] = m
        rent, val = num(antigo["rentabilidade"]), num(antigo["valuation"])
        cagr_antigo, cagr_novo = num(antigo["cagr_div"]), m["cagr_div"]
        if cagr_antigo is None or cagr_novo is None:
            cagr_antigo = cagr_novo = None
        comp_antigo = notas_isoladas(
            num(antigo["payout"]), False, 0, m["div_liq_ebit"],
            num(antigo["anos_pagos"]), num(antigo["cortes"]), cagr_antigo, penalizar=False,
        )
        comp_novo = notas_isoladas(
            m["payout"], m.get("sem_cobertura"), m.get("anos_prejuizo_com_provento") or 0, m["div_liq_ebit"],
            m["anos_pagos"], m["cortes"], cagr_novo, penalizar=True,
        )
        isolado_antigo = score_isolado(
            num(antigo["payout"]), False, 0, m["div_liq_ebit"],
            num(antigo["anos_pagos"]), num(antigo["cortes"]), cagr_antigo,
            rent, val, penalizar=False,
        )
        isolado_novo = score_isolado(
            m["payout"], m.get("sem_cobertura"), m.get("anos_prejuizo_com_provento") or 0, m["div_liq_ebit"],
            m["anos_pagos"], m["cortes"], cagr_novo, rent, val, penalizar=True,
        )
        publicado = num(antigo["score"])
        if isolado_antigo is None or isolado_novo is None or publicado is None:
            novo, delta = None, None
        else:
            delta = round(isolado_novo - isolado_antigo, 1)
            novo = round(min(100.0, max(0.0, publicado + delta)), 1)
        p_extra = num(antigo["jev_p_extraordinario"])
        linha = {
            "ticker": antigo["ticker"], "setor": antigo.get("setor"),
            "score_antigo": publicado, "score_novo": novo,
            "delta": delta,
            "isolado_antigo": isolado_antigo, "isolado_novo": isolado_novo,
            "dy": num(antigo["dy"]), "dy_recorrente": r.get("dy_recorrente"),
            "payout_antigo": num(antigo["payout"]), "payout_novo": m["payout"],
            "payout_fonte": m["payout_fonte"],
            "decisao_antiga": antigo["decisao"],
            "decisao_score_novo": decisao(antigo["jev_classe"], p_extra, novo),
            "decisao_nova": decisao(antigo["jev_classe"], p_extra, novo),
            "jev_classe": antigo["jev_classe"], "jev_p_extraordinario": p_extra,
            "jev_classe_nova": "", "jev_p_extraordinario_novo": "",
            "jev_unanime_antigo": "", "jev_unanime_novo": "",
            "anos_prejuizo_com_provento": m["anos_prejuizo_com_provento"],
            "natureza": r.get("natureza_proventos"),
            "gatilho_natureza": r.get("gatilho_natureza") or "",
            "causa": "",
            "_comp_antigo": comp_antigo, "_comp_novo": comp_novo,
            "pago_de_reserva": r.get("pago_de_reserva"),
            "dividendo_acima_fcf": m.get("dividendo_acima_fcf"),
            "cagr_novo": m["cagr_div"],
        }
        linhas.append(linha)
        estados_novos.append((linha, acoes.estado_jev(r)))
        estados_antigos.append((linha, estado_antigo(antigo, bruto)))

    jev_novo = jev_base = None
    if not args.sem_jev:
        jev_base = chamar_jev(estados_antigos, args.repeticoes, "antigo")
        jev_novo = chamar_jev(estados_novos, args.repeticoes, "novo")
    if jev_novo and jev_base:
        for linha in linhas:
            classe_a, _, unanime_a = maioria(jev_base[linha["ticker"]])
            classe_n, p_n, unanime_n = maioria(jev_novo[linha["ticker"]])
            linha["jev_unanime_antigo"] = unanime_a
            linha["jev_unanime_novo"] = unanime_n
            linha["jev_classe_nova"] = classe_n or "empate"
            linha["jev_p_extraordinario_novo"] = p_n
            if classe_n:
                linha["decisao_nova"] = decisao(classe_n, p_n, linha["score_novo"])
    elif jev_novo or jev_base:
        print("Jev incompleto: uma das duas levas não voltou. A decisão nova fica com o Jev antigo.")
    for linha in linhas:
        linha["causa"] = causa_decisao(linha)
        del linha["_comp_antigo"], linha["_comp_novo"]

    os.makedirs(os.path.dirname(SAIDA_CSV), exist_ok=True)
    with open(SAIDA_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(linhas[0]), delimiter=";")
        w.writeheader()
        w.writerows(linhas)

    def cruzou_60(x):
        a, b = x["score_antigo"], x["score_novo"]
        return a is not None and b is not None and (a >= 60) != (b >= 60)

    mudou_score = [x for x in linhas if x["decisao_antiga"] != x["decisao_score_novo"]]
    mudou = [x for x in linhas if x["decisao_antiga"] != x["decisao_nova"]]
    travessias = [x for x in linhas if cruzou_60(x)]
    fontes = {}
    for x in linhas:
        fontes[x["payout_fonte"]] = fontes.get(x["payout_fonte"], 0) + 1
    deltas = sorted(x["delta"] for x in linhas if x["delta"] is not None)
    mediana = deltas[len(deltas) // 2] if deltas else None
    maiores = sorted(linhas, key=lambda z: abs(z["delta"] or 0), reverse=True)[:12]
    n_base = len(linhas)
    if jev_base:
        nao_unanimes = [x for x in linhas if x["jev_unanime_antigo"] is False]
        taxa = len(nao_unanimes) / n_base if n_base else 0
        alem = [x for x in mudou if x["jev_unanime_antigo"] is True]
    else:
        nao_unanimes, taxa, alem = [], None, []

    texto = [
        "# Ranking antigo × novo — pregão de 26/10/2026",
        "",
        "Este arquivo é gerado por `python comparacoes/comparar_ranking.py`. Os números daqui valem;",
        "não copie uma contagem de decisão para outro texto à mão.",
        "",
        "Base antiga: `resultados/resultado_dividendos.csv` (DY bruto × P/L, série bruta, Jev daquela execução).",
        "Score novo: o score publicado mais o delta da metodologia. O delta compara os dois lados sem",
        "liquidez corrente e sem crescimento de receita — não há cache do Fundamentus de 26/10 para esses",
        "dois campos, e o de 30/10 não entra. Rentabilidade e valuation ficam nos pilares gravados.",
        "O payout novo é a soma da CVM em cinco anos, ou DY bruto × P/L quando o lucro não foi lido,",
        "diverge do valor de mercado ÷ P/L, ou não há base. A série de dividendos do lado novo é líquida de JCP.",
        "",
        "Comandos, na raiz do repositório, com o `.env` preenchido:",
        "",
        "    python comparacoes/comparar_ranking.py",
        "    python comparacoes/comparar_ranking.py --repeticoes 1",
        "    python comparacoes/comparar_ranking.py --sem-jev",
        "    python comparacoes/comparar_ranking.py --limpar-cache-jev",
        "",
        "O padrão chama o Jev três vezes no estado antigo e três no estado novo, com temperature 0",
        "(se a API recusar o campo, a chamada segue sem ele). A decisão nova é a maioria.",
        "Sem chave, o Jev não é chamado e nenhuma classe é inventada.",
        "",
        f"Empresas: {len(linhas)}. Mediana do delta de score: {mediana}.",
        f"Fonte do payout: {fontes}.",
        f"Decisão diferente só pelo score, com o Jev antigo: {len(mudou_score)}.",
        f"Cruzaram o score 60: {len(travessias)}.",
        "",
    ]
    nat_cont = Counter(x["natureza"] for x in linhas)
    gat_cont = Counter(x["gatilho_natureza"] or "(sem gatilho)" for x in linhas if x["natureza"] == "extraordinario")
    texto += [
        f"Natureza: {dict(nat_cont)}.",
        f"Extraordinário por gatilho: {dict(gat_cont)}.",
        "Retomada: " + ", ".join(x["ticker"] for x in linhas if x["natureza"] == "retomada") + ".",
        "Antecipação nov/dez 2025: " + ", ".join(
            x["ticker"] for x in linhas if "antecipacao_tributacao_2026" in (x["gatilho_natureza"] or "")) + ".",
        "A oscilação de 12 meses vai no estado do Jev (detalhes.php desta execução) e não entra no score.",
        "dy_recorrente é o DY bruto sem os proventos de nov/dez de 2025 acima da mediana; também não entra no score.",
        "",
        "## Natureza nos papéis pedidos",
        "",
        "| Ticker | Natureza | Gatilho | Score antigo | Score novo |",
        "|---|---|---|---:|---:|",
    ]
    por_ticker = {x["ticker"]: x for x in linhas}
    for tk in ("VIVT3", "UGPA3", "TGMA3", "LEVE3", "EZTC3", "MOTV3", "RANI3", "MOVI3", "CXSE3",
               "TIMS3", "CURY3", "POMO4", "PLPL3", "LREN3", "BRSR6", "ABEV3", "COGN3", "TEND3", "SEER3"):
        x = por_ticker.get(tk)
        if not x:
            texto.append(f"| {tk} | — | — | | |")
            continue
        texto.append(
            f"| {tk} | {x['natureza']} | {x['gatilho_natureza'] or '—'} | {x['score_antigo']} | {x['score_novo']} |"
        )
    texto += [
        "",
        "## Decisão diferente com o Jev antigo e o score novo",
        "",
        "Ainda não é o efeito do estado novo no julgamento. É o score novo com a classe publicada em 26/10.",
        "",
        "| Ticker | Score antigo | Score novo | Delta | Decisão antiga | Decisão com score novo (Jev antigo) | Causa | Fonte |",
        "|---|---:|---:|---:|---|---|---|---|",
    ]
    if mudou_score:
        for x in sorted(mudou_score, key=lambda z: z["ticker"]):
            texto.append(
                f"| {x['ticker']} | {x['score_antigo']} | {x['score_novo']} | {x['delta']} | "
                f"{x['decisao_antiga']} | {x['decisao_score_novo']} | {x['causa'] or '—'} | {x['payout_fonte']} |"
            )
    else:
        texto.append("| — | | | | nenhuma | | | |")
    texto += [
        "",
        "## Cruzaram 60",
        "",
        "| Ticker | Score antigo | Score novo | Decisão antiga | Decisão com score novo (Jev antigo) | Decisão com Jev novo | Fonte |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for x in sorted(travessias, key=lambda z: z["ticker"]):
        jev_novo_txt = x["decisao_nova"] if jev_novo else "não chamado"
        texto.append(
            f"| {x['ticker']} | {x['score_antigo']} | {x['score_novo']} | {x['decisao_antiga']} | "
            f"{x['decisao_score_novo']} | {jev_novo_txt} | {x['payout_fonte']} |"
        )
    if not travessias:
        texto.append("| — | | | | | | |")
    texto += ["", "## Maiores variações de score", "",
              "| Ticker | Score antigo | Score novo | Delta | Payout antigo | Payout novo | Fonte | Anos com prejuízo e provento |",
              "|---|---:|---:|---:|---:|---:|---|---:|"]
    for x in maiores:
        texto.append(
            f"| {x['ticker']} | {x['score_antigo']} | {x['score_novo']} | {x['delta']} | "
            f"{payout_txt(x['payout_antigo'], None)} | {payout_txt(x['payout_novo'], x['payout_fonte'])} | "
            f"{x['payout_fonte']} | {x['anos_prejuizo_com_provento']} |"
        )
    texto.append("")
    if jev_novo and jev_base:
        texto += [
            "## Estabilidade do Jev",
            "",
            f"Repetições por estado: {args.repeticoes}. Temperature pedida: {TEMPERATURA}.",
            f"Não unânimes no estado antigo: {len(nao_unanimes)} de {n_base}.",
            f"Taxa de virada de base: {taxa:.1%}.",
            f"Decisão da maioria nova diferente da publicada em 26/10: {len(mudou)}.",
            f"Dessas, em papel unânime no estado antigo (além da base): {len(alem)}.",
            "",
            "### Além da base",
            "",
            "| Ticker | Decisão antiga | Decisão nova | Causa | Jev antigo | Jev novo | P(extra) antigo | P(extra) novo | Score novo |",
            "|---|---|---|---|---|---|---:|---:|---:|",
        ]
        for x in sorted(alem, key=lambda z: z["ticker"]):
            texto.append(
                f"| {x['ticker']} | {x['decisao_antiga']} | {x['decisao_nova']} | {x['causa'] or '—'} | "
                f"{x['jev_classe']} | {x['jev_classe_nova']} | {x['jev_p_extraordinario']} | "
                f"{x['jev_p_extraordinario_novo']} | {x['score_novo']} |"
            )
        if not alem:
            texto.append("| — | nenhuma | | | | | | | |")
        texto += [
            "",
            "### Todas as decisões diferentes da publicação",
            "",
            "| Ticker | Decisão antiga | Decisão nova | Causa | Jev antigo | Jev novo | Unânime no antigo | Score novo |",
            "|---|---|---|---|---|---|---|---:|",
        ]
        for x in sorted(mudou, key=lambda z: z["ticker"]):
            texto.append(
                f"| {x['ticker']} | {x['decisao_antiga']} | {x['decisao_nova']} | {x['causa'] or '—'} | "
                f"{x['jev_classe']} | {x['jev_classe_nova']} | {x['jev_unanime_antigo']} | {x['score_novo']} |"
            )
        if not mudou:
            texto.append("| — | nenhuma | | | | | | |")
    else:
        materiais = [(x, motivos_materiais(x)) for x in linhas]
        materiais = [(x, m) for x, m in materiais if m]
        texto += [
            "## Jev não foi chamado nesta execução",
            "",
            "Não há chave neste ambiente, ou a execução usou `--sem-jev`. "
            "Nenhuma classe nova foi inventada. A coluna «decisão com score novo» usa o Jev **antigo** "
            "com o score novo — isso não mede o efeito do estado novo no julgamento.",
            "A taxa de virada de base não foi medida.",
            "",
            "Quem tem a chave roda, na raiz:",
            "",
            "    python comparacoes/comparar_ranking.py",
            "",
            "O padrão faz três chamadas no estado antigo e a maioria de três no estado novo, e reescreve este arquivo.",
            "Uma chamada só, sem a taxa de virada: `python comparacoes/comparar_ranking.py --repeticoes 1`.",
            "",
            "Enquanto isso, estes são os papéis cujo estado mudou de forma material "
            "(fonte ou valor do payout, natureza do provento, dividendo acima do FCF, provento em ano de prejuízo "
            "ou pagamento vindo de reserva de lucros). São os mais propensos a mudar de decisão quando o Jev for chamado.",
            "",
            "| Ticker | Decisão antiga | Score antigo | Score novo | Payout antigo | Payout novo | O que mudou |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
        for x, motivos in sorted(materiais, key=lambda z: z[0]["ticker"]):
            texto.append(
                f"| {x['ticker']} | {x['decisao_antiga']} | {x['score_antigo']} | {x['score_novo']} | "
                f"{payout_txt(x['payout_antigo'], None)} | {payout_txt(x['payout_novo'], x['payout_fonte'])} | "
                f"{', '.join(motivos)} |"
            )
    texto.append("")
    with open(SAIDA_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(texto) + "\n")
    print(f"Mediana do delta: {mediana}. Fontes: {fontes}.")
    print(f"Decisão diferente só pelo score: {len(mudou_score)}. Cruzaram 60: {len(travessias)}.")
    if taxa is not None:
        print(f"Taxa de virada de base: {taxa:.1%}. Além da base: {len(alem)}.")
    print(f"CSV em {SAIDA_CSV}")


if __name__ == "__main__":
    main()
