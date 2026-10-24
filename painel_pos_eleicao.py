"""Painel geral pós-eleição: desempenho acumulado de Ibovespa, referências, setores e ativos ao longo dos pregões.

Junta o que as leituras diárias (pastas leituras/AAAA_MM_DD) já produziram e reconstrói a série completa de cada ativo:
  - fechamentos diários no Yahoo Finance do pregão pré-eleição (--data-base, 02/10) até o último pregão disponível;
  - cadastro dos ativos (nome, setor da B3, fatores do Jev, decisão) do dashboard_eleicao.html da pasta mais recente;
  - contexto de cada pregão (CONTEXTO) e pesquisas do 2º turno (PESQUISAS) de cada pasta diária.
Acumulado = fechamento do dia ÷ fechamento de --data-base − 1. Referências: Ibovespa (^BVSP), small caps (ETF SMAL11),
FIIs (ETF XFIX11, aproximação do IFIX, que não tem série no Yahoo) e dólar (BRL=X).

Saída: dashboard_pos_eleicao.html (de dashboard_pos_eleicao_template.html), em resultados/.
Uso:  python painel_pos_eleicao.py [--data-base 2026-10-02]
Não é recomendação de investimento.
"""
import argparse
import ast
import datetime as dt
import glob
import json
import math
import os
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

from dividendos import fetch  # noqa: E402

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=3mo&interval=1d"
TEMPLATE_HTML = os.path.join(RAIZ, "templates", "dashboard_pos_eleicao_template.html")
SAIDA_HTML = os.path.join(RAIZ, "resultados", "dashboard_pos_eleicao.html")
REFERENCIAS = [("ibov", "Ibovespa", "^BVSP"), ("smll", "Small caps (SMAL11)", "SMAL11.SA"),
               ("ifix", "FIIs (XFIX11)", "XFIX11.SA"), ("dolar", "Dólar (R$/US$)", "BRL=X")]


def serie(simbolo):
    """{data: fechamento} do Yahoo Finance; {} se não houver."""
    try:
        r = json.loads(fetch(None, YAHOO.format(simbolo), "utf-8", f"yahoo3m_{simbolo}"))["chart"]["result"][0]
        # Data em UTC − 2h. Barras da B3 vêm às 13h UTC do próprio dia. As de câmbio (BRL=X) vêm às 23h UTC e, conferidas
        # com o fechamento do dólar comercial, correspondem ao dia em UTC; barras de sábado (cotação ao vivo
        # lida no fim de semana) e de domingo trazem o fechamento de sexta. A
        # cotação ao vivo da noite chega com hora de 0h–1h UTC do dia seguinte: o −2h a mantém no pregão de hoje.
        out = {}
        for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
            d = dt.datetime.fromtimestamp(t - 7200, dt.timezone.utc).date()
            if c is not None:
                out[d - dt.timedelta(days=max(0, d.weekday() - 4))] = c  # sáb −1, dom −2
        return out
    except Exception:
        return {}


def pastas_diarias():
    return sorted(p for p in glob.glob(os.path.join(RAIZ, "leituras", "2026_*")) if os.path.isdir(p))


def literal(arquivo, nome):
    """Valor literal de uma atribuição de topo (CONTEXTO, PESQUISAS, ERRO_PESQUISA) sem executar o script."""
    if not os.path.exists(arquivo):
        return None
    for no in ast.parse(open(arquivo, encoding="utf-8").read()).body:
        if isinstance(no, ast.Assign) and any(getattr(t, "id", None) == nome for t in no.targets):
            return ast.literal_eval(no.value)
    return None


def p_vitoria(pesquisas, erro):
    """Mesma conta de fatores_2turno.p_vitoria_oposicao: Φ((média dos válidos − 50%) / erro)."""
    validos = statistics.mean(f / (f + l) for _, _, f, l in pesquisas)
    return validos, 0.5 * (1 + math.erf((validos - 0.5) / (erro * math.sqrt(2))))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-base", default="2026-10-02", help="pregão pré-eleição (base do acumulado)")
    base = dt.date.fromisoformat(ap.parse_args().data_base)

    pastas = pastas_diarias()
    ultima = pastas[-1]
    html = open(os.path.join(ultima, "dashboard_eleicao.html"), encoding="utf-8").read()
    cad = json.loads(re.search(r"const DADOS = (\{.*?\});\n", html, re.S).group(1).replace("<\\/", "</"))

    ativos = [("acao", a) for a in cad["acoes"]] + [("fii", f) for f in cad["fiis"]]
    simbolos = [s for _, _, s in REFERENCIAS] + [f"{a['ticker']}.SA" for _, a in ativos]
    with ThreadPoolExecutor(max_workers=8) as pool:
        series = dict(zip(simbolos, pool.map(serie, simbolos)))

    ibov = series["^BVSP"]
    if base not in ibov:
        sys.exit(f"Sem fechamento do Ibovespa em {base:%d/%m/%Y}.")
    dias = sorted(d for d in ibov if d >= base)
    rotulos = [f"{d:%d/%m}" for d in dias]

    def acumulado(s):
        p0 = s.get(base)
        return [None if not p0 or s.get(d) is None else s[d] / p0 - 1 for d in dias]

    refs = [{"id": i, "nome": n, "acum": acumulado(series[s]),
             "nivel": [series[s].get(d) for d in dias]} for i, n, s in REFERENCIAS]

    saida_ativos, sem_serie = [], []
    for classe, a in ativos:
        s = series[f"{a['ticker']}.SA"]
        acum = acumulado(s)
        if acum[-1] is None:
            sem_serie.append(a["ticker"])
            continue
        e = {"classe": classe, "ticker": a["ticker"], "nome": a.get("nome"), "decisao": a.get("decisao"),
             "dy_antes": a.get("dy_antes"), "acum": acum, "fech": [s.get(d) for d in dias]}
        if classe == "acao":
            e.update({k: a.get(k) for k in ("setor_economico", "setor", "segmento", "p_governo", "p_dolar", "juros",
                                            "oposicao", "p_programas", "juros_dir")})
        else:
            e.update({"setor_economico": "Fundos imobiliários", "setor": a.get("tipo"), "segmento": a.get("segmento")})
        saida_ativos.append(e)

    # Pesquisas do 2º turno vigentes no fechamento de cada pregão (pastas que já têm fatores_2turno.py).
    pesquisas = {}
    for p in pastas:
        d = dt.date(*map(int, os.path.basename(p).split("_")))
        q = literal(os.path.join(p, "fatores_2turno.py"), "PESQUISAS")
        if q:
            validos, prob = p_vitoria(q, literal(os.path.join(p, "fatores_2turno.py"), "ERRO_PESQUISA") or 0.03)
            pesquisas[f"{d:%d/%m}"] = {"validos": validos, "p": prob, "n": len(q),
                                       "lista": [{"instituto": i, "data": x, "flavio": f, "lula": l} for i, x, f, l in q]}

    # Contexto: a pasta mais recente acumula as linhas de todos os pregões ("dd/mm: ...").
    contexto = []
    for texto, url in literal(os.path.join(ultima, "leitura_pos_eleicao.py"), "CONTEXTO") or []:
        m = re.match(r"\D*?(\d{2}/\d{2})", texto)
        contexto.append({"dia": m.group(1) if m else None, "texto": texto, "url": url})

    dados = {
        "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "data_base": f"{base:%d/%m/%Y}",
        "dias": rotulos, "referencias": refs, "ativos": saida_ativos, "sem_serie": sem_serie,
        "pesquisas": pesquisas, "contexto": contexto, "fonte_cadastro": os.path.basename(ultima),
    }
    template = open(TEMPLATE_HTML, encoding="utf-8").read()
    with open(SAIDA_HTML, "w", encoding="utf-8") as f:
        f.write(template.replace("/*__DADOS__*/null", json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")))

    ib = refs[0]["acum"]
    print(f"{len(dias)} pregões ({rotulos[0]} a {rotulos[-1]}); Ibovespa acumulado {100 * ib[-1]:+.2f}%; "
          f"{len(saida_ativos)} ativos" + (f"; sem série: {', '.join(sem_serie)}" if sem_serie else ""))
    print(f"Painel em {os.path.relpath(SAIDA_HTML)}. Não é recomendação de investimento.")


if __name__ == "__main__":
    main()
