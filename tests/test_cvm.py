"""Payout e FCF a partir de recortes do DFP consolidado da CVM. Sem rede."""
from pathlib import Path

import pytest

from b3lib.cvm import (
    fator_escala, fluxo, ler_csv, lucro_controladora, lucro_diverge_do_fundamentus, norm_cd,
    pago_de_reserva_no_exercicio, proventos_declarados, resumo_empresa, resumo_textos,
    resumir, selecionar_versao, setor_financeiro,
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
    assert resumo["dividendo_acima_fcf"] is None


def test_bradesco_jcp_quando_a_coluna_pl_vem_zerada():
    dre, dmpl, dfc = _textos()
    resumo = resumo_textos(dre, dmpl, dfc, "906", [2025], financeiro=True)
    ano = resumo["por_ano"][0]
    assert ano["proventos"] == pytest.approx(14_499_273 * 1000)
    assert ano["payout"] == pytest.approx(14_499_273 / 23_672_706)
    assert ano["dividendos_pagos"] == pytest.approx(11_834_032 * 1000)
    # alienação positiva não reduz o capex; as duas aquisições somam
    assert ano["capex"] == pytest.approx((5_022_225 + 6_608_243) * 1000)
    assert resumo["dividendo_acima_fcf"] is None


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
    assert resumo["payout_agregado"] == pytest.approx(1500 / 2800)
    assert resumo["anos"] == 3
    assert resumo["util_no_score"] is True
    # 2024 e 2025 pagam acima do FCF; 2023 não. Maioria -> sim.
    assert por[2023]["dividendos_pagos"] == 150
    assert por[2024]["fcf"] == pytest.approx(50)
    assert resumo["dividendo_acima_fcf"] is True


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


def test_dva_2025_bate_o_payout_publicado_e_nao_usa_a_conta_pai():
    """Recorte real do DFP 2025. Petrobras divulgou R$ 41,23 bi, 37,44% do lucro (Valor, 16/04/2026)."""
    textos = _textos()
    dva = (FIX / "dva_con.csv").read_text(encoding="utf-8")
    petr = resumo_textos(*textos, "9512", [2025], dva_txt=dva)
    ano = petr["por_ano"][0]
    assert ano["fonte_proventos"] == "dva"
    assert ano["proventos"] == pytest.approx((30_682_000 + 10_554_000) * 1000)
    assert ano["payout"] == pytest.approx(0.3744, abs=0.0001)
    # A DMPL na coluna Patrimônio Líquido daria 42,309 bi (38,4%), acima do número aprovado.
    assert ano["proventos"] < 42_309_000 * 1000

    bb = resumo_textos(*textos, "1023", [2025], dva_txt=dva)
    bb_ano = bb["por_ano"][0]
    assert bb_ano["fonte_proventos"] == "dva"
    assert bb_ano["proventos"] == pytest.approx(5_219_106 * 1000)
    assert bb_ano["payout"] == pytest.approx(5_219_106 / 13_698_124)

    taesa = resumo_textos(*textos, "20257", [2025], dva_txt=dva)
    taesa_ano = taesa["por_ano"][0]
    assert taesa_ano["fonte_proventos"] == "dva"
    assert taesa_ano["proventos"] == pytest.approx((552_889 + 571_182) * 1000)
    assert taesa_ano["payout"] == pytest.approx((552_889 + 571_182) / 1_579_863)


def test_soma_da_janela_inclui_ano_de_prejuizo():
    anos = [
        {"ano": 2023, "lucro": 100.0, "proventos": 40.0, "payout": 0.4, "fcf": 80.0, "dividendos_pagos": 10.0},
        {"ano": 2024, "lucro": -50.0, "proventos": 30.0, "payout": None, "fcf": 10.0, "dividendos_pagos": 30.0},
        {"ano": 2025, "lucro": 100.0, "proventos": 40.0, "payout": 0.4, "fcf": 80.0, "dividendos_pagos": 10.0},
    ]
    r = resumir(anos)
    assert r["payout_agregado"] == pytest.approx(110 / 150)
    assert r["anos_prejuizo_com_provento"] == 1
    assert r["util_no_score"] is True
    assert r["sem_cobertura"] is False


def test_capex_de_concessao_e_arrendamento_sem_misturar_divida():
    linhas = [
        {"CD_CONTA": "6.01", "DS_CONTA": "Caixa Líquido Atividades Operacionais", "VL_CONTA": "1000", "ESCALA_MOEDA": "UNIDADE"},
        {"CD_CONTA": "6.02.03", "DS_CONTA": "Adições de ativo contratual", "VL_CONTA": "-400", "ESCALA_MOEDA": "UNIDADE"},
        {"CD_CONTA": "6.02.02", "DS_CONTA": "Aquisições de Imobilizado", "VL_CONTA": "-100", "ESCALA_MOEDA": "UNIDADE"},
        {"CD_CONTA": "6.03.03", "DS_CONTA": "Pagamentos de arrendamentos", "VL_CONTA": "-50", "ESCALA_MOEDA": "UNIDADE"},
        {"CD_CONTA": "6.03.04", "DS_CONTA": "Amortização de empréstimos e arrendamentos", "VL_CONTA": "-900", "ESCALA_MOEDA": "UNIDADE"},
        {"CD_CONTA": "6.03.08", "DS_CONTA": "Dividendos pagos", "VL_CONTA": "-80", "ESCALA_MOEDA": "UNIDADE"},
    ]
    caixa = fluxo(linhas)
    assert caixa["capex"] == 500
    assert caixa["arrendamento"] == 50
    assert caixa["fcf"] == 450
    assert caixa["dividendos_pagos"] == 80


def _por_ano(txt, cd):
    linhas = [d for d in ler_csv(txt) if norm_cd(d.get("CD_CVM")) == norm_cd(cd)]
    return selecionar_versao(linhas)


def test_abc_2022_nao_trata_lucro_como_zero():
    """3.11.01 veio 0 no consolidado; o lucro está em 3.11 (789.718 mil)."""
    r = resumo_textos(*_textos(), "20958", [2022])
    assert r["por_ano"][0]["lucro"] == pytest.approx(789_718 * 1000)
    assert r["anos_prejuizo_com_provento"] == 0


def test_lavvi_e_yduq_2025_nao_ficam_com_payout_zero():
    """A DVA lista dividendos 0, mas a DMPL tem distribuição de verdade."""
    textos = _textos()
    dva = (FIX / "dva_con.csv").read_text(encoding="utf-8")
    lavvi = resumo_textos(*textos, "25062", [2025], dva_txt=dva)
    ano = lavvi["por_ano"][0]
    assert ano["fonte_proventos"] == "dmpl"
    assert ano["proventos"] == pytest.approx(403_531 * 1000)
    assert ano["payout"] == pytest.approx(403_531 / 414_072)
    yduq = resumo_textos(*textos, "21016", [2025], dva_txt=dva)
    y = yduq["por_ano"][0]
    assert y["fonte_proventos"] == "dmpl"
    assert y["proventos"] == pytest.approx((68_923 + 106_896) * 1000)
    assert y["payout"] == pytest.approx((68_923 + 106_896) / 181_491)


def test_itau_2025_lucro_esta_na_3_13_e_nao_na_reversao():
    """DFP individual: 3.11 é reversão de JCP (zero); o lucro do período é a 3.13, R$ 45,659 bi."""
    txt = (FIX / "itau_2025_ind_dre.csv").read_text(encoding="utf-8")
    linhas = selecionar_versao(ler_csv(txt))[2025]
    assert lucro_controladora(linhas) == pytest.approx(45_659_000 * 1000)


def test_ir_diferido_nao_e_lucro_do_periodo():
    """No individual do BTG a 3.09 é IR diferido; o lucro é a 3.13."""
    linhas = [
        {"CD_CONTA": "3.09", "DS_CONTA": "IR Diferido", "VL_CONTA": "1289820", "ESCALA_MOEDA": "MIL"},
        {"CD_CONTA": "3.11", "DS_CONTA": "Reversão dos Juros sobre Capital Próprio", "VL_CONTA": "0", "ESCALA_MOEDA": "MIL"},
        {"CD_CONTA": "3.13", "DS_CONTA": "Lucro/Prejuízo do Período", "VL_CONTA": "15947050", "ESCALA_MOEDA": "MIL"},
    ]
    assert lucro_controladora(linhas) == pytest.approx(15_947_050 * 1000)


def test_lucro_zero_na_maioria_dos_anos_e_falha_de_leitura():
    anos = [
        {"ano": y, "lucro": 0.0, "proventos": 10.0, "payout": None, "fcf": None, "dividendos_pagos": None}
        for y in range(2021, 2026)
    ]
    r = resumir(anos)
    assert r["lucro_nao_encontrado"] is True
    assert r["anos_prejuizo_com_provento"] == 0
    assert r["util_no_score"] is False
    assert r["payout_agregado"] is None
    assert r["pago_de_reserva"] is False


def test_lucro_somado_pequeno_demais_nao_entra_no_score():
    anos = [
        {"ano": y, "lucro": 1.0, "proventos": 100.0, "payout": None, "fcf": 1.0, "dividendos_pagos": 1.0}
        for y in (2023, 2024, 2025)
    ]
    r = resumir(anos)
    assert r["sem_base_de_lucro"] is True
    assert r["payout_agregado"] == pytest.approx(100)
    assert r["payout_exibido"] == pytest.approx(3)
    assert r["util_no_score"] is False
    assert r["anos_prejuizo_com_provento"] == 0


def test_divergencia_so_pega_lucro_zerado_ou_com_sinal_trocado():
    """ITUB4 lido como zero (a 3.11 era a reversão de JCP) diverge; tamanho diferente não."""
    assert lucro_diverge_do_fundamentus(0, 46_600_000_000) is True
    assert lucro_diverge_do_fundamentus(45_659_000_000, 46_600_000_000) is False
    assert lucro_diverge_do_fundamentus(-100, 50) is True
    assert lucro_diverge_do_fundamentus(100, -50) is True
    # KLBN11 em 26/10: CVM 2025 R$ 1,68 bi contra 12 meses de R$ 0,56 bi. Períodos diferentes, mesmo sinal.
    assert lucro_diverge_do_fundamentus(1_678_211_000, 561_000_000) is False
    # Sem o lucro do Fundamentus não há com o que comparar.
    assert lucro_diverge_do_fundamentus(0, None) is False
    assert lucro_diverge_do_fundamentus(None, 100) is False


def test_provento_acima_do_lucro_debitado_em_reserva():
    dmpl = [
        {"CD_CONTA": "5.04.10", "DS_CONTA": "Dividendos adicionais", "COLUNA_DF": "Reservas de Lucro",
         "VL_CONTA": "-40", "ESCALA_MOEDA": "UNIDADE"},
        {"CD_CONTA": "5.04.10", "DS_CONTA": "Dividendos adicionais", "COLUNA_DF": "Patrimônio Líquido",
         "VL_CONTA": "-40", "ESCALA_MOEDA": "UNIDADE"},
    ]
    assert pago_de_reserva_no_exercicio(dmpl, 100, 140) is True
    assert pago_de_reserva_no_exercicio(dmpl, 200, 140) is False


def test_bb_2025_payout_sai_da_controladora():
    """O RI fala em 30% sobre o lucro de R$ 17,8 bi. O consolidado atribuído (R$ 13,7 bi) dava 38%."""
    dre = (FIX / "dre_con.csv").read_text(encoding="utf-8")
    dva = (FIX / "dva_con.csv").read_text(encoding="utf-8")
    ind_dre = (FIX / "bb_2025_ind_dre.csv").read_text(encoding="utf-8")
    ind_dva = (FIX / "bb_2025_ind_dva.csv").read_text(encoding="utf-8")
    con = (_por_ano(dre, "1023"), {}, {}, _por_ano(dva, "1023"))
    ind = (_por_ano(ind_dre, "1023"), {}, {}, _por_ano(ind_dva, "1023"))
    r = resumo_empresa(con, ind, [2025], financeiro=True)
    ano = r["por_ano"][0]
    assert ano["lucro"] == pytest.approx(18_151_787 * 1000)
    assert ano["proventos"] == pytest.approx(5_183_839 * 1000)
    assert ano["fonte_proventos"] == "dva"
    assert ano["payout"] == pytest.approx(5_183_839 / 18_151_787)
    consolidado = resumo_empresa(con, ({}, {}, {}, {}), [2025], financeiro=False)
    assert consolidado["por_ano"][0]["payout"] == pytest.approx(5_219_106 / 13_698_124)
