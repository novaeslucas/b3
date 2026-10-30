"""Leitura pós-2º turno atualizada com o pregão de 29/10/2026 sobre os ativos já analisados.

Cópia de ../2026_10_28/leitura_pos_eleicao.py com as datas e o contexto de 29/10/2026. Dois horizontes:
  - dia:        fechamento do pregão analisado ÷ fechamento do pregão anterior (29/10 ÷ 28/10);
  - acumulado:  fechamento do pregão analisado ÷ fechamento pré-eleição (29/10 ÷ 02/10).
O fator "efeito de vitória da oposição" do Jev segue testado contra a reação real (oposição derrotada em 25/10).

Base da análise: os mesmos dados do Fundamentus e as mesmas respostas do Jev da leitura de 05/10
(--snapshot 2026-10-05), para que só o preço mude entre uma leitura e outra. Os dados e o cache ficam na raiz
do projeto (dois níveis acima, ../..); as saídas desta leitura ficam nesta pasta:
  leitura_pos_eleicao.csv e dashboard_eleicao.html (de dashboard_eleicao_template.html).

Passos:
  1. Busca no Yahoo Finance os fechamentos das três datas para cada ativo e para o Ibovespa.
  2. Reaplica o preço do pregão analisado aos indicadores que dependem dele (DY, P/L, P/VP, EV/EBITDA aproximado)
     e recalcula o score determinístico. Payout, ROE, histórico de proventos etc. não mudam com o preço.
  3. Fatores do Jev por ação (governo, dólar, juros): reaproveitados do cache da leitura de 05/10.
  4. Cruza as variações com esses fatores, com a decisão (estudar/monitorar/descartar) e com o tipo de FII.
  5. Setores das ações pela classificação oficial da B3 (setores_b3.py): Setor Econômico / Subsetor / Segmento;
     quando diverge do Fundamentus, vale a B3.
  6. Fatores do 2º turno (fatores_2turno.py): efeito de uma vitória da oposição (−2..+2), dependência de programas
     sociais e direção do efeito de juros menores (−2..+2); P(vitória) vem das pesquisas, em código.

Uso:  python leitura_pos_eleicao.py [--data-base 2026-10-02] [--data-anterior 2026-10-28]
                                    [--data-pregao 2026-10-29] [--snapshot 2026-10-05]
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

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))  # leituras/AAAA_MM_DD -> raiz do projeto
sys.path.insert(0, RAIZ)

import dividendos as acoes  # noqa: E402
import fatores_2turno  # noqa: E402  (nesta pasta)
import fiis  # noqa: E402
import setores_b3  # noqa: E402  (nesta pasta)
from dividendos import CACHE_DIR, PROVIDERS, ask_jev, fetch, load_env  # noqa: E402

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1mo&interval=1d"
JEV_CACHE = os.path.join(CACHE_DIR, "jev_eleicao.json")
SAIDA_CSV = os.path.join(AQUI, "leitura_pos_eleicao.csv")
SAIDA_HTML = os.path.join(AQUI, "dashboard_eleicao.html")
TEMPLATE_HTML = os.path.join(AQUI, "dashboard_eleicao_template.html")

# Contexto do pregão exibido no topo do dashboard (texto, link da fonte).
CONTEXTO = [
    ("1º turno (04/10): Flávio Bolsonaro 47,03% × Lula 45,16% — 2º turno em 25/10",
     "https://pt.wikipedia.org/wiki/Elei%C3%A7%C3%A3o_presidencial_no_Brasil_em_2026"),
    ("05/10: Ibovespa +7,70% (maior alta desde 2020), dólar −4,12% a R$ 5,00, juros futuros em forte queda",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-05102026/"),
    ("06/10: Ibovespa −0,52% (realização de lucros), dólar −0,51% a R$ 4,976 — abaixo de R$ 5 pela 1ª vez desde maio; "
     "juros futuros seguem em queda; bancos privados sobem (BBDC4 +4,40%), BBAS3 −6,42% após rebaixamento do JPMorgan",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-06102026/"),
    ("07/10: Ibovespa −0,74% (2ª queda seguida), dólar +0,70% a R$ 5,011; mercado cobra sinais fiscais concretos; "
     "Treasuries em máximas de vários anos e petróleo acima de US$ 100; bancos caem (BBDC4 −3,68%, ITUB4 −2,66%), VALE3 −2,30%",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-07102026/"),
    ("08/10: Ibovespa +0,94%, dólar +0,27% a R$ 5,024, DIs em queda na maior parte da curva; pesquisa PoderData/Aya: "
     "Flávio 53% × Lula 47% dos válidos no 2º turno; small caps e varejo sobem (AZZA3 +7,71%, MGLU3 +5,56%); "
     "Brent +4,07% (Irã/Ormuz) puxa PETR4 +2,12%; VALE3 −1,61% com minério na mínima em 18 meses",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-08102026/"),
    ("09/10: Ibovespa +1,38% a 209.067 — maior fechamento da história; semana +8,82%, a mais forte de 2026; "
     "dólar −0,78% a R$ 4,985; DIs em queda em toda a curva; Datafolha: Flávio 49% × Lula 45% (empate técnico), "
     "Atlas: 51,1% × 45,7%; IPCA de setembro +0,82% (12 meses 4,58%, acima do teto da meta); varejo dispara "
     "(MGLU3 +19,22%, LREN3 +11,56%); telecom cai com avanço da Starlink (VIVT3 −5,25%, TIMS3 −4,18%)",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-09102026/"),
    ("13/10 (após o feriado de 12/10): Ibovespa −1,04% a 206.888, dólar +0,71% a R$ 5,020, DIs em alta em toda a curva; small caps −3,28%; BTG/Nexus: Flávio 48% × Lula 45% (52% × 48% dos válidos), Futura: 50% × 46,6%; Focus: IPCA 2026 a 5,22%; varejo e educação caem (VAMO3 −11,22%, YDUQ3 −8,45%, LREN3 −8,03%, MGLU3 −7,04%, RENT3 −5,72% após rebaixamento); petroleiras sobem (PRIO3 +3,58%, PETR4 +1,52%)",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-13102026/"),
    ("14/10: Ibovespa −0,18% a 206.506, dólar +0,17% a R$ 5,028, DIs mistos; CPI dos EUA 3,7% em 12 meses; pacote de benefícios do governo chega a R$ 260 bi; Flávio confirma presença no debate da Band; BHIA3 −24,31%, MGLU3 −4,49%, bancos caem (BPAC11 −2,81%, SANB11 −2,08%, ITUB4 −1,41%); PETR4 +2,22% com petróleo e Ormuz",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-14102026/"),
    ("15/10: Ibovespa −1,06% a 204.324 (3ª queda seguida), dólar +1,17% a R$ 5,087, DIs sobem até 0,25 p.p.; Quaest (14/10): Flávio 46% × Lula 44% (empate técnico); Senado aprova fim da escala 6x1; varejo de agosto +0,1% e indústria −0,6% (risco de PIB negativo no 3º tri); incorporadoras caem (DIRR3 −9,26%, MRVE3 −4,94%)",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-15102026/"),
    ("16/10: Ibovespa +0,54% a 205.430 (semana −1,74%; small caps −6,08%), dólar −0,28% a R$ 5,073, DIs em queda; Datafolha: Flávio 47% × Lula 45% (vantagem cai de 4 para 2 pontos), CNT/MDA 46,8% × 45,9%, PoderData 50% × 50% dos válidos; Brent +3,72% a US$ 109,86 (PETR4 +4,04%); MGLU3 +6,93%",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-16102026/"),
    ("19/10: Ibovespa −0,17% a 205.079, dólar −0,49% a R$ 5,048; BTG/Nexus: Flávio 46,5% × Lula 45,9%, Atlas: 49,8% × 47,6%; Bolsa Família reajustado antecipado para antes do 2º turno; varejo e construção sobem (AZZA3 +13,25%, MRVE3 +10,94%, COGN3 +8,65%, VAMO3 +8,11%); PETR4 −2,82%, seguradoras caem (PSSA3 −3,13%, CXSE3 −2,95%)",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-19102026/"),
    ("20/10: Ibovespa +1,57% a 208.296, dólar −0,58% a R$ 5,019, DIs em queda; Real Time Big Data: Flávio 52% × Lula 48% dos válidos; varejo dispara (BHIA3 +20,17%, MGLU3 +14,79%, AMER3 +15,58%), VAMO3 +8,32%, HAPV3 +7,55%, bancos e B3 sobem (B3SA3 +5,38%, BBDC4 +2,89%); VALE3 −1,39%",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-20102026/"),
    ("21/10: Ibovespa −0,86% a 206.495, dólar +0,17% a R$ 5,027, DIs em queda; Quaest (20/10): Flávio 45% × Lula 44% (rejeição 52% × 47%), Meio/Ideia: 51,1% × 48,9% dos válidos; Fitch espera aperto fiscal em 2027 qualquer que seja o vencedor; WEGE3 −7,34% após o 3T26, HAPV3 −8,48%, COGN3 −7,08%; telecom sobe (TIMS3 +1,73%)",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-21102026/"),
    ("22/10: Ibovespa −1,15% a 204.120, dólar +0,22% a R$ 5,038 (2ª alta seguida), DIs em alta em toda a curva (DI1F29 +0,15 p.p. a 12,71%); arrecadação federal recorde em setembro (+5,62% real); Brent +4,00% a US$ 113,17 com ataques houthis; Nasdaq −2,60% com ações de IA; siderurgia e mineração caem (CSNA3 −5,43%, USIM5 −4,81%, VALE3 −3,97%, GGBR4 −3,92%), EMBJ3 −4,66%, bancos caem (BBDC4 −2,08%, ITUB4 −1,84%); BBSE3 +2,05%, PRIO3 +1,85%; Datafolha sai após o fechamento",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-22102026/"),
    ("23/10: Ibovespa +0,47% a 205.083 (semana −0,17%), small caps +2,02%, dólar −0,90% a R$ 4,993 (semana −1,64%), DIs em queda em toda a curva; IPCA-15 de outubro +0,32%, abaixo do esperado (12 meses 4,62%); Datafolha (22/10): empate em 50% × 50% dos válidos, Vox Brasil: Lula 50,6% × Flávio 49,4% dos válidos; último debate na Globo à noite; bancos e B3 sobem (BBAS3 +3,34%, B3SA3 +1,72%), HAPV3 +2,32%, PETR4 +1,47% com Brent a US$ 116,72",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-23102026/"),
    ("25/10 (2º turno): Lula reeleito com 51,79% dos válidos × Flávio Bolsonaro 48,21%; Flávio diz que vai pedir a anulação da eleição — fontes do TSE e do STF veem chance remota",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-26102026/"),
    ("26/10: Ibovespa −13,88% a 176.608 (mínima 169.516) — circuit breaker às 9h57, o 1º desde março de 2020; small caps −26,92%, IFIX −5,68%; dólar +8,25% a R$ 5,403 (máxima R$ 5,605); DIs longos +2,5 a +2,7 p.p. (DI1F29 15,25%, DI1F35 15,20%); risco-país +26%; Tesouro cancela leilões de prefixados e IPCA+ e recompra R$ 7,1 bi em NTN-F; Durigan (Fazenda) promete mudar a lei para buscar o centro da meta fiscal; quedas extremas em domésticos (MGLU3 −58,23%, VAMO3 −56,79%, HAPV3 −54,93%, CEAB3 −47,47%, CSAN3 −46,73%, B3SA3 −36,28%), bancos (BBAS3 −29,33%, BPAC11 −24,83%, BBDC4 −21,70%, ITUB4 −13,66%), PETR4 −8,18%; exportadoras sobem (SUZB3 +10,08%, EMBJ3 +8,89%, WEGE3 +4,21%, VALE3 +4,01%)",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-26102026/"),
    ("27/10: Ibovespa −0,51% a 175.710 (2ª queda seguida; semana −14,32%), small caps −0,53%, IFIX −1,19%; dólar −0,97% a R$ 5,350; DIs em queda em toda a curva (DI1F29 −0,51 p.p. a 14,74%); Durigan diz que o governo tem medidas fiscais prontas, Alckmin e Rafael Fonteles cotados para a Fazenda, Galípolo nega; Citi e BCA rebaixam ativos brasileiros; Tesouro recompra R$ 16,8 bi em NTN-B; IPP de setembro +1,54%; repique em domésticos (TEND3 +10,20%, MGLU3 +8,49%, DIRR3 +5,35%, VAMO3 +5,05%, TIMS3 +5,03%); NATU3 −6,94%, TOTS3 −6,78%, MRVE3 −5,71%; VALE3 −2,14% com minério na mínima em 2 anos, PETR4 −2,02%, bancos caem (BBDC4 −1,38%, ITUB4 −1,22%)",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-27102026/"),
    ("28/10: Ibovespa −2,68% a 171.006 (semana −16,62%, mês −8,23%), small caps −4,22%, IFIX −1,75%; dólar −0,19% a R$ 5,340; DIs longos sobem (DI1F33 +0,135 p.p. a 15,04%), DI1F27 estável; Fed mantém os juros em 3,75%–4,00% (10 × 2, duas votam por alta); saída de R$ 2,8 bi de estrangeiros no dia seguinte à eleição; Flávio Bolsonaro vai para os EUA; Brent a US$ 115,32; quedas em domésticos (MOVI3 −16,24%, CSAN3 −12,31%, VAMO3 −11,34%, COGN3 −10,00%, AZZA3 −9,64%, MGLU3 −9,22%) e bancos (BBDC4 −6,68%, SANB11 −5,88%, ITUB4 −3,42%, BBAS3 −3,39%); KLBN11 −6,37%, VALE3 −2,57%; sobem MBRF3 +2,82%, TIMS3 +2,26%, WEGE3 +2,25%, PRIO3 +1,80%",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-28102026/"),
    ("29/10: Ibovespa −1,06% a 169.186 (4ª queda seguida; semana −17,50%), small caps −2,57%, IFIX −0,60%; dólar +0,89% a R$ 5,387; DIs longos sobem (DI1F35 +0,135 p.p. a 15,12%); fiscal do novo mandato no centro, IOF citado como fonte de arrecadação; Nasdaq −1,38% com Alphabet; PCE dos EUA 3,6%, BCE mantém juros e Lagarde anuncia saída; IGP-M de outubro 1,47%; exportadoras sobem (GGBR4 +5,69%, EMBJ3 +5,25%, GOAU4 +5,16%, WEGE3 +4,41%, SUZB3 +3,37%); domésticos caem (BEEF3 −6,92%, MRVE3 −6,70%, DIRR3 −6,16%, COGN3 −5,85%, AZZA3 −5,60%), ABEV3 −4,40% após balanço, BBDC4 −3,54%, VALE3 −2,17%",
     "https://www.infomoney.com.br/mercados/ibovespa-hoje-bolsa-de-valores-ao-vivo-29102026/"),
]

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

def fechamentos(ticker, datas, pregao):
    """Fechamentos (Yahoo Finance) nas datas pedidas; None onde não houver."""
    simbolo = ticker if ticker.startswith("^") else f"{ticker}.SA"
    try:
        texto = fetch(None, YAHOO.format(simbolo), "utf-8", f"yahoo_{simbolo}_{pregao:%Y%m%d}")
        r = json.loads(texto)["chart"]["result"][0]
        fech = {}
        for ts, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
            if c is not None:
                fech[dt.datetime.fromtimestamp(ts, dt.timezone(dt.timedelta(hours=-3))).date()] = c
        return [fech.get(d) for d in datas]
    except Exception:
        return [None] * len(datas)


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
    acoes.classificar(cands)  # respostas já em cache: mesmos dados do snapshot
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
    """Média por grupo nos dois horizontes."""
    print(f"\n{titulo}{'':<14}{'dia':>30}{'acumulado':>38}")
    grupos = {}
    for r in linhas:
        grupos.setdefault(chave(r), []).append(r)
    for k, v in sorted(grupos.items(), key=lambda kv: -statistics.mean(x["var_acum"] for x in kv[1])):
        print(f"  {str(k):<34}{media_pct([x['var_dia'] for x in v])}   {media_pct([x['var_acum'] for x in v])}")


def r2(y, X):
    """R² de mínimos quadrados (equações normais com eliminação de Gauss-Jordan, sem numpy)."""
    n = len(X[0])
    A = [[sum(x[i] * x[j] for x in X) for j in range(n)] + [sum(x[i] * v for x, v in zip(X, y))] for i in range(n)]
    for i in range(n):
        piv = max(range(i, n), key=lambda k: abs(A[k][i]))
        A[i], A[piv] = A[piv], A[i]
        for k in range(n):
            if k != i:
                fat = A[k][i] / A[i][i]
                A[k] = [a - fat * b for a, b in zip(A[k], A[i])]
    b = [A[i][n] / A[i][i] for i in range(n)]
    m = statistics.mean(y)
    return 1 - sum((v - sum(bi * xi for bi, xi in zip(b, x))) ** 2 for v, x in zip(y, X)) / sum((v - m) ** 2 for v in y)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-base", default="2026-10-02", help="pregão pré-eleição (preço usado nas análises)")
    ap.add_argument("--data-anterior", default="2026-10-28", help="pregão anterior ao analisado")
    ap.add_argument("--data-pregao", default="2026-10-29", help="pregão analisado")
    ap.add_argument("--snapshot", default="2026-10-05", help="dia dos dados do Fundamentus/Jev usados como base")
    args = ap.parse_args()
    base, anterior, pregao = (dt.date.fromisoformat(d) for d in (args.data_base, args.data_anterior, args.data_pregao))
    acoes.HOJE = fiis.HOJE = dt.date.fromisoformat(args.snapshot)  # reaproveita cache do Fundamentus e do Jev
    os.chdir(RAIZ)  # dividendos.py e fiis.py usam caminhos relativos à raiz (cache/, CSVs, .env)
    load_env()

    ca, cf = carregar_acoes(), carregar_fiis()
    ativos = [(r, acoes) for r in ca] + [(r, fiis) for r in cf]
    datas = [base, anterior, pregao]
    with ThreadPoolExecutor(max_workers=8) as pool:
        precos = list(pool.map(lambda x: fechamentos(x[0]["ticker"], datas, pregao), ativos))
        ibov, ifix = pool.map(lambda t: fechamentos(t, datas, pregao), ["^BVSP", "IFIX.SA"])

    sem_preco = []
    for (r, mod), (p0, p1, p2) in zip(ativos, precos):
        if not (p0 and p1 and p2):
            sem_preco.append(r["ticker"])
            r["var_dia"] = None
            continue
        if abs(p0 / r["cotacao"] - 1) > 0.02:  # base do Yahoo deve bater com a cotação usada na análise
            print(f"  aviso: {r['ticker']} base Yahoo {p0:.2f} x Fundamentus {r['cotacao']:.2f}")
        r["cot_anterior"] = p1
        r["var_dia"] = p2 / p1 - 1
        r["var_acum"] = p2 / p0 - 1
        r["novo"] = reprecificar(r, p2 / r["cotacao"], mod)
    fatores_jev(ca)
    # Classificação setorial: B3 prevalece sobre o Fundamentus (só exibição e agrupamento; as respostas do Jev
    # já em cache foram dadas com o setor do Fundamentus).
    with ThreadPoolExecutor(max_workers=4) as pool:
        for r, c in zip(ca, pool.map(lambda r: setores_b3.classificacao(r["ticker"], r.get("setor"), r.get("subsetor")), ca)):
            r["b3"] = c
    fatores_2turno.fatores(ca)
    divergentes = [r["ticker"] for r in ca if r["b3"]["diverge_subsetor"] or r["b3"]["diverge_segmento"]]
    print(f"Classificação setorial: B3 para {sum(r['b3']['fonte'] == 'B3' for r in ca)} de {len(ca)} ações; "
          f"diverge do Fundamentus (vale a B3): {', '.join(divergentes) or 'nenhuma'}")

    var = lambda p, i, j: p[j] / p[i] - 1 if p[i] and p[j] else None
    fmt = lambda v: "indisponível" if v is None else f"{100 * v:+.2f}%"
    print(f"\nIbovespa: dia ({pregao:%d/%m} vs {anterior:%d/%m}) {fmt(var(ibov, 1, 2))}   "
          f"acumulado ({pregao:%d/%m} vs {base:%d/%m}) {fmt(var(ibov, 0, 2))}   IFIX {fmt(var(ifix, 0, 2))}")
    if sem_preco:
        print(f"Sem cotação no Yahoo: {', '.join(sem_preco)}")
    ca = [r for r in ca if r["var_dia"] is not None]
    cf = [r for r in cf if r["var_dia"] is not None]

    # ------------------------------------------------ ações
    print("\n=================== AÇÕES ===================")
    print(f"Média das {len(ca)} ações:  dia {media_pct([r['var_dia'] for r in ca])}   "
          f"acumulado {media_pct([r['var_acum'] for r in ca])}")
    grupo("Por decisão da análise de dividendos:", ca, lambda r: r["decisao"])
    grupo("Por setor econômico (B3):", ca, lambda r: (r["b3"]["setor_economico"] or "-")[:33])
    f = lambda r: r["fatores"]
    grupo("Por exposição ao governo federal (Jev):", ca,
          lambda r: "exposta (P >= 0,5)" if f(r)["p_governo"] >= 0.5 else "pouco exposta (P < 0,5)")
    grupo("Por receita em dólar (Jev):", ca,
          lambda r: "receita em dólar (P >= 0,5)" if f(r)["p_dolar"] >= 0.5 else "receita em reais (P < 0,5)")
    grupo("Por sensibilidade a juros domésticos (Jev, escala 0-2):", ca,
          lambda r: "alta (>= 1,5)" if f(r)["juros"] >= 1.5 else "moderada (0,5-1,5)" if f(r)["juros"] >= 0.5 else "baixa (< 0,5)")

    g2 = lambda r: r["fatores_2t"]
    grupo("Efeito de vitória da oposição (Jev, -2..+2):", ca,
          lambda r: "ganha (>= +0,5)" if g2(r)["oposicao"] >= 0.5 else "perde (<= -0,5)" if g2(r)["oposicao"] <= -0.5 else "neutra")
    grupo("Dependência de programas sociais (Jev):", ca,
          lambda r: "depende (P >= 0,5)" if g2(r)["p_programas"] >= 0.5 else "não depende (P < 0,5)")
    grupo("Efeito de juros menores (Jev, -2..+2):", ca,
          lambda r: "ganha (>= +0,5)" if g2(r)["juros_direcao"] >= 0.5 else "perde (<= -0,5)" if g2(r)["juros_direcao"] <= -0.5 else "neutro")
    validos, p_op = fatores_2turno.p_vitoria_oposicao()
    print(f"\nPesquisas: Flávio com {100 * validos:.1f}% dos válidos em média -> P(vitória) ~ {100 * p_op:.0f}% "
          f"(erro assumido {100 * fatores_2turno.ERRO_PESQUISA:.0f} p.p.)")
    for nome, cols in (("fatores antigos", ["p_governo", "p_dolar", "juros"]),
                       ("antigos + 2º turno", ["p_governo", "p_dolar", "juros", "oposicao", "p_programas", "juros_direcao"])):
        X = [[1.0] + [{**f(r), **g2(r)}[c] for c in cols] for r in ca]
        print(f"R² da variação acumulada explicada por {nome}: {r2([r['var_acum'] for r in ca], X):.2f}   "
              f"do dia: {r2([r['var_dia'] for r in ca], X):.2f}")
    # Teste do fator direcional com o resultado conhecido: correlação entre "ganha com a oposição" e a variação do dia
    # (esperado: negativa, já que a oposição perdeu).
    xs, ys = [g2(r)["oposicao"] for r in ca], [r["var_dia"] for r in ca]
    print(f"Correlação (efeito oposição do Jev × variação do dia): {statistics.correlation(xs, ys):+.2f}  "
          f"(esperado < 0 com a derrota da oposição)")

    cab = (f"{'Ticker':<8}{'Setor':<24}{'Dia':>8}{'Acum.':>8}{'DY antes':>9}{'DY hoje':>8}{'Score':>13}  "
           f"{'Gov':>4}{'US$':>5}{'Juros':>6}  Decisão")
    linha = lambda r: (f"{r['ticker']:<8}{(r['b3']['subsetor'] or '')[:23]:<24}{100 * r['var_dia']:+7.2f}%{100 * r['var_acum']:+7.2f}%"
                       f"{100 * r['dy']:8.1f}%{100 * r['novo']['dy']:7.1f}%{r['score']:6.1f}->{r['novo']['score']:<5.1f}  "
                       f"{f(r)['p_governo']:4.2f}{f(r)['p_dolar']:5.2f}{f(r)['juros']:6.2f}  {r['decisao']}")
    ca.sort(key=lambda r: -r["var_dia"])
    print(f"\nMaiores altas do dia:\n{cab}")
    for r in ca[:8]:
        print(linha(r))
    print(f"\nMaiores quedas do dia:\n{cab}")
    for r in ca[-8:]:
        print(linha(r))
    print("\nAções em 'estudar':")
    for r in [r for r in ca if r["decisao"] == "estudar"]:
        print(linha(r))
    abaixo = [f"{r['ticker']} ({100 * r['novo']['dy']:.1f}%)" for r in ca if r["novo"]["dy"] < 0.05 <= r["dy"]]
    print(f"\nDY abaixo do mínimo de 5% com o preço de {pregao:%d/%m}: {', '.join(abaixo) or 'nenhuma'}")

    # ------------------------------------------------ FIIs
    print("\n=================== FIIs ===================")
    print(f"Média dos {len(cf)} FIIs:  dia {media_pct([r['var_dia'] for r in cf])}   "
          f"acumulado {media_pct([r['var_acum'] for r in cf])}")
    grupo("Por tipo (Jev):", cf, lambda r: r["jev"]["tipo"])
    grupo("Por decisão da análise de rendimentos:", cf, lambda r: r["decisao"])
    abaixo = [f"{r['ticker']} ({100 * r['novo']['dy']:.1f}%)" for r in cf if r["novo"]["dy"] < 0.08 <= r["dy"]]
    print(f"\nDY abaixo do mínimo de 8% com o preço de {pregao:%d/%m}: {', '.join(abaixo) or 'nenhum'}")

    with open(SAIDA_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["classe_ativo", "ticker", "setor_segmento", "tipo_fii_jev", "var_dia", "var_acumulada",
                    "cotacao_base", "cotacao_anterior", "cotacao_pregao", "dy_antes", "dy_hoje", "pl_ou_pvp_hoje",
                    "score_antes", "score_hoje", "jev_classe", "decisao", "jev_p_governo", "jev_p_dolar", "jev_juros_0a2",
                    "b3_setor_economico", "b3_segmento", "fonte_setor", "setor_fundamentus",
                    "jev_oposicao_m2a2", "jev_p_programas_sociais", "jev_juros_direcao_m2a2"])
        for r in ca:
            w.writerow(["acao", r["ticker"], r["b3"]["subsetor"], None, r["var_dia"], r["var_acum"], r["cotacao"],
                        r["cot_anterior"], r["novo"]["cotacao"], r["dy"], r["novo"]["dy"], r["novo"]["pl"], r["score"],
                        r["novo"]["score"], r["jev"]["classe"], r["decisao"], f(r)["p_governo"], f(r)["p_dolar"],
                        f(r)["juros"], r["b3"]["setor_economico"], r["b3"]["segmento"], r["b3"]["fonte"], r.get("setor"),
                        g2(r)["oposicao"], g2(r)["p_programas"], g2(r)["juros_direcao"]])
        for r in cf:
            w.writerow(["fii", r["ticker"], r["segmento"], r["jev"]["tipo"], r["var_dia"], r["var_acum"], r["cotacao"],
                        r["cot_anterior"], r["novo"]["cotacao"], r["dy"], r["novo"]["dy"], r["novo"]["pvp"], r["score"],
                        r["novo"]["score"], r["jev"]["classe"], r["decisao"], None, None, None, None, None, None, None,
                        None, None, None])
    gerar_dashboard(ca, cf, datas, ibov, ifix, sem_preco)
    print(f"\nDetalhes em {os.path.relpath(SAIDA_CSV)} e {os.path.relpath(SAIDA_HTML)}. "
          f"Não é recomendação de investimento.")


def gerar_dashboard(ca, cf, datas, ibov, ifix, sem_preco):
    """Grava dashboard_eleicao.html a partir do template, embutindo os dados desta leitura."""
    base, anterior, pregao = datas
    var = lambda p, i, j: p[j] / p[i] - 1 if p[i] and p[j] else None
    comum = lambda r: {
        "ticker": r["ticker"], "var_dia": r["var_dia"], "var_acum": r["var_acum"],
        "cot_base": r["cotacao"], "cot_anterior": r["cot_anterior"], "cot_pregao": r["novo"]["cotacao"],
        "dy_antes": r["dy"], "dy_hoje": r["novo"]["dy"], "pvp_hoje": r["novo"]["pvp"],
        "score_antes": r["score"], "score_hoje": r["novo"]["score"],
        "jev_classe": r["jev"]["classe"], "jev_confianca": r["jev"]["confianca"], "decisao": r["decisao"],
    }
    dados = {
        "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "data_base": f"{base:%d/%m/%Y}", "data_anterior": f"{anterior:%d/%m/%Y}", "data_pregao": f"{pregao:%d/%m/%Y}",
        "ibov_dia": var(ibov, 1, 2), "ibov_acum": var(ibov, 0, 2),
        "ifix_dia": var(ifix, 1, 2), "ifix_acum": var(ifix, 0, 2),
        "sem_preco": sem_preco,
        "pesquisas": [{"instituto": i, "data": d, "flavio": fl, "lula": lu} for i, d, fl, lu in fatores_2turno.PESQUISAS],
        "erro_pesquisa": fatores_2turno.ERRO_PESQUISA, "contexto": [{"texto": t, "url": u} for t, u in CONTEXTO],
        "acoes": [{**comum(r), "nome": r.get("empresa"), "pl_hoje": r["novo"]["pl"],
                   # setor = Subsetor da B3 (mesmo nível do "Setor" do Fundamentus); B3 prevalece em divergência
                   "setor": r["b3"]["subsetor"], "setor_economico": r["b3"]["setor_economico"],
                   "segmento": r["b3"]["segmento"], "atividade": r["b3"]["atividade"], "fonte_setor": r["b3"]["fonte"],
                   "setor_fundamentus": f"{r.get('setor') or '–'} / {r.get('subsetor') or '–'}",
                   "diverge_setor": bool(r["b3"]["diverge_subsetor"] or r["b3"]["diverge_segmento"]),
                   "p_governo": r["fatores"]["p_governo"], "p_dolar": r["fatores"]["p_dolar"],
                   "juros": r["fatores"]["juros"],
                   "oposicao": r["fatores_2t"]["oposicao"], "oposicao_conf": r["fatores_2t"]["oposicao_confianca"],
                   "p_programas": r["fatores_2t"]["p_programas"],
                   "juros_dir": r["fatores_2t"]["juros_direcao"]} for r in ca],
        "fiis": [{**comum(r), "nome": r.get("nome"), "segmento": r["segmento"], "tipo": r["jev"]["tipo"]} for r in cf],
    }
    template = open(TEMPLATE_HTML, encoding="utf-8").read()
    payload = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    with open(SAIDA_HTML, "w", encoding="utf-8") as f:
        f.write(template.replace("/*__DADOS__*/null", payload))


if __name__ == "__main__":
    main()
