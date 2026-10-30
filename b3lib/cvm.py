"""Payout a partir do DFP da CVM (dados.cvm.gov.br).

- lucro pelo texto da conta (não pelo código: em banco a 3.11 individual pode ser reversão de JCP);
- proventos do exercício pela DVA (folhas de dividendos e JCP em 7.08.04 ou, em banco, 7.09.04);
  a conta-pai inclui lucro retido e não entra. Sem essas folhas, DMPL com subcontas; senão, caixa da DFC;
- payout da janela = soma dos proventos ÷ soma dos lucros, prejuízo incluído;
- FCF ≈ caixa operacional − capex (imobilizado, intangível, ativo contratual) − principal de
  arrendamento no financiamento. ``dividendo_acima_fcf`` não afirma que houve dívida nova.
"""
import csv
import io
import os
import time
import urllib.error
import zipfile
from collections import defaultdict

from b3lib.texto import sem_acento

URL_DFP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
JANELA = 5
MIN_ANOS_SCORE = 3
_COL_PL = "Patrimônio Líquido"
_COL_TOTAIS = {_COL_PL, "Patrimônio Líquido Consolidado", "Participação dos Não Controladores"}


def norm_cd(cd):
    s = str(cd or "").strip()
    return str(int(s)) if s.isdigit() else s


def fator_escala(escala):
    e = sem_acento(escala or "").strip().upper()
    if e in ("MIL", "MILHAR"):
        return 1_000.0
    if e in ("MILHAO", "MILHOES"):
        return 1_000_000.0
    return 1.0  # UNIDADE e o que não reconhecemos: o valor já está em R$


def setor_financeiro(setor, subsetor=""):
    t = sem_acento(f"{setor or ''} {subsetor or ''}").lower()
    return any(k in t for k in (
        "banco", "segur", "financeiro", "financeira", "previd", "corretora", "bolsa", "intermedi",
    ))


def anos_exercicio(hoje, janela=JANELA):
    fim = hoje.year - 1
    return list(range(fim - janela + 1, fim + 1))


def anos_arquivo_dfp(hoje, janela=JANELA):
    """O zip ``dfp_cia_aberta_AAAA`` é o exercício de referência AAAA (DT_REFER), não o ano da entrega.

    Também se baixa o ano corrente: companhia com exercício já encerrado pode estar nele,
    e a CVM republica retificações.
    """
    anos = list(anos_exercicio(hoje, janela))
    if hoje.year not in anos:
        anos.append(hoje.year)
    return anos


def ler_csv(texto):
    return list(csv.DictReader(io.StringIO(texto), delimiter=";"))


def _ordem_ultimo(valor):
    return sem_acento(valor or "").strip().upper().startswith("ULTIMO")


def _reais(row):
    try:
        return float(row["VL_CONTA"]) * fator_escala(row.get("ESCALA_MOEDA"))
    except (TypeError, ValueError):
        return 0.0


def _conta_provento_dmpl(row):
    """Dividendos e JCP na DMPL, inclusive subcontas não fixas (mínimo obrigatório, adicional proposto)."""
    nome = sem_acento(row.get("DS_CONTA") or "").lower()
    if any(k in nome for k in ("prescrit", "recebid", "instrumento elegivel")):
        return False
    return "divid" in nome or "juros sobre o capital proprio" in nome or "juros sobre capital proprio" in nome or "jcp" in nome


def _folhas(codigos):
    """Tira a conta-pai quando a filha já está na lista, para não somar o mesmo lançamento duas vezes."""
    return [c for c in codigos if not any(o != c and o.startswith(c + ".") for o in codigos)]


def _valor_conta_dmpl(rows):
    """Um lançamento por conta: Patrimônio Líquido se negativo; senão a soma dos componentes negativos."""
    pl = next((r for r in rows if (r.get("COLUNA_DF") or "").strip() == _COL_PL), None)
    if pl is not None and _reais(pl) < 0:
        return -_reais(pl)
    componentes = ("Reservas de Lucro", "Lucros ou Prejuízos Acumulados", "Outros Resultados Abrangentes")
    neg = [_reais(r) for r in rows
           if (r.get("COLUNA_DF") or "").strip() in componentes and _reais(r) < 0]
    return -sum(neg) if neg else 0.0


def proventos_declarados(linhas_dmpl):
    """R$ de dividendos + JCP na DMPL, ou None se não há demonstração.

    A coluna Patrimônio Líquido do ano t costuma trazer o adicional do ano t−1 aprovado na AGO.
    Por isso a DVA do próprio exercício tem preferência; esta função é o fallback.
    """
    if not linhas_dmpl:
        return None
    por_conta = defaultdict(list)
    for d in linhas_dmpl:
        if _conta_provento_dmpl(d):
            por_conta[d.get("CD_CONTA") or ""].append(d)
    if not por_conta:
        return 0.0
    total = 0.0
    for codigo in _folhas(por_conta):
        total += _valor_conta_dmpl(por_conta[codigo])
    return total


def _conta_dva_provento(row):
    """Folha de dividendos ou JCP na remuneração de capitais próprios (7.08.04 ou 7.09.04 em banco).

    A conta-pai (7.08.04 / 7.09.04) inclui lucros retidos e daria payout de ~100%. Fica de fora.
    """
    codigo = row.get("CD_CONTA") or ""
    partes = codigo.split(".")
    if len(partes) != 4 or partes[0] != "7" or partes[2] != "04":
        return False
    if partes[1] not in ("08", "09"):
        return False
    nome = sem_acento(row.get("DS_CONTA") or "").lower().strip()
    if any(k in nome for k in ("retid", "prejuizo", "nao controlador", "não controlador")):
        return False
    return nome.startswith("dividendos") or nome.startswith("juros sobre o capital proprio") or nome.startswith("juros sobre capital proprio")


def proventos_dva(linhas_dva):
    """Dividendos + JCP atribuídos ao lucro do exercício. None se a DVA não trouxer essas folhas."""
    if not linhas_dva:
        return None
    achou = False
    total = 0.0
    for d in linhas_dva:
        if _conta_dva_provento(d):
            achou = True
            total += _reais(d)
    return total if achou else None


def selecionar_versao(linhas):
    """Por ano de DT_FIM_EXERC, fica a maior VERSAO e, no empate, a DT_REFER mais recente. Só o ÚLTIMO."""
    grupos = defaultdict(list)
    for d in linhas:
        if not _ordem_ultimo(d.get("ORDEM_EXERC")):
            continue
        fim = (d.get("DT_FIM_EXERC") or "")[:4]
        if not fim.isdigit():
            continue
        try:
            ver = int(d.get("VERSAO") or 0)
        except ValueError:
            ver = 0
        grupos[(int(fim), ver, d.get("DT_REFER") or "")].append(d)
    escolhido = {}
    for (ano, ver, ref), rows in grupos.items():
        atual = escolhido.get(ano)
        if atual is None or (ver, ref) >= (atual[0], atual[1]):
            escolhido[ano] = (ver, ref, rows)
    return {ano: rows for ano, (_, _, rows) in escolhido.items()}


def _nome_conta(row):
    return sem_acento(row.get("DS_CONTA") or "").lower().strip()


def _eh_lucro_do_periodo(nome):
    """Lucro do exercício, não reversão de JCP, não lucro por ação e não linha intermediária."""
    if any(k in nome for k in (
        "por acao", "basico por acao", "diluid", "descontinuad", "antes dos tribut",
        "antes da tribut", "antes das particip", "reversao dos juros", "reversao de juros",
    )):
        return False
    if nome.startswith("atribuido a socios da empresa controladora") or nome.startswith("atribuido aos socios da empresa controladora"):
        return True
    return nome.startswith((
        "lucro/prejuizo do periodo",
        "lucro ou prejuizo do periodo",
        "lucro/prejuizo consolidado do periodo",
        "lucro ou prejuizo consolidado do periodo",
        "lucro/prejuizo liquido do periodo",
        "lucro ou prejuizo liquido do periodo",
        "lucro/prejuizo liquido consolidado do periodo",
        "lucro ou prejuizo liquido consolidado do periodo",
    ))


def lucro_controladora(linhas_dre):
    """Lucro pelo texto da conta, não pelo código.

    Em vários bancos a 3.11 individual é «Reversão dos Juros sobre Capital Próprio» (zero)
    e o lucro está na 3.13. No consolidado o lucro costuma ser 3.09/3.09.01, não 3.11.
    Atribuído à controladora ganha quando está preenchido; senão, o lucro do período.
    """
    if not linhas_dre:
        return None
    atribuidos = []
    periodos = []
    for d in linhas_dre:
        nome = _nome_conta(d)
        if not _eh_lucro_do_periodo(nome):
            continue
        valor = _reais(d)
        if "atribuido" in nome and "controladora" in nome:
            atribuidos.append(valor)
        else:
            periodos.append(valor)
    for valor in atribuidos:
        if valor not in (0.0,):
            return valor
    for valor in periodos:
        if valor not in (0.0,):
            return valor
    if atribuidos:
        return atribuidos[0]
    if periodos:
        return periodos[0]
    return None


# Lucro implícito do Fundamentus = valor de mercado ÷ P/L. Acima disso, o lucro da CVM não entra no score.
TOLERANCIA_LUCRO_FUNDAMENTUS = 0.30
# Payout agregado acima disso: o lucro somado é pequeno demais para ser base.
TETO_PAYOUT_EXIBIDO = 3.0


def lucro_diverge_do_fundamentus(lucro_cvm, valor_mercado, pl, tolerancia=TOLERANCIA_LUCRO_FUNDAMENTUS):
    """True se o lucro da CVM e o implícito (valor de mercado ÷ P/L) discordam mais que a tolerância.

    Lucro lido como zero, com P/L e valor de mercado preenchidos, também diverge: foi o caso
    de ITUB4 e BMGB4 quando a conta 3.11 era a reversão de JCP.
    """
    if lucro_cvm is None or not valor_mercado or valor_mercado <= 0 or not pl or pl <= 0:
        return False
    implicito = valor_mercado / pl
    if implicito <= 0:
        return False
    if lucro_cvm == 0:
        return True
    base = max(abs(implicito), abs(lucro_cvm))
    return abs(lucro_cvm - implicito) / base > tolerancia


def pago_de_reserva_no_exercicio(linhas_dmpl, lucro, proventos):
    """O provento passou do lucro do ano e a DMPL debitou reserva de lucros.

    Roteamento do lucro do próprio exercício por «Reservas de Lucro» não conta: a distribuição
    ainda cabe no lucro. O que conta é o excesso, coberto por reserva de exercícios anteriores.
    """
    if not linhas_dmpl or not proventos or proventos <= 0:
        return False
    if lucro is not None and lucro > 0 and proventos <= lucro:
        return False
    excesso = proventos if lucro is None or lucro <= 0 else proventos - lucro
    por_conta = defaultdict(list)
    for d in linhas_dmpl:
        if _conta_provento_dmpl(d):
            por_conta[d.get("CD_CONTA") or ""].append(d)
    reserva = 0.0
    for codigo in _folhas(por_conta):
        for r in por_conta[codigo]:
            if (r.get("COLUNA_DF") or "").strip() != "Reservas de Lucro":
                continue
            valor = _reais(r)
            if valor < 0:
                reserva += -valor
    return reserva > 0 and reserva + 1.0 >= excesso


def _linha_capex(row):
    """Aquisição em investimento: imobilizado, intangível e ativo contratual/concessão (ex.: CPFL)."""
    codigo = row.get("CD_CONTA") or ""
    if not codigo.startswith("6.02."):
        return False
    nome = sem_acento(row.get("DS_CONTA") or "").lower()
    if "titulo" in nome or "valor mobiliario" in nome:
        return False
    return any(k in nome for k in ("imobiliz", "intang", "ativo contratual", "ativos contratuais", "concess"))


def _linha_arrendamento(row):
    """Principal de arrendamento (IFRS 16) no financiamento. Linha misturada com empréstimo não entra."""
    codigo = row.get("CD_CONTA") or ""
    if not codigo.startswith("6.03."):
        return False
    nome = sem_acento(row.get("DS_CONTA") or "").lower()
    if "arrend" not in nome and "leasing" not in nome:
        return False
    if any(k in nome for k in ("emprestimo", "debentur", "financiamento")):
        return False
    return True


def _linha_dividendo_pago(row):
    codigo = row.get("CD_CONTA") or ""
    if not codigo.startswith("6.03."):
        return False
    nome = sem_acento(row.get("DS_CONTA") or "").lower()
    if "recebid" in nome or "arrend" in nome:
        return False
    return "divid" in nome or "juros sobre" in nome or "jcp" in nome


def fluxo(linhas_dfc):
    """cfo, capex, arrendamento, fcf, dividendos_pagos. FCF = CFO − capex − arrendamento.

    Sem capex identificado o FCF fica ausente: não tratamos o caixa operacional sozinho como FCF.
    """
    if not linhas_dfc:
        return {"cfo": None, "capex": None, "arrendamento": None, "fcf": None, "dividendos_pagos": None}
    cfo = None
    capex = 0.0
    teve_capex = False
    arrend = 0.0
    teve_arrend = False
    pagos = 0.0
    teve_pago = False
    for d in linhas_dfc:
        if d.get("CD_CONTA") == "6.01":
            cfo = _reais(d)
        valor = _reais(d)
        if valor >= 0:
            continue
        if _linha_capex(d):
            capex += -valor
            teve_capex = True
        elif _linha_arrendamento(d):
            arrend += -valor
            teve_arrend = True
        elif _linha_dividendo_pago(d):
            pagos += -valor
            teve_pago = True
    fcf = cfo - capex - arrend if cfo is not None and (teve_capex or teve_arrend) else None
    return {
        "cfo": cfo,
        "capex": capex if teve_capex else None,
        "arrendamento": arrend if teve_arrend else None,
        "fcf": fcf,
        "dividendos_pagos": pagos if teve_pago else (0.0 if linhas_dfc else None),
    }


def montar_anos(dre_por_ano, dmpl_por_ano, dfc_por_ano, anos, dva_por_ano=None):
    """Um exercício da janela. Provento do ano: DVA, senão DMPL, senão caixa pago."""
    dva_por_ano = dva_por_ano or {}
    saida = []
    for ano in anos:
        dre = dre_por_ano.get(ano) or []
        dmpl = dmpl_por_ano.get(ano) or []
        dfc = dfc_por_ano.get(ano) or []
        dva = dva_por_ano.get(ano) or []
        lucro = lucro_controladora(dre) if dre else None
        da_dva = proventos_dva(dva) if dva else None
        declarados = proventos_declarados(dmpl) if dmpl else None
        caixa = fluxo(dfc)
        # DVA positiva vence (é a remuneração atribuída ao lucro do ano; na Petrobras a DMPL
        # infla com o adicional do ano anterior). DVA zerada com DMPL positiva é omissão
        # da folha (Lavvi, Yduqs): vale a DMPL.
        if da_dva:
            proventos, fonte = da_dva, "dva"
        elif declarados:
            proventos, fonte = declarados, "dmpl"
        elif caixa["dividendos_pagos"]:
            proventos, fonte = caixa["dividendos_pagos"], "dfc"
        elif da_dva == 0 or declarados == 0 or caixa["dividendos_pagos"] == 0:
            proventos, fonte = 0.0, "dva" if da_dva == 0 else ("dmpl" if declarados == 0 else "dfc")
        else:
            proventos, fonte = None, None
        payout = proventos / lucro if proventos is not None and lucro and lucro > 0 else None
        reserva = pago_de_reserva_no_exercicio(dmpl, lucro, proventos) if dmpl and proventos else False
        saida.append({
            "ano": ano, "lucro": lucro, "proventos": proventos, "fonte_proventos": fonte,
            "payout": payout, "pago_de_reserva": reserva, **caixa,
        })
    return saida


def resumir(anos_montados, financeiro=False):
    """Payout da janela = soma dos proventos ÷ soma dos lucros, prejuízo incluído.

    Ano de lucro quase zero não estoura a média. Distribuir com prejuízo acumulado
    fica sem cobertura. ``anos_prejuizo_com_provento`` alimenta a penalidade do score.
    ``dividendo_acima_fcf`` é caixa de provento acima do FCF, que pode sair de caixa
    acumulado — não é prova de que a empresa se endividou para pagar.
    """
    com_dados = [a for a in anos_montados if a["lucro"] is not None and a["proventos"] is not None]
    soma_lucro = sum(a["lucro"] for a in com_dados)
    soma_prov = sum(a["proventos"] for a in com_dados)
    n = len(anos_montados)
    sem_lucro_util = sum(1 for a in anos_montados if a["lucro"] in (None, 0, 0.0))
    # Zero ou ausência na maioria dos anos é falha de leitura, não uma sequência de prejuízo.
    lucro_nao_encontrado = n >= 3 and sem_lucro_util * 2 > n
    prejuizo_com = 0 if lucro_nao_encontrado else sum(
        1 for a in com_dados if a["lucro"] < 0 and a["proventos"] > 0)
    sem_base = (not lucro_nao_encontrado) and bool(com_dados) and (
        soma_lucro <= 0 or (soma_prov > 0 and soma_lucro < soma_prov / TETO_PAYOUT_EXIBIDO))
    agregado = soma_prov / soma_lucro if com_dados and soma_lucro > 0 and not lucro_nao_encontrado else None
    exibido = None if agregado is None else min(agregado, TETO_PAYOUT_EXIBIDO)
    if financeiro:
        flag = None
    else:
        comparaveis = [a["dividendos_pagos"] > a["fcf"] for a in anos_montados
                       if a["fcf"] is not None and a["dividendos_pagos"] is not None]
        flag = (sum(comparaveis) > len(comparaveis) / 2) if len(comparaveis) >= 2 else None
    ultimo = next((a for a in reversed(anos_montados) if a["lucro"] not in (None, 0, 0.0)), None)
    return {
        "payout_agregado": agregado,
        "payout_exibido": exibido,
        "payout_medio": exibido,
        "anos": 0 if lucro_nao_encontrado else len(com_dados),
        "util_no_score": (not lucro_nao_encontrado) and (not sem_base) and agregado is not None and len(com_dados) >= MIN_ANOS_SCORE,
        "sem_cobertura": sem_base and not lucro_nao_encontrado,
        "sem_base_de_lucro": sem_base,
        "lucro_nao_encontrado": lucro_nao_encontrado,
        "anos_prejuizo_com_provento": prejuizo_com,
        "lucro_recente": None if ultimo is None else ultimo["lucro"],
        "pago_de_reserva": False if lucro_nao_encontrado else any(a.get("pago_de_reserva") for a in anos_montados),
        "dividendo_acima_fcf": flag,
        "por_ano": anos_montados,
    }


def resumo_textos(dre_txt, dmpl_txt, dfc_txt, cd_cvm, anos, financeiro=False, dva_txt=""):
    """Atalho dos testes e de quem já tem o CSV em memória."""
    cd = norm_cd(cd_cvm)

    def filtra(txt):
        return [d for d in ler_csv(txt) if norm_cd(d.get("CD_CVM")) == cd] if txt else []

    return resumir(montar_anos(
        selecionar_versao(filtra(dre_txt)),
        selecionar_versao(filtra(dmpl_txt)),
        selecionar_versao(filtra(dfc_txt)),
        anos,
        selecionar_versao(filtra(dva_txt)),
    ), financeiro=financeiro)


def _decodificar_cvm(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _filtrar_membro(zf, nome, cds):
    texto = _decodificar_cvm(zf.read(nome))
    return [d for d in ler_csv(texto) if norm_cd(d.get("CD_CVM")) in cds]


def ler_zips(caminhos, cds, individual=False):
    """Junta DRE, DMPL, DFC e DVA já na versão escolhida por ano.

    ``individual=False`` lê o consolidado (``_con_``). ``True`` lê a controladora (``_ind_``).
    """
    cds = {norm_cd(c) for c in cds}
    tag = "ind" if individual else "con"
    baldes = {"dre": [], "dmpl": [], "dfc": [], "dva": []}
    prefixos = (
        (f"dfp_cia_aberta_DRE_{tag}_", "dre"),
        (f"dfp_cia_aberta_DMPL_{tag}_", "dmpl"),
        (f"dfp_cia_aberta_DFC_MI_{tag}_", "dfc"),
        (f"dfp_cia_aberta_DVA_{tag}_", "dva"),
    )
    for caminho in caminhos:
        with zipfile.ZipFile(caminho) as zf:
            for nome in zf.namelist():
                base = os.path.basename(nome)
                for prefixo, chave in prefixos:
                    if base.startswith(prefixo):
                        baldes[chave].extend(_filtrar_membro(zf, nome, cds))
    por_cd = {}
    for cd in cds:
        por_cd[cd] = tuple(
            selecionar_versao([d for d in baldes[chave] if norm_cd(d.get("CD_CVM")) == cd])
            for chave in ("dre", "dmpl", "dfc", "dva")
        )
    return por_cd


def fundir_anos(consolidado, individual, preferir_individual):
    """Por ano, a demonstração a usar. Banco prefere a controladora; os demais, o consolidado."""
    consolidado = consolidado or {}
    individual = individual or {}
    saida = {}
    for ano in set(consolidado) | set(individual):
        cons = consolidado.get(ano) or []
        ind = individual.get(ano) or []
        if preferir_individual and ind:
            saida[ano] = ind
        elif cons:
            saida[ano] = cons
        else:
            saida[ano] = ind
    return saida


def resumo_empresa(con, ind, anos, financeiro=False):
    """Payout da janela. Instituição financeira usa a controladora, que é quem declara o JCP."""
    con = con or ({}, {}, {}, {})
    ind = ind or ({}, {}, {}, {})
    partes = [fundir_anos(con[i], ind[i], financeiro) for i in range(4)]
    if not any(partes):
        return None
    return resumir(montar_anos(partes[0], partes[1], partes[2], anos, partes[3]), financeiro=financeiro)


IDADE_CACHE_DFP_S = 7 * 24 * 3600


def _zip_integro(caminho):
    try:
        with zipfile.ZipFile(caminho) as zf:
            return bool(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _mtime_remoto(url):
    import urllib.request
    from email.utils import parsedate_to_datetime
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            bruto = resp.headers.get("Last-Modified")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if not bruto:
        return None
    try:
        return parsedate_to_datetime(bruto).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _gravar_zip(url, destino):
    import urllib.request
    parcial = destino + ".parcial"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(parcial, "wb") as f:
        while True:
            bloco = resp.read(1 << 20)
            if not bloco:
                break
            f.write(bloco)
    if not _zip_integro(parcial):
        os.remove(parcial)
        raise zipfile.BadZipFile(url)
    os.replace(parcial, destino)


def baixar_dfp(ano, cache_dir, idade_max_s=IDADE_CACHE_DFP_S):
    """Zip do DFP em cache. Republicação da CVM: depois de ``idade_max_s``, olha o Last-Modified.

    O download vai para um arquivo temporário e só substitui o zip se ele abrir.
    Zip corrompido é apagado e baixado de novo uma vez.
    """
    os.makedirs(cache_dir, exist_ok=True)
    destino = os.path.join(cache_dir, f"dfp_cia_aberta_{ano}.zip")
    url = URL_DFP.format(ano=ano)
    if os.path.exists(destino) and not _zip_integro(destino):
        os.remove(destino)
    if os.path.exists(destino):
        idade = time.time() - os.path.getmtime(destino)
        if idade < idade_max_s:
            return destino
        remoto = _mtime_remoto(url)
        if remoto is not None and remoto <= os.path.getmtime(destino) + 1:
            return destino
        print(f"  CVM: DFP {ano} desatualizado; baixando de novo")
    else:
        print(f"  CVM: baixando DFP {ano} ({url})")
    try:
        _gravar_zip(url, destino)
    except zipfile.BadZipFile:
        print(f"  CVM: zip de {ano} inválido; tentando de novo")
        _gravar_zip(url, destino)
    return destino


def pacotes_por_cd(cds, hoje, cache_dir, anos=None):
    """(anos, consolidado, individual) para o chamador escolher a demonstração por setor."""
    anos = list(anos or anos_exercicio(hoje))
    caminhos = [baixar_dfp(ano, cache_dir) for ano in anos_arquivo_dfp(hoje, janela=len(anos))]
    return anos, ler_zips(caminhos, cds, False), ler_zips(caminhos, cds, True)


def payout_por_cd(cds, hoje, cache_dir, anos=None):
    """Resumo consolidado. Preferir ``pacotes_por_cd`` + ``resumo_empresa`` quando houver setor."""
    anos, con, ind = pacotes_por_cd(cds, hoje, cache_dir, anos)
    saida = {}
    for cd in con:
        resumo = resumo_empresa(con.get(cd), ind.get(cd), anos, financeiro=False)
        if resumo:
            saida[cd] = resumo
    return saida
