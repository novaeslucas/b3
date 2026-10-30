"""Números, score e compatibilidade da série bruta usada pelas leituras antigas."""
import datetime as dt
from pathlib import Path

import pytest

from b3lib.numeros import lin, media, num

RAIZ = Path(__file__).resolve().parents[1]


def test_num_brasileiro():
    assert num("1.234,5%") == pytest.approx(12.345)
    assert num("12,5%") == pytest.approx(0.125)
    assert num("1.234,5") == pytest.approx(1234.5)
    assert num("-") is None
    assert num("") is None
    assert num("12,00%") == pytest.approx(0.12)


def test_lin_satura_nos_dois_sentidos():
    assert lin(None, 1.2, 0.9) is None
    assert lin(1.2, 1.2, 0.9) == 0
    assert lin(0.9, 1.2, 0.9) == 1
    assert lin(0, 1.2, 0.9) == 1
    assert lin(2, 1.2, 0.9) == 0
    assert lin(8, 20, 5) == pytest.approx(0.8)


def test_media_ignora_ausente():
    assert media([1, None, 3]) == 2
    assert media([None, None]) is None


def test_score_no_topo_da_escala():
    import dividendos
    r = {
        "dy": 0.08, "pl": 8, "roe": 0.20, "roic": 0.15, "mrg_liq": 0.20,
        "liq_corr": 1.5, "cresc_rec_5a": 0.15, "pvp": 1, "ev_ebitda": 4,
        "m": {"payout": 0.64, "div_liq_ebit": 1.0, "anos_pagos": 6, "cortes": 0, "cagr_div": 0.10},
    }
    total, pilares = dividendos.score(r)
    assert pilares["sustentabilidade"] == 100
    assert pilares["valuation"] == 93
    assert total == 99.0


def test_payout_acima_de_120_zera_o_subindicador():
    import dividendos
    r = {
        "dy": 0.2, "pl": 8, "roe": 0.2, "roic": 0.15, "mrg_liq": 0.2,
        "liq_corr": 1.5, "cresc_rec_5a": 0.15, "pvp": 1, "ev_ebitda": 4,
        "m": {"payout": 1.5, "div_liq_ebit": 1.0, "anos_pagos": 6, "cortes": 0, "cagr_div": 0.10},
    }
    _, pilares = dividendos.score(r)
    assert pilares["sustentabilidade"] == 67


def test_metricas_sem_cvm_continua_dy_vezes_pl():
    """Leituras em leituras/ não preenchem a série líquida nem a CVM: o payout não muda."""
    import dividendos
    dividendos.HOJE = dt.date(2026, 10, 30)
    r = {
        "dy": 0.10, "pl": 8,
        "proventos_ano": {ano: 1.0 for ano in range(2020, 2026)},
        "div_liquida": 20, "ebit_12m": 10,
    }
    m = dividendos.metricas(r)
    assert m["payout"] == pytest.approx(0.8)
    assert m["payout_fonte"] == "dy_x_pl"
    assert m["anos_completos"] == [2020, 2021, 2022, 2023, 2024, 2025]
    assert m["serie_div"] == [1, 1, 1, 1, 1, 1]
    assert m["div_liq_ebit"] == pytest.approx(2)
    assert m["dividendo_acima_fcf"] is None


def test_metricas_usa_payout_cvm_quando_ha_tres_anos():
    import dividendos
    dividendos.HOJE = dt.date(2026, 10, 30)
    r = {
        "dy": 0.10, "pl": 8,
        "proventos_ano": {ano: 1.0 for ano in range(2020, 2026)},
        "div_liquida": None, "ebit_12m": None,
        "cvm": {"util_no_score": True, "payout_agregado": 0.42, "anos": 4, "dividendo_acima_fcf": True},
    }
    m = dividendos.metricas(r)
    assert m["serie_div"] == [1.0] * 6
    assert m["payout"] == pytest.approx(0.42)
    assert m["payout_fonte"] == "cvm"
    assert m["payout_dy_pl"] == pytest.approx(0.8)
    assert m["dividendo_acima_fcf"] is True


def test_lucro_nao_encontrado_volta_ao_dy_vezes_pl_sem_penalidade():
    import dividendos
    dividendos.HOJE = dt.date(2026, 10, 30)
    r = {
        "dy": 0.10, "pl": 8,
        "proventos_ano": {ano: 1.0 for ano in range(2020, 2026)},
        "div_liquida": None, "ebit_12m": None,
        "cvm": {
            "lucro_nao_encontrado": True, "util_no_score": False, "sem_cobertura": False,
            "anos_prejuizo_com_provento": 5, "payout_agregado": None, "lucro_recente": 0,
        },
    }
    m = dividendos.metricas(r)
    assert m["payout_fonte"] == "lucro_nao_encontrado"
    assert m["payout"] == pytest.approx(0.8)
    assert m["anos_prejuizo_com_provento"] == 0
    assert m["sem_cobertura"] is False


def test_lucro_que_diverge_do_fundamentus_nao_entra_no_score():
    import dividendos
    dividendos.HOJE = dt.date(2026, 10, 30)
    r = {
        "dy": 0.10, "pl": 9.35, "valor_mercado": 436_002_000_000,
        "proventos_ano": {ano: 1.0 for ano in range(2020, 2026)},
        "div_liquida": None, "ebit_12m": None,
        "cvm": {
            "util_no_score": True, "payout_agregado": 0.99, "lucro_recente": 0,
            "anos_prejuizo_com_provento": 5, "sem_cobertura": False,
        },
    }
    m = dividendos.metricas(r)
    assert m["payout_fonte"] == "lucro_diverge"
    assert m["payout"] == pytest.approx(0.10 * 9.35)
    assert m["anos_prejuizo_com_provento"] == 0


def test_sem_base_de_lucro_tira_a_nota_e_nao_soma_penalidade():
    import dividendos
    dividendos.HOJE = dt.date(2026, 10, 30)
    r = {
        "dy": 0.08, "pl": 8, "roe": 0.2, "roic": 0.15, "mrg_liq": 0.2,
        "liq_corr": 1.5, "cresc_rec_5a": 0.15, "pvp": 1, "ev_ebitda": 4,
        "proventos_ano": {ano: 1.0 for ano in range(2020, 2026)},
        "div_liquida": None, "ebit_12m": None,
        "cvm": {
            "sem_base_de_lucro": True, "sem_cobertura": True, "util_no_score": False,
            "payout_exibido": 3.0, "payout_agregado": 7.47, "anos_prejuizo_com_provento": 1,
            "lucro_recente": 214_436_000,
        },
    }
    m = dividendos.metricas(r)
    assert m["payout_fonte"] == "sem_base_de_lucro"
    assert m["payout"] is None
    assert m["sem_cobertura"] is True
    assert m["anos_prejuizo_com_provento"] == 0
    r["m"] = m
    total, pilares = dividendos.score(r)
    # payout de fora; alavancagem ausente; liquidez corrente no topo → pilar 100
    assert pilares["sustentabilidade"] == 100
    sem_payout = dict(r)
    sem_payout["m"] = dict(m)
    sem_payout["m"]["sem_cobertura"] = False
    sem_payout["m"]["payout"] = None
    sem_payout["m"]["anos_prejuizo_com_provento"] = 0
    assert dividendos.score(sem_payout)[0] == total


def test_estado_do_jev_e_o_de_26_10_mais_a_fonte_do_payout():
    """O PR do payout não mexe no julgamento: perguntas iguais, estado com um campo a mais."""
    import json
    import dividendos
    dividendos.HOJE = dt.date(2026, 10, 26)
    r = {
        "ticker": "TIMS3", "empresa": "TIM", "setor": "Telecomunicações", "subsetor": None,
        "dy": 0.08, "pl": 20, "pvp": 1, "ev_ebitda": 4,
        "roe": 0.1, "roic": 0.1, "mrg_liq": 0.1, "cresc_rec_5a": 0.0, "liq_corr": 1,
        "div_liq_patrim": 0.2, "oscil_12m": 0.0, "lucro_12m": 1, "ebit_12m": 1, "div_liquida": 1,
        "proventos_ano": {},
        "m": {
            "anos_completos": [2020, 2021, 2022, 2023, 2024, 2025], "serie_div": [1, 1, 1, 1, 1, 1],
            "payout": 0.5, "payout_fonte": "cvm", "payout_dy_pl": 1.6, "dividendo_acima_fcf": True,
            "anos_pagos": 6, "cortes": 0, "cagr_div": 0.0, "div_ultimo_vs_media": 1.0, "div_liq_ebit": 1.0,
        },
    }
    estado = dividendos.estado_jev(r)
    assert set(estado["company"]) == {
        "ticker", "name", "sector", "subsector", "dividend_yield_12m", "payout_ratio", "payout_source",
        "dividends_per_share_by_year_BRL", "dividends_per_share_current_year_so_far_BRL",
        "years_with_dividends", "dividend_cuts_over_20pct", "dividend_cagr",
        "last_year_dividend_vs_prior_average", "net_income_12m", "ebit_12m", "net_debt", "net_debt_to_ebit",
        "net_debt_to_equity", "roe", "roic", "net_margin", "revenue_growth_5y_cagr", "share_price_change_12m",
        "pe", "price_to_book", "ev_ebitda",
    }
    assert estado["company"]["payout_source"] == "cvm"
    perguntas = json.dumps(dividendos.PERGUNTAS)
    for termo in ("nature", "antecip", "payout_source", "fcf", "reserve"):
        assert termo not in perguntas


def test_cache_do_fetch_usa_o_dia_explicito(tmp_path, monkeypatch):
    import b3lib.http as http
    monkeypatch.setattr(http, "CACHE_DIR", str(tmp_path))

    class Resp:
        def read(self):
            return "ok".encode("latin-1")

    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: Resp())
    http.fetch("/resultado.php", dia=dt.date(2026, 10, 26), nome="teste_dia")
    nomes = [p.name for p in tmp_path.iterdir()]
    assert any("20261026" in n for n in nomes)
    assert not hasattr(http, "_HOJE")
    for nome in ("dividendos.py", "fiis.py", "leitura_pos_eleicao.py", "painel_pos_eleicao.py",
                 "painel_besst.py", "b3lib/http.py"):
        texto = (RAIZ / nome).read_text(encoding="utf-8")
        assert "_create_unverified_context" not in texto, nome
