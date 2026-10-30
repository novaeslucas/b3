"""Payout a partir do DFP consolidado da CVM (dados abertos, dados.cvm.gov.br).

Primeiro passo, de propósito limitado:

- exercício anual (DFP), não o ITR trimestral;
- lucro atribuído à controladora (conta 3.11.01; se faltar, 3.11);
- proventos declarados na DMPL (contas fixas de dividendos e JCP), coluna
  Patrimônio Líquido; se essa coluna vier zerada, reserva de lucros + lucros
  acumulados; se ainda zerada, o maior lançamento negativo numa coluna que não
  seja total nem participação de não controladores (caso visto em banco);
- se a DMPL não trouxer provento e a DFC tiver pagamento, usa o caixa pago;
- média aritmética dos últimos exercícios com lucro positivo (janela de 5,
  usada no score quando há pelo menos 3);
- FCF ≈ caixa operacional (6.01) − aquisições de imobilizado e intangível
  (linhas 6.02.* negativas com esse texto). Dividendos pagos acima do FCF na
  maioria dos anos marca ``paga_com_divida``. Setor financeiro fica de fora:
  o caixa operacional de banco não mede geração de caixa como o de uma empresa
  não financeira.

Adiado: ITR do ano corrente, conciliação ação a ação com o número de papéis,
ajuste de JCP que já reduziu o lucro, e leitura de notas explicativas.
"""
import csv
import io
import os
import zipfile
from collections import defaultdict

from b3lib.texto import sem_acento

URL_DFP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
JANELA = 5
MIN_ANOS_SCORE = 3
_COL_PL = "Patrimônio Líquido"
_COL_COMPONENTES = ("Reservas de Lucro", "Lucros ou Prejuízos Acumulados")
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
    """O zip ``dfp_cia_aberta_AAAA`` reúne o que foi entregue no ano AAAA (exercício anterior)."""
    exercicios = anos_exercicio(hoje, janela)
    return list(range(exercicios[0] + 1, hoje.year + 1))


def ler_csv(texto):
    return list(csv.DictReader(io.StringIO(texto), delimiter=";"))


def _ordem_ultimo(valor):
    return sem_acento(valor or "").strip().upper().startswith("ULTIMO")


def _reais(row):
    try:
        return float(row["VL_CONTA"]) * fator_escala(row.get("ESCALA_MOEDA"))
    except (TypeError, ValueError):
        return 0.0


def _conta_provento(row):
    if (row.get("ST_CONTA_FIXA") or "").upper() != "S":
        return False
    nome = sem_acento(row.get("DS_CONTA") or "").lower().strip()
    if "prescrit" in nome or "recebid" in nome:
        return False
    return nome in ("dividendos", "juros sobre capital proprio", "juros sobre o capital proprio")


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


def proventos_declarados(linhas_dmpl):
    """R$ declarados (dividendos + JCP brutos) ou None se não há DMPL."""
    if not linhas_dmpl:
        return None
    por_conta = defaultdict(list)
    for d in linhas_dmpl:
        if _conta_provento(d):
            por_conta[d.get("CD_CONTA")].append(d)
    total = 0.0
    achou = False
    for rows in por_conta.values():
        pl = next((r for r in rows if (r.get("COLUNA_DF") or "").strip() == _COL_PL), None)
        if pl is not None and _reais(pl) < 0:
            total += -_reais(pl)
            achou = True
            continue
        pref = [_reais(r) for r in rows
                if (r.get("COLUNA_DF") or "").strip() in _COL_COMPONENTES and _reais(r) < 0]
        if pref:
            total += -sum(pref)
            achou = True
            continue
        outras = [_reais(r) for r in rows
                  if (r.get("COLUNA_DF") or "").strip()
                  and (r.get("COLUNA_DF") or "").strip() not in _COL_TOTAIS
                  and (r.get("COLUNA_DF") or "").strip() not in _COL_COMPONENTES
                  and _reais(r) < 0]
        if outras:
            total += -min(outras)
            achou = True
    if achou:
        return total
    return 0.0


def _linha_capex(row):
    codigo = row.get("CD_CONTA") or ""
    if not codigo.startswith("6.02."):
        return False
    nome = sem_acento(row.get("DS_CONTA") or "").lower()
    return "imobiliz" in nome or "intang" in nome


def _linha_dividendo_pago(row):
    codigo = row.get("CD_CONTA") or ""
    if not codigo.startswith("6.03."):
        return False
    nome = sem_acento(row.get("DS_CONTA") or "").lower()
    if "recebid" in nome:
        return False
    return "divid" in nome or "juros sobre" in nome or "jcp" in nome


def fluxo(linhas_dfc):
    """cfo, capex (positivo, módulo das aquisições), fcf, dividendos_pagos. None no que faltar."""
    if not linhas_dfc:
        return {"cfo": None, "capex": None, "fcf": None, "dividendos_pagos": None}
    cfo = None
    capex = 0.0
    teve_capex = False
    pagos = 0.0
    teve_pago = False
    for d in linhas_dfc:
        if d.get("CD_CONTA") == "6.01":
            cfo = _reais(d)
        valor = _reais(d)
        if _linha_capex(d) and valor < 0:
            capex += -valor
            teve_capex = True
        if _linha_dividendo_pago(d) and valor < 0:
            pagos += -valor
            teve_pago = True
    fcf = cfo - capex if cfo is not None and teve_capex else None
    return {
        "cfo": cfo,
        "capex": capex if teve_capex else None,
        "fcf": fcf,
        "dividendos_pagos": pagos if teve_pago else (0.0 if linhas_dfc else None),
    }


def montar_anos(dre_por_ano, dmpl_por_ano, dfc_por_ano, anos):
    """Lista de exercícios na janela, com payout do ano quando o lucro é positivo."""
    saida = []
    for ano in anos:
        dre, dmpl, dfc = dre_por_ano.get(ano) or [], dmpl_por_ano.get(ano) or [], dfc_por_ano.get(ano) or []
        lucro = lucro_controladora(dre) if dre else None
        declarados = proventos_declarados(dmpl) if dmpl else None
        caixa = fluxo(dfc)
        if declarados:
            proventos, fonte = declarados, "dmpl"
        elif caixa["dividendos_pagos"]:
            proventos, fonte = caixa["dividendos_pagos"], "dfc"
        elif declarados is not None or caixa["dividendos_pagos"] is not None:
            proventos, fonte = 0.0, "dmpl" if declarados is not None else "dfc"
        else:
            proventos, fonte = None, None
        payout = proventos / lucro if proventos is not None and lucro and lucro > 0 else None
        saida.append({
            "ano": ano, "lucro": lucro, "proventos": proventos, "fonte_proventos": fonte,
            "payout": payout, **caixa,
        })
    return saida


def resumir(anos_montados, financeiro=False):
    """Média do payout e flag de dividendo acima do FCF. ``anos`` conta só exercícios com payout."""
    validos = [a["payout"] for a in anos_montados if a["payout"] is not None]
    media = sum(validos) / len(validos) if validos else None
    if financeiro:
        flag = None
    else:
        comparaveis = [a["dividendos_pagos"] > a["fcf"] for a in anos_montados
                       if a["fcf"] is not None and a["dividendos_pagos"] is not None]
        flag = (sum(comparaveis) > len(comparaveis) / 2) if len(comparaveis) >= 2 else None
    return {
        "payout_medio": media,
        "anos": len(validos),
        "util_no_score": media is not None and len(validos) >= MIN_ANOS_SCORE,
        "paga_com_divida": flag,
        "por_ano": anos_montados,
    }


def resumo_textos(dre_txt, dmpl_txt, dfc_txt, cd_cvm, anos, financeiro=False):
    """Atalho dos testes e de quem já tem o CSV em memória."""
    cd = norm_cd(cd_cvm)

    def filtra(txt):
        return [d for d in ler_csv(txt) if norm_cd(d.get("CD_CVM")) == cd]

    return resumir(montar_anos(
        selecionar_versao(filtra(dre_txt)),
        selecionar_versao(filtra(dmpl_txt)),
        selecionar_versao(filtra(dfc_txt)),
        anos,
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
    """Junta DRE, DMPL e DFC consolidados de vários zips anuais, já na versão escolhida por ano."""
    cds = {norm_cd(c) for c in cds}
    dre, dmpl, dfc = [], [], []
    for caminho in caminhos:
        with zipfile.ZipFile(caminho) as zf:
            nomes = zf.namelist()
            for nome in nomes:
                base = os.path.basename(nome)
                if base.startswith("dfp_cia_aberta_DRE_con_"):
                    dre.extend(_filtrar_membro(zf, nome, cds))
                elif base.startswith("dfp_cia_aberta_DMPL_con_"):
                    dmpl.extend(_filtrar_membro(zf, nome, cds))
                elif base.startswith("dfp_cia_aberta_DFC_MI_con_"):
                    dfc.extend(_filtrar_membro(zf, nome, cds))
    por_cd = {}
    for cd in cds:
        por_cd[cd] = (
            selecionar_versao([d for d in dre if norm_cd(d.get("CD_CVM")) == cd]),
            selecionar_versao([d for d in dmpl if norm_cd(d.get("CD_CVM")) == cd]),
            selecionar_versao([d for d in dfc if norm_cd(d.get("CD_CVM")) == cd]),
        )
    return por_cd


def baixar_dfp(ano, cache_dir):
    """Grava o zip do DFP em cache e devolve o caminho. SSL verificado (dados.cvm.gov.br)."""
    import urllib.request
    os.makedirs(cache_dir, exist_ok=True)
    destino = os.path.join(cache_dir, f"dfp_cia_aberta_{ano}.zip")
    if os.path.exists(destino) and os.path.getsize(destino) > 0:
        return destino
    url = URL_DFP.format(ano=ano)
    print(f"  CVM: baixando DFP {ano} ({url})")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        dados = resp.read()
    with open(destino, "wb") as f:
        f.write(dados)
    return destino


def payout_por_cd(cds, hoje, cache_dir, anos=None):
    """{cd_cvm normalizado: resumo} para os códigos encontrados no DFP. Não marca setor financeiro."""
    anos = list(anos or anos_exercicio(hoje))
    caminhos = [baixar_dfp(ano, cache_dir) for ano in anos_arquivo_dfp(hoje, janela=len(anos))]
    bruto = ler_zips(caminhos, cds)
    saida = {}
    for cd, (dre, dmpl, dfc) in bruto.items():
        if not dre and not dmpl and not dfc:
            continue
        saida[cd] = resumir(montar_anos(dre, dmpl, dfc, anos), financeiro=False)
    return saida
