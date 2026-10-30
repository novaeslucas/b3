# Ajustes de robustez e de metodologia

Os arquivos em `resultados/` **não foram regenerados**. O CSV e os dashboards publicados continuam
sendo a análise anterior (DY e payout brutos, payout = DY × P/L). A próxima execução de
`python dividendos.py` passa a gravar por cima desses arquivos com as regras abaixo. As pastas
`leituras/` não foram editadas: são o registro de cada pregão.

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
