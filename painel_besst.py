"""Painel BESST: bancos, energia, saneamento, seguros e telecom com a cotação de agora (pregão em andamento).

BESST = Bancos, Energia, Saneamento, Seguros e Telecomunicações, os setores "perenes" de dividendos. O universo é a
lista BESST abaixo: as empresas já analisadas em dividendos.py (com score, decisão e fatores do Jev) mais as grandes
do setor que não passaram nos filtros de dividendos (só preço e DY aproximado).

Para cada empresa:
  - preço de agora (último negócio no Yahoo Finance; cache por minuto, para pegar o preço da execução);
  - variação do dia (agora ÷ fechamento de --data-anterior) e acumulado (agora ÷ fechamento de --data-base);
  - série diária desde --data-base, para o acumulado por subsetor;
  - DY de agora = DY do snapshot do Fundamentus (--snapshot) × cotação do snapshot ÷ preço de agora;
  - se já analisada: score, decisão e fatores do Jev da leitura do pregão em andamento
    (leituras/AAAA_MM_DD_pregao_em_andamento/dashboard_eleicao.html).

Pregão não finalizado (padrão): a saída vai para resultados_pregao_em_andamento/painel_besst_AAAA_MM_DD.html,
separada dos painéis de pregões finalizados. Com --finalizado, depois do fechamento: o preço é o de fechamento, o
cadastro vem de leituras/AAAA_MM_DD/ e a saída vai para resultados/painel_besst_AAAA_MM_DD.html.

Uso:  python painel_besst.py [--data-base 2026-10-02] [--data-anterior 2026-10-23] [--pregao 2026-10-26]
                             [--snapshot 2026-10-05] [--finalizado]
Não é recomendação de investimento.
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

import dividendos as acoes  # noqa: E402
from dividendos import fetch  # noqa: E402

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=3mo&interval=1d"
TEMPLATE_HTML = os.path.join(RAIZ, "templates", "painel_besst_template.html")
SAIDA_HTML = os.path.join(RAIZ, "resultados_pregao_em_andamento", "painel_besst_{:%Y_%m_%d}.html")
SAIDA_FINALIZADO = os.path.join(RAIZ, "resultados", "painel_besst_{:%Y_%m_%d}.html")
PAINEL_FINALIZADO = os.path.join(RAIZ, "resultados", "dashboard_pos_eleicao.html")
AGORA = dt.datetime.now()

# (ticker, nome, subsetor BESST, controle estatal)
BESST = [
    ("ITUB4", "Itaú Unibanco", "Bancos", False),
    ("BBDC4", "Bradesco", "Bancos", False),
    ("BBAS3", "Banco do Brasil", "Bancos", True),
    ("SANB11", "Santander Brasil", "Bancos", False),
    ("BPAC11", "BTG Pactual", "Bancos", False),
    ("ITSA4", "Itaúsa (holding do Itaú)", "Bancos", False),
    ("ABCB4", "ABC Brasil", "Bancos", False),
    ("BRSR6", "Banrisul", "Bancos", True),
    ("BMGB4", "Banco BMG", "Bancos", False),
    ("BRBI11", "BR Partners", "Bancos", False),
    ("PINE4", "Banco Pine", "Bancos", False),
    ("BPAN4", "Banco Pan", "Bancos", False),
    ("AXIA3", "Axia Energia (ex-Eletrobras)", "Energia", False),
    ("ELET3", "Eletrobras", "Energia", False),
    ("EGIE3", "Engie Brasil", "Energia", False),
    ("EQTL3", "Equatorial", "Energia", False),
    ("CPFE3", "CPFL Energia", "Energia", False),
    ("CMIG4", "Cemig", "Energia", True),
    ("CPLE3", "Copel", "Energia", False),
    ("TAEE11", "Taesa", "Energia", False),
    ("ISAE4", "ISA Energia", "Energia", False),
    ("ALUP11", "Alupar", "Energia", False),
    ("ENGI11", "Energisa", "Energia", False),
    ("AURE3", "Auren", "Energia", False),
    ("SBSP3", "Sabesp", "Saneamento", False),
    ("SAPR11", "Sanepar", "Saneamento", True),
    ("CSMG3", "Copasa", "Saneamento", True),
    ("BBSE3", "BB Seguridade", "Seguros", True),
    ("CXSE3", "Caixa Seguridade", "Seguros", True),
    ("PSSA3", "Porto Seguro", "Seguros", False),
    ("IRBR3", "IRB Re", "Seguros", False),
    ("WIZC3", "Wiz Co", "Seguros", False),
    ("VIVT3", "Telefônica Brasil (Vivo)", "Telecom", False),
    ("TIMS3", "TIM", "Telecom", False),
    ("FIQE3", "Unifique", "Telecom", False),
]
REFERENCIAS = [("ibov", "Ibovespa", "^BVSP"), ("dolar", "Dólar (R$/US$)", "BRL=X")]


def serie(simbolo):
    """({data: fechamento}, horário do último negócio) do Yahoo; ({}, None) se não houver. Mesma regra de datas do
    painel_pos_eleicao.py (UTC − 2h; barras de sábado/domingo do câmbio contam como sexta; a última barra vale para o
    dia de meta.regularMarketTime)."""
    try:
        r = json.loads(fetch(None, YAHOO.format(simbolo), "utf-8", f"yahoo3m_{simbolo}_{AGORA:%H%M}"))["chart"]["result"][0]
        out = {}
        tss, closes, ult = r["timestamp"], r["indicators"]["quote"][0]["close"], r["meta"].get("regularMarketTime")
        for k, (t, c) in enumerate(zip(tss, closes)):
            if k == len(tss) - 1 and ult:  # cotação corrente: vale para o dia de regularMarketTime em Brasília
                d = dt.datetime.fromtimestamp(ult, dt.timezone(dt.timedelta(hours=-3))).date()
            else:
                d = dt.datetime.fromtimestamp(t - 7200, dt.timezone.utc).date()
            if c is not None:
                out[d - dt.timedelta(days=max(0, d.weekday() - 4))] = c
        ts = ult
        hora = dt.datetime.fromtimestamp(ts, dt.timezone(dt.timedelta(hours=-3))).strftime("%H:%M") if ts else None
        return out, hora
    except Exception:
        return {}, None


def dados_embutidos(arquivo):
    if not os.path.exists(arquivo):
        return None
    return json.loads(re.search(r"const DADOS = (\{.*?\});\n", open(arquivo, encoding="utf-8").read(), re.S)
                      .group(1).replace("<\\/", "</"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-base", default="2026-10-02", help="pregão pré-eleição (base do acumulado)")
    ap.add_argument("--data-anterior", default="2026-10-23", help="último pregão finalizado (base do dia)")
    ap.add_argument("--pregao", default="2026-10-26", help="pregão em andamento")
    ap.add_argument("--snapshot", default="2026-10-05", help="dia dos dados do Fundamentus usados para o DY")
    ap.add_argument("--finalizado", action="store_true", help="pregão já fechado: preço de fechamento, saída em resultados/")
    args = ap.parse_args()
    base, anterior, pregao = (dt.date.fromisoformat(d) for d in (args.data_base, args.data_anterior, args.pregao))

    acoes.HOJE = dt.date.fromisoformat(args.snapshot)  # resultado.php do snapshot, já em cache
    fundamentus = {r["ticker"]: r for r in acoes.carregar_mercado()}
    pasta = f"{pregao:%Y_%m_%d}" + ("" if args.finalizado else "_pregao_em_andamento")
    leitura = dados_embutidos(os.path.join(RAIZ, "leituras", pasta, "dashboard_eleicao.html"))
    analisadas = {a["ticker"]: a for a in (leitura or {}).get("acoes", [])}
    contexto = [c for c in (leitura or {}).get("contexto", []) if c["texto"].startswith((f"{pregao:%d/%m}", "25/10"))]

    simbolos = [s for _, _, s in REFERENCIAS] + [f"{t}.SA" for t, *_ in BESST]
    with ThreadPoolExecutor(max_workers=8) as pool:
        resp = dict(zip(simbolos, pool.map(serie, simbolos)))
    series = {s: v[0] for s, v in resp.items()}
    horario = resp["^BVSP"][1]

    # Dias finalizados que o Yahoo trocou pela cotação ao vivo (acontece com BRL=X) vêm do painel finalizado.
    fin = dados_embutidos(PAINEL_FINALIZADO)
    if fin:
        dias_fin = [dt.datetime.strptime(f"{r}/{base.year}", "%d/%m/%Y").date() for r in fin["dias"]]
        pares = [(s, r["nivel"]) for (_, _, s), r in zip(REFERENCIAS, [x for x in fin["referencias"]
                                                                        if x["id"] in ("ibov", "dolar")])]
        pares += [(f"{a['ticker']}.SA", a["fech"]) for a in fin["ativos"]]
        for simbolo, valores in pares:
            if simbolo in series:
                for d, v in zip(dias_fin, valores):
                    if v is not None:
                        series[simbolo].setdefault(d, v)

    if pregao not in series["^BVSP"]:
        sys.exit(f"Sem preço do Ibovespa em {pregao:%d/%m/%Y} no Yahoo.")
    dias = sorted(d for d in series["^BVSP"] if base <= d <= pregao)
    rotulos = [f"{d:%d/%m}" + (" (parcial)" if d == pregao and not args.finalizado else "") for d in dias]

    def acumulado(s):
        p0 = s.get(base)
        return [None if not p0 or s.get(d) is None else s[d] / p0 - 1 for d in dias]

    def var(s, i, j):
        return s[j] / s[i] - 1 if s.get(i) and s.get(j) else None

    refs = [{"id": i, "nome": n, "acum": acumulado(series[s]), "nivel": [series[s].get(d) for d in dias],
             "dia": var(series[s], anterior, pregao)} for i, n, s in REFERENCIAS]

    empresas, sem_preco = [], []
    for ticker, nome, subsetor, estatal in BESST:
        s = series[f"{ticker}.SA"]
        if s.get(pregao) is None or s.get(base) is None:
            sem_preco.append(ticker)
            continue
        f, a = fundamentus.get(ticker) or {}, analisadas.get(ticker)
        agora = s[pregao]
        dy_agora = f["dy"] * f["cotacao"] / agora if f.get("dy") is not None and f.get("cotacao") else None
        e = {"ticker": ticker, "nome": nome, "subsetor": subsetor, "estatal": estatal,
             "preco": agora, "fech_anterior": s.get(anterior), "fech_base": s[base],
             "dia": var(s, anterior, pregao), "acum": acumulado(s),
             "dy_snapshot": f.get("dy"), "dy_agora": dy_agora,
             "pl_agora": f["pl"] * agora / f["cotacao"] if f.get("pl") and f.get("cotacao") else None,
             "pvp_agora": f["pvp"] * agora / f["cotacao"] if f.get("pvp") and f.get("cotacao") else None,
             "roe": f.get("roe"), "analisada": bool(a)}
        if a:
            e.update({k: a.get(k) for k in ("decisao", "jev_classe", "score_antes", "score_hoje", "p_governo",
                                            "juros", "oposicao", "juros_dir")})
        empresas.append(e)

    dados = {
        "gerado_em": AGORA.strftime("%d/%m/%Y %H:%M"), "horario_precos": horario,
        "data_base": f"{base:%d/%m/%Y}", "data_anterior": f"{anterior:%d/%m/%Y}", "data_pregao": f"{pregao:%d/%m/%Y}",
        "dias": rotulos, "referencias": refs, "empresas": empresas, "sem_preco": sem_preco, "contexto": contexto,
        "snapshot": f"{acoes.HOJE:%d/%m/%Y}", "finalizado": args.finalizado, "fonte_cadastro": pasta,
    }
    saida = (SAIDA_FINALIZADO if args.finalizado else SAIDA_HTML).format(pregao)
    os.makedirs(os.path.dirname(saida), exist_ok=True)
    template = open(TEMPLATE_HTML, encoding="utf-8").read()
    with open(saida, "w", encoding="utf-8") as fh:
        fh.write(template.replace("/*__DADOS__*/null", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")))

    print(f"{'PREGÃO FINALIZADO — fechamento' if args.finalizado else 'PREGÃO EM ANDAMENTO — preços'} de "
          f"{pregao:%d/%m} às {horario or '?'}; Ibovespa no dia "
          f"{100 * refs[0]['dia']:+.2f}%")
    for sub in dict.fromkeys(e["subsetor"] for e in empresas):
        g = [e for e in empresas if e["subsetor"] == sub]
        print(f"  {sub:<11} n={len(g):<3} dia {100 * statistics.mean(e['dia'] for e in g if e['dia'] is not None):+7.2f}%"
              f"   acumulado {100 * statistics.mean(e['acum'][-1] for e in g):+7.2f}%")
    if sem_preco:
        print(f"Sem cotação no Yahoo (fora do painel): {', '.join(sem_preco)}")
    print(f"Painel em {os.path.relpath(saida)}. Não é recomendação de investimento.")


if __name__ == "__main__":
    main()
