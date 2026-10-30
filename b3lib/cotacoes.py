"""Cotação do dia a partir do gráfico intradiário do Yahoo, agenda da coleta e cadastro do painel ao vivo.

Sem rede: o download fica no script (``painel_ao_vivo.py``, com ``b3lib.http.baixar``, sem cache).
Horários em Brasília (UTC−3 fixo; o Brasil não tem horário de verão desde 2019).
"""
import datetime as dt

BRASILIA = dt.timezone(dt.timedelta(hours=-3))
# O Yahoo publica a B3 com até 15 min de atraso: depois do fim do pregão ainda chegam negócios.
ATRASO_YAHOO = dt.timedelta(minutes=15)


def _hora(ts):
    return dt.datetime.fromtimestamp(ts, BRASILIA) if ts else None


def cotacao(resultado):
    """Resumo do dia de um resultado de ``chart?range=1d&interval=5m`` (já passado por ``chart_resultado``).

    Variação contra o fechamento anterior (``previousClose``). Barra sem preço é ignorada.
    Volume financeiro é aproximado: soma de fechamento × volume de cada barra de 5 min.
    """
    meta = resultado.get("meta") or {}
    q = ((resultado.get("indicators") or {}).get("quote") or [{}])[0]
    barras = [(t, o, h, l, c, v) for t, o, h, l, c, v in zip(resultado.get("timestamp") or [], q.get("open") or [],
                                                            q.get("high") or [], q.get("low") or [],
                                                            q.get("close") or [], q.get("volume") or [])
              if c is not None]
    preco = meta.get("regularMarketPrice")
    if preco is None and barras:
        preco = barras[-1][4]
    if preco is None:
        raise ValueError("Yahoo sem preço")
    anterior = meta.get("previousClose") or meta.get("chartPreviousClose")
    abertura = next((o for _, o, *_ in barras if o is not None), None)
    maxima = meta.get("regularMarketDayHigh") or max((h for *_, h, _l, _c, _v in barras if h is not None), default=None)
    minima = meta.get("regularMarketDayLow") or min((l for *_, l, _c, _v in barras if l is not None), default=None)
    volume = meta.get("regularMarketVolume")
    if volume is None:
        volume = sum(v or 0 for *_, v in barras)
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    hora = _hora(meta.get("regularMarketTime"))
    return {
        "preco": preco, "anterior": anterior,
        "var": preco / anterior - 1 if anterior else None,
        "var_rs": preco - anterior if anterior else None,
        "abertura": abertura, "maxima": maxima, "minima": minima, "volume": volume,
        "financeiro": sum(c * (v or 0) for *_, c, v in barras) or None,
        "min52": meta.get("fiftyTwoWeekLow"), "max52": meta.get("fiftyTwoWeekHigh"),
        "hora": hora.isoformat() if hora else None,
        "nome_yahoo": meta.get("longName") or meta.get("shortName"),
        "spark": [round(c, 4) for *_, c, _v in barras],
        "pregao": [_hora(regular.get("start")), _hora(regular.get("end"))] if regular else None,
    }


def _proximo_dia_util(dia):
    dia += dt.timedelta(days=1)
    while dia.weekday() >= 5:
        dia += dt.timedelta(days=1)
    return dia


def agenda(agora, intervalo, inicio, fim):
    """(situação do mercado, horário da próxima coleta).

    ``inicio``/``fim`` são o pregão regular informado pelo Yahoo (só o horário é usado, aplicado ao dia de
    ``agora``). Durante o pregão e até 15 min depois do fim (atraso do Yahoo) a coleta segue a cada
    ``intervalo``; fora disso, a próxima é na abertura do próximo dia útil. Feriados não são conhecidos: num
    feriado o coletor só confirma que nada mudou e volta a esperar.
    """
    def abre(dia):
        return dt.datetime.combine(dia, inicio.timetz())

    hoje = agora.date()
    if agora.weekday() < 5:
        ini, fec = abre(hoje), dt.datetime.combine(hoje, fim.timetz())
        if agora < ini:
            return "pré-abertura", ini
        if agora <= fec:
            return "aberto", agora + intervalo
        if agora <= fec + ATRASO_YAHOO:
            return "fechado (recebendo atraso de 15 min)", agora + intervalo
    return "fechado", abre(_proximo_dia_util(hoje))


def montar_universo(*cadastros):
    """Une cadastros {ticker: {campo: valor}} na ordem de prioridade: o primeiro valor não vazio vence.

    A ordem dos tickers é a de primeira aparição.
    """
    out = {}
    for cad in cadastros:
        for ticker, campos in cad.items():
            atual = out.setdefault(ticker, {"ticker": ticker})
            for k, v in campos.items():
                if v not in (None, "") and atual.get(k) in (None, ""):
                    atual[k] = v
    return list(out.values())


def relacoes(tickers, curadas=()):
    """Arestas entre empresas relacionadas presentes no universo.

    Automática: mesma raiz de ticker (os 4 primeiros caracteres), ou seja, ON/PN/units da mesma companhia.
    ``curadas``: (a, b, tipo) escritas à mão (controladora/controlada, mesmo grupo); entram só se os dois
    tickers estiverem no universo.
    """
    presentes = list(dict.fromkeys(tickers))
    arestas = []
    por_raiz = {}
    for t in presentes:
        por_raiz.setdefault(t[:4], []).append(t)
    for grupo in por_raiz.values():
        for i, a in enumerate(grupo):
            for b in grupo[i + 1:]:
                arestas.append({"a": a, "b": b, "tipo": "mesma companhia (ON/PN/unit)"})
    conj = set(presentes)
    for a, b, tipo in curadas:
        if a in conj and b in conj and a[:4] != b[:4]:
            arestas.append({"a": a, "b": b, "tipo": tipo})
    return arestas
