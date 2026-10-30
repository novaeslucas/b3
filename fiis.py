"""Classifica a qualidade dos rendimentos de FIIs com score determinístico + Jev.

Mesma ideia de dividendos.py, adaptada a fundos imobiliários:
  1. Fundamentus (fii_resultado.php): indicadores de todos os FIIs em uma requisição.
  2. Filtros rígidos no código: liquidez, DY mínimo, P/VP válido.
  3. Fundamentus (detalhes.php e fii_proventos.php) por candidato: FFO, rendimento distribuído, venda de
     ativos, patrimônio, gestão e histórico mensal de rendimentos.
  4. Score de Qualidade do Rendimento (0-100) calculado aqui no código, com pesos explícitos.
  5. Jev classifica cada fundo (SUSTENTAVEL / ATENCAO / FRAGIL), diz se o DY atual parece inflado por
     ganho não recorrente / reserva / inflação alta / queda da cota, e identifica o tipo do fundo
     (tijolo, papel, fundo de fundos...).
  6. Conferência: rendimentos dos últimos 12 meses do Fundamentus x B3 (GetListedSupplementFunds;
     a B3 só expõe esse período para FIIs).
  7. Saídas: tabela no console, resultado_fiis.csv e dashboard_fiis.html (de dashboard_fiis_template.html).

Uso:  python fiis.py [--dy-min 8] [--liq-min 1000000] [--limite 80] [--sem-jev] [--sem-b3]
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
import math
import os
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

from dividendos import CACHE_DIR, HOJE, PROVIDERS, ask_jev, fetch, lin, load_env, media, num, pct
from b3lib.log import configurar, registrar_falha, resumo_falhas

B3_FUNDOS = "https://sistemaswebb3-listados.b3.com.br/fundsProxy/fundsCall/GetListedSupplementFunds"
JEV_CACHE = os.path.join(CACHE_DIR, "jev_fiis.json")
SAIDA_CSV = os.path.join("resultados", "resultado_fiis.csv")
SAIDA_HTML = os.path.join("resultados", "dashboard_fiis.html")
TEMPLATE_HTML = os.path.join("templates", "dashboard_fiis_template.html")
ANOS_HIST = 5  # anos completos de rendimentos considerados (exclui o ano corrente)

PESOS = {"sustentabilidade": 0.30, "regularidade": 0.25, "crescimento": 0.15, "portfolio": 0.15, "valuation": 0.15}

CLASSES = {
    "RENDIMENTO_SUSTENTAVEL": "Sustainable: monthly distributions are covered by recurring cash earnings (FFO), "
                              "have been paid every month with a stable or rising trend, and the portfolio "
                              "(vacancy, diversification, size) supports them.",
    "RENDIMENTO_ATENCAO": "Watch: distributions look broadly payable but at least one relevant concern exists, "
                          "such as distributing more than FFO, reliance on asset-sale gains, rising vacancy, a "
                          "recent decline in monthly payments, or a small or concentrated portfolio.",
    "RENDIMENTO_FRAGIL": "Fragile: the current distribution level is unlikely to be maintained: well above FFO, "
                         "funded by one-off gains or reserves, high vacancy, repeated cuts, or a yield driven "
                         "mainly by a collapse in the unit price.",
}

TIPOS = {
    "tijolo": "Brick-and-mortar fund that owns physical real estate (offices, logistics warehouses, shopping "
              "malls, retail, hospitals, schools, hotels) and lives on rental income.",
    "papel": "Paper fund that invests mainly in real-estate receivables such as CRIs, LCIs or mortgage-backed "
             "securities; income follows interest rates and inflation indexes.",
    "fundo_de_fundos": "Fund of funds that invests mainly in units of other real-estate funds.",
    "hibrido": "Hybrid fund with a relevant mix of physical properties and real-estate securities.",
    "desenvolvimento": "Development fund that builds or develops properties for sale or future rental.",
    "outro": "None of the above, or not enough information to tell.",
}

PERGUNTAS = {
    "classe": {
        "type": "choice",
        "instructions": "How sustainable are the monthly distributions of the Brazilian real-estate investment "
                        "fund (FII) in `fund` over the next few years? Weigh together coverage by FFO, reliance "
                        "on asset-sale gains, regularity and trend of past distributions, vacancy and portfolio "
                        "diversification, and what is normal for its kind of fund (paper funds swing with "
                        "inflation and interest rates; brick funds depend on occupancy and rents).",
        "criteria": CLASSES,
    },
    "extraordinario": {
        "type": "noul",
        "instructions": "Is the trailing 12-month dividend yield of the fund in `fund` inflated by something "
                        "that is not recurring — gains from selling properties or securities, use of retained "
                        "reserves, a temporary inflation spike in indexed receivables, or a sharp drop in the "
                        "unit price — rather than reflecting the fund's recurring income?",
        "criteria": {
            "true": "The current yield looks inflated: distributions exceed recurring FFO, depend on asset "
                    "sales or reserves, recently jumped above the historical pattern, or the unit price fell "
                    "sharply.",
            "false": "The current yield is consistent with the fund's recurring income and distribution history.",
        },
    },
    "tipo": {
        "type": "choice",
        "instructions": "What kind of real-estate fund is the fund in `fund`, judging by its name, segment, "
                        "management style and the presence or absence of physical-property data (property "
                        "count, vacancy, cap rate)?",
        "criteria": TIPOS,
    },
}


# ---------------------------------------------------------------- coleta

def carregar_mercado():
    page = fetch("/fii_resultado.php")
    cols = ["ticker", "segmento", "cotacao", "ffo_yield", "dy", "pvp", "valor_mercado", "liquidez",
            "qtd_imoveis", "preco_m2", "aluguel_m2", "cap_rate", "vacancia"]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = [html.unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) < len(cols):
            continue
        nome = re.search(r'title="([^"]*)"', tr)
        rows.append({"ticker": tds[0], "segmento": tds[1], "nome": html.unescape(nome.group(1)) if nome else None,
                     **{c: num(v) for c, v in zip(cols[2:], tds[2:len(cols)])}})
    return rows


def filtrar(rows, dy_min, liq_min, limite):
    ok = [r for r in rows if (r["liquidez"] or 0) >= liq_min and (r["dy"] or 0) >= dy_min and (r["pvp"] or 0) > 0]
    return sorted(ok, key=lambda r: -r["liquidez"])[:limite]


def detalhes(ticker):
    page = fetch(f"/detalhes.php?papel={ticker}")
    campos = {}
    pares = re.findall(r'<span class="txt">([^<]+)</span></td>\s*<td class="data[^"]*">\s*'
                       r'<span class="(?:txt|oscil)">(?:<a[^>]*>|<font[^>]*>)?\s*([^<]*)', page, re.S)
    for label, valor in pares:
        campos.setdefault(html.unescape(label).strip(), html.unescape(valor).strip())  # 1ª ocorrência = 12 meses
    return {
        "mandato": campos.get("Mandato") or None,
        "gestao": campos.get("Gestão") or None,
        "oscil_12m": num(campos.get("12 meses")),
        "ffo_12m": num(campos.get("FFO")),
        "rend_distribuido_12m": num(campos.get("Rend. Distribuído")),
        "venda_ativos_12m": num(campos.get("Venda de ativos")),
        "receita_12m": num(campos.get("Receita")),
        "patrim_liq": num(campos.get("Patrim Líquido")),
        "ativos": num(campos.get("Ativos")),
        "vp_cota": num(campos.get("VP/Cota")),
    }


def rendimentos(ticker):
    """Lista de (data-com, valor por cota) dos rendimentos; amortizações são contadas à parte."""
    page = fetch(f"/fii_proventos.php?papel={ticker}&tipo=2")
    rend, amort = [], 0
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) == 4 and re.fullmatch(r"\d\d/\d\d/\d{4}", tds[0]):
            data = dt.datetime.strptime(tds[0], "%d/%m/%Y").date()
            if tds[1].lower().startswith("rendimento"):
                rend.append((data, num(tds[3]) or 0.0))
            elif data >= HOJE - dt.timedelta(days=365):
                amort += 1
    return rend, amort


def rendimentos_b3(ticker):
    """Rendimentos da cota principal (ISIN ...CTF...) segundo a B3: só os últimos ~12 meses."""
    codigo = ticker[:4]
    p = base64.b64encode(json.dumps({"cnpj": "0", "identifierFund": codigo, "typeFund": 7},
                                    separators=(",", ":")).encode()).decode()
    texto = fetch(None, f"{B3_FUNDOS}/{p}", "utf-8", f"b3_fii_{codigo}")
    if not texto.lstrip().startswith("{"):
        return None  # fundo sem cadastro no endpoint (código trocado, incorporado...)
    d = json.loads(texto)
    out = []
    for e in d.get("cashDividends") or []:
        if "CTF" in e["assetIssued"] and e["label"].upper().startswith("RENDIMENTO"):
            out.append((dt.datetime.strptime(e["lastDatePrior"], "%d/%m/%Y").date(), num(e["rate"]) or 0.0))
    return out


def conferir(r):
    try:
        b3 = rendimentos_b3(r["ticker"])
    except Exception as e:  # endpoint não oficial: falha não deve parar a análise
        registrar_falha(r["ticker"], "conferência B3", e)
        return f"erro B3: {type(e).__name__}"
    if b3 is None:
        return "sem cadastro na B3"
    if not b3:
        return "sem rendimentos na B3"
    fund = {}
    for data, v in r["rendimentos"]:
        fund[data] = fund.get(data, 0.0) + v
    b3_por_data = {}
    for data, v in b3:
        b3_por_data[data] = b3_por_data.get(data, 0.0) + v
    r["b3_12m"] = sum(b3_por_data.values())
    diverg = [d for d, v in b3_por_data.items() if abs(fund.get(d, 0.0) - v) > max(0.01 * v, 0.005)]
    if not diverg:
        return f"confere ({len(b3_por_data)} pagamentos)"
    return f"diverge em {len(diverg)} de {len(b3_por_data)} pagamentos (" + \
        ", ".join(d.strftime("%m/%Y") for d in sorted(diverg)[:3]) + ("…" if len(diverg) > 3 else "") + ")"


# ---------------------------------------------------------------- métricas e score

def metricas(r):
    rend = r["rendimentos"]
    por_ano, por_mes = {}, {}
    for data, v in rend:
        por_ano[data.year] = por_ano.get(data.year, 0.0) + v
        por_mes[(data.year, data.month)] = por_mes.get((data.year, data.month), 0.0) + v
    completos = [HOJE.year - i for i in range(ANOS_HIST, 0, -1)]
    serie = [por_ano.get(a, 0.0) for a in completos]
    cortes = sum(1 for a, b in zip(serie, serie[1:]) if a > 0 and b < a * 0.8)
    ini, fim = serie[0], serie[-1]
    cagr = (fim / ini) ** (1 / (len(serie) - 1)) - 1 if ini > 0 and fim > 0 else None

    # últimos 24 meses fechados (o mês corrente pode ainda não ter data-com)
    meses = []
    a, m = HOJE.year, HOJE.month
    for _ in range(24):
        m -= 1
        if m == 0:
            a, m = a - 1, 12
        meses.append((a, m))
    meses.reverse()
    mensal = [por_mes.get(k, 0.0) for k in meses]
    ult12, ult6, ant6 = mensal[-12:], sum(mensal[-6:]), sum(mensal[-12:-6])
    pagos12 = sum(1 for v in ult12 if v > 0)
    cv = statistics.pstdev(ult12) / statistics.mean(ult12) if sum(ult12) > 0 else None

    ffo, dist, venda = r.get("ffo_12m"), r.get("rend_distribuido_12m"), r.get("venda_ativos_12m")
    return {
        "anos_completos": completos, "serie_anual": serie, "cortes": cortes, "cagr": cagr,
        "meses": [f"{a}-{m:02d}" for a, m in meses], "mensal": mensal,
        "meses_pagos_12": pagos12, "cv_12": cv,
        "tendencia_6m": ult6 / ant6 if ant6 > 0 else None,
        "payout_ffo": dist / ffo if ffo and ffo > 0 and dist is not None else None,
        "dep_venda": max(venda, 0.0) / ffo if ffo and ffo > 0 and venda is not None else None,
    }


def score(r):
    m = r["m"]
    p = m["payout_ffo"]
    # FFO do Fundamentus é contábil e FIIs distribuem por regime de caixa: 100-110% é comum em fundos sólidos.
    s_payout = None if p is None else (1.0 if p <= 1.1 else lin(p, 1.5, 1.1))
    if p is None and r.get("ffo_12m") is not None and r["ffo_12m"] <= 0:
        s_payout = 0.0  # FFO negativo: distribuição sem lastro operacional
    tijolo = (r["qtd_imoveis"] or 0) > 0
    pilares = {
        "sustentabilidade": media([s_payout, lin(m["dep_venda"], 0.3, 0.0)]),
        "regularidade": media([lin(m["meses_pagos_12"], 9, 12), lin(m["cv_12"], 0.4, 0.05), lin(m["cortes"], 3, 0)]),
        "crescimento": media([lin(m["cagr"], -0.05, 0.05), lin(m["tendencia_6m"], 0.8, 1.05)]),
        "portfolio": media([
            lin(math.log10(r["valor_mercado"]) if r["valor_mercado"] else None, math.log10(2e8), math.log10(2e9)),
            lin(r["vacancia"], 0.20, 0.02) if tijolo else None,
            lin(r["qtd_imoveis"], 1, 10) if tijolo else None,
        ]),
        "valuation": media([lin(r["pvp"], 1.3, 0.85), lin(r["ffo_yield"], 0.05, 0.11)]),
    }
    disponiveis = {k: v for k, v in pilares.items() if v is not None}
    total = sum(PESOS[k] * v for k, v in disponiveis.items()) / sum(PESOS[k] for k in disponiveis)
    return round(100 * total, 1), {k: None if v is None else round(100 * v) for k, v in pilares.items()}


# ---------------------------------------------------------------- Jev

def estado_jev(r):
    m = r["m"]
    brl = lambda v: None if v is None else f"R$ {v / 1e6:,.1f} mi"
    tijolo = (r["qtd_imoveis"] or 0) > 0
    return {
        "context": "Indicators of a Brazilian real-estate investment fund (FII) listed on B3, computed from public "
                   "data. FFO is recurring earnings on an accrual basis, while FIIs distribute on a cash basis, so "
                   "distribution/FFO slightly above 100% is common in healthy funds; well above it means paying "
                   "out more than recurring earnings. Property data (count, vacancy, cap rate) is absent for "
                   "funds that do not own physical properties.",
        "fund": {
            "ticker": r["ticker"], "name": r.get("nome"), "segment": r["segmento"], "mandate": r.get("mandato"),
            "management": r.get("gestao"),
            "dividend_yield_12m": pct(r["dy"]), "ffo_yield": pct(r["ffo_yield"]),
            "price_to_book": r["pvp"], "unit_price_change_12m": pct(r.get("oscil_12m")),
            "market_value": brl(r["valor_mercado"]), "net_assets": brl(r.get("patrim_liq")),
            "ffo_12m": brl(r.get("ffo_12m")), "distributed_12m": brl(r.get("rend_distribuido_12m")),
            "asset_sale_gains_12m": brl(r.get("venda_ativos_12m")),
            "distribution_to_ffo": pct(m["payout_ffo"]),
            "distributions_per_unit_by_year_BRL": {str(a): round(v, 4) for a, v in zip(m["anos_completos"], m["serie_anual"])},
            "distributions_per_unit_last_12_months_BRL": {k: round(v, 4) for k, v in zip(m["meses"][-12:], m["mensal"][-12:])},
            "months_paid_last_12": m["meses_pagos_12"],
            "last_6m_vs_previous_6m": None if m["tendencia_6m"] is None else f"{m['tendencia_6m']:.2f}x",
            "amortizations_last_12m": r["amortizacoes_12m"],
            "property_count": int(r["qtd_imoveis"]) if tijolo else None,
            "vacancy": pct(r["vacancia"]) if tijolo else None,
            "cap_rate": pct(r["cap_rate"]) if tijolo else None,
        },
    }


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
            resps = list(pool.map(lambda p: ask_jev(p[1], url, model, os.environ[env], PERGUNTAS), pendentes))
        tokens = sum(x["usage"]["input_tokens"] for x in resps)
        print(f"Jev ({resps[0]['model']}) classificou {len(pendentes)} FIIs; tokens de entrada: {tokens}")
        for (r, _), resp in zip(pendentes, resps):
            a = resp["answers"]
            cache[r["jev_key"]] = {
                "ticker": r["ticker"],
                "classe": a["classe"]["choice"],
                "confianca": round(a["classe"]["confidence"], 3),
                "probabilidades": {k: round(v, 3) for k, v in a["classe"]["probabilities"].items()},
                "p_extraordinario": round(a["extraordinario"]["noul"], 3),
                "tipo": a["tipo"]["choice"],
                "tipo_confianca": round(a["tipo"]["confidence"], 3),
            }
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(JEV_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    for r in candidatas:
        r["jev"] = cache[r["jev_key"]]


# ---------------------------------------------------------------- relatório

def decisao(r):
    """Política explícita, no código, combinando score e julgamentos do Jev.

    Diferente de ações: P(extra) alto sozinho não descarta um FII, porque Dist./FFO um pouco acima de 100%
    (FFO contábil x distribuição por caixa) já empurra essa probabilidade para cima em fundos tradicionais.
    Ele só impede a decisão "estudar".
    """
    j = r.get("jev")
    if not j:
        return "-"
    if j["classe"] == "RENDIMENTO_FRAGIL":
        return "descartar"
    if j["classe"] == "RENDIMENTO_SUSTENTAVEL" and r["score"] >= 60 and j["p_extraordinario"] < 0.5:
        return "estudar"
    return "monitorar"


def relatorio(candidatas):
    ordem = {"estudar": 0, "monitorar": 1, "descartar": 2, "-": 3}
    candidatas.sort(key=lambda r: (ordem[r["decisao"]], -r["score"]))
    curto = {"RENDIMENTO_SUSTENTAVEL": "SUSTENTÁVEL", "RENDIMENTO_ATENCAO": "ATENÇÃO", "RENDIMENTO_FRAGIL": "FRÁGIL"}
    f = lambda v, d=1: "   -" if v is None else f"{100 * v:5.{d}f}%"
    print(f"\n{'Ticker':<8}{'Segmento':<20}{'Tipo (Jev)':<16}{'DY':>7}{'P/VP':>6}{'Dist/FFO':>9}{'Vac.':>7}"
          f"{'Meses':>6}{'6m/6m':>7}{'Score':>7}  {'Jev':<12}{'conf':>5}{'P(extra)':>9}  Decisão")
    for r in candidatas:
        m, j = r["m"], r.get("jev") or {}
        tend = "   -" if m["tendencia_6m"] is None else f"{m['tendencia_6m']:.2f}"
        print(f"{r['ticker']:<8}{(r['segmento'] or '')[:19]:<20}{(j.get('tipo') or '-')[:15]:<16}{f(r['dy'])}"
              f"{r['pvp']:>6.2f}{f(m['payout_ffo'], 0):>9}{f(r['vacancia'] if r['qtd_imoveis'] else None):>7}"
              f"{m['meses_pagos_12']:>4}/12{tend:>7}{r['score']:>7.1f}  {curto.get(j.get('classe'), '-'):<12}"
              f"{j.get('confianca', 0):>5.2f}{j.get('p_extraordinario', 0):>9.2f}  {r['decisao']}")

    with open(SAIDA_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["ticker", "nome", "segmento", "tipo_jev", "dy", "ffo_yield", "pvp", "dist_ffo", "dep_venda",
                    "vacancia", "qtd_imoveis", "meses_pagos_12", "cv_12", "tendencia_6m", "cortes", "cagr", "score",
                    *PESOS, "jev_classe", "jev_confianca", "jev_p_extraordinario", "decisao", "conferencia_b3"])
        for r in candidatas:
            m, j = r["m"], r.get("jev") or {}
            w.writerow([r["ticker"], r.get("nome"), r["segmento"], j.get("tipo"), r["dy"], r["ffo_yield"], r["pvp"],
                        m["payout_ffo"], m["dep_venda"], r["vacancia"], r["qtd_imoveis"], m["meses_pagos_12"],
                        m["cv_12"], m["tendencia_6m"], m["cortes"], m["cagr"], r["score"],
                        *(r["pilares"][k] for k in PESOS), j.get("classe"), j.get("confianca"),
                        j.get("p_extraordinario"), r["decisao"], r.get("conferencia_b3")])

    divergentes = [f"{r['ticker']} ({r['conferencia_b3']})" for r in candidatas
                   if (r.get("conferencia_b3") or "").startswith(("diverge", "erro"))]
    if divergentes:
        print(f"\nConferência com a B3 (últimos 12 meses) — revisar: {'; '.join(divergentes)}")
    gerar_dashboard(candidatas)
    print(f"\nDetalhes em {SAIDA_CSV} e {SAIDA_HTML}. Decisão: estudar = Jev SUSTENTÁVEL + score >= 60 + "
          f"P(extra) < 0,5; descartar = FRÁGIL. Não é recomendação de investimento.")


def gerar_dashboard(candidatas):
    anos = candidatas[0]["m"]["anos_completos"] if candidatas else []
    dados = {
        "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "anos": anos, "meses": candidatas[0]["m"]["meses"] if candidatas else [], "pesos": PESOS,
        "fundos": [{
            "ticker": r["ticker"], "nome": r.get("nome"), "segmento": r["segmento"], "gestao": r.get("gestao"),
            "cotacao": r["cotacao"], "dy": r["dy"], "ffo_yield": r["ffo_yield"], "pvp": r["pvp"],
            "valor_mercado": r["valor_mercado"], "liquidez": r["liquidez"], "oscil_12m": r.get("oscil_12m"),
            "qtd_imoveis": r["qtd_imoveis"], "vacancia": r["vacancia"], "cap_rate": r["cap_rate"],
            "dist_ffo": r["m"]["payout_ffo"], "dep_venda": r["m"]["dep_venda"],
            "meses_pagos_12": r["m"]["meses_pagos_12"], "cv_12": r["m"]["cv_12"],
            "tendencia_6m": r["m"]["tendencia_6m"], "cortes": r["m"]["cortes"], "cagr": r["m"]["cagr"],
            "anual": [round(v, 4) for v in r["m"]["serie_anual"]], "mensal": [round(v, 4) for v in r["m"]["mensal"]],
            "b3_12m": r.get("b3_12m"), "amortizacoes_12m": r["amortizacoes_12m"],
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
    ap.add_argument("--dy-min", type=float, default=8.0, help="DY mínimo em %% (padrão 8)")
    ap.add_argument("--liq-min", type=float, default=1_000_000, help="liquidez média diária mínima em R$")
    ap.add_argument("--limite", type=int, default=80, help="máximo de FIIs analisados (mais líquidos)")
    ap.add_argument("--sem-jev", action="store_true", help="só o score determinístico")
    ap.add_argument("--sem-b3", action="store_true", help="não confere os rendimentos com a B3")
    args = ap.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    configurar()
    load_env()

    mercado = carregar_mercado()
    candidatas = filtrar(mercado, args.dy_min / 100, args.liq_min, args.limite)
    print(f"{len(mercado)} FIIs no Fundamentus; {len(candidatas)} passaram nos filtros "
          f"(DY >= {args.dy_min:g}%, liquidez >= R$ {args.liq_min:,.0f}, P/VP > 0).")

    def enriquecer(r):
        try:
            r.update(detalhes(r["ticker"]))
            r["rendimentos"], r["amortizacoes_12m"] = rendimentos(r["ticker"])
            return r
        except Exception as e:
            registrar_falha(r["ticker"], "coleta", e)
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        candidatas = [r for r in pool.map(enriquecer, candidatas) if r]
    corte = HOJE - dt.timedelta(days=730)
    sem_hist = [r["ticker"] for r in candidatas if not any(d >= corte for d, _ in r["rendimentos"])]
    if sem_hist:
        print(f"Sem rendimentos nos últimos 24 meses no Fundamentus (fora da análise): {', '.join(sem_hist)}")
        candidatas = [r for r in candidatas if r["ticker"] not in sem_hist]
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
