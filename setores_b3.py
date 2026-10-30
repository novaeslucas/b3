"""Ponto de entrada atual da classificação setorial da B3.

Uso:  python setores_b3.py

As cópias em leituras/AAAA_MM_DD/setores_b3.py são o registro congelado de cada pregão.
"""
import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

import dividendos as acoes  # noqa: E402
from b3lib.setores import classificacao  # noqa: E402

SAIDA_CSV = os.path.join(RAIZ, "setores_b3.csv")


def main():
    os.chdir(RAIZ)
    linhas = list(csv.DictReader(open(acoes.csv_da_analise(acoes.HOJE), encoding="utf-8-sig"), delimiter=";"))
    fund = {r["ticker"]: acoes.detalhes(r["ticker"]) for r in linhas}
    with ThreadPoolExecutor(max_workers=4) as pool:
        res = dict(zip(fund, pool.map(lambda t: classificacao(t, fund[t]["setor"], fund[t]["subsetor"], acoes.HOJE), fund)))

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
