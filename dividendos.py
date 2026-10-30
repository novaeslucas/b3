"""Classifica a qualidade dos dividendos das ações da B3 com score determinístico + Jev.

Fluxo:
  1. Fundamentus (resultado.php): indicadores de todas as ações em uma requisição.
  2. Filtros rígidos no código: liquidez, DY mínimo, lucro positivo, um ticker por empresa.
  3. Fundamentus (detalhes.php e proventos.php) por candidata: setor, dívida, lucro, EBIT,
     oscilação de 12 meses e proventos por ano.
  4. Dividend Quality Score (0-100) calculado aqui no código, com pesos explícitos.
     O DY e a série por ação entram líquidos de IR sobre JCP. O payout do score prefere a média
     de 5 anos da CVM (proventos declarados ÷ lucro da controladora); sem DFP suficiente, cai
     para DY líquido × P/L. Há flag de provento recorrente/extraordinário e de dividendo acima do FCF.
  5. Jev classifica cada empresa (SUSTENTAVEL / ATENCAO / FRAGIL) e diz se o DY atual parece
    inflado por um pagamento extraordinário ou por queda da cotação. O estado inclui a natureza
    do provento e a fonte do payout (o hash do cache muda quando esse estado muda).
  6. Conferência: proventos por ano do Fundamentus (brutos) x B3 (GetListedCashDividends, por espécie ON/PN).
  7. Saídas: tabela no console, resultado_dividendos.csv e dashboard.html (gerado de dashboard_template.html).

O Jev não calcula nada: recebe os indicadores já calculados e devolve julgamentos com probabilidades.
Downloads ficam em cache/ (válidos no dia) e respostas do Jev em cache/jev.json (por conteúdo).
Funções compartilhadas ficam em b3lib/.

Uso:  python dividendos.py [--dy-min 5] [--liq-min 2000000] [--limite 80] [--sem-jev] [--sem-b3]
                          [--sem-cvm] [--precos-yahoo AAAA-MM-DD]
Com --precos-yahoo, a cotação de cada ação é trocada pelo fechamento do Yahoo Finance naquela data antes dos filtros, e
os indicadores que dependem do preço (DY, P/L, P/VP, EV/EBITDA aproximado, oscilação de 12 meses) são reajustados.
Serve para analisar depois de um pregão que o Fundamentus ainda não incorporou (ele atualiza de madrugada).
Usa o Jev via OpenRouter (OPENROUTER_API_KEY) ou direto na TypeSafe (TYPESAFE_API_KEY), conforme o .env.
Não é recomendação de investimento.
"""
import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from b3lib.cvm import (
    MIN_ANOS_SCORE, anos_exercicio, norm_cd, payout_por_cd, resumir, setor_financeiro,
)
from b3lib.fundamentus import parse_campos_detalhe, parse_resultado_acoes
from b3lib.http import B3, CACHE_DIR, fetch as _fetch, load_env
from b3lib.http import definir_hoje
from b3lib.log import configurar, registrar_falha, resumo_falhas
from b3lib.numeros import lin, media, num
from b3lib.proventos import (
    agregar_por_ano, aplicar_dy_liquido, classificar_natureza, parse_proventos_html, tabela_anual,
)
from b3lib.yahoo import chart_resultado, fechamentos_brasilia

PROVIDERS = {  # variável de ambiente -> (endpoint, modelo)
    "OPENROUTER_API_KEY": ("https://openrouter.ai/api/alpha/decisions", "~typesafe/jev-latest"),
    "TYPESAFE_API_KEY": ("https://api.typesafe.ai/v1/systemone", "jev-latest"),
}
FUNDAMENTUS = "https://www.fundamentus.com.br"
ESPECIES = {"3": "ON", "4": "PN", "5": "PNA", "6": "PNB"}  # sufixo do ticker -> espécie na B3
JEV_CACHE = os.path.join(CACHE_DIR, "jev.json")
SAIDA_CSV = os.path.join("resultados", "resultado_dividendos.csv")
SAIDA_HTML = os.path.join("resultados", "dashboard.html")
TEMPLATE_HTML = os.path.join("templates", "dashboard_template.html")
HOJE = dt.date.today()
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}.SA?range=1mo&interval=1d"
PRECOS_EM = None  # texto sobre a origem das cotações (só com --precos-yahoo)
ANOS_HIST = 6  # anos completos de proventos considerados (exclui o ano corrente, ainda parcial)

# Pesos do Dividend Quality Score; cada pilar é a média dos sub-indicadores disponíveis.
PESOS = {"sustentabilidade": 0.25, "historico": 0.20, "rentabilidade": 0.20, "crescimento": 0.20, "valuation": 0.15}

CLASSES = {
    "DIVIDENDO_SUSTENTAVEL": "Sustainable: dividends are covered by recurring earnings, have been paid every year "
                             "with a stable or rising trend, leverage is manageable for the sector and "
                             "profitability is solid.",
    "DIVIDENDO_ATENCAO": "Watch: dividends look broadly payable but at least one relevant concern exists, such as "
                         "payout close to or above earnings, high or rising leverage, irregular or declining "
                         "payments, or weak profitability.",
    "DIVIDENDO_FRAGIL": "Fragile: the current dividend level is unlikely to be maintained: payout well above "
                        "earnings, collapsing or negative profits, heavy leverage, frequent cuts, or a yield "
                        "driven mainly by a one-off distribution.",
}


# ---------------------------------------------------------------- utilidades

def fetch(path, url=None, encoding="latin-1", nome=None):
    """Mesma assinatura de antes. O dia do cache é ``HOJE`` deste módulo (scripts antigos o reatribuem)."""
    definir_hoje(HOJE)
    return _fetch(path, url, encoding, nome)


# ---------------------------------------------------------------- coleta

def carregar_mercado():
    """Tabela resultado.php: uma linha por ticker com os principais indicadores."""
    return parse_resultado_acoes(fetch("/resultado.php"))


def csv_da_analise(data):
    """CSV da análise feita com os dados do Fundamentus de `data`: resultados/snapshot_AAAA_MM_DD/ se a análise
    daquele dia foi arquivada (as leituras diárias usam o snapshot de 05/10), senão o resultado atual."""
    arquivado = os.path.join("resultados", f"snapshot_{data:%Y_%m_%d}", "resultado_dividendos.csv")
    return arquivado if os.path.exists(arquivado) else SAIDA_CSV


def fechamento_yahoo(ticker, data):
    """(fechamento na data, fechamento do pregão anterior) no Yahoo Finance; (None, None) se não houver."""
    try:
        texto = fetch(None, YAHOO.format(ticker), "utf-8", f"yahoo_div_{ticker}_{data:%Y%m%d}")
        fech = fechamentos_brasilia(chart_resultado(texto))
        antes = [d for d in fech if d < data]
        return fech.get(data), fech[max(antes)] if antes else None
    except Exception as e:
        registrar_falha(ticker, "yahoo", e)
        return None, None


def reprecificar(rows, data, liq_min):
    """Troca a cotação do Fundamentus pelo fechamento do Yahoo em `data` e reajusta os indicadores de preço.

    Só para os tickers com liquidez suficiente (os demais nem passam no filtro). O fator é preço novo ÷ cotação do
    Fundamentus; avisa quando a cotação do Fundamentus não bate com o pregão anterior do Yahoo (> 2%)."""
    alvo = [r for r in rows if (r["liq_2m"] or 0) >= liq_min and r["cotacao"]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        precos = list(pool.map(lambda r: fechamento_yahoo(r["ticker"], data), alvo))
    sem = []
    for r, (novo, anterior) in zip(alvo, precos):
        if not novo:
            sem.append(r["ticker"])
            continue
        if anterior and abs(anterior / r["cotacao"] - 1) > 0.02:
            print(f"  aviso: {r['ticker']} Fundamentus {r['cotacao']:.2f} x Yahoo do pregão anterior {anterior:.2f}")
        f = novo / r["cotacao"]
        r["fator_preco"], r["cotacao"] = f, novo
        r["dy"] = r["dy"] / f if r["dy"] is not None else None
        for c in ("pl", "pvp", "psr", "p_ativo", "p_capgiro", "p_ebit", "p_acl"):
            r[c] = r[c] * f if r[c] is not None else None
        # EV = valor de mercado + dívida líquida; sem o valor de mercado separado, escala o múltiplo inteiro
        # (superestima a variação em empresas muito alavancadas). Mesma aproximação das leituras diárias.
        for c in ("ev_ebit", "ev_ebitda"):
            r[c] = r[c] * f if r[c] else r[c]
    print(f"Cotações de {data:%d/%m/%Y} (Yahoo) aplicadas a {len(alvo) - len(sem)} tickers"
          + (f"; sem cotação, mantêm o Fundamentus: {', '.join(sem)}" if sem else ""))


def filtrar(rows, dy_min, liq_min, limite):
    ok = [r for r in rows if (r["liq_2m"] or 0) >= liq_min and (r["dy"] or 0) >= dy_min and (r["pl"] or 0) > 0]
    por_empresa = {}  # mesma empresa (4 letras) -> ticker mais líquido
    for r in ok:
        raiz = r["ticker"][:4]
        if raiz not in por_empresa or r["liq_2m"] > por_empresa[raiz]["liq_2m"]:
            por_empresa[raiz] = r
    return sorted(por_empresa.values(), key=lambda r: -r["liq_2m"])[:limite]


def detalhes(ticker):
    campos = parse_campos_detalhe(fetch(f"/detalhes.php?papel={ticker}"))
    return {
        "setor": campos.get("Setor"),
        "subsetor": campos.get("Subsetor"),
        "empresa": campos.get("Empresa"),
        "oscil_12m": num(campos.get("12 meses")),
        "div_bruta": num(campos.get("Dív. Bruta")),
        "div_liquida": num(campos.get("Dív. Líquida")),
        "lucro_12m": num(campos.get("Lucro Líquido")),
        "ebit_12m": num(campos.get("EBIT")),
    }


def proventos_por_ano(ticker):
    """Tabela 'por ano' de proventos.php (R$ por ação, dividendos + JCP, valor bruto).

    Mantida para as leituras antigas, que comparam essa série com a B3. O score novo usa a série
    líquida montada em ``enriquecer``.
    """
    page = fetch(f"/proventos.php?papel={ticker}&tipo=2")
    return tabela_anual(page) or agregar_por_ano(parse_proventos_html(page), liquido=False)


def b3_get(metodo, params):
    p = base64.b64encode(json.dumps(params, separators=(",", ":")).encode()).decode()
    nome = f"b3_{metodo}_" + "_".join(str(v) for k, v in params.items() if k != "language")
    return json.loads(fetch(None, f"{B3}/{metodo}/{p}", "utf-8", nome))


def proventos_b3(ticker):
    """Proventos em dinheiro por ano (data-com) segundo a B3, para a espécie do ticker.

    Retorna (anos, situação). Units não são comparáveis: a B3 informa só ON/PN e a composição da unit
    varia por empresa. A série da B3 é bruta, como a tabela anual do Fundamentus.
    """
    especie = ESPECIES.get(ticker[4:])
    if not especie:
        return {}, "unit (não comparável)"
    raiz = ticker[:4]
    empresas = b3_get("GetInitialCompanies", {"language": "pt-br", "pageNumber": 1, "pageSize": 20, "company": raiz})
    nomes = [e["tradingName"] for e in empresas.get("results", []) if e["issuingCompany"] == raiz]
    if not nomes:
        return {}, "sem cadastro na B3"
    nome = nomes[0].replace("/", "").replace(".", "")
    # Ordenação da B3 é por espécie e depois por data, então é preciso ler todas as páginas.
    # pageSize acima de ~60 faz a B3 omitir registros silenciosamente.
    # Linhas idênticas NÃO são removidas: na prática são parcelas reais (ex.: Cemig paga em duas parcelas
    # iguais), e somá-las bate com o Fundamentus.
    eventos, pagina = [], 1
    while True:
        resp = b3_get("GetListedCashDividends", {"language": "pt-br", "pageNumber": pagina, "pageSize": 60,
                                                 "tradingName": nome})
        res = resp.get("results") or []
        for e in res:
            if e["typeStock"].strip() == especie and e["lastDatePriorEx"][-4:].isdigit():
                eventos.append((e["lastDatePriorEx"], e["valueCash"], e["quotedPerShares"]))
        if not res or pagina >= resp["page"]["totalPages"]:
            break
        pagina += 1
    anos = {}
    for data, valor, por in eventos:
        ano = int(data[-4:])
        anos[ano] = anos.get(ano, 0.0) + (num(valor) or 0.0) / (num(por) or 1.0)
    return anos, "ok" if anos else "sem proventos na B3"


def conferir(r):
    """Compara as séries anuais Fundamentus x B3 nos anos completos."""
    try:
        b3, situacao = proventos_b3(r["ticker"])
    except Exception as e:  # endpoint não oficial: falha não deve parar a análise
        registrar_falha(r["ticker"], "conferência B3", e)
        b3, situacao = {}, f"erro B3: {type(e).__name__}"
    r["proventos_b3"] = b3
    if situacao != "ok":
        return situacao
    divergentes = []
    for ano in r["m"]["anos_completos"]:
        f, b = r["proventos_ano"].get(ano, 0.0), b3.get(ano, 0.0)
        if abs(f - b) > 0.10 * max(f, b) and abs(f - b) > 0.01:
            divergentes.append(str(ano))
    return "confere" if not divergentes else "diverge em " + ", ".join(divergentes)


# ---------------------------------------------------------------- métricas e score

def metricas(r):
    # Série líquida quando o enriquecimento novo a calculou; senão a série bruta (leituras antigas).
    anos = r.get("proventos_ano_liquido") or r["proventos_ano"]
    completos = [HOJE.year - i for i in range(ANOS_HIST, 0, -1)]  # ex.: 2020..2025
    serie = [anos.get(a, 0.0) for a in completos]
    pagos = sum(1 for v in serie if v > 0)
    cortes = sum(1 for a, b in zip(serie, serie[1:]) if a > 0 and b < a * 0.8)  # queda > 20% a/a
    ini, fim = serie[0], serie[-1]
    cagr = (fim / ini) ** (1 / (len(serie) - 1)) - 1 if ini > 0 and fim > 0 else None
    payout_aprox = r["dy"] * r["pl"] if r["dy"] is not None and r["pl"] else None
    cvm = r.get("cvm") or {}
    if cvm.get("util_no_score"):
        payout, fonte = cvm["payout_medio"], "cvm"
    else:
        payout, fonte = payout_aprox, "dy_x_pl"
    ebit = r.get("ebit_12m")
    alav = r["div_liquida"] / ebit if r.get("div_liquida") is not None and ebit and ebit > 0 else None
    return {
        "anos_completos": completos, "serie_div": serie, "anos_pagos": pagos, "cortes": cortes,
        "cagr_div": cagr, "payout": payout, "payout_dy_pl": payout_aprox, "payout_fonte": fonte,
        "paga_com_divida": cvm.get("paga_com_divida"), "cvm_anos": cvm.get("anos"),
        "div_liq_ebit": alav,
        "div_ultimo_vs_media": fim / (sum(serie[:-1]) / (len(serie) - 1)) if sum(serie[:-1]) > 0 else None,
    }


def score(r):
    m = r["m"]
    payout = m["payout"]
    s_payout = None if payout is None else (0.0 if payout > 1.2 else lin(payout, 1.2, 0.9) if payout > 0.9 else 1.0)
    pilares = {
        "sustentabilidade": media([s_payout, lin(m["div_liq_ebit"], 4.0, 1.0), lin(r["liq_corr"], 0.8, 1.5)]),
        "historico": media([lin(m["anos_pagos"], ANOS_HIST - 3, ANOS_HIST), lin(m["cortes"], 3, 0)]),
        "rentabilidade": media([lin(r["roe"], 0.05, 0.20), lin(r["roic"], 0.05, 0.15), lin(r["mrg_liq"], 0.0, 0.20)]),
        "crescimento": media([lin(r["cresc_rec_5a"], 0.0, 0.15), lin(m["cagr_div"], -0.05, 0.10)]),
        "valuation": media([lin(r["pl"], 20, 5), lin(r["pvp"], 4, 1), lin(r["ev_ebitda"] or None, 12, 4)]),
    }
    disponiveis = {k: v for k, v in pilares.items() if v is not None}
    total = sum(PESOS[k] * v for k, v in disponiveis.items()) / sum(PESOS[k] for k in disponiveis)
    return round(100 * total, 1), {k: None if v is None else round(100 * v) for k, v in pilares.items()}


# ---------------------------------------------------------------- Jev

def pct(v):
    return None if v is None else f"{100 * v:.1f}%"


def estado_jev(r):
    m = r["m"]
    brl = lambda v: None if v is None else f"R$ {v / 1e9:.2f} bn"
    serie_ano = r.get("proventos_ano_liquido") or r.get("proventos_ano") or {}
    natureza = r.get("natureza_proventos") or "indisponivel"
    return {
        "context": "Fundamental indicators of a company listed on the Brazilian stock exchange (B3), computed from "
                   "public data. dividend_yield_12m and the per-share history below are net of withholding tax on JCP "
                   "(interest on capital): 15% before 2026-01-01 and 17.5% on payments from that date. "
                   "Payout is the 5-year average of declared distributions over controlling shareholders' net "
                   "income from CVM DFP filings when company.payout_source is 'cvm'; otherwise it is net dividend "
                   "yield times P/E. company.dividend_nature (recorrente, misto, extraordinario) was computed in "
                   "code from labeled one-off dividends and from a spike versus the prior median. "
                   "company.pays_dividends_above_fcf flags non-financial companies whose cash dividends exceeded "
                   "free cash flow in most of the last five years. Banks and insurers do not report EBIT, so "
                   "EBIT-based leverage is missing for them, and the FCF flag is not applied to financials.",
        "company": {
            "ticker": r["ticker"], "name": r.get("empresa"), "sector": r.get("setor"), "subsector": r.get("subsetor"),
            "dividend_yield_12m": pct(r["dy"]),
            "dividend_yield_12m_gross": pct(r.get("dy_bruto")),
            "payout_ratio": pct(m["payout"]),
            "payout_source": m.get("payout_fonte") or "dy_x_pl",
            "payout_yield_times_pe": pct(m.get("payout_dy_pl")),
            "dividend_nature": natureza,
            "extraordinary_share_last_complete_year": pct(r.get("parcela_extraordinaria")),
            "pays_dividends_above_fcf": m.get("paga_com_divida"),
            "dividends_per_share_by_year_BRL": {str(a): round(v, 4) for a, v in zip(m["anos_completos"], m["serie_div"])},
            "dividends_per_share_current_year_so_far_BRL": round(serie_ano.get(HOJE.year, 0.0), 4),
            "years_with_dividends": f"{m['anos_pagos']} of {ANOS_HIST}",
            "dividend_cuts_over_20pct": m["cortes"],
            "dividend_cagr": pct(m["cagr_div"]),
            "last_year_dividend_vs_prior_average": None if m["div_ultimo_vs_media"] is None
            else f"{m['div_ultimo_vs_media']:.2f}x",
            "net_income_12m": brl(r.get("lucro_12m")),
            "ebit_12m": brl(r.get("ebit_12m")),
            "net_debt": brl(r.get("div_liquida")),
            "net_debt_to_ebit": None if m["div_liq_ebit"] is None else f"{m['div_liq_ebit']:.2f}x",
            "net_debt_to_equity": r["div_liq_patrim"],
            "roe": pct(r["roe"]), "roic": pct(r["roic"]), "net_margin": pct(r["mrg_liq"]),
            "revenue_growth_5y_cagr": pct(r["cresc_rec_5a"]),
            "share_price_change_12m": pct(r.get("oscil_12m")),
            "pe": r["pl"], "price_to_book": r["pvp"], "ev_ebitda": r["ev_ebitda"],
        },
    }


PERGUNTAS = {
    "classe": {
        "type": "choice",
        "instructions": "How sustainable is the dividend policy of the company in `company` over the next few "
                        "years? Weigh together earnings coverage (payout and its source), leverage, profitability, "
                        "regularity and trend of past dividends, company.dividend_nature, and "
                        "company.pays_dividends_above_fcf when it is not null. Also weigh what is normal for its "
                        "sector (for example, regulated utilities and insurers usually sustain high payouts; "
                        "commodity producers swing with prices).",
        "criteria": CLASSES,
    },
    "extraordinario": {
        "type": "noul",
        "instructions": "Is the trailing 12-month dividend yield of the company in `company` inflated by a "
                        "one-off or extraordinary distribution, or by a sharp share-price drop, rather than "
                        "reflecting its recurring dividend capacity? Use company.dividend_nature and "
                        "company.extraordinary_share_last_complete_year, which the code already computed. "
                        "A large share of JCP is not, by itself, an extraordinary distribution.",
        "criteria": {
            "true": "The current yield looks inflated: dividend_nature is extraordinario or misto with a large "
                    "extraordinary share, last year's dividends are far above the historical pattern, payout is "
                    "well above earnings, or the price fell sharply.",
            "false": "The current yield is consistent with the company's recurring earnings and dividend history "
                     "(dividend_nature recorrente, or the extraordinary share is immaterial).",
        },
    },
}


def ask_jev(state, url, model, key, perguntas=PERGUNTAS):
    body = json.dumps({"state": state, "model": model, "questions": perguntas}).encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {key}",
                                                          "Content-Type": "application/json"})
    for tentativa in range(5):
        try:
            return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            if e.code in (429, 529, 502, 503) and tentativa < 4:
                time.sleep(2 ** tentativa)
                continue
            raise RuntimeError(f"Erro da API ({url}) {e.code}: {e.read().decode(errors='replace')}")


def classificar(candidatas):
    cache = json.load(open(JEV_CACHE, encoding="utf-8")) if os.path.exists(JEV_CACHE) else {}
    pendentes = []
    for r in candidatas:
        state = estado_jev(r)
        r["jev_key"] = hashlib.sha1(json.dumps([state, PERGUNTAS], sort_keys=True).encode()).hexdigest()
        if r["jev_key"] not in cache:
            pendentes.append((r, state))
    if pendentes:
        env = next((e for e in PROVIDERS if os.environ.get(e)), None)
        if not env:
            sys.exit("Nenhuma chave encontrada no .env. Adicione OPENROUTER_API_KEY=... ou TYPESAFE_API_KEY=...")
        url, model = PROVIDERS[env]
        with ThreadPoolExecutor(max_workers=8) as pool:
            resps = list(pool.map(lambda p: ask_jev(p[1], url, model, os.environ[env]), pendentes))
        tokens = sum(x["usage"]["input_tokens"] for x in resps)
        print(f"Jev ({resps[0]['model']}) classificou {len(pendentes)} empresas; tokens de entrada: {tokens}")
        for (r, _), resp in zip(pendentes, resps):
            a = resp["answers"]
            cache[r["jev_key"]] = {
                "ticker": r["ticker"],
                "classe": a["classe"]["choice"],
                "confianca": round(a["classe"]["confidence"], 3),
                "probabilidades": {k: round(v, 3) for k, v in a["classe"]["probabilities"].items()},
                "p_extraordinario": round(a["extraordinario"]["noul"], 3),
            }
        with open(JEV_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    for r in candidatas:
        r["jev"] = cache[r["jev_key"]]


# ---------------------------------------------------------------- relatório

def decisao(r):
    """Política explícita, no código, combinando score e julgamentos do Jev."""
    j = r.get("jev")
    if not j:
        return "-"
    if j["classe"] == "DIVIDENDO_FRAGIL" or j["p_extraordinario"] >= 0.7:
        return "descartar"
    if j["classe"] == "DIVIDENDO_SUSTENTAVEL" and r["score"] >= 60 and j["p_extraordinario"] < 0.5:
        return "estudar"
    return "monitorar"


def relatorio(candidatas):
    ordem = {"estudar": 0, "monitorar": 1, "descartar": 2, "-": 3}
    candidatas.sort(key=lambda r: (ordem[r["decisao"]], -r["score"]))
    curto = {"DIVIDENDO_SUSTENTAVEL": "SUSTENTÁVEL", "DIVIDENDO_ATENCAO": "ATENÇÃO", "DIVIDENDO_FRAGIL": "FRÁGIL"}
    f = lambda v, d=1: "   -" if v is None else f"{100 * v:5.{d}f}%"
    nat = {"recorrente": "recorr.", "misto": "misto", "extraordinario": "extraord."}
    print(f"\n{'Ticker':<7}{'Setor':<22}{'DY':>7}{'Payout':>8}{'ROE':>7}{'Pagos':>6}{'Cortes':>7}"
          f"{'CAGR div':>9}{'Score':>7}  {'Jev':<12}{'conf':>5}{'P(extra)':>9}  {'Proventos':<10}Decisão")
    for r in candidatas:
        m, j = r["m"], r.get("jev") or {}
        fonte = " CVM" if m.get("payout_fonte") == "cvm" else ""
        print(f"{r['ticker']:<7}{(r.get('setor') or '')[:21]:<22}{f(r['dy'])}{f(m['payout'], 0):>8}{f(r['roe'], 0):>7}"
              f"{m['anos_pagos']:>4}/{ANOS_HIST}{m['cortes']:>7}{f(m['cagr_div']):>9}{r['score']:>7.1f}  "
              f"{curto.get(j.get('classe'), '-'):<12}{j.get('confianca', 0):>5.2f}{j.get('p_extraordinario', 0):>9.2f}"
              f"  {nat.get(r.get('natureza_proventos'), '  -'):<10}{r['decisao']}{fonte}")

    with open(SAIDA_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["ticker", "empresa", "setor", "dy", "payout", "roe", "roic", "pl", "pvp", "div_liq_ebit",
                    "anos_pagos", "cortes", "cagr_div", "score", *PESOS, "jev_classe", "jev_confianca",
                    "jev_p_extraordinario", "decisao", "conferencia_b3", "dy_bruto", "natureza_proventos",
                    "parcela_extraordinaria", "payout_fonte", "payout_dy_pl", "paga_com_divida", "cvm_anos"])
        for r in candidatas:
            m, j = r["m"], r.get("jev") or {}
            divida = {True: "sim", False: "nao"}.get(m.get("paga_com_divida"), "")
            w.writerow([r["ticker"], r.get("empresa"), r.get("setor"), r["dy"], m["payout"], r["roe"], r["roic"],
                        r["pl"], r["pvp"], m["div_liq_ebit"], m["anos_pagos"], m["cortes"], m["cagr_div"],
                        r["score"], *(r["pilares"][k] for k in PESOS), j.get("classe"), j.get("confianca"),
                        j.get("p_extraordinario"), r["decisao"], r.get("conferencia_b3"), r.get("dy_bruto"),
                        r.get("natureza_proventos"), r.get("parcela_extraordinaria"), m.get("payout_fonte"),
                        m.get("payout_dy_pl"), divida, m.get("cvm_anos")])

    divergentes = [f"{r['ticker']} ({r['conferencia_b3']})" for r in candidatas
                   if (r.get("conferencia_b3") or "").startswith(("diverge", "erro"))]
    if divergentes:
        print(f"\nConferência com a B3 — revisar: {'; '.join(divergentes)}")
    gerar_dashboard(candidatas)
    print(f"\nDetalhes em {SAIDA_CSV} e {SAIDA_HTML}. Decisão: estudar = Jev SUSTENTÁVEL + score >= 60 + "
          f"P(extra) < 0,5; descartar = FRÁGIL ou P(extra) >= 0,7. Não é recomendação de investimento.")


def gerar_dashboard(candidatas):
    """Grava dashboard.html a partir do template, embutindo os dados desta execução."""
    anos = candidatas[0]["m"]["anos_completos"] if candidatas else []
    dados = {
        "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "precos_em": PRECOS_EM,
        "anos": anos, "ano_corrente": HOJE.year, "pesos": PESOS,
        "empresas": [{
            "ticker": r["ticker"], "empresa": r.get("empresa"), "setor": r.get("setor"),
            "subsetor": r.get("subsetor"), "cotacao": r["cotacao"], "dy": r["dy"], "dy_bruto": r.get("dy_bruto"),
            "payout": r["m"]["payout"], "payout_fonte": r["m"].get("payout_fonte"),
            "payout_dy_pl": r["m"].get("payout_dy_pl"), "paga_com_divida": r["m"].get("paga_com_divida"),
            "cvm_anos": r["m"].get("cvm_anos"),
            "natureza_proventos": r.get("natureza_proventos"),
            "parcela_extraordinaria": r.get("parcela_extraordinaria"),
            "roe": r["roe"], "roic": r["roic"], "mrg_liq": r["mrg_liq"], "pl": r["pl"], "pvp": r["pvp"],
            "ev_ebitda": r["ev_ebitda"], "div_liq_ebit": r["m"]["div_liq_ebit"], "liq_2m": r["liq_2m"],
            "oscil_12m": r.get("oscil_12m"), "cresc_rec_5a": r["cresc_rec_5a"],
            "anos_pagos": r["m"]["anos_pagos"], "cortes": r["m"]["cortes"], "cagr_div": r["m"]["cagr_div"],
            "div_fundamentus": [round(r["proventos_ano"].get(a, 0.0), 4) for a in anos],
            "div_b3": [round(r.get("proventos_b3", {}).get(a, 0.0), 4) for a in anos] if r.get("proventos_b3") else None,
            "div_ano_corrente": round(r["proventos_ano"].get(HOJE.year, 0.0), 4),
            "score": r["score"], "pilares": r["pilares"], "jev": r.get("jev"),
            "decisao": r["decisao"], "conferencia_b3": r.get("conferencia_b3"),
        } for r in candidatas],
    }
    template = open(TEMPLATE_HTML, encoding="utf-8").read()
    payload = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    with open(SAIDA_HTML, "w", encoding="utf-8") as f:
        f.write(template.replace("/*__DADOS__*/null", payload))


def codigo_cvm(ticker):
    """Código CVM do emissor na B3 (cadastro GetInitialCompanies), ou None."""
    raiz = ticker[:4]
    empresas = b3_get("GetInitialCompanies", {"language": "pt-br", "pageNumber": 1, "pageSize": 20, "company": raiz})
    for e in empresas.get("results") or []:
        if e.get("issuingCompany") == raiz and e.get("codeCVM"):
            return norm_cd(e["codeCVM"])
    return None


def anexar_cvm(candidatas):
    """Preenche r['cvm'] com a média de payout do DFP. Falha de rede não derruba a análise."""
    def um(r):
        try:
            return codigo_cvm(r["ticker"])
        except Exception as e:
            registrar_falha(r["ticker"], "cadastro B3 (CVM)", e)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        codigos = list(pool.map(um, candidatas))
    cds = {cd for cd in codigos if cd}
    if not cds:
        print("CVM: nenhum código CVM obtido; payout segue como DY × P/L.")
        return
    try:
        por_cd = payout_por_cd(cds, HOJE, os.path.join(CACHE_DIR, "cvm"))
    except Exception as e:
        registrar_falha("-", "download DFP da CVM", e)
        print("CVM: download ou leitura do DFP falhou; payout segue como DY × P/L.")
        return
    sem = []
    for r, cd in zip(candidatas, codigos):
        base = por_cd.get(cd) if cd else None
        if not base:
            sem.append(r["ticker"])
            continue
        r["cd_cvm"] = cd
        r["cvm"] = resumir(base["por_ano"], financeiro=setor_financeiro(r.get("setor"), r.get("subsetor")))
    janela = anos_exercicio(HOJE)
    print(f"CVM: payout de {len(candidatas) - len(sem)} empresas (DFP {janela[0]}–{janela[-1]}, "
          f"média usada no score com pelo menos {MIN_ANOS_SCORE} anos)"
          + (f"; sem demonstração: {', '.join(sem)}" if sem else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dy-min", type=float, default=5.0, help="DY mínimo em %% (padrão 5)")
    ap.add_argument("--liq-min", type=float, default=2_000_000, help="liquidez média diária mínima em R$")
    ap.add_argument("--limite", type=int, default=80, help="máximo de empresas analisadas (mais líquidas)")
    ap.add_argument("--sem-jev", action="store_true", help="só o score determinístico")
    ap.add_argument("--sem-b3", action="store_true", help="não confere os proventos com a B3")
    ap.add_argument("--sem-cvm", action="store_true", help="não baixa o DFP da CVM; payout fica DY × P/L")
    ap.add_argument("--precos-yahoo", help="usa o fechamento do Yahoo desta data (AAAA-MM-DD) no lugar da cotação")
    args = ap.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    configurar()
    load_env()

    global PRECOS_EM
    mercado = carregar_mercado()
    if args.precos_yahoo:
        data = dt.date.fromisoformat(args.precos_yahoo)
        reprecificar(mercado, data, args.liq_min)
        PRECOS_EM = f"fechamento de {data:%d/%m/%Y} (Yahoo Finance) sobre indicadores do Fundamentus baixados em {HOJE:%d/%m/%Y}"
    candidatas = filtrar(mercado, args.dy_min / 100, args.liq_min, args.limite)
    print(f"{len(mercado)} tickers no Fundamentus; {len(candidatas)} empresas passaram nos filtros "
          f"(DY >= {args.dy_min:g}%, liquidez >= R$ {args.liq_min:,.0f}, P/L > 0).")

    def enriquecer(r):
        try:
            r.update(detalhes(r["ticker"]))
            if r.get("fator_preco") and r.get("oscil_12m") is not None:
                r["oscil_12m"] = (1 + r["oscil_12m"]) * r["fator_preco"] - 1
            page = fetch(f"/proventos.php?papel={r['ticker']}&tipo=2")
            eventos = parse_proventos_html(page)
            r["proventos_ano"] = tabela_anual(page) or agregar_por_ano(eventos, liquido=False)
            r["proventos_ano_liquido"] = agregar_por_ano(eventos, liquido=True, incluir_outros=False)
            completos = [HOJE.year - i for i in range(ANOS_HIST, 0, -1)]
            r.update(classificar_natureza(eventos, completos))
            r["dy_bruto"] = r["dy"]
            r["dy"], r["fator_jcp"] = aplicar_dy_liquido(r["dy"], eventos, HOJE)
            return r
        except Exception as e:
            registrar_falha(r["ticker"], "coleta", e)
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        candidatas = [r for r in pool.map(enriquecer, candidatas) if r]
    if not candidatas:
        resumo_falhas()
        sys.exit("Nenhuma empresa com coleta completa.")
    if not args.sem_cvm:
        anexar_cvm(candidatas)
    for r in candidatas:
        r["m"] = metricas(r)
        r["score"], r["pilares"] = score(r)
    if not args.sem_b3:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for r, situacao in zip(candidatas, pool.map(conferir, candidatas)):
                r["conferencia_b3"] = situacao

    if not args.sem_jev:
        classificar(candidatas)
    for r in candidatas:
        r["decisao"] = decisao(r)
    relatorio(candidatas)
    resumo_falhas()


if __name__ == "__main__":
    main()
