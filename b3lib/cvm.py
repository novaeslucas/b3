"""Payout a partir do DFP consolidado da CVM (dados.cvm.gov.br).

- lucro da controladora (3.11.01; se faltar, 3.11);
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


def lucro_controladora(linhas_dre):
    por_codigo = {d.get("CD_CONTA"): _reais(d) for d in linhas_dre}
    if "3.11.01" in por_codigo:
        return por_codigo["3.11.01"]
    return por_codigo.get("3.11")


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
        if da_dva is not None:
            proventos, fonte = da_dva, "dva"
        elif declarados:
            proventos, fonte = declarados, "dmpl"
        elif caixa["dividendos_pagos"]:
            proventos, fonte = caixa["dividendos_pagos"], "dfc"
        elif declarados is not None or caixa["dividendos_pagos"] is not None or da_dva == 0:
            proventos, fonte = 0.0, "dva" if da_dva == 0 else ("dmpl" if declarados is not None else "dfc")
        else:
            proventos, fonte = None, None
        payout = proventos / lucro if proventos is not None and lucro and lucro > 0 else None
        saida.append({
            "ano": ano, "lucro": lucro, "proventos": proventos, "fonte_proventos": fonte,
            "payout": payout, **caixa,
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
    prejuizo_com = sum(1 for a in com_dados if a["lucro"] <= 0 and a["proventos"] > 0)
    sem_cobertura = bool(com_dados) and soma_lucro <= 0 and soma_prov > 0
    agregado = soma_prov / soma_lucro if com_dados and soma_lucro > 0 else None
    if financeiro:
        flag = None
    else:
        comparaveis = [a["dividendos_pagos"] > a["fcf"] for a in anos_montados
                       if a["fcf"] is not None and a["dividendos_pagos"] is not None]
        flag = (sum(comparaveis) > len(comparaveis) / 2) if len(comparaveis) >= 2 else None
    return {
        "payout_agregado": agregado,
        "payout_medio": agregado,
        "anos": len(com_dados),
        "util_no_score": agregado is not None and len(com_dados) >= MIN_ANOS_SCORE,
        "sem_cobertura": sem_cobertura and len(com_dados) >= MIN_ANOS_SCORE,
        "anos_prejuizo_com_provento": prejuizo_com,
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


def ler_zips(caminhos, cds):
    """Junta DRE, DMPL, DFC e DVA consolidados, já na versão escolhida por ano."""
    cds = {norm_cd(c) for c in cds}
    baldes = {"dre": [], "dmpl": [], "dfc": [], "dva": []}
    prefixos = (
        ("dfp_cia_aberta_DRE_con_", "dre"),
        ("dfp_cia_aberta_DMPL_con_", "dmpl"),
        ("dfp_cia_aberta_DFC_MI_con_", "dfc"),
        ("dfp_cia_aberta_DVA_con_", "dva"),
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


def payout_por_cd(cds, hoje, cache_dir, anos=None):
    """{cd_cvm normalizado: resumo} para os códigos encontrados no DFP. Não marca setor financeiro."""
    anos = list(anos or anos_exercicio(hoje))
    caminhos = [baixar_dfp(ano, cache_dir) for ano in anos_arquivo_dfp(hoje, janela=len(anos))]
    bruto = ler_zips(caminhos, cds)
    saida = {}
    for cd, (dre, dmpl, dfc, dva) in bruto.items():
        if not dre and not dmpl and not dfc and not dva:
            continue
        saida[cd] = resumir(montar_anos(dre, dmpl, dfc, anos, dva), financeiro=False)
    return saida
