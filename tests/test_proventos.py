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


def test_natureza_recorrente_misto_e_extraordinario_na_janela_do_dy():
    def ev(ano, bruto, tipo="DIVIDENDO", mes=6, dia=1):
        return {
            "data_com": dt.date(ano, mes, dia), "classe": "dividendo", "tipo": tipo,
            "valor_bruto": bruto, "valor_liquido": bruto,
            "extraordinario": "EXTRA" in tipo,
        }
    anos = [2020, 2021, 2022, 2023, 2024, 2025]
    hoje = dt.date(2025, 12, 31)
    recorrente = classificar_natureza([ev(a, 1) for a in anos], hoje, anos)
    assert recorrente["natureza_proventos"] == "recorrente"
    assert recorrente["parcela_extraordinaria"] == 0

    misto = [ev(a, 1) for a in anos if a != 2025] + [ev(2025, 0.8), ev(2025, 0.2, "DIVIDENDO EXTRAORDINARIO")]
    nat = classificar_natureza(misto, hoje, anos)
    assert nat["natureza_proventos"] == "misto"
    assert nat["parcela_extraordinaria"] == pytest.approx(0.2)

    # 2025 foi normal; o extraordinário cai em 2026, dentro dos 12 meses do DY.
    base = [ev(a, 1) for a in anos]
    base.append(ev(2026, 5, "DIVIDENDO EXTRAORDINARIO", mes=3))
    pico = classificar_natureza(base, dt.date(2026, 10, 30), anos + [2026])
    assert pico["natureza_proventos"] == "extraordinario"
    assert pico["parcela_extraordinaria"] > 0.5
    assert pico["proventos_janela_liquido"] == pytest.approx(5)


def test_crescimento_do_dividendo_com_payout_estavel_e_recorrente():
    """O valor por ação dobra; o payout de 12 meses fica na mediana. Não é extraordinário."""
    def ev(ano, bruto, mes=6, dia=1):
        return {
            "data_com": dt.date(ano, mes, dia), "classe": "dividendo", "tipo": "DIVIDENDO",
            "valor_bruto": bruto, "valor_liquido": bruto, "extraordinario": False,
        }
    anos = [2020, 2021, 2022, 2023, 2024, 2025]
    eventos = [ev(a, 1) for a in anos] + [ev(2026, 5, mes=3)]
    payouts = {a: 0.40 for a in anos}
    nat = classificar_natureza(
        eventos, dt.date(2026, 10, 30), anos + [2026],
        payouts_por_ano=payouts, payout_12m=0.42,
    )
    assert nat["natureza_proventos"] == "recorrente"
    assert nat["parcela_extraordinaria"] == 0


def test_pico_de_payout_rotulo_e_reserva():
    def ev(ano, bruto, tipo="DIVIDENDO", mes=6):
        return {
            "data_com": dt.date(ano, mes, 1), "classe": "dividendo", "tipo": tipo,
            "valor_bruto": bruto, "valor_liquido": bruto,
            "extraordinario": "EXTRA" in tipo,
        }
    anos = [2020, 2021, 2022, 2023, 2024, 2025]
    hoje = dt.date(2026, 10, 26)
    eventos = [ev(2026, 1, mes=5)]
    payouts = {2021: 0.22, 2022: 0.22, 2023: 0.21, 2024: 0.30}
    pico = classificar_natureza(eventos, hoje, anos, payouts_por_ano=payouts, payout_12m=0.96)
    assert pico["natureza_proventos"] == "extraordinario"
    assert pico["parcela_extraordinaria"] > 0.5

    reserva = classificar_natureza(eventos, hoje, anos, payouts_por_ano=payouts, payout_12m=0.25, pago_de_reserva=True)
    assert reserva["natureza_proventos"] == "extraordinario"
    assert reserva["pago_de_reserva"] is True


def test_cury_plpl_e_pomo_pelo_payout_de_12_meses():
    """DY×P/L de 26/10 contra o payout anual do DFP 2021–2024 (antes da janela).

    PLPL3 cresceu o dividendo com o lucro e fica misto, não extraordinário.
    CURY3 e POMO4 estouram a mediana de payout.
    """
    hoje = dt.date(2026, 10, 26)
    anos = [2020, 2021, 2022, 2023, 2024, 2025]
    evento = [{
        "data_com": dt.date(2026, 5, 1), "classe": "dividendo", "tipo": "DIVIDENDO",
        "valor_bruto": 1.0, "valor_liquido": 1.0, "extraordinario": False,
    }]

    def nat(payouts, dy, pl):
        return classificar_natureza(evento, hoje, anos, payouts_por_ano=payouts, payout_12m=dy * pl)

    cury = nat(
        {2021: 0.6605371756079171, 2022: 0.4763993512890856, 2023: 0.7649725488568078, 2024: 0.3913837650017004},
        0.1537389221465786, 7.590732286306528,
    )
    plpl = nat(
        {2021: 0.0636705506122026, 2022: 0.2400002992242611, 2023: 0.11822203355749682, 2024: 0.34460654288240494},
        0.0934124962881209, 3.5262412748720062,
    )
    pomo = nat(
        {2021: 0.22438308562201498, 2022: 0.22093944967202092, 2023: 0.21462484638409918, 2024: 0.30343186232884645},
        0.22590439255005507, 4.261488628587383,
    )
    assert cury["natureza_proventos"] == "extraordinario"
    assert plpl["natureza_proventos"] == "misto"
    assert pomo["natureza_proventos"] == "extraordinario"
    assert plpl["parcela_extraordinaria"] < 0.5 < pomo["parcela_extraordinaria"]


def test_aliquota_historica_e_a_partir_de_2026():
    from b3lib.proventos import ALIQUOTA_JCP_ATE_2025, ALIQUOTA_JCP_DESDE_2026, DATA_NOVA_ALIQUOTA_JCP
    assert aliquota_jcp(dt.date(2025, 12, 31)) == ALIQUOTA_JCP_ATE_2025 == 0.15
    assert aliquota_jcp(DATA_NOVA_ALIQUOTA_JCP) == ALIQUOTA_JCP_DESDE_2026 == 0.175
    assert aliquota_jcp(None) == 0.15
