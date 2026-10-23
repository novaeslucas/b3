"""Fatores do Jev ligados ao 2º turno (25/10/2026) e à direção do efeito dos juros.

Complementa os fatores de leitura_pos_eleicao.py (governo, dólar, juros), que medem exposição/intensidade mas não
o sentido do efeito:
  - oposicao:           Score −2..+2 — efeito de uma vitória de Flávio Bolsonaro, comparada à reeleição de Lula, sobre
                        lucro e valuation da empresa (estatais com risco de intervenção tendem a +, dependentes de
                        programas federais tendem a −);
  - programas_sociais:  Noul — dependência relevante de programas federais (MCMV, Bolsa Família, crédito subsidiado...);
  - juros_direcao:      Score −2..+2 — efeito de uma queda forte dos juros domésticos (bancos/seguradoras podem perder
                        margem financeira; varejo a crédito, incorporadoras e alavancadas ganham).

As respostas ficam em cache/jev_2turno.json, separado de jev_eleicao.json: mudar estas perguntas não invalida as
anteriores. A probabilidade de vitória de cada candidato NÃO é perguntada ao Jev: vem das pesquisas (PESQUISAS) e
de uma conta explícita em código (p_vitoria_oposicao), e o dashboard permite ajustá-la.
Não é recomendação de investimento.
"""
import hashlib
import json
import math
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(AQUI)))  # raiz do projeto

import dividendos as acoes  # noqa: E402
from dividendos import CACHE_DIR, PROVIDERS, ask_jev  # noqa: E402

JEV_CACHE = os.path.join(CACHE_DIR, "jev_2turno.json")

# Pesquisas do 2º turno: (instituto, data, Flávio, Lula) em % dos votos totais ou válidos.
PESQUISAS = [  # vigentes no fechamento de 20/10: a mais recente de cada instituto
    ("PoderData/Aya", "16/10", 50.0, 50.0),
    ("Datafolha", "15/10", 47.0, 45.0),
    ("Atlas", "19/10", 49.8, 47.6),
    ("BTG/Nexus", "19/10", 46.5, 45.9),
    ("Futura", "13/10", 50.0, 46.6),
    ("Quaest", "14/10", 46.0, 44.0),
    ("CNT/MDA", "16/10", 46.8, 45.9),
    ("Real Time Big Data", "20/10", 52.0, 48.0),
]
ERRO_PESQUISA = 0.03  # desvio-padrão assumido para a diferença entre a média das pesquisas e o resultado (válidos)

ELEICAO = {
    "event": "Brazil presidential runoff on 25 October 2026 between Flávio Bolsonaro (PL, opposition) and the "
             "incumbent Luiz Inácio Lula da Silva (PT). First round on 4 October: Flávio 47.03%, Lula 45.16%.",
    "how_investors_read_the_candidates": {
        "Flávio Bolsonaro": "market-oriented agenda: fiscal tightening, privatizations, less political interference "
                            "in state-owned companies and public banks, lighter regulation; markets expect lower "
                            "long-term interest rates and a stronger real if he wins.",
        "Lula": "continuity: active role of state-owned companies and public banks (BNDES, Banco do Brasil, Caixa) "
                "in policy, expansion of social programs (Minha Casa Minha Vida, Bolsa Família) and subsidized "
                "credit, more state influence over prices of state companies.",
    },
}

NIVEIS_EFEITO = ["strongly worse", "somewhat worse", "roughly unaffected", "somewhat better", "strongly better"]

PERGUNTAS = {
    "oposicao": {
        "type": "score",
        "instructions": "Compared with a re-election of Lula, how would a victory of Flávio Bolsonaro in "
                        "`election` change the expected earnings and stock valuation of the company in `company`? "
                        "Consider the company's ownership (state control), regulation, dependence on federal "
                        "programs or public banks, and its exposure to domestic rates and to the real.",
        "criteria": [
            "Strongly worse: the company depends heavily on federal programs, subsidies or policies that would "
            "be cut or reversed under Flávio Bolsonaro.",
            "Somewhat worse: some revenue or advantage tied to policies of the current government is at risk.",
            "Roughly unaffected: results depend mainly on global prices or market competition, not on who governs.",
            "Somewhat better: benefits from lower domestic rates, a stronger real or lighter regulation expected "
            "under a market-oriented government.",
            "Strongly better: state-controlled company, public bank or regulated business where less political "
            "interference, privatization or much lower rates would substantially re-rate the stock.",
        ],
    },
    "programas_sociais": {
        "type": "noul",
        "instructions": "Does a material part of the revenue or demand of the company in `company` depend on "
                        "Brazilian federal social programs or subsidized credit — such as Minha Casa Minha Vida "
                        "housing, Bolsa Família cash transfers, FIES student loans, Farmácia Popular, or "
                        "subsidized rural or BNDES credit?",
        "criteria": {
            "true": "Yes: a significant share of sales, customers or funding comes through federal social "
                    "programs or subsidized credit lines.",
            "false": "No: the business does not depend materially on federal social programs or subsidized credit.",
        },
    },
    "juros_direcao": {
        "type": "score",
        "instructions": "If Brazilian domestic interest rates (Selic and long-term rates) fell substantially over "
                        "the next year, how would the earnings and valuation of the company in `company` change?",
        "criteria": [
            "Strongly worse: earnings come largely from interest on cash, float or lending spreads that shrink "
            "when rates fall, such as some insurers, brokers living on float, or banks with high net interest "
            "margin on low-cost deposits.",
            "Somewhat worse: falling rates reduce financial income somewhat more than they help demand or "
            "funding costs.",
            "Roughly unaffected: revenue tied to global prices or to the dollar, little domestic debt.",
            "Somewhat better: lower financing costs or stronger domestic demand help moderately.",
            "Strongly better: highly leveraged company, homebuilder, retailer selling on credit, car rental, or "
            "long-duration business whose valuation rises sharply when rates fall.",
        ],
    },
}


def p_vitoria_oposicao(pesquisas=PESQUISAS, erro=ERRO_PESQUISA):
    """P(Flávio vence) = Φ((média dos válidos − 50%) / erro). Conta simples e explícita, não é modelo eleitoral."""
    validos = statistics.mean(f / (f + l) for _, _, f, l in pesquisas)
    return validos, 0.5 * (1 + math.erf((validos - 0.5) / (erro * math.sqrt(2))))


def estado(r):
    """Estado do Jev: indicadores da análise de dividendos + classificação da B3 + contexto da eleição."""
    s = acoes.estado_jev(r)
    s["company"]["b3_classification"] = " / ".join(x for x in (r["b3"]["setor_economico"], r["b3"]["subsetor"],
                                                                r["b3"]["segmento"]) if x)
    s["company"]["activity"] = r["b3"]["atividade"]
    s["election"] = ELEICAO
    return s


def fatores(cands):
    """Preenche r['fatores_2t'] = {oposicao (−2..+2), p_programas, juros_direcao (−2..+2), confiancas}."""
    cache = json.load(open(JEV_CACHE, encoding="utf-8")) if os.path.exists(JEV_CACHE) else {}
    pend = []
    for r in cands:
        s = estado(r)
        c = s["company"]
        r["key_2t"] = hashlib.sha1(json.dumps([c["ticker"], c["name"], c["b3_classification"], c["activity"],
                                               ELEICAO, PERGUNTAS], sort_keys=True).encode()).hexdigest()
        if r["key_2t"] not in cache:
            pend.append((r, s))
    if pend:
        env = next((e for e in PROVIDERS if os.environ.get(e)), None)
        if not env:
            sys.exit("Nenhuma chave encontrada no .env. Adicione OPENROUTER_API_KEY=... ou TYPESAFE_API_KEY=...")
        url, model = PROVIDERS[env]
        with ThreadPoolExecutor(max_workers=8) as pool:
            resps = list(pool.map(lambda p: ask_jev(p[1], url, model, os.environ[env], PERGUNTAS), pend))
        print(f"Jev ({resps[0]['model']}) avaliou fatores do 2º turno de {len(pend)} empresas; "
              f"tokens de entrada: {sum(x['usage']['input_tokens'] for x in resps)}")
        for (r, _), resp in zip(pend, resps):
            a = resp["answers"]
            cache[r["key_2t"]] = {
                "ticker": r["ticker"],
                "oposicao": round(a["oposicao"]["score"] - 2, 2),  # níveis 0..4 -> −2..+2
                "oposicao_confianca": round(a["oposicao"]["confidence"], 3),
                "p_programas": round(a["programas_sociais"]["noul"], 3),
                "juros_direcao": round(a["juros_direcao"]["score"] - 2, 2),
                "juros_direcao_confianca": round(a["juros_direcao"]["confidence"], 3),
            }
        with open(JEV_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    for r in cands:
        r["fatores_2t"] = cache[r["key_2t"]]
