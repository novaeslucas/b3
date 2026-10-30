"""Payout e FCF a partir de recortes do DFP consolidado da CVM. Sem rede."""
from pathlib import Path

import pytest

from b3lib.cvm import (
    fator_escala, fluxo, norm_cd, proventos_declarados, resumo_textos, selecionar_versao, setor_financeiro,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def _textos():
    return (
        (FIX / "dre_con.csv").read_text(encoding="utf-8"),
        (FIX / "dmpl_con.csv").read_text(encoding="utf-8"),
        (FIX / "dfc_mi_con.csv").read_text(encoding="utf-8"),
    )


def test_norm_cd_e_escala():
    assert norm_cd("009512") == norm_cd("9512") == "9512"
    assert fator_escala("MIL") == 1000
    assert fator_escala("UNIDADE") == 1


def test_setor_financeiro_nao_pega_energia():
    assert setor_financeiro("Intermediação Financeira", "Bancos")
    assert setor_financeiro("Seguradoras")
    assert not setor_financeiro("Energia Elétrica", "Energia Elétrica")


def test_petrobras_2025_nao_dobra_coluna_de_total():
    """Números do DFP consolidado entregue em 2025 (conta em milhares de reais)."""
    dre, dmpl, dfc = _textos()
    resumo = resumo_textos(dre, dmpl, dfc, "9512", [2025])
    ano = resumo["por_ano"][0]
    assert ano["lucro"] == pytest.approx(110_129_000 * 1000)
    assert ano["proventos"] == pytest.approx(42_309_000 * 1000)
    assert ano["fonte_proventos"] == "dmpl"
    assert ano["payout"] == pytest.approx(42_309_000 / 110_129_000)
    assert ano["capex"] == pytest.approx(108_714_000 * 1000)
    assert ano["dividendos_pagos"] == pytest.approx((45_205_000 + 233_000) * 1000)
    assert ano["fcf"] == pytest.approx((200_333_000 - 108_714_000) * 1000)
    assert ano["fcf"] > ano["dividendos_pagos"]
    assert resumo["util_no_score"] is False  # um exercício só não entra no score
    assert resumo["paga_com_divida"] is None


def test_bradesco_jcp_quando_a_coluna_pl_vem_zerada():
    dre, dmpl, dfc = _textos()
    resumo = resumo_textos(dre, dmpl, dfc, "906", [2025], financeiro=True)
    ano = resumo["por_ano"][0]
    assert ano["proventos"] == pytest.approx(14_499_273 * 1000)
    assert ano["payout"] == pytest.approx(14_499_273 / 23_672_706)
    assert ano["dividendos_pagos"] == pytest.approx(11_834_032 * 1000)
    # alienação positiva não reduz o capex; as duas aquisições somam
    assert ano["capex"] == pytest.approx((5_022_225 + 6_608_243) * 1000)
    assert resumo["paga_com_divida"] is None


def test_media_de_tres_anos_versao_mais_nova_e_flag_de_divida():
    dre, dmpl, dfc = _textos()
    resumo = resumo_textos(dre, dmpl, dfc, "1", [2023, 2024, 2025])
    por = {a["ano"]: a for a in resumo["por_ano"]}
    assert por[2023]["lucro"] == 800  # 3.11.01, não o consolidado 1000 nem o penúltimo
    assert por[2023]["proventos"] == 400  # coluna Patrimônio Líquido, não a soma com as reservas
    assert por[2025]["lucro"] == 1000  # versão 2, não a versão 1 (lucro 50)
    assert por[2025]["proventos"] == 600
    assert por[2023]["payout"] == pytest.approx(0.5)
    assert por[2024]["payout"] == pytest.approx(0.5)
    assert por[2025]["payout"] == pytest.approx(0.6)
    assert resumo["payout_medio"] == pytest.approx((0.5 + 0.5 + 0.6) / 3)
    assert resumo["anos"] == 3
    assert resumo["util_no_score"] is True
    # 2024 e 2025 pagam acima do FCF; 2023 não. Maioria -> sim.
    assert por[2023]["dividendos_pagos"] == 150
    assert por[2024]["fcf"] == pytest.approx(50)
    assert resumo["paga_com_divida"] is True


def test_funcoes_puras_aceitam_linhas_soltas():
    linhas = selecionar_versao([
        {"ORDEM_EXERC": "PENÚLTIMO", "DT_FIM_EXERC": "2024-12-31", "VERSAO": "9", "DT_REFER": "2025-01-01",
         "CD_CONTA": "3.11.01", "VL_CONTA": "1", "ESCALA_MOEDA": "UNIDADE"},
        {"ORDEM_EXERC": "ÚLTIMO", "DT_FIM_EXERC": "2025-12-31", "VERSAO": "1", "DT_REFER": "2026-03-01",
         "CD_CONTA": "6.01", "DS_CONTA": "Caixa Líquido Atividades Operacionais", "VL_CONTA": "10",
         "ESCALA_MOEDA": "UNIDADE"},
    ])
    assert 2024 not in linhas
    caixa = fluxo(linhas[2025])
    assert caixa["cfo"] == 10
    assert caixa["fcf"] is None  # sem linha de capex, não inventamos zero
    assert proventos_declarados([]) is None
