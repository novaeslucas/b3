"""Leitura do JSON de gráfico do Yahoo Finance. A rede fica nos scripts; aqui só o parse."""
import datetime as dt
import json


def chart_resultado(texto):
    data = json.loads(texto)
    result = (data.get("chart") or {}).get("result")
    if not result:
        err = (data.get("chart") or {}).get("error") or {}
        raise ValueError(err.get("description") or "Yahoo sem série")
    return result[0]


def fechamentos_brasilia(resultado):
    """{data em Brasília: fechamento} das barras diárias. Barra sem close é ignorada."""
    fech = {}
    tz = dt.timezone(dt.timedelta(hours=-3))
    for ts, c in zip(resultado.get("timestamp") or [], resultado["indicators"]["quote"][0]["close"]):
        if c is not None:
            fech[dt.datetime.fromtimestamp(ts, tz).date()] = c
    return fech


def serie_painel(resultado):
    """({data: fechamento}, horário do último negócio) com a regra dos painéis.

    Barras da B3 vêm às 13h UTC do próprio dia. As de câmbio (BRL=X) vêm às 23h UTC e,
    conferidas com o fechamento do dólar comercial, correspondem ao dia em UTC; barras de
    sábado e domingo trazem o fechamento de sexta. A última barra vale para o dia de
    meta.regularMarketTime, em Brasília.
    """
    out = {}
    tss = resultado.get("timestamp") or []
    closes = resultado["indicators"]["quote"][0]["close"]
    ult = (resultado.get("meta") or {}).get("regularMarketTime")
    for k, (t, c) in enumerate(zip(tss, closes)):
        if k == len(tss) - 1 and ult:
            d = dt.datetime.fromtimestamp(ult, dt.timezone(dt.timedelta(hours=-3))).date()
        else:
            d = dt.datetime.fromtimestamp(t - 7200, dt.timezone.utc).date()
        if c is not None:
            out[d - dt.timedelta(days=max(0, d.weekday() - 4))] = c
    hora = (dt.datetime.fromtimestamp(ult, dt.timezone(dt.timedelta(hours=-3))).strftime("%H:%M")
            if ult else None)
    return out, hora
