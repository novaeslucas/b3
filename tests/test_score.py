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


def test_metricas_usa_serie_liquida_e_payout_cvm_quando_ha_tres_anos():
    import dividendos
    dividendos.HOJE = dt.date(2026, 10, 30)
    r = {
        "dy": 0.10, "pl": 8,
        "proventos_ano": {ano: 1.0 for ano in range(2020, 2026)},
        "proventos_ano_liquido": {ano: 0.5 for ano in range(2020, 2026)},
        "div_liquida": None, "ebit_12m": None,
        "cvm": {"util_no_score": True, "payout_agregado": 0.42, "anos": 4, "dividendo_acima_fcf": True},
    }
    m = dividendos.metricas(r)
    assert m["serie_div"] == [0.5] * 6
    assert m["payout"] == pytest.approx(0.42)
    assert m["payout_fonte"] == "cvm"
    assert m["payout_dy_pl"] == pytest.approx(0.8)
    assert m["dividendo_acima_fcf"] is True


def test_payout_de_fallback_usa_dy_bruto():
    import dividendos
    dividendos.HOJE = dt.date(2026, 10, 30)
    r = {
        "dy": 0.085, "dy_bruto": 0.10, "pl": 8,
        "proventos_ano": {ano: 1.0 for ano in range(2020, 2026)},
        "div_liquida": None, "ebit_12m": None,
    }
    m = dividendos.metricas(r)
    assert m["payout"] == pytest.approx(0.8)
    assert m["payout_fonte"] == "dy_x_pl"


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
