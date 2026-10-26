"""Classificação setorial oficial da B3 x Fundamentus para as ações já analisadas.

A B3 classifica as companhias em três níveis — Setor Econômico / Subsetor / Segmento — pelos produtos e
serviços que mais contribuem para a receita. A fonte é o endpoint de detalhe da companhia
(listedCompaniesProxy/CompanyCall/GetDetail), campo "industryClassification", o mesmo usado pela página
"Empresas Listadas" do site da B3.

O Fundamentus usa dois níveis com nomes equivalentes aos dois níveis inferiores da B3:
  Fundamentus "Setor"    <->  B3 "Subsetor"
  Fundamentus "Subsetor" <->  B3 "Segmento"
e não tem o nível mais alto (Setor Econômico).

Regra: quando as duas fontes divergem, vale a B3 (função classificacao()).

Uso como script:  python setores_b3.py   -> imprime a comparação e grava setores_b3.csv nesta pasta.
"""
import base64
import csv
import json
import os
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))  # leituras/AAAA_MM_DD -> raiz do projeto
sys.path.insert(0, RAIZ)

import dividendos as acoes  # noqa: E402
from dividendos import B3, fetch  # noqa: E402

SAIDA_CSV = os.path.join(AQUI, "setores_b3.csv")


def _b3(metodo, params, nome):
    p = base64.b64encode(json.dumps(params, separators=(",", ":")).encode()).decode()
    texto = fetch(None, f"{B3}/{metodo}/{p}", "utf-8", nome)
    return json.loads(texto) if texto.lstrip().startswith("{") else None


def classificacao_b3(ticker):
    """{'setor_economico', 'subsetor', 'segmento', 'atividade'} segundo a B3, ou None se não encontrar."""
    raiz = ticker[:4]
    lista = _b3("GetInitialCompanies", {"language": "pt-br", "pageNumber": 1, "pageSize": 20, "company": raiz},
                f"b3_GetInitialCompanies_1_20_{raiz}") or {}
    cvm = next((e["codeCVM"] for e in lista.get("results", []) if e["issuingCompany"] == raiz), None)
    if not cvm:
        return None
    det = _b3("GetDetail", {"codeCVM": cvm, "language": "pt-br"}, f"b3_GetDetail_{cvm}")
    if not det or not det.get("industryClassification"):
        return None
    partes = [p.strip() for p in det["industryClassification"].split("/")]
    partes += [None] * (3 - len(partes))
    return {"setor_economico": partes[0], "subsetor": partes[1], "segmento": partes[2],
            "atividade": (det.get("activity") or "").strip() or None}


def _norm(s):
    """Normaliza para comparar nomes: sem acento, minúsculo, sem pontuação e espaços repetidos."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def iguais(a, b):
    """Mesmo nome, ou um abreviado do outro (ex.: 'Serv.Méd.Hospit. Análises e Diagnósticos')."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return na == nb
    if na == nb:
        return True
    # abreviações do Fundamentus: cada palavra abreviada é prefixo da palavra correspondente
    wa, wb = na.split(), nb.split()
    return len(wa) == len(wb) and all(x.startswith(y) or y.startswith(x) for x, y in zip(wa, wb))


def classificacao(ticker, setor_fundamentus, subsetor_fundamentus):
    """Classificação a usar: B3 quando disponível (prevalece em divergência), senão Fundamentus."""
    b3 = classificacao_b3(ticker)
    if not b3:
        return {"fonte": "Fundamentus", "setor_economico": None, "subsetor": setor_fundamentus,
                "segmento": subsetor_fundamentus, "atividade": None, "diverge_subsetor": None,
                "diverge_segmento": None}
    return {"fonte": "B3", **b3,
            "diverge_subsetor": not iguais(b3["subsetor"], setor_fundamentus),
            "diverge_segmento": not iguais(b3["segmento"], subsetor_fundamentus)}


def main():
    os.chdir(RAIZ)
    linhas = list(csv.DictReader(open(acoes.csv_da_analise(acoes.HOJE), encoding="utf-8-sig"), delimiter=";"))
    fund = {r["ticker"]: acoes.detalhes(r["ticker"]) for r in linhas}
    with ThreadPoolExecutor(max_workers=4) as pool:
        res = dict(zip(fund, pool.map(lambda t: classificacao(t, fund[t]["setor"], fund[t]["subsetor"]), fund)))

    sem_b3 = [t for t, c in res.items() if c["fonte"] != "B3"]
    div_sub = [t for t, c in res.items() if c["diverge_subsetor"]]
    div_seg = [t for t, c in res.items() if c["diverge_segmento"]]
    print(f"{len(res)} ações; classificação B3 encontrada para {len(res) - len(sem_b3)}"
          + (f" (sem B3: {', '.join(sem_b3)})" if sem_b3 else ""))
    print(f"Divergências no nível Subsetor (Fundamentus 'Setor'): {len(div_sub)}")
    print(f"Divergências no nível Segmento (Fundamentus 'Subsetor'): {len(div_seg)}\n")
    print(f"{'Ticker':<8}{'Fundamentus Setor / Subsetor':<58}{'B3 Setor Econômico / Subsetor / Segmento'}")
    for t in sorted(set(div_sub) | set(div_seg)):
        c, f = res[t], fund[t]
        marca = ("S" if t in div_sub else " ") + ("G" if t in div_seg else " ")
        print(f"{t:<8}{(f['setor'] or '') + ' / ' + (f['subsetor'] or ''):<58}"
              f"{c['setor_economico']} / {c['subsetor']} / {c['segmento']}  [{marca}]")

    por_setor = {}
    for t, c in res.items():
        por_setor.setdefault(c["setor_economico"] or "-", []).append(t)
    print("\nAções por Setor Econômico da B3:")
    for s, ts in sorted(por_setor.items(), key=lambda kv: -len(kv[1])):
        print(f"  {s:<40}{len(ts):>3}  {', '.join(sorted(ts))}")

    with open(SAIDA_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["ticker", "fundamentus_setor", "fundamentus_subsetor", "b3_setor_economico", "b3_subsetor",
                    "b3_segmento", "b3_atividade", "diverge_subsetor", "diverge_segmento", "fonte_adotada"])
        for t, c in sorted(res.items()):
            w.writerow([t, fund[t]["setor"], fund[t]["subsetor"], c["setor_economico"], c["subsetor"],
                        c["segmento"], c["atividade"], c["diverge_subsetor"], c["diverge_segmento"], c["fonte"]])
    print(f"\n[S] diverge no subsetor  [G] diverge no segmento.  Detalhes em {os.path.relpath(SAIDA_CSV)}")


if __name__ == "__main__":
    main()
