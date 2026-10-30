"""Leitura do pregão pós-eleição (05/10/2026) sobre os ativos já analisados.

Parte das análises de dividendos.py e fiis.py (mesmos dados do Fundamentus, cotação de 02/10) e:
  1. Busca no Yahoo Finance o fechamento de 02/10 e o do pregão pós-eleição para cada ativo, Ibovespa e IFIX.
  2. Reaplica o preço novo aos indicadores que dependem dele (DY, P/L, P/VP, EV/EBITDA aproximado) e
     recalcula o score determinístico. Payout, ROE, histórico de proventos etc. não mudam com o preço.
  3. Pergunta ao Jev, para cada ação, três fatores ligados à eleição (só sobre o negócio, não sobre o preço):
     exposição a decisões do governo federal, receita atrelada ao dólar e sensibilidade aos juros domésticos.
  4. Cruza a variação do dia com esses fatores, com a decisão (estudar/monitorar/descartar) e com o tipo de FII.

A classificação de qualidade dos dividendos não é refeita: um pregão muda preço, não fundamento.
Saídas: resumo no console, leitura_pos_eleicao.csv e dashboard_eleicao.html (de dashboard_eleicao_template.html).

Uso:  python leitura_pos_eleicao.py [--data-base 2026-10-02] [--data-pregao 2026-10-05]
Não é recomendação de investimento.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

import dividendos as acoes
import fiis
from dividendos import CACHE_DIR, PROVIDERS, ask_jev, fetch, load_env
from b3lib.log import configurar, registrar_falha, resumo_falhas
from b3lib.yahoo import chart_resultado, fechamentos_brasilia

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1mo&interval=1d"
JEV_CACHE = os.path.join(CACHE_DIR, "jev_eleicao.json")
SAIDA_CSV = os.path.join("resultados", "leitura_pos_eleicao.csv")
SAIDA_HTML = os.path.join("resultados", "dashboard_eleicao.html")
TEMPLATE_HTML = os.path.join("templates", "dashboard_eleicao_template.html")

PERGUNTAS = {
    "governo": {
        "type": "noul",
        "instructions": "Do decisions of Brazil's federal government materially drive the results of the company in "
                        "`company` — because the government controls it (state-owned or federal golden share), sets "
                        "or regulates its prices, tariffs or concessions, or is a dominant customer or funder?",
        "criteria": {
            "true": "Yes: state-controlled company, regulated tariffs or concessions, government-set prices, or "
                    "heavy dependence on federal policy, public banks or government contracts.",
            "false": "No: a private company whose results depend mainly on market competition, not federal decisions.",
        },
    },
    "dolar": {
        "type": "noul",
        "instructions": "Is a large part of the revenue of the company in `company` earned in US dollars or priced "
                        "in dollars, such as commodity exports (iron ore, pulp, oil, protein, steel) or foreign sales?",
        "criteria": {
            "true": "Yes: exporter or commodity producer whose revenue rises in reais when the dollar strengthens.",
            "false": "No: revenue is mostly earned and priced in reais in the domestic market.",
        },
    },
    "juros": {
        "type": "score",
        "instructions": "How sensitive are the earnings and valuation of the company in `company` to Brazilian "
                        "domestic interest rates (Selic and long-term rates)?",
        "criteria": [
            "Low: revenue is largely in dollars or set by global commodity prices, little domestic debt; domestic "
            "rates barely affect results.",
            "Moderate: domestic business with some exposure through debt costs or consumer demand, but not driven "
            "by credit.",
            "High: results or valuation depend heavily on domestic rates, such as retailers selling on credit, "
            "homebuilders, highly leveraged companies, long-duration regulated utilities, or brokers and "
            "exchanges whose volumes rise when rates fall.",
        ],
    },
}


# ---------------------------------------------------------------- cotações

def fechamentos(ticker, base, pregao):
    """(fechamento na data-base, fechamento no pregão) segundo o Yahoo Finance."""
    simbolo = ticker if ticker.startswith("^") else f"{ticker}.SA"
    try:
        texto = fetch(None, YAHOO.format(simbolo), "utf-8", f"yahoo_{simbolo}_{pregao:%Y%m%d}")
        fech = fechamentos_brasilia(chart_resultado(texto))
        return fech.get(base), fech.get(pregao)
    except Exception as e:
        registrar_falha(ticker, "yahoo", e)
        return None, None


# ---------------------------------------------------------------- reconstrução das análises (cache do Fundamentus)

def carregar_acoes():
    mercado = acoes.carregar_mercado()
    lidos = {r["ticker"] for r in csv.DictReader(open(acoes.csv_da_analise(acoes.HOJE), encoding="utf-8-sig"), delimiter=";")}
    cands = [r for r in mercado if r["ticker"] in lidos]
    for r in cands:
        r.update(acoes.detalhes(r["ticker"]))
        r["proventos_ano"] = acoes.proventos_por_ano(r["ticker"])
        r["m"] = acoes.metricas(r)
        r["score"], r["pilares"] = acoes.score(r)
    acoes.classificar(cands)  # respostas já em cache: mesmos dados da última execução
    for r in cands:
        r["decisao"] = acoes.decisao(r)
    return cands


def carregar_fiis():
    mercado = fiis.carregar_mercado()
    lidos = {r["ticker"] for r in csv.DictReader(open(fiis.SAIDA_CSV, encoding="utf-8-sig"), delimiter=";")}
    cands = [r for r in mercado if r["ticker"] in lidos]
    for r in cands:
        r.update(fiis.detalhes(r["ticker"]))
        r["rendimentos"], r["amortizacoes_12m"] = fiis.rendimentos(r["ticker"])
        r["m"] = fiis.metricas(r)
        r["score"], r["pilares"] = fiis.score(r)
    fiis.classificar(cands)
    for r in cands:
        r["decisao"] = fiis.decisao(r)
    return cands


def reprecificar(r, fator, modulo):
    """Cópia de r com os indicadores dependentes de preço ajustados pelo fator (preço novo / preço base)."""
    n = dict(r)
    n["cotacao"] = r["cotacao"] * fator
    n["dy"] = r["dy"] / fator if r["dy"] is not None else None
    n["pvp"] = r["pvp"] * fator if r["pvp"] is not None else None
    if modulo is acoes:
        n["pl"] = r["pl"] * fator if r["pl"] is not None else None
        # EV = valor de mercado + dívida líquida; sem o valor de mercado separado, escala o múltiplo inteiro
        # (superestima a variação em empresas muito alavancadas).
        n["ev_ebitda"] = r["ev_ebitda"] * fator if r["ev_ebitda"] else r["ev_ebitda"]
    else:
        n["ffo_yield"] = r["ffo_yield"] / fator if r["ffo_yield"] is not None else None
        n["valor_mercado"] = r["valor_mercado"] * fator if r["valor_mercado"] else r["valor_mercado"]
    n["m"] = modulo.metricas(n)
    n["score"], n["pilares"] = modulo.score(n)
    return n


# ---------------------------------------------------------------- Jev: fatores ligados à eleição

def fatores_jev(cands):
    cache = json.load(open(JEV_CACHE, encoding="utf-8")) if os.path.exists(JEV_CACHE) else {}
    pend = []
    for r in cands:
        state = acoes.estado_jev(r)
        r["eleicao_key"] = hashlib.sha1(json.dumps([state["company"]["ticker"], state["company"]["name"],
                                                     state["company"]["sector"], PERGUNTAS], sort_keys=True).encode()).hexdigest()
        if r["eleicao_key"] not in cache:
            pend.append((r, state))
    if pend:
        env = next((e for e in PROVIDERS if os.environ.get(e)), None)
        if not env:
            sys.exit("Nenhuma chave encontrada no .env. Adicione OPENROUTER_API_KEY=... ou TYPESAFE_API_KEY=...")
        url, model = PROVIDERS[env]
        with ThreadPoolExecutor(max_workers=8) as pool:
            resps = list(pool.map(lambda p: ask_jev(p[1], url, model, os.environ[env], PERGUNTAS), pend))
        print(f"Jev ({resps[0]['model']}) avaliou fatores de {len(pend)} empresas; "
              f"tokens de entrada: {sum(x['usage']['input_tokens'] for x in resps)}")
        for (r, _), resp in zip(pend, resps):
            a = resp["answers"]
            cache[r["eleicao_key"]] = {"ticker": r["ticker"], "p_governo": round(a["governo"]["noul"], 3),
                                       "p_dolar": round(a["dolar"]["noul"], 3),
                                       "juros": round(a["juros"]["score"], 2),
                                       "juros_confianca": round(a["juros"]["confidence"], 3)}
        with open(JEV_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    for r in cands:
        r["fatores"] = cache[r["eleicao_key"]]


# ---------------------------------------------------------------- relatório

def media_pct(vals):
    vals = [v for v in vals if v is not None]
    return (f"{100 * statistics.mean(vals):+6.2f}%  (mediana {100 * statistics.median(vals):+6.2f}%, n={len(vals)})"
            if vals else "   -")


def grupo(titulo, linhas, chave):
    print(f"\n{titulo}")
    grupos = {}
    for r in linhas:
        grupos.setdefault(chave(r), []).append(r["var_dia"])
    for k, v in sorted(grupos.items(), key=lambda kv: -statistics.mean(kv[1])):
        print(f"  {str(k):<34}{media_pct(v)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-base", default="2026-10-02", help="pregão anterior (preço usado nas análises)")
    ap.add_argument("--data-pregao", default="2026-10-05", help="pregão analisado")
    args = ap.parse_args()
    base, pregao = dt.date.fromisoformat(args.data_base), dt.date.fromisoformat(args.data_pregao)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    configurar()
    load_env()

    ca, cf = carregar_acoes(), carregar_fiis()
    ativos = [(r, acoes) for r in ca] + [(r, fiis) for r in cf]
    with ThreadPoolExecutor(max_workers=8) as pool:
        precos = list(pool.map(lambda x: fechamentos(x[0]["ticker"], base, pregao), ativos))
        ibov, ifix = pool.map(lambda t: fechamentos(t, base, pregao), ["^BVSP", "IFIX.SA"])

    sem_preco = []
    for (r, mod), (p0, p1) in zip(ativos, precos):
        if not p0 or not p1:
            sem_preco.append(r["ticker"])
            r["var_dia"] = None
            continue
        if abs(p0 / r["cotacao"] - 1) > 0.02:  # base do Yahoo deve bater com a cotação usada na análise
            print(f"  aviso: {r['ticker']} base Yahoo {p0:.2f} x Fundamentus {r['cotacao']:.2f}")
        r["var_dia"] = p1 / p0 - 1
        r["novo"] = reprecificar(r, p1 / r["cotacao"], mod)
    fatores_jev(ca)

    var = lambda p: f"{100 * (p[1] / p[0] - 1):+.2f}%" if p[0] and p[1] else "indisponível"
    print(f"\nPregão {pregao:%d/%m/%Y} vs {base:%d/%m/%Y}:  Ibovespa {var(ibov)}   IFIX {var(ifix)}")
    if sem_preco:
        print(f"Sem cotação no Yahoo: {', '.join(sem_preco)}")
    ca = [r for r in ca if r["var_dia"] is not None]
    cf = [r for r in cf if r["var_dia"] is not None]

    # ------------------------------------------------ ações
    print("\n=================== AÇÕES ===================")
    print(f"Média das {len(ca)} ações analisadas: {media_pct([r['var_dia'] for r in ca])}")
    grupo("Por decisão da análise de dividendos:", ca, lambda r: r["decisao"])
    grupo("Por setor (Fundamentus):", ca, lambda r: (r.get("setor") or "-")[:33])
    f = lambda r: r["fatores"]
    grupo("Por exposição ao governo federal (Jev):", ca,
          lambda r: "exposta (P >= 0,5)" if f(r)["p_governo"] >= 0.5 else "pouco exposta (P < 0,5)")
    grupo("Por receita em dólar (Jev):", ca,
          lambda r: "receita em dólar (P >= 0,5)" if f(r)["p_dolar"] >= 0.5 else "receita em reais (P < 0,5)")
    grupo("Por sensibilidade a juros domésticos (Jev, escala 0-2):", ca,
          lambda r: "alta (>= 1,5)" if f(r)["juros"] >= 1.5 else "moderada (0,5-1,5)" if f(r)["juros"] >= 0.5 else "baixa (< 0,5)")

    ca.sort(key=lambda r: -r["var_dia"])
    cab = (f"{'Ticker':<8}{'Setor':<24}{'Dia':>8}{'DY antes':>9}{'DY hoje':>8}{'Score':>13}  "
           f"{'Gov':>4}{'US$':>5}{'Juros':>6}  Decisão")
    linha = lambda r: (f"{r['ticker']:<8}{(r.get('setor') or '')[:23]:<24}{100 * r['var_dia']:+7.2f}%"
                       f"{100 * r['dy']:8.1f}%{100 * r['novo']['dy']:7.1f}%{r['score']:6.1f}->{r['novo']['score']:<5.1f}  "
                       f"{f(r)['p_governo']:4.2f}{f(r)['p_dolar']:5.2f}{f(r)['juros']:6.2f}  {r['decisao']}")
    print(f"\nMaiores altas:\n{cab}")
    for r in ca[:10]:
        print(linha(r))
    print(f"\nMenores variações:\n{cab}")
    for r in ca[-10:]:
        print(linha(r))
    print("\nAções em 'estudar':")
    for r in [r for r in ca if r["decisao"] == "estudar"]:
        print(linha(r))
    abaixo = [f"{r['ticker']} ({100 * r['novo']['dy']:.1f}%)" for r in ca if r["novo"]["dy"] < 0.05 <= r["dy"]]
    print(f"\nDY caiu abaixo do mínimo de 5% com o preço de hoje: {', '.join(abaixo) or 'nenhuma'}")

    # ------------------------------------------------ FIIs
    print("\n=================== FIIs ===================")
    print(f"Média dos {len(cf)} FIIs analisados: {media_pct([r['var_dia'] for r in cf])}")
    grupo("Por tipo (Jev):", cf, lambda r: r["jev"]["tipo"])
    grupo("Por segmento (Fundamentus):", cf, lambda r: r["segmento"])
    grupo("Por decisão da análise de rendimentos:", cf, lambda r: r["decisao"])
    cf.sort(key=lambda r: -r["var_dia"])
    cab = f"{'Ticker':<8}{'Tipo':<17}{'Dia':>8}{'DY antes':>9}{'DY hoje':>8}{'P/VP hoje':>10}{'Score':>13}  Decisão"
    linha_f = lambda r: (f"{r['ticker']:<8}{r['jev']['tipo']:<17}{100 * r['var_dia']:+7.2f}%{100 * r['dy']:8.1f}%"
                         f"{100 * r['novo']['dy']:7.1f}%{r['novo']['pvp']:10.2f}{r['score']:6.1f}->{r['novo']['score']:<5.1f}  "
                         f"{r['decisao']}")
    print(f"\nMaiores altas:\n{cab}")
    for r in cf[:8]:
        print(linha_f(r))
    print(f"\nMenores variações:\n{cab}")
    for r in cf[-8:]:
        print(linha_f(r))
    print("\nFIIs em 'estudar':")
    for r in [r for r in cf if r["decisao"] == "estudar"]:
        print(linha_f(r))
    abaixo = [f"{r['ticker']} ({100 * r['novo']['dy']:.1f}%)" for r in cf if r["novo"]["dy"] < 0.08 <= r["dy"]]
    print(f"\nDY caiu abaixo do mínimo de 8% com o preço de hoje: {', '.join(abaixo) or 'nenhum'}")

    with open(SAIDA_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["classe_ativo", "ticker", "setor_segmento", "tipo_fii_jev", "var_dia", "cotacao_base",
                    "cotacao_pregao", "dy_antes", "dy_hoje", "pl_ou_pvp_hoje", "score_antes", "score_hoje",
                    "jev_classe", "decisao", "jev_p_governo", "jev_p_dolar", "jev_juros_0a2"])
        for r in ca:
            w.writerow(["acao", r["ticker"], r.get("setor"), None, r["var_dia"], r["cotacao"], r["novo"]["cotacao"],
                        r["dy"], r["novo"]["dy"], r["novo"]["pl"], r["score"], r["novo"]["score"], r["jev"]["classe"],
                        r["decisao"], f(r)["p_governo"], f(r)["p_dolar"], f(r)["juros"]])
        for r in cf:
            w.writerow(["fii", r["ticker"], r["segmento"], r["jev"]["tipo"], r["var_dia"], r["cotacao"],
                        r["novo"]["cotacao"], r["dy"], r["novo"]["dy"], r["novo"]["pvp"], r["score"], r["novo"]["score"],
                        r["jev"]["classe"], r["decisao"], None, None, None])
    gerar_dashboard(ca, cf, base, pregao, ibov, ifix, sem_preco)
    print(f"\nDetalhes em {SAIDA_CSV} e {SAIDA_HTML}. Não é recomendação de investimento.")
    resumo_falhas()


def gerar_dashboard(ca, cf, base, pregao, ibov, ifix, sem_preco):
    """Grava dashboard_eleicao.html a partir do template, embutindo os dados desta leitura."""
    var = lambda p: p[1] / p[0] - 1 if p[0] and p[1] else None
    comum = lambda r: {
        "ticker": r["ticker"], "var_dia": r["var_dia"], "cot_base": r["cotacao"], "cot_pregao": r["novo"]["cotacao"],
        "dy_antes": r["dy"], "dy_hoje": r["novo"]["dy"], "pvp_hoje": r["novo"]["pvp"],
        "score_antes": r["score"], "score_hoje": r["novo"]["score"],
        "jev_classe": r["jev"]["classe"], "jev_confianca": r["jev"]["confianca"], "decisao": r["decisao"],
    }
    dados = {
        "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "data_base": f"{base:%d/%m/%Y}", "data_pregao": f"{pregao:%d/%m/%Y}",
        "ibov": var(ibov), "ifix": var(ifix), "sem_preco": sem_preco,
        "acoes": [{**comum(r), "nome": r.get("empresa"), "setor": r.get("setor"), "pl_hoje": r["novo"]["pl"],
                   "p_governo": r["fatores"]["p_governo"], "p_dolar": r["fatores"]["p_dolar"],
                   "juros": r["fatores"]["juros"]} for r in ca],
        "fiis": [{**comum(r), "nome": r.get("nome"), "segmento": r["segmento"], "tipo": r["jev"]["tipo"]} for r in cf],
    }
    template = open(TEMPLATE_HTML, encoding="utf-8").read()
    payload = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    with open(SAIDA_HTML, "w", encoding="utf-8") as f:
        f.write(template.replace("/*__DADOS__*/null", payload))


if __name__ == "__main__":
    main()
