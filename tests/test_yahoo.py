"""Parse do gráfico do Yahoo, sem rede."""
import datetime as dt
import json

from b3lib.yahoo import chart_resultado, fechamentos_brasilia, serie_painel


def _ts(ano, mes, dia, hora, tz_horas):
    tz = dt.timezone(dt.timedelta(hours=tz_horas))
    return int(dt.datetime(ano, mes, dia, hora, tzinfo=tz).timestamp())


def test_fechamento_em_brasilia():
    ts = _ts(2026, 10, 26, 16, -3)
    texto = json.dumps({"chart": {"result": [{
        "timestamp": [ts],
        "indicators": {"quote": [{"close": [10.5]}]},
        "meta": {},
    }]}})
    fech = fechamentos_brasilia(chart_resultado(texto))
    assert fech[dt.date(2026, 10, 26)] == 10.5


def test_serie_painel_enrola_fim_de_semana_e_usa_a_ultima_barra():
    # domingo 23h UTC → data UTC 1/11, que volta para a sexta 30/10
    domingo = _ts(2026, 11, 1, 23, 0)
    # última barra: o relógio da barra seria outro dia; vale regularMarketTime em Brasília
    ultima = _ts(2026, 11, 2, 1, 0)
    mercado = _ts(2026, 11, 2, 18, -3)
    resultado = {
        "timestamp": [domingo, ultima],
        "indicators": {"quote": [{"close": [5.0, 9.0]}]},
        "meta": {"regularMarketTime": mercado},
    }
    serie, hora = serie_painel(resultado)
    assert serie[dt.date(2026, 10, 30)] == 5.0
    assert serie[dt.date(2026, 11, 2)] == 9.0
    assert hora == "18:00"


def test_yahoo_sem_serie_explica_o_erro():
    try:
        chart_resultado('{"chart": {"result": null, "error": {"description": "símbolo inválido"}}}')
    except ValueError as e:
        assert "símbolo inválido" in str(e)
    else:
        raise AssertionError("era para falhar")
