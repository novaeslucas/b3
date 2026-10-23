"""Classifica a qualidade dos dividendos das ações da B3 com score determinístico + Jev.

Fluxo:
  1. Fundamentus (resultado.php): indicadores de todas as ações em uma requisição.
  2. Filtros rígidos no código: liquidez, DY mínimo, lucro positivo, um ticker por empresa.
  3. Fundamentus (detalhes.php e proventos.php) por candidata: setor, dívida, lucro, EBIT,
     oscilação de 12 meses e proventos por ano.
  4. Dividend Quality Score (0-100) calculado aqui no código, com pesos explícitos.
  5. Jev classifica cada empresa (SUSTENTAVEL / ATENCAO / FRAGIL) e diz se o DY atual parece
     inflado por um pagamento extraordinário ou por queda da cotação.
  6. Conferência: proventos por ano do Fundamentus x B3 (GetListedCashDividends, por espécie ON/PN).
  7. Saídas: tabela no console, resultado_dividendos.csv e dashboard.html (gerado de dashboard_template.html).

O Jev não calcula nada: recebe os indicadores já calculados e devolve julgamentos com probabilidades.
Downloads ficam em cache/ (válidos no dia) e respostas do Jev em cache/jev.json (por conteúdo).

Uso:  python dividendos.py [--dy-min 5] [--liq-min 2000000] [--limite 80] [--sem-jev] [--sem-b3]
Usa o Jev via OpenRouter (OPENROUTER_API_KEY) ou direto na TypeSafe (TYPESAFE_API_KEY), conforme o .env.
Não é recomendação de investimento.
"""
import argparse
import base64
import csv
import datetime as dt
import hashlib
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROVIDERS = {  # variável de ambiente -> (endpoint, modelo)
    "OPENROUTER_API_KEY": ("https://openrouter.ai/api/alpha/decisions", "~typesafe/jev-latest"),
    "TYPESAFE_API_KEY": ("https://api.typesafe.ai/v1/systemone", "jev-latest"),
}
FUNDAMENTUS = "https://www.fundamentus.com.br"
B3 = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
SSL_B3 = ssl._create_unverified_context()  # o certificado do endpoint da B3 costuma falhar na validação
ESPECIES = {"3": "ON", "4": "PN", "5": "PNA", "6": "PNB"}  # sufixo do ticker -> espécie na B3
CACHE_DIR = "cache"
JEV_CACHE = os.path.join(CACHE_DIR, "jev.json")
SAIDA_CSV = os.path.join("resultados", "resultado_dividendos.csv")
SAIDA_HTML = os.path.join("resultados", "dashboard.html")
TEMPLATE_HTML = os.path.join("templates", "dashboard_template.html")
HOJE = dt.date.today()
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

def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def num(s):
    """'1.234,5%' -> 0.012345 ; '1.234,5' -> 1234.5 ; '-' / '' -> None"""
    s = html.unescape(s or "").strip()
    if s in ("", "-"):
        return None
    pct = s.endswith("%")
    s = s.rstrip("%").replace(".", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v / 100 if pct else v


def fetch(path, url=None, encoding="latin-1", nome=None):
    """GET (Fundamentus por padrão) com cache diário em disco."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    fn = os.path.join(CACHE_DIR, re.sub(r"[^\w]+", "_", nome or path) + f"_{HOJE:%Y%m%d}.html")
    if os.path.exists(fn):
        return open(fn, encoding="utf-8").read()
    req = urllib.request.Request(url or FUNDAMENTUS + path, headers={"User-Agent": "Mozilla/5.0"})
    for tentativa in range(3):
        try:
            text = urllib.request.urlopen(req, timeout=30, context=SSL_B3 if url else None).read().decode(encoding)
            break
        except (urllib.error.URLError, TimeoutError):
            if tentativa == 2:
                raise
            time.sleep(2 ** tentativa)
    with open(fn, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def lin(x, ruim, bom):
    """Mapeia x linearmente para 0..1 (ruim -> 0, bom -> 1), com saturação. Funciona nos dois sentidos."""
    if x is None:
        return None
    t = (x - ruim) / (bom - ruim)
    return max(0.0, min(1.0, t))


def media(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


# ---------------------------------------------------------------- coleta

def carregar_mercado():
    """Tabela resultado.php: uma linha por ticker com os principais indicadores."""
    page = fetch("/resultado.php")
    cols = ["ticker", "cotacao", "pl", "pvp", "psr", "dy", "p_ativo", "p_capgiro", "p_ebit", "p_acl",
            "ev_ebit", "ev_ebitda", "mrg_bruta", "mrg_ebit", "mrg_liq", "liq_corr", "roic", "roe",
            "liq_2m", "patrim_liq", "div_liq_patrim", "cresc_rec_5a"]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) != len(cols):
            continue
        ticker = re.sub(r"<[^>]+>", "", tds[0]).strip()
        rows.append({"ticker": ticker, **{c: num(re.sub(r"<[^>]+>", "", v)) for c, v in zip(cols[1:], tds[1:])}})
    return rows


def filtrar(rows, dy_min, liq_min, limite):
    ok = [r for r in rows if (r["liq_2m"] or 0) >= liq_min and (r["dy"] or 0) >= dy_min and (r["pl"] or 0) > 0]
    por_empresa = {}  # mesma empresa (4 letras) -> ticker mais líquido
    for r in ok:
        raiz = r["ticker"][:4]
        if raiz not in por_empresa or r["liq_2m"] > por_empresa[raiz]["liq_2m"]:
            por_empresa[raiz] = r
    return sorted(por_empresa.values(), key=lambda r: -r["liq_2m"])[:limite]


def detalhes(ticker):
    page = fetch(f"/detalhes.php?papel={ticker}")
    campos = {}
    pares = re.findall(r'<span class="txt">([^<]+)</span></td>\s*<td class="data[^"]*">\s*'
                       r'<span class="(?:txt|oscil)">(?:<a[^>]*>|<font[^>]*>)?\s*([^<]*)', page, re.S)
    for label, valor in pares:
        campos.setdefault(html.unescape(label).strip(), html.unescape(valor).strip())  # 1ª ocorrência = 12 meses
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
    """Tabela 'por ano' de proventos.php (R$ por ação, dividendos + JCP)."""
    page = fetch(f"/proventos.php?papel={ticker}&tipo=2")
    anos = {}
    for tbody in re.findall(r"<tbody>(.*?)</tbody>", page, re.S):
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody, re.S):
            tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(tds) == 2 and re.fullmatch(r"\d{4}", tds[0]):
                anos[int(tds[0])] = num(tds[1]) or 0.0
    return anos


def b3_get(metodo, params):
    p = base64.b64encode(json.dumps(params, separators=(",", ":")).encode()).decode()
    nome = f"b3_{metodo}_" + "_".join(str(v) for k, v in params.items() if k != "language")
    return json.loads(fetch(None, f"{B3}/{metodo}/{p}", "utf-8", nome))


def proventos_b3(ticker):
    """Proventos em dinheiro por ano (data-com) segundo a B3, para a espécie do ticker.

    Retorna (anos, situação). Units não são comparáveis: a B3 informa só ON/PN e a composição da unit
    varia por empresa. JCP é bruto, como no Fundamentus.
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
    anos = r["proventos_ano"]
    completos = [HOJE.year - i for i in range(ANOS_HIST, 0, -1)]  # ex.: 2020..2025
    serie = [anos.get(a, 0.0) for a in completos]
    pagos = sum(1 for v in serie if v > 0)
    cortes = sum(1 for a, b in zip(serie, serie[1:]) if a > 0 and b < a * 0.8)  # queda > 20% a/a
    ini, fim = serie[0], serie[-1]
    cagr = (fim / ini) ** (1 / (len(serie) - 1)) - 1 if ini > 0 and fim > 0 else None
    payout = r["dy"] * r["pl"] if r["dy"] is not None and r["pl"] else None  # dividendos / lucro
    ebit = r.get("ebit_12m")
    alav = r["div_liquida"] / ebit if r.get("div_liquida") is not None and ebit and ebit > 0 else None
    return {
        "anos_completos": completos, "serie_div": serie, "anos_pagos": pagos, "cortes": cortes,
        "cagr_div": cagr, "payout": payout, "div_liq_ebit": alav,
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
    return {
        "context": "Fundamental indicators of a company listed on the Brazilian stock exchange (B3), computed from "
                   "public data. Dividends include JCP (interest on capital). Payout = dividend yield x P/E. "
                   "Banks and insurers do not report EBIT, so EBIT-based leverage is missing for them.",
        "company": {
            "ticker": r["ticker"], "name": r.get("empresa"), "sector": r.get("setor"), "subsector": r.get("subsetor"),
            "dividend_yield_12m": pct(r["dy"]),
            "payout_ratio": pct(m["payout"]),
            "dividends_per_share_by_year_BRL": {str(a): round(v, 4) for a, v in zip(m["anos_completos"], m["serie_div"])},
            "dividends_per_share_current_year_so_far_BRL": round(r["proventos_ano"].get(HOJE.year, 0.0), 4),
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
                        "years? Weigh together earnings coverage (payout), leverage, profitability, regularity "
                        "and trend of past dividends, and what is normal for its sector (for example, regulated "
                        "utilities and insurers usually sustain high payouts; commodity producers swing with "
                        "prices).",
        "criteria": CLASSES,
    },
    "extraordinario": {
        "type": "noul",
        "instructions": "Is the trailing 12-month dividend yield of the company in `company` inflated by a "
                        "one-off or extraordinary distribution, or by a sharp share-price drop, rather than "
                        "reflecting its recurring dividend capacity?",
        "criteria": {
            "true": "The current yield looks inflated: last year's dividends are far above the historical "
                    "pattern, payout is well above earnings, or the price fell sharply.",
            "false": "The current yield is consistent with the company's recurring earnings and dividend history.",
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
    print(f"\n{'Ticker':<7}{'Setor':<26}{'DY':>7}{'Payout':>8}{'ROE':>7}{'Pagos':>6}{'Cortes':>7}"
          f"{'CAGR div':>9}{'Score':>7}  {'Jev':<12}{'conf':>5}{'P(extra)':>9}  Decisão")
    for r in candidatas:
        m, j = r["m"], r.get("jev") or {}
        print(f"{r['ticker']:<7}{(r.get('setor') or '')[:25]:<26}{f(r['dy'])}{f(m['payout'], 0):>8}{f(r['roe'], 0):>7}"
              f"{m['anos_pagos']:>4}/{ANOS_HIST}{m['cortes']:>7}{f(m['cagr_div']):>9}{r['score']:>7.1f}  "
              f"{curto.get(j.get('classe'), '-'):<12}{j.get('confianca', 0):>5.2f}{j.get('p_extraordinario', 0):>9.2f}"
              f"  {r['decisao']}")

    with open(SAIDA_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["ticker", "empresa", "setor", "dy", "payout", "roe", "roic", "pl", "pvp", "div_liq_ebit",
                    "anos_pagos", "cortes", "cagr_div", "score", *PESOS, "jev_classe", "jev_confianca",
                    "jev_p_extraordinario", "decisao", "conferencia_b3"])
        for r in candidatas:
            m, j = r["m"], r.get("jev") or {}
            w.writerow([r["ticker"], r.get("empresa"), r.get("setor"), r["dy"], m["payout"], r["roe"], r["roic"],
                        r["pl"], r["pvp"], m["div_liq_ebit"], m["anos_pagos"], m["cortes"], m["cagr_div"],
                        r["score"], *(r["pilares"][k] for k in PESOS), j.get("classe"), j.get("confianca"),
                        j.get("p_extraordinario"), r["decisao"], r.get("conferencia_b3")])

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
        "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "anos": anos, "ano_corrente": HOJE.year, "pesos": PESOS,
        "empresas": [{
            "ticker": r["ticker"], "empresa": r.get("empresa"), "setor": r.get("setor"),
            "subsetor": r.get("subsetor"), "cotacao": r["cotacao"], "dy": r["dy"], "payout": r["m"]["payout"],
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dy-min", type=float, default=5.0, help="DY mínimo em %% (padrão 5)")
    ap.add_argument("--liq-min", type=float, default=2_000_000, help="liquidez média diária mínima em R$")
    ap.add_argument("--limite", type=int, default=80, help="máximo de empresas analisadas (mais líquidas)")
    ap.add_argument("--sem-jev", action="store_true", help="só o score determinístico")
    ap.add_argument("--sem-b3", action="store_true", help="não confere os proventos com a B3")
    args = ap.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    load_env()

    mercado = carregar_mercado()
    candidatas = filtrar(mercado, args.dy_min / 100, args.liq_min, args.limite)
    print(f"{len(mercado)} tickers no Fundamentus; {len(candidatas)} empresas passaram nos filtros "
          f"(DY >= {args.dy_min:g}%, liquidez >= R$ {args.liq_min:,.0f}, P/L > 0).")

    def enriquecer(r):
        r.update(detalhes(r["ticker"]))
        r["proventos_ano"] = proventos_por_ano(r["ticker"])
        return r
    with ThreadPoolExecutor(max_workers=4) as pool:
        candidatas = list(pool.map(enriquecer, candidatas))
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


if __name__ == "__main__":
    main()
