"""Cotação intradiária, agenda da coleta, universo e relações do painel ao vivo, sem rede."""
import datetime as dt
import json
import os

import pytest

from b3lib.cotacoes import BRASILIA, agenda, cotacao, montar_universo, relacoes
from b3lib.yahoo import chart_resultado

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "yahoo_chart_intradia.json")


def _resultado():
    return chart_resultado(open(FIXTURE, encoding="utf-8").read())


def test_cotacao_do_dia_contra_o_fechamento_anterior():
    c = cotacao(_resultado())
    assert c["preco"] == 52.84 and c["anterior"] == 52.2
    assert c["var"] == pytest.approx(52.84 / 52.2 - 1)
    assert c["var_rs"] == pytest.approx(0.64)
    assert c["maxima"] == 53.66 and c["minima"] == 52.36
    assert c["volume"] == 29728500
    assert c["abertura"] == pytest.approx(52.45, abs=1e-6)
    assert c["nome_yahoo"].startswith("Petróleo Brasileiro")
    # a barra sem fechamento (índice 3) fica fora da linha intradiária e do volume financeiro
    assert len(c["spark"]) == 5
    q = _resultado()["indicators"]["quote"][0]
    esperado = sum(p * v for p, v in zip(q["close"], q["volume"]) if p is not None)
    assert c["financeiro"] == pytest.approx(esperado)
    assert c["hora"].endswith("-03:00")
    inicio, fim = c["pregao"]
    assert (inicio.strftime("%H:%M"), fim.strftime("%H:%M")) == ("10:00", "17:00")


def test_cotacao_sem_meta_usa_as_barras():
    r = _resultado()
    for k in ("regularMarketPrice", "previousClose", "chartPreviousClose", "regularMarketDayHigh",
              "regularMarketDayLow", "regularMarketVolume"):
        r["meta"].pop(k)
    c = cotacao(r)
    q = r["indicators"]["quote"][0]
    assert c["preco"] == q["close"][-1]
    assert c["var"] is None and c["var_rs"] is None
    validos = [i for i, x in enumerate(q["close"]) if x is not None]
    assert c["maxima"] == max(q["high"][i] for i in validos)
    assert c["volume"] == sum(q["volume"][i] for i in validos)


def test_cotacao_sem_preco_nenhum_falha():
    with pytest.raises(ValueError):
        cotacao({"meta": {}, "timestamp": [], "indicators": {"quote": [{}]}})


def _bra(*a):
    return dt.datetime(*a, tzinfo=BRASILIA)


PREGAO = (_bra(2026, 10, 29, 10, 0), _bra(2026, 10, 29, 17, 0))  # horário vale para qualquer dia
INTERVALO = dt.timedelta(minutes=2)


def test_agenda_durante_o_pregao_segue_o_intervalo():
    agora = _bra(2026, 10, 30, 14, 3)  # sexta
    assert agenda(agora, INTERVALO, *PREGAO) == ("aberto", agora + INTERVALO)


def test_agenda_antes_da_abertura_espera_as_10h():
    assert agenda(_bra(2026, 10, 30, 8, 0), INTERVALO, *PREGAO) == ("pré-abertura", _bra(2026, 10, 30, 10, 0))


def test_agenda_continua_15_min_depois_do_fechamento_pelo_atraso_do_yahoo():
    agora = _bra(2026, 10, 30, 17, 10)
    situacao, prox = agenda(agora, INTERVALO, *PREGAO)
    assert situacao.startswith("fechado (") and prox == agora + INTERVALO


def test_agenda_depois_do_pregao_de_sexta_vai_para_segunda():
    assert agenda(_bra(2026, 10, 30, 18, 0), INTERVALO, *PREGAO) == ("fechado", _bra(2026, 11, 2, 10, 0))


def test_agenda_no_fim_de_semana_vai_para_segunda():
    assert agenda(_bra(2026, 10, 31, 12, 0), INTERVALO, *PREGAO) == ("fechado", _bra(2026, 11, 2, 10, 0))


def test_montar_universo_prioriza_o_primeiro_cadastro_e_completa_vazios():
    pos = {"ITUB4": {"nome": "ITAUUNIBANCO PN", "setor_economico": "Financeiro", "segmento": None}}
    div = {"ITUB4": {"nome": "outro nome", "segmento": "Bancos"}, "B3SA3": {"nome": "B3 ON"}}
    u = montar_universo(pos, div)
    assert [x["ticker"] for x in u] == ["ITUB4", "B3SA3"]
    assert u[0] == {"ticker": "ITUB4", "nome": "ITAUUNIBANCO PN", "setor_economico": "Financeiro",
                    "segmento": "Bancos"}


def test_relacoes_pela_raiz_do_ticker_e_curadas_presentes():
    arestas = relacoes(["SAPR4", "SAPR11", "ITSA4", "ITUB4", "VALE3", "SAPR4"],
                       [("ITSA4", "ITUB4", "controladora"), ("BBAS3", "BBSE3", "controladora")])
    pares = {(a["a"], a["b"]): a["tipo"] for a in arestas}
    assert pares == {("SAPR4", "SAPR11"): "mesma companhia (ON/PN/unit)", ("ITSA4", "ITUB4"): "controladora"}


def test_dados_do_painel_sao_json_valido_no_js(tmp_path, monkeypatch):
    import painel_ao_vivo as p
    monkeypatch.setattr(p, "PASTA_SAIDA", str(tmp_path))
    monkeypatch.setattr(p, "SAIDA_JS", str(tmp_path / "d.js"))
    monkeypatch.setattr(p, "SAIDA_HTML", str(tmp_path / "p.html"))
    dados = {"status": {"coletando": True}, "cotacoes": [{"ticker": "X", "empresa": "</script> & Cia"}]}
    p.publicar(dados, template="<script>let DADOS = /*__DADOS__*/null;\n</script>")
    js = (tmp_path / "d.js").read_text(encoding="utf-8")
    assert js.startswith("receberDados(") and js.endswith(");\n")
    assert json.loads(js[len("receberDados("):-3].replace("<\\/", "</")) == dados
    assert "</script> &" not in (tmp_path / "p.html").read_text(encoding="utf-8")
    assert p.dados_embutidos(str(tmp_path / "p.html")) == dados
