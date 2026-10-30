# Ajustes de robustez e de metodologia

Os arquivos em `resultados/` **não foram regenerados**. O CSV e os dashboards publicados continuam
sendo a análise anterior (DY e payout brutos, payout = DY × P/L). A próxima execução de
`python dividendos.py` passa a gravar por cima desses arquivos com as regras abaixo. As pastas
`leituras/` não foram editadas: são o registro de cada pregão.

A seção [Revisão da metodologia](#revisão-da-metodologia) corrige o payout, o filtro de DY,
a agregação e o JCP. Onde os dois textos divergirem, vale a revisão.

## 1. Verificação SSL

**Por quê.** `dividendos.py` usava um contexto que não verifica certificado em todo `fetch` com URL
explícita. Isso incluía o Yahoo Finance, não só a B3.

**O que foi feito.** A verificação ficou ligada para todos os hosts. Em 30/10/2026 o endpoint da B3
(`sistemaswebb3-listados.b3.com.br`), o Yahoo, o Fundamentus e a CVM responderam com certificado
válido no trust store padrão do Python 3.12, então não há exceção. Se a B3 voltar a falhar, o erro
entra no resumo de falhas em vez de desligar a verificação para o mercado inteiro.

A cópia congelada `leituras/2026_10_06/dividendos.py` ainda desliga a verificação. Ela não é o
script da raiz; as leituras diárias importam o `dividendos.py` atual.

Não muda score por si. Muda a chance de a coleta falhar de forma visível se o certificado for ruim.

## 2. Falhas que sumiam

**Por quê.** Vários `except Exception` devolviam `None` ou `{}` sem dizer qual ticker e por quê
(Yahoo em `dividendos.py`, `leitura_pos_eleicao.py` e nos dois painéis; coleta por papel). Uma
falha no meio do pool também derrubava a execução inteira.

**O que foi feito.** `b3lib/log.py` registra um aviso e, no fim, imprime um resumo por etapa
(coleta, Yahoo, conferência com a B3, cadastro CVM, download do DFP). O papel que falha na coleta
sai daquela execução; os outros seguem. A conferência com a B3 continua marcada como `erro B3: ...`
na linha do ativo.

Não muda o score de quem foi coletado. Pode mudar a lista, se um ticker que antes interrompia o
script agora é só omitido.

## 3. Payout: DFP da CVM, em vez de só DY × P/L

**Por quê.** DY × P/L mistura o provento dos últimos 12 meses com o lucro contábil do mesmo
período e com o preço. Um lucro não recorrente ou um ano atípico distorce a cobertura, e o número
não é a média que um investidor de dividendo olha (3 a 5 anos).

**O que foi feito.** Primeiro passo, em `b3lib/cvm.py`, com os ZIP anuais de
`https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/` (SSL verificado, cache em `cache/cvm/`):

- lucro da controladora, conta `3.11.01` (se faltar, `3.11`), demonstração consolidada, exercício
  `ÚLTIMO`, maior `VERSAO`;
- proventos declarados na DMPL: contas fixas de dividendos e de juros sobre capital próprio, coluna
  Patrimônio Líquido. Se essa coluna vier zerada, usam-se reserva de lucros e lucros acumulados.
  Se ainda zerada, fica o maior lançamento negativo numa coluna que não seja total nem participação
  de não controladores (é o caso do Bradesco no DFP de 2025, em que o JCP não aparece em
  Patrimônio Líquido);
- se a DMPL não trouxer provento e a DFC tiver pagamento, entra o caixa pago;
- média aritmética dos últimos 5 exercícios completos com lucro positivo. O score só usa essa
  média quando há pelo menos 3 anos (`payout_fonte = cvm`). Senão continua DY × P/L
  (`payout_fonte = dy_x_pl`);
- fluxo de caixa livre aproximado = caixa operacional (`6.01`) menos aquisições de imobilizado e
  intangível (linhas `6.02.*` negativas com esse texto). Se os dividendos pagos superam esse FCF
  na maioria dos anos com os dois números, `paga_com_divida = sim`. Banco, seguradora e o restante
  do setor financeiro ficam sem essa marca: o caixa operacional deles não mede geração de caixa
  como o de uma empresa industrial.

O código CVM vem do cadastro da B3 (`GetInitialCompanies`). `--sem-cvm` pula o download.
`--sem-b3` só desliga a conferência de proventos, não o DFP.

**Adiado.**

- ITR do ano corrente (o DFP não cobre o exercício que ainda não fechou).
- Nota explicativa para tirar lucro e provento não recorrentes da média.
- Conciliação dos reais da CVM com o provento por ação do Fundamentus (o payout da CVM é da
  companhia, não por papel).
- DMPL de banco cuja distribuição esteja partida em colunas fora do fallback acima.
- FCFE/FCFF completo (variação de capital de giro já está dentro do `6.01`; não há ajuste de
  arrendamento nem de juro da dívida).

**Efeito no score.** O pilar de sustentabilidade (25%) usa esse payout. Quem tinha DY × P/L alto
por um lucro fraco de 12 meses, mas uma média de 5 anos folgada, sobe. O contrário também: média
alta de distribuição sobre lucro derruba o pilar, mesmo com DY × P/L bonito num único ano. Com
menos de 3 exercícios úteis, o número nem entra e o score segue no fallback. Os dashboards e o CSV
em `resultados/` ainda mostram a conta antiga até a próxima execução.

## 4. JCP líquido no DY e na série do score

**Por quê.** O Fundamentus e a B3 publicam o JCP bruto. Quem recebe o crédito sofre IRRF. Um banco
que paga quase tudo como JCP (BMGB4 é o exemplo típico) aparecia com DY e histórico inflados em
relação ao que cai na conta.

**O que foi feito.** Na série por ação e no DY de 12 meses usados no score, o JCP entra líquido:

- 15% até o pagamento em 31/12/2025;
- 17,5% no JCP pago ou creditado a partir de 01/01/2026 (Lei Complementar 224/2025).

Sem data de pagamento, vale a data-com. O filtro inicial (DY ≥ 5%) continua no DY bruto do
Fundamentus, porque o detalhe do provento só é lido depois do corte. `dy_bruto` fica no CSV e no
painel. A conferência com a B3 continua nos dois brutos, para não acusar divergência só por causa
do imposto.

A retenção de 10% sobre dividendos acima de R$ 50 mil no mês (Lei 15.270/2025) depende da pessoa
e do conjunto de pagadoras. Fica de fora.

O payout da CVM **não** desconta esse IR: o JCP bruto é o que a companhia declarou, e o lucro já
é depois da despesa de JCP. Descontar de novo o IR do acionista faria a cobertura parecer mais
folgada do que é.

**Efeito no score.** O DY exibido e os pilares de histórico e crescimento (anos pagos, cortes,
CAGR) passam a usar a série líquida. Se a mistura dividendo/JCP for estável, o CAGR quase não
muda e o nível cai na proporção do JCP (cerca de 15% do trecho que é JCP). Uma troca de
dividendo para JCP, ou o contrário, pode criar ou esconder um corte de 20%. No fallback
DY × P/L, o payout também cai para quem vive de JCP, e o pilar de sustentabilidade fica mais
duro. BMGB4 e outros bancos pagadores de JCP tendem a perder DY e, quando não houver média da
CVM, a perder payout. Com a média da CVM, o payout do score deixa de depender desse desconto.

## 5. Provento recorrente ou extraordinário, e o Jev

**Por quê.** O estudo pedia ao Jev se o DY parecia inflado, mas não dizia, no dado de cada ativo,
se o próprio provento era recorrente ou extraordinário.

**O que foi feito.** Para o último ano completo, em código:

- evento de dividendo cujo tipo contém “extraordinário”, “especial” ou “não recorrente”;
- excesso do ano sobre a mediana dos anos anteriores com pagamento, quando há pelo menos três e
  o ano passa de 1,8× essa mediana.

A parcela é o maior dos dois (para não contar o mesmo pico duas vezes). Acima de 50% do ano, a
natureza é `extraordinario`; entre 0 e 50%, `misto`; o resto, `recorrente`. JCP não vira
extraordinário só por ser JCP. A série do score **não** tira essa parcela: o histórico de cortes
já pune irregularidade, e cortar o pico por regra fixa erraria o dividendo alto e recorrente de
commodities. A marca vai para o CSV, para o detalhe do dashboard e para o estado do Jev
(`dividend_nature` e `extraordinary_share_last_complete_year`), e o texto da pergunta manda o
modelo usar isso.

O cache do Jev é o SHA-1 do estado mais as perguntas. Como os dois mudaram, as chaves antigas de
`cache/jev.json` deixam de servir para a classificação de dividendos. O arquivo não foi apagado.
A próxima execução sem `--sem-jev` chama a API de novo e pode mudar classe, confiança e
P(extraordinário) — e, com isso, a decisão `estudar` / `monitorar` / `descartar`. As chaves da
eleição e do 2º turno usam ticker, nome e setor, não o estado inteiro, e continuam válidas.
`--sem-jev` e `--precos-yahoo` seguem iguais.

## 6. Módulo compartilhado

**Por quê.** `fetch`, `lin`, `media` e o parse de série do Yahoo estavam repetidos, e
`setores_b3.py` era copiado em cada pasta de `leituras/`.

**O que foi feito.** `b3lib/` guarda HTTP, números, log, parse do Fundamentus, proventos, Yahoo,
DFP e a classificação setorial. O `setores_b3.py` da raiz só chama esse módulo. Os scripts da
raiz (`dividendos.py`, `fiis.py`, `leitura_pos_eleicao.py`, os dois painéis) usam o módulo.
`fetch`, `lin`, `media`, `num` e `HOJE` continuam exportados por `dividendos.py`, que é o que as
leituras diárias importam.

As cópias em `leituras/` não foram reescritas. Uma leitura antiga, se for executada de novo,
ainda chama o `dividendos.py` da raiz: sem a série líquida e sem o bloco `cvm` no dicionário, o
payout dela continua DY × P/L sobre a série bruta. O comportamento numérico dessas releituras
não muda por causa do JCP nem da CVM.

## 7. Testes

**Por quê.** Não havia teste. Qualquer ajuste de payout ou de HTML quebrava em silêncio.

**O que foi feito.** `pytest` em `requirements-dev.txt` (não entra na execução do estudo).
`python -m pytest` cobre número brasileiro, score, série bruta de compatibilidade, HTML de
`resultado.php` e de `proventos.php`, alíquota de JCP, natureza do provento, DFP (Petrobras e
Bradesco de 2025, média de três anos, versão mais nova, flag de dívida, setor financeiro) e o
parse de data do Yahoo. Tudo com fixture local, sem rede.

## O que pode mudar na classificação, em resumo

| Peça | Direção provável |
|---|---|
| DY e CAGR de quem paga muito JCP | DY menor; CAGR estável se a mistura não mudar |
| Payout no score | Passa à média de 5 anos da CVM quando há 3 exercícios; senão DY líquido × P/L, mais baixo no JCP |
| Sustentabilidade | Sobe ou desce com essa média, não mais com um único P/L |
| Histórico e crescimento | Série líquida; um corte novo aparece se o líquido cair mais de 20% num ano |
| Decisão com Jev | Pode mudar na próxima chamada, porque o estado e a pergunta são outros |
| Universo do filtro (DY ≥ 5%) | Continua no DY bruto; um papel pode entrar e, líquido, ficar abaixo de 5% |
| `resultados/` já publicados | Iguais até alguém rodar de novo |

## Revisão da metodologia

Segunda passagem, no mesmo pull request. Os números publicados em `resultados/` continuam intocados.
A comparação de ranking foi para `comparacoes/`, não para cima do CSV antigo.

### 1. Payout de fallback voltou a ser bruto

**Por quê.** O payout é conta da companhia. O fallback tinha passado a usar o DY líquido × P/L,
enquanto a CVM entra com dividendo e JCP brutos. As duas fontes não conversavam.

**O que foi feito.** `metricas` usa `dy_bruto` (o DY do Fundamentus, antes do IR do JCP) × P/L.
Sem `dy_bruto` — caso das leituras antigas, que não preenchem o campo — segue o `dy` que elas
já tinham, também bruto. O DY líquido continua só no rendimento do acionista e na série por ação.

### 2. Filtro e score não usam o mesmo DY, e isso ficou explícito

**Por quê.** `--dy-min 5` corta pelo DY bruto, e a tabela mostrava o líquido sem dizer qual era qual.

**O que foi feito.** O significado de `--dy-min` não mudou: continua o DY bruto do Fundamentus,
o mesmo de antes desta revisão. A ajuda da flag, o console (`DYliq` e `DYbr`) e o detalhe do
dashboard mostram os dois. Um papel pode passar no filtro e aparecer com DY líquido abaixo de 5%.

### 3. Payout da CVM: DVA do exercício, não a média da DMPL fixa

**Por quê.** A DMPL só com as contas fixas «Dividendos» e «Juros sobre Capital Próprio» perde
subcontas («dividendos intercalares», «mínimos obrigatórios», «adicional proposto»). E o movimento
do ano t na coluna Patrimônio Líquido mistura o adicional do ano t−1 aprovado na AGO.

**O que foi feito.** A fonte preferida é a Demonstração do Valor Adicionado, folhas de dividendos
e de JCP na remuneração de capitais próprios: `7.08.04.01` + `7.08.04.02` nas empresas não
financeiras e `7.09.04.01` + `7.09.04.02` nos bancos (o plano de contas da DVA de banco numera
a distribuição em 7.09). A conta-pai (`7.08.04` / `7.09.04`) inclui lucros retidos e daria payout
perto de 100%; fica de fora. Se essas folhas não existem, cai para a DMPL com as subcontas,
sem somar pai e filha, e sem a coluna de não controladores. O caixa da DFC continua como último recurso.

**Validação no DFP de 2025** (valores em R$ bilhões; lucro é o da controladora, conta 3.11.01):

| Papel | Lucro | DVA (JCP + dividendos) | Payout DVA | DMPL que a versão anterior usava | Payout divulgado |
|---|---:|---:|---:|---:|---|
| PETR4 | 110,129 | 41,236 (30,682 + 10,554) | 37,44% | 42,309 na coluna Patrimônio Líquido (38,4%) | R$ 41,23 bi, 37,44% do lucro, aprovados na AGO e noticiados pelo Valor em 16/04/2026 |
| TAEE11 | 1,580 | 1,124 (0,553 + 0,571) | 71,1% | só o JCP fixo, 0,553 (35%); intercalares, mínimo e adicional ficavam de fora | a DVA é a que fecha com a soma dessas linhas da DMPL quando elas entram |
| BBAS3 | 13,698 | 5,219 de JCP e 0 de dividendos | 38,1% | a conta fixa não caía em Patrimônio Líquido; o mesmo 5,219 está espalhado em reserva e outros resultados | a DVA do banco está em 7.09.04, não em 7.08.04 |

A DVA de 2025 da Petrobras é o número que a companhia aprovou. A DMPL pela coluna de patrimônio
fica R$ 1,073 bi acima, o movimento de reserva que não é remuneração atribuída ao lucro do ano.
Ficou a DVA. O teste `test_dva_2025_bate_o_payout_publicado_e_nao_usa_a_conta_pai` trava esses
recortes.

Janela de cinco anos (soma/soma, ver o item 4): PETR4 75,0%, BBAS3 47,2%, TAEE11 56,5%.
O 75% da Petrobras não contradiz os 37% de 2025: 2021, 2022 e 2024 distribuíram quase o lucro
inteiro.

**Limite.** Se a DVA lista dividendos e JCP zerados em todos os anos (YDUQ3 no universo de 26/10),
o payout contábil vai a zero mesmo com DY de mercado alto. Isso é o que o demonstrativo diz —
não houve remuneração atribuída ao lucro do exercício — e o DY líquido continua visível à parte.

### 4. Soma sobre soma, com ano de prejuízo

**Por quê.** A média dos payouts anuais joga fora o ano de prejuízo e explode quando o lucro
é quase zero. Pagar no prejuízo parecia saudável, porque o ano nem entrava.

**O que foi feito.** O payout da janela é a soma dos proventos dividida pela soma dos lucros,
prejuízo incluído. Com lucro acumulado menor ou igual a zero e provento positivo, a cobertura
é zero no score (`cvm_sem_lucro`). Cada ano com prejuízo e provento declarado tira 0,5 da nota
de payout (0 a 1), até zerar (`PENALIDADE_PREJUIZO_COM_PROVENTO`). A faixa 90%–120% não mudou.

### 5. Natureza na janela de 12 meses do DY

**Por quê.** A marca olhava só o último ano cheio. Um dividendo extraordinário de 2026, com 2025
normal, não aparecia.

**O que foi feito.** A janela é a mesma do DY (12 meses até a data da execução), comparada à
mediana dos anos civis inteiros anteriores ao início dessa janela. O teste cobre um extraordinário
em março de 2026 com 2020–2025 estáveis.

### 6. `dividendo_acima_fcf`, no lugar de `paga_com_divida`

**Por quê.** Dividendo maior que o FCF pode sair de caixa acumulado. O nome antigo afirmava dívida.

**O que foi feito.** O flag passou a se chamar `dividendo_acima_fcf`. O FCF é caixa operacional
menos aquisições de imobilizado, intangível e ativo contratual de concessão (o caso da CPFL,
«Adições de ativo contratual»), menos o principal de arrendamento IFRS 16 lançado no financiamento
(«pagamentos de arrendamentos», «passivo de arrendamento»). Linha que mistura arrendamento com
empréstimo ou debênture não entra: não dá para separar o principal do leasing. Banco e seguradora
continuam sem o flag.

### 7. Faixa do score, sem recalibrar

A faixa (nota cheia até 90% de payout, zero acima de 120%) foi desenhada para payout bruto.
Como o fallback voltou a ser bruto e a CVM também é bruta, a faixa permanece.

Comparação no mesmo universo de 26/10 (`comparacoes/ranking_2026_10_26.csv` e o `.md`):
80 empresas, Jev antigo, score novo. Mediana do delta de score: **+0,1**. Decisão diferente: **nenhuma**.
Cinco cruzaram 60 e continuaram na mesma decisão porque o Jev já segurava (ABCB4, BRAP4, ALLD3,
YDUQ3, VALE3). As maiores idas e vindas são de payout contábil, não de escala de DY: PETR4 sobe
13 pontos (payout de 35% no DY×P/L para 75% na soma de cinco anos, os dois ainda abaixo de 90%);
ABCB4 cai 12,5 (payout de 67% para 154%, com dois anos de prejuízo e provento). Liquidez corrente
e crescimento de receita dessa conta saíram do Fundamentus de 30/10, porque o CSV antigo não os
guarda; rentabilidade e valuation ficaram nos pilares gravados.

### 8. Alíquota do JCP conferida na lei

A Lei Complementar 224/2025, art. 8º, deu nova redação ao art. 9º, § 2º, da Lei 9.249/1995:
os juros ficam sujeitos ao IRRF de **17,5%** «na data do pagamento ou do crédito ao beneficiário».
O art. 14, III, manda essa regra produzir efeito em **1º de janeiro de 2026**. Não é a data da
deliberação. Texto: [planalto.gov.br/ccivil_03/leis/lcp/lcp224.htm](https://planalto.gov.br/ccivil_03/leis/lcp/lcp224.htm).

Os 17,5% até 2027 e 20% a partir de 2028 que circulam em comentário estão no art. 7º, II-C, e são
CSLL de sociedade de capitalização, não IRRF de JCP. A alíquota do JCP não tem esse segundo degrau
na lei.

No código: `ALIQUOTA_JCP_ATE_2025` (15%), `ALIQUOTA_JCP_DESDE_2026` (17,5%) e
`DATA_NOVA_ALIQUOTA_JCP`. A data usada é a de pagamento do Fundamentus. Sem ela, entra a data-com,
que não é o crédito individualizado — limitação registrada.

### Engenharia que acompanhou

- O zip do DFP deixa de ser eterno. Com mais de 7 dias, um HEAD olha o `Last-Modified`; se a CVM
  republicou, baixa de novo. O arquivo chega em `.parcial` e só substitui o zip se ele abre.
  `BadZipFile` apaga e tenta uma segunda vez.
- O nome do zip é o ano de `DT_REFER` (o exercício), não o ano seguinte. A leva anterior baixava
  o ano da entrega e perdia o exercício mais antigo da janela.
- `b3lib.http.fetch` recebe `dia`. Não há mais `_HOJE` global. `dividendos.fetch` lê `HOJE` na
  hora da chamada, então `acoes.HOJE = data` nas leituras antigas continua valendo.
- Ticker que falha na coleta permanece no CSV e no dashboard com decisão `coleta incompleta`,
  em vez de sumir.
- Reexecutar um script de `leituras/` importa o `dividendos.py` da raiz: SSL verificado e o mesmo
  `fetch`. O `setores_b3.py` de cada pasta segue com a cópia local e chama esse `fetch`; não quebra.
  `metricas` sem `dy_bruto` e sem bloco `cvm` continua DY × P/L sobre a série bruta. O arquivo
  `leituras/2026_10_06/dividendos.py` ainda desliga a verificação se alguém executar esse arquivo
  diretamente; não é o que as leituras importam.
- Os painéis e o `setores_b3.py` da raiz não foram esvaziados. O parse de data do Yahoo e a
  classificação setorial já estão em `b3lib`. O resto é montagem de HTML e lista BESST, específica
  de cada script; mexer nisso não mudava a metodologia.

