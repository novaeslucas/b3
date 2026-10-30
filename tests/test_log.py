"""Falhas por ticker ficam visíveis e não dependem de rede."""
from b3lib.log import falhas, limpar_falhas, registrar_falha, resumo_falhas


def test_resumo_lista_o_ticker(capsys):
    limpar_falhas()
    registrar_falha("BMGB4", "yahoo", TimeoutError("expirou"))
    registrar_falha("PETR4", "coleta", ValueError("html vazio"))
    resumo_falhas()
    saida = capsys.readouterr().out
    assert "BMGB4" in saida
    assert "PETR4" in saida
    assert "yahoo" in saida
    assert "coleta" in saida
    assert len(falhas()) == 2
    limpar_falhas()
