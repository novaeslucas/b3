"""Classificação setorial oficial da B3 (GetDetail), para os scripts novos.

As cópias em ``leituras/AAAA_MM_DD/setores_b3.py`` continuam sendo o registro daquele
pregão e não foram alteradas. Este módulo é o ponto único do código atual.
"""
import base64
import json
import unicodedata

from b3lib.http import B3, fetch


def _b3(metodo, params, nome):
    p = base64.b64encode(json.dumps(params, separators=(",", ":")).encode()).decode()
    texto = fetch(None, f"{B3}/{metodo}/{p}", "utf-8", nome)
    return json.loads(texto) if texto.lstrip().startswith("{") else None


def classificacao_b3(ticker):
    """{'setor_economico', 'subsetor', 'segmento', 'atividade'} segundo a B3, ou None."""
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
            "atividade": (det.get("activity") or "").strip() or None, "code_cvm": str(cvm)}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def iguais(a, b):
    """Mesmo nome, ou um abreviado do outro (ex.: 'Serv.Méd.Hospit. Análises e Diagnósticos')."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return na == nb
    if na == nb:
        return True
    wa, wb = na.split(), nb.split()
    return len(wa) == len(wb) and all(x.startswith(y) or y.startswith(x) for x, y in zip(wa, wb))


def classificacao(ticker, setor_fundamentus, subsetor_fundamentus):
    """B3 quando disponível (prevalece em divergência), senão Fundamentus."""
    b3 = classificacao_b3(ticker)
    if not b3:
        return {"fonte": "Fundamentus", "setor_economico": None, "subsetor": setor_fundamentus,
                "segmento": subsetor_fundamentus, "atividade": None, "diverge_subsetor": None,
                "diverge_segmento": None, "code_cvm": None}
    return {"fonte": "B3", **b3,
            "diverge_subsetor": not iguais(b3["subsetor"], setor_fundamentus),
            "diverge_segmento": not iguais(b3["segmento"], subsetor_fundamentus)}
