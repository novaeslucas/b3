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


def _ev(ano, bruto, tipo="DIVIDENDO", mes=6, dia=1, fator=None):
    classe = "ajuste" if fator else "dividendo"
    return {
        "data_com": dt.date(ano, mes, dia), "classe": classe, "tipo": tipo,
        "valor_bruto": bruto, "valor_liquido": bruto,
        "extraordinario": "EXTRA" in tipo,
        "fator": fator,
    }


def test_pico_usa_proventos_por_acao_dos_dois_lados():
    """12 meses contra a mediana dos anos fechados. Razão > 1,8 é extraordinário."""
    hoje = dt.date(2026, 10, 26)
    eventos = [_ev(ano, 1.0) for ano in (2021, 2022, 2023, 2024, 2025)]
    eventos.append(_ev(2026, 1.9, mes=3))
    nat = classificar_natureza(eventos, hoje)
    assert nat["mediana_proventos_acao"] == pytest.approx(1.0)
    assert nat["proventos_12m_bruto"] == pytest.approx(1.9)
    assert nat["natureza_proventos"] == "extraordinario"
    assert nat["gatilho_natureza"] == "pico_proventos"


def test_queda_de_lucro_com_dividendo_estavel_e_recorrente():
    """TIMS3 e VIVT3 no pregão de 26/10: o provento por ação não estoura a mediana.

    O DY × P/L de quem teve lucro em queda não entra nesta conta.
    """
    hoje = dt.date(2026, 10, 26)

    def serie(anuais, ttm):
        eventos = [_ev(ano, valor) for ano, valor in anuais.items()]
        eventos.append(_ev(2026, ttm, mes=6))
        return classificar_natureza(eventos, hoje)

    tims = serie({2021: 0.4329, 2022: 0.5785, 2023: 0.9089, 2024: 0.9599, 2025: 2.5121}, 1.4714)
    vivt = serie({2021: 3.4626, 2022: 3.0563, 2023: 2.0585, 2024: 1.8953, 2025: 1.2383}, 1.4360)
    assert tims["natureza_proventos"] != "extraordinario"
    assert vivt["natureza_proventos"] != "extraordinario"
    assert tims["natureza_proventos"] == "recorrente"
    assert vivt["natureza_proventos"] == "recorrente"


def test_dividendo_rotulado_extraordinario():
    hoje = dt.date(2026, 10, 26)
    eventos = [_ev(ano, 1.0) for ano in (2021, 2022, 2023, 2024, 2025)]
    eventos.append(_ev(2026, 5.0, "DIVIDENDO EXTRAORDINARIO", mes=3))
    nat = classificar_natureza(eventos, hoje)
    assert nat["natureza_proventos"] == "extraordinario"
    assert "rotulado" in nat["gatilho_natureza"]
    assert "pico_proventos" in nat["gatilho_natureza"]


def test_faixa_mista_so_com_rotulo_e_reserva_e_historico_curto():
    hoje = dt.date(2026, 10, 26)
    base = [_ev(ano, 1.0) for ano in (2021, 2022, 2023, 2024, 2025)]
    sem_rotulo = classificar_natureza(base + [_ev(2026, 1.5, mes=3)], hoje)
    assert sem_rotulo["natureza_proventos"] == "recorrente"
    com_rotulo = classificar_natureza(base + [_ev(2026, 1.5, "DIVIDENDO EXTRAORDINARIO", mes=3)], hoje)
    assert com_rotulo["natureza_proventos"] == "misto"
    assert com_rotulo["gatilho_natureza"] == "rotulado"

    reserva = classificar_natureza(base + [_ev(2026, 1.0, mes=3)], hoje, pago_de_reserva=True)
    assert reserva["natureza_proventos"] == "extraordinario"
    assert reserva["gatilho_natureza"] == "reserva"

    curto = classificar_natureza([_ev(2024, 1.0), _ev(2025, 1.0), _ev(2026, 3.0, mes=3)], hoje)
    assert curto["natureza_proventos"] == "historico_insuficiente"


def test_desdobramento_ajusta_o_provento_antigo():
    """1 ação vira 2: o provento anterior à data é dividido por 2 antes da mediana."""
    hoje = dt.date(2026, 10, 26)
    eventos = [_ev(ano, 2.0) for ano in (2021, 2022, 2023, 2024)]
    eventos.append(_ev(2025, 1.0, "DESDOBRAMENTO", mes=1, dia=2, fator=2.0))
    eventos.append(_ev(2025, 1.0))
    eventos.append(_ev(2026, 1.0, mes=3))
    nat = classificar_natureza(eventos, hoje)
    assert nat["mediana_proventos_acao"] == pytest.approx(1.0)
    assert nat["natureza_proventos"] == "recorrente"


def test_aliquota_historica_e_a_partir_de_2026():
    from b3lib.proventos import ALIQUOTA_JCP_ATE_2025, ALIQUOTA_JCP_DESDE_2026, DATA_NOVA_ALIQUOTA_JCP
    assert aliquota_jcp(dt.date(2025, 12, 31)) == ALIQUOTA_JCP_ATE_2025 == 0.15
    assert aliquota_jcp(DATA_NOVA_ALIQUOTA_JCP) == ALIQUOTA_JCP_DESDE_2026 == 0.175
    assert aliquota_jcp(None) == 0.15
