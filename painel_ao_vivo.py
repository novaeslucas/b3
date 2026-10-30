"""Painel ao vivo: cotações do pregão (atraso de até 15 min) com altas, baixas e o mapa setorial da B3.

Universo: os mesmos tickers dos outros painéis
  - ativos do painel pós-eleição (resultados/dashboard_pos_eleicao.html), já com a classificação setorial da B3;
  - ações da análise de dividendos (resultados/resultado_dividendos.csv);
  - lista BESST de painel_besst.py (lida sem importar o script).
Quem não tem setor da B3 no painel pós-eleição é classificado por b3lib.setores (cache diário em cache/).

Coleta: gráfico intradiário do Yahoo Finance (chart?range=1d&interval=5m, ticker com .SA), sem cache em disco.
O script roda em loop: a cada --intervalo segundos durante o pregão (e até 15 min depois do fechamento, por causa
do atraso do Yahoo); fora do pregão dorme até a abertura do próximo dia útil. A cada coleta regrava
  - resultados_ao_vivo/painel_ao_vivo_dados.js  (receberDados({...}); a página recarrega este arquivo a cada 15 s);
  - resultados_ao_vivo/painel_ao_vivo.html      (template com os dados embutidos: abre sozinho como retrato).
Antes de cada coleta o .js é regravado com "coletando", para o indicador do topo da página.
Abrir resultados_ao_vivo/painel_ao_vivo.html direto no navegador (file://) funciona: um <script> pode ser
recarregado de arquivo local, ao contrário de fetch() de um JSON.

Uso:  python painel_ao_vivo.py [--intervalo 120] [--uma-vez]
Não é recomendação de investimento.
"""
import argparse
import ast
import csv
import datetime as dt
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

from b3lib.cotacoes import BRASILIA, agenda, cotacao, montar_universo, relacoes  # noqa: E402
from b3lib.http import baixar  # noqa: E402
from b3lib.log import configurar, falhas, limpar_falhas, registrar_falha, resumo_falhas  # noqa: E402
from b3lib.setores import classificacao_b3  # noqa: E402
from b3lib.yahoo import chart_resultado  # noqa: E402

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1d&interval=5m"
TEMPLATE_HTML = os.path.join(RAIZ, "templates", "painel_ao_vivo_template.html")
PASTA_SAIDA = os.path.join(RAIZ, "resultados_ao_vivo")
SAIDA_HTML = os.path.join(PASTA_SAIDA, "painel_ao_vivo.html")
SAIDA_JS = os.path.join(PASTA_SAIDA, "painel_ao_vivo_dados.js")
PAINEL_POS_ELEICAO = os.path.join(RAIZ, "resultados", "dashboard_pos_eleicao.html")
CSV_DIVIDENDOS = os.path.join(RAIZ, "resultados", "resultado_dividendos.csv")
PAINEL_BESST = os.path.join(RAIZ, "painel_besst.py")
FII = "Fundos imobiliários"
REFERENCIAS = [("ibov", "Ibovespa", "^BVSP"), ("smll", "Small caps (SMAL11)", "SMAL11.SA"),
               ("ifix", "FIIs (XFIX11)", "XFIX11.SA"), ("dolar", "Dólar (R$/US$)", "BRL=X")]
# Relações que a raiz do ticker não mostra. Escritas à mão a partir de fato público e estável; só entram se os dois
# tickers estiverem no universo. ON/PN/units da mesma companhia saem sozinhas da raiz do ticker.
RELACOES_CURADAS = [
    ("ITSA4", "ITUB4", "controladora (Itaúsa → Itaú Unibanco)"),
    ("BBAS3", "BBSE3", "controladora (Banco do Brasil → BB Seguridade)"),
    ("AXIA3", "ELET3", "mesma companhia (Eletrobras passou a se chamar Axia Energia)"),
]


def dados_embutidos(arquivo):
    if not os.path.exists(arquivo):
        return None
    return json.loads(re.search(r"(?:const|let) DADOS = (\{.*?\});\n", open(arquivo, encoding="utf-8").read(), re.S)
                      .group(1).replace("<\\/", "</"))


def lista_besst(arquivo=PAINEL_BESST):
    """BESST de painel_besst.py sem executar o script: [(ticker, nome, subsetor BESST, estatal)]."""
    for no in ast.parse(open(arquivo, encoding="utf-8").read()).body:
        if isinstance(no, ast.Assign) and any(getattr(t, "id", None) == "BESST" for t in no.targets):
            return ast.literal_eval(no.value)
    return []


def cadastro():
    """Universo do painel com setor econômico, subsetor e segmento da B3."""
    pos = {}
    for a in (dados_embutidos(PAINEL_POS_ELEICAO) or {}).get("ativos", []):
        pos[a["ticker"]] = {"nome": a.get("nome"), "classe": a["classe"], "setor_economico": a.get("setor_economico"),
                            "subsetor": a.get("setor"), "segmento": a.get("segmento")}
    div = {}
    if os.path.exists(CSV_DIVIDENDOS):
        for r in csv.DictReader(open(CSV_DIVIDENDOS, encoding="utf-8-sig"), delimiter=";"):
            div[r["ticker"]] = {"nome": r["empresa"], "classe": "acao", "setor_fundamentus": r["setor"]}
    besst = {t: {"nome": n, "classe": "acao", "besst": s} for t, n, s, _ in lista_besst()}
    universo = montar_universo(pos, div, besst)

    faltam = [u for u in universo if u["classe"] == "acao" and not u.get("setor_economico")]

    def classificar(u):
        try:
            return classificacao_b3(u["ticker"])
        except Exception as e:
            registrar_falha(u["ticker"], "setor B3", e)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        for u, c in zip(faltam, pool.map(classificar, faltam)):
            if c:
                u.update(setor_economico=c["setor_economico"], subsetor=c["subsetor"], segmento=c["segmento"])
            else:  # sem B3: fica o setor do Fundamentus, marcado
                u["setor_economico"] = "Sem classificação B3"
                u["subsetor"] = u.get("setor_fundamentus") or u.get("besst")
    for u in universo:
        if u["classe"] == "fii":
            u["setor_economico"] = FII
    return universo


def cotar(simbolo):
    try:
        return cotacao(chart_resultado(baixar(YAHOO.format(simbolo))))
    except Exception as e:
        registrar_falha(simbolo, "yahoo ao vivo", e)
        return None


def escrever(caminho, texto):
    """Grava de forma atômica: a página nunca lê um arquivo pela metade."""
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(tmp, caminho)


def publicar(dados, template=None):
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    js = json.dumps(dados, ensure_ascii=False, default=str).replace("</", "<\\/")
    escrever(SAIDA_JS, f"receberDados({js});\n")
    template = template or open(TEMPLATE_HTML, encoding="utf-8").read()
    escrever(SAIDA_HTML, template.replace("/*__DADOS__*/null", js))


def coletar(universo, anteriores):
    """Cotação de cada ticker; quem falhar mantém a cotação anterior marcada como desatualizada."""
    simbolos = [s for _, _, s in REFERENCIAS] + [f"{u['ticker']}.SA" for u in universo]
    with ThreadPoolExecutor(max_workers=8) as pool:
        resp = dict(zip(simbolos, pool.map(cotar, simbolos)))
    pregao = next((c["pregao"] for c in resp.values() if c and c.get("pregao") and c["pregao"][0]), None)
    linhas = []
    for u in universo:
        c = resp[f"{u['ticker']}.SA"]
        if c is None:
            c = dict(anteriores.get(u["ticker"]) or {}, desatualizada=True) if anteriores.get(u["ticker"]) else None
        if c is None:
            continue
        c.pop("pregao", None)
        linhas.append({**u, **c, "empresa": c.get("nome_yahoo") or u.get("nome")})
    refs = []
    for i, n, s in REFERENCIAS:
        c = resp[s]
        if c:
            c.pop("pregao", None)
            refs.append({"id": i, "nome": n, **c})
    return linhas, refs, pregao


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--intervalo", type=int, default=120, help="segundos entre coletas durante o pregão (mín. 30)")
    ap.add_argument("--uma-vez", action="store_true", help="coleta uma vez, grava e sai")
    args = ap.parse_args()
    intervalo = dt.timedelta(seconds=max(30, args.intervalo))
    configurar()
    template = open(TEMPLATE_HTML, encoding="utf-8").read()

    print("Montando o universo e a classificação setorial…")
    universo = cadastro()
    arestas = relacoes([u["ticker"] for u in universo], RELACOES_CURADAS)
    print(f"{len(universo)} ativos; {len(arestas)} relações entre empresas.")
    dados = {"status": {"coletando": False, "intervalo_s": intervalo.total_seconds(), "n_universo": len(universo)},
             "cotacoes": [], "referencias": [], "relacoes": arestas}
    # Retrato anterior: a página não fica vazia enquanto a primeira coleta roda.
    antigo = dados_embutidos(SAIDA_HTML)
    if antigo:
        dados.update(cotacoes=antigo.get("cotacoes", []), referencias=antigo.get("referencias", []))
        dados["status"].update({k: antigo["status"].get(k) for k in ("ultima_coleta", "mercado", "n_cotacoes")})
    try:
        laco(dados, universo, intervalo, args.uma_vez, template)
    except KeyboardInterrupt:  # a página passa a mostrar "coletor parado"
        dados["status"].update(coletando=False, proxima_coleta=None)
        publicar(dados, template)
        print("\nColeta interrompida.")
    print(f"Painel em {os.path.relpath(SAIDA_HTML)}. Não é recomendação de investimento.")
    resumo_falhas()


def laco(dados, universo, intervalo, uma_vez, template):
    pregao = (dt.datetime.now(BRASILIA).replace(hour=10, minute=0, second=0, microsecond=0),
              dt.datetime.now(BRASILIA).replace(hour=17, minute=0, second=0, microsecond=0))
    while True:
        inicio = dt.datetime.now(BRASILIA)
        dados["status"].update(coletando=True, inicio_coleta=inicio.isoformat(timespec="seconds"))
        publicar(dados, template)
        limpar_falhas()
        anteriores = {c["ticker"]: c for c in dados["cotacoes"]}
        linhas, refs, p = coletar(universo, anteriores)
        pregao = p or pregao
        agora = dt.datetime.now(BRASILIA)
        mercado, proxima = agenda(agora, intervalo, *pregao)
        dados.update(cotacoes=linhas, referencias=refs or dados["referencias"])
        dados["status"].update(
            coletando=False, ultima_coleta=agora.isoformat(timespec="seconds"),
            duracao_s=round((agora - inicio).total_seconds(), 1),
            proxima_coleta=None if uma_vez else proxima.isoformat(timespec="seconds"),
            mercado=mercado, pregao=[x.strftime("%H:%M") for x in pregao],
            n_cotacoes=sum(not c.get("desatualizada") for c in linhas), n_universo=len(universo),
            falhas=[f"{t} ({etapa})" for t, etapa, _ in falhas()])
        publicar(dados, template)
        print(f"{agora:%d/%m %H:%M:%S} {dados['status']['n_cotacoes']}/{len(universo)} cotações em "
              f"{dados['status']['duracao_s']} s; mercado {mercado}"
              + ("" if uma_vez else f"; próxima às {proxima:%d/%m %H:%M:%S}"), flush=True)
        if uma_vez:
            return
        time.sleep(max(1.0, (proxima - dt.datetime.now(BRASILIA)).total_seconds()))


if __name__ == "__main__":
    main()
