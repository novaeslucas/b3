"""Índices da B3 por papel e carteira do Ibovespa, sem rede (amostras reais de 30/10/2026, reduzidas)."""
import os

import pytest

from b3lib.indices import carteira, indices_por_papel, ordenar

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _ler(nome):
    return open(os.path.join(FIX, nome), encoding="utf-8").read()


def test_indices_por_papel_ordena_amplos_antes_dos_setoriais():
    por = indices_por_papel(_ler("b3_stock_index.json"))
    assert set(por) == {"ITUB4", "MXRF11", "ABCB4", "MMMC34"}
    assert por["ITUB4"][:5] == ["IBOV", "IBXX", "IBXL", "IBRA", "IDIV"]
    assert len(por["ITUB4"]) == 22 and len(set(por["ITUB4"])) == 22
    assert por["MXRF11"] == ["IFIX", "IFIL"]
    assert por["MMMC34"] == ["BDRX"]


def test_ordenar_poe_desconhecidos_no_fim_em_ordem_alfabetica_sem_repetir():
    assert ordenar(["ZZZZ", "SMLL", "AAAA", "IBOV", "SMLL"]) == ["IBOV", "SMLL", "AAAA", "ZZZZ"]


def test_carteira_do_ibovespa_em_fracao():
    peso = carteira(_ler("b3_carteira_ibov.json"))
    assert set(peso) == {"ALOS3", "ITUB4", "PETR4"}
    assert peso["ALOS3"] == pytest.approx(0.00476)
    assert 0.05 < peso["ITUB4"] < 0.15
