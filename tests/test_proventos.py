"""Parse do HTML do Fundamentus e alíquota do JCP."""
import datetime as dt
from pathlib import Path

import pytest

from b3lib.fundamentus import parse_resultado_acoes
from b3lib.proventos import (
    agregar_por_ano, aliquota_jcp, deduplicar_eventos, parse_proventos_html, tabela_anual,
)

FIX = Path(__file__).resolve().parent / "fixtures"


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


def test_seer3_parcelas_com_pagamento_diferente_contam_as_duas():
    """Fundamentus de SEER3: mesma data-com e valor, datas de pagamento diferentes.

    São parcelas. Só some a linha quando a data de pagamento também repete.
    """
    html = """
    <table><tbody>
    <tr><td>31/08/2026</td><td>0,2028</td><td>DIVIDENDO</td><td>30/10/2026</td><td>1</td></tr>
    <tr><td>31/08/2026</td><td>0,2028</td><td>DIVIDENDO</td><td>25/09/2026</td><td>1</td></tr>
    <tr><td>06/04/2026</td><td>0,2388</td><td>DIVIDENDO</td><td>29/05/2026</td><td>1</td></tr>
    <tr><td>06/04/2026</td><td>0,2388</td><td>DIVIDENDO</td><td>30/04/2026</td><td>1</td></tr>
    <tr><td>06/04/2026</td><td>0,2388</td><td>DIVIDENDO</td><td>30/04/2026</td><td>1</td></tr>
    </tbody></table>
    """
    eventos = parse_proventos_html(html)
    assert len(eventos) == 4
    assert sum(e["valor_bruto"] for e in eventos if e["data_com"] == dt.date(2026, 4, 6)) == pytest.approx(0.2388 * 2)
    repetida = deduplicar_eventos(eventos + [eventos[0]])
    assert len(repetida) == 4


def test_aliquota_historica_e_a_partir_de_2026():
    from b3lib.proventos import ALIQUOTA_JCP_ATE_2025, ALIQUOTA_JCP_DESDE_2026, DATA_NOVA_ALIQUOTA_JCP
    assert aliquota_jcp(dt.date(2025, 12, 31)) == ALIQUOTA_JCP_ATE_2025 == 0.15
    assert aliquota_jcp(DATA_NOVA_ALIQUOTA_JCP) == ALIQUOTA_JCP_DESDE_2026 == 0.175
    assert aliquota_jcp(None) == 0.15
