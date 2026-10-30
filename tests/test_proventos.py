"""Parse do HTML do Fundamentus e regras de JCP / natureza do provento."""
import datetime as dt
from pathlib import Path

import pytest

from b3lib.fundamentus import parse_resultado_acoes
from b3lib.proventos import (
    agregar_por_ano, aliquota_jcp, aplicar_dy_liquido, classificar_natureza,
    parse_proventos_html, tabela_anual,
)

FIX = Path(__file__).resolve().parent / "fixtures"
HOJE = dt.date(2026, 10, 30)


def test_resultado_php():
    rows = parse_resultado_acoes((FIX / "resultado_amostra.html").read_text(encoding="utf-8"))
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "BMGB4"
    assert r["cotacao"] == pytest.approx(5.2)
    assert r["dy"] == pytest.approx(0.12)
    assert r["pl"] == pytest.approx(6.5)
    assert r["liq_2m"] == pytest.approx(3_000_000)
    assert r["cresc_rec_5a"] == pytest.approx(0.08)


def test_proventos_jcp_liquido_e_por_mil_acoes():
    page = (FIX / "proventos_amostra.html").read_text(encoding="utf-8")
    eventos = parse_proventos_html(page)
    por_tipo = {(e["data_com"], e["classe"]): e for e in eventos}
    jcp_2025 = por_tipo[(dt.date(2025, 3, 15), "jcp")]
    assert jcp_2025["valor_bruto"] == pytest.approx(1)
    assert jcp_2025["valor_liquido"] == pytest.approx(0.85)
    jcp_2026 = por_tipo[(dt.date(2026, 8, 10), "jcp")]
    assert jcp_2026["valor_liquido"] == pytest.approx(0.825)
    sem_pagamento = por_tipo[(dt.date(2026, 2, 2), "jcp")]
    assert sem_pagamento["valor_liquido"] == pytest.approx(1 - aliquota_jcp(dt.date(2026, 2, 2)))
    extra = next(e for e in eventos if e["extraordinario"])
    assert extra["valor_bruto"] == pytest.approx(2)
    assert extra["data_com"].year == 2024
    assert tabela_anual(page)[2025] == pytest.approx(3)
    liquido = agregar_por_ano(eventos, liquido=True, incluir_outros=False)
    assert liquido[2025] == pytest.approx(2.85)
    assert liquido[2024] == pytest.approx(2)


def test_dy_bruto_so_perde_o_ir_do_jcp():
    page = (FIX / "proventos_amostra.html").read_text(encoding="utf-8")
    eventos = parse_proventos_html(page)
    dy, fator = aplicar_dy_liquido(0.12, eventos, HOJE)
    # janela até 30/10/2026: os dois JCP de 2026 (1,00 cada; 0,825 e 0,825) e nada de 2025
    assert fator == pytest.approx(0.825)
    assert dy == pytest.approx(0.12 * 0.825)


def test_natureza_recorrente_misto_e_extraordinario():
    def ev(ano, bruto, tipo="DIVIDENDO", liquido=None):
        return {
            "data_com": dt.date(ano, 6, 1), "classe": "dividendo", "tipo": tipo,
            "valor_bruto": bruto, "valor_liquido": bruto if liquido is None else liquido,
            "extraordinario": "EXTRA" in tipo,
        }
    anos = [2020, 2021, 2022, 2023, 2024, 2025]
    recorrente = classificar_natureza([ev(a, 1) for a in anos], anos)
    assert recorrente["natureza_proventos"] == "recorrente"
    assert recorrente["parcela_extraordinaria"] == 0

    misto = [ev(a, 1) for a in anos if a != 2025] + [ev(2025, 0.8), ev(2025, 0.2, "DIVIDENDO EXTRAORDINARIO")]
    nat = classificar_natureza(misto, anos)
    assert nat["natureza_proventos"] == "misto"
    assert nat["parcela_extraordinaria"] == pytest.approx(0.2)

    page = (FIX / "proventos_amostra.html").read_text(encoding="utf-8")
    pico = classificar_natureza(parse_proventos_html(page), anos)
    assert pico["natureza_proventos"] == "extraordinario"
    assert pico["parcela_extraordinaria"] > 0.5


def test_aliquota_historica_e_a_partir_de_2026():
    assert aliquota_jcp(dt.date(2025, 12, 31)) == 0.15
    assert aliquota_jcp(dt.date(2026, 1, 1)) == 0.175
    assert aliquota_jcp(None) == 0.15
