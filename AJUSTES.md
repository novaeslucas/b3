# Ajustes de robustez e payout da CVM

Este PR junta a parte **estável** do trabalho feito na branch `cursor/ajustes-robustez-metodologia-f620`
(PR #1): engenharia e payout pela CVM. O resto daquela branch — natureza do provento, antecipação de
dividendos de 2025, DY líquido de JCP e as perguntas novas do Jev — ficou de fora de propósito e está
descrito em [Fora deste PR](#fora-deste-pr).

Os arquivos em `resultados/` **não foram regenerados**. As pastas `leituras/` não foram editadas.

## Por que separar

Nas seis rodadas do PR #1, as decisões oscilaram com o texto do prompt, não com os dados. Papéis em
*descartar*: 52 na publicação de 26/10, 19 numa rodada e 44 na seguinte, sem nenhum dado de mercado novo.
Cada rodada corrigia o efeito colateral da anterior, e não havia critério de aceite fora da própria
contagem de decisões. Com tudo no mesmo PR, não dava para atribuir uma mudança de decisão a uma causa.

Aqui só muda uma coisa no que o Jev vê: o payout. As perguntas são as de 26/10, palavra por palavra.

## Engenharia

- **SSL verificado** em todos os hosts. O código antigo desligava a verificação em qualquer `fetch` com
  URL explícita, inclusive no Yahoo. Em 30/10/2026 B3, Yahoo, Fundamentus e CVM responderam com
  certificado válido. A cópia congelada `leituras/2026_10_06/dividendos.py` ainda desliga a verificação,
  mas não é o que as leituras importam.
- **Falhas visíveis.** `b3lib/log.py` registra ticker e etapa e imprime um resumo no fim. O papel que
  falha na coleta continua no CSV e no painel com decisão `coleta incompleta`, em vez de sumir.
- **`b3lib/`** reúne HTTP com cache diário, números, parse do Fundamentus, proventos, Yahoo, CVM e
  classificação setorial. `dividendos.py` continua exportando `fetch`, `lin`, `media`, `num` e `HOJE`,
  que as leituras diárias importam; `acoes.HOJE = data` continua valendo.
- **Testes** com `pytest` (`requirements-dev.txt`), só com fixtures locais, sem rede.

## Payout pela CVM

Em `b3lib/cvm.py`, com os ZIP anuais de `https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/`
(cerca de 13 MB por ano, em `cache/cvm/`).

- **Proventos do exercício:** DVA, folhas de dividendos e de JCP (`7.08.04.01`/`.02`; em banco,
  `7.09.04.01`/`.02`). A conta-pai inclui lucro retido e fica de fora. Se a DVA não traz as folhas, ou
  as traz zeradas com a DMPL mostrando distribuição (LAVV3, YDUQ3), vale a DMPL com as subcontas. O
  caixa pago da DFC é o último recurso.
- **Lucro:** o da controladora, localizado pelo **nome** da conta, não pelo código. Em vários bancos a
  3.11 individual é «Reversão dos Juros sobre Capital Próprio» (zerada) e o lucro está na 3.13; no
  consolidado costuma estar na 3.09. Instituição financeira usa a demonstração individual, que é quem
  declara o JCP.
- **Janela:** soma dos proventos ÷ soma do lucro em até 5 exercícios, prejuízo incluído nas duas somas.
  O score só usa esse número com pelo menos 3 anos (`payout_fonte = cvm`).
- **Ano de prejuízo com provento** tira 0,5 da nota de payout (0 a 1), até zerar.
- **Quando não confiar no lucro da CVM**, o payout volta a DY × P/L, sem penalidade:
  lucro zero ou ausente na maioria dos anos (`lucro_nao_encontrado`), ou lucro recente da CVM zerado ou
  com sinal trocado em relação ao «Lucro Líquido» de 12 meses do Fundamentus (`lucro_diverge`). Diferença
  de tamanho não conta: os períodos são diferentes, e uma tolerância de 30% contra valor de mercado ÷ P/L
  mandava 39 dos 80 papéis de 26/10 para um DY × P/L que estoura (issue #2).
- **Sem base de lucro:** soma do lucro ≤ 0 ou payout agregado acima de 300%. O payout exibido fica `n/d`
  e, havendo provento, a nota de payout é 0, como acima de 120%: cobertura pior nunca rende score
  maior. Sem provento nenhum, o payout sai da média e os pesos restantes se renormalizam.
- A faixa do score não mudou: nota cheia até 90% de payout, zero acima de 120%.

**Conferência com números divulgados (exercício de 2025):**

| Papel | Lucro usado | Proventos | Payout 2025 | Referência |
|---|---:|---:|---:|---|
| PETR4 | R$ 110,129 bi | R$ 41,236 bi (DVA) | 37,4% | R$ 41,23 bi, 37,44% do lucro, aprovados na AGO |
| BBAS3 | R$ 18,152 bi (individual) | R$ 5,184 bi de JCP | 28,6% | Relatório da administração: payout de 30% (R$ 5,2 bi sobre R$ 17,8 bi) |
| ITUB4 | R$ 45,659 bi (individual, 3.13) | R$ 33,704 bi | 73,8% | RI: proventos de R$ 33,7 bi, 72% do lucro recorrente de R$ 46,8 bi |
| BMGB4 | R$ 560,7 mi (individual, 3.13) | R$ 323,6 mi de JCP | 57,7% | Release: lucro de R$ 561 mi e JCP de R$ 323,6 mi |
| TAEE11 | R$ 1,580 bi | R$ 1,124 bi | 71,1% | soma das linhas de provento da DMPL |

Os recortes reais do DFP desses casos (e de ABCB4, LAVV3 e YDUQ3) estão em `tests/fixtures/`.

**Dividendo acima do FCF** (caixa operacional menos capex, ativo de concessão e principal de
arrendamento IFRS 16) é calculado e sai no CSV e no painel como informação. Não entra no score nem no Jev.

## O que muda para o Jev

O estado é o de 26/10 com um campo a mais, `payout_source`, e uma frase no contexto explicando de onde
vem o payout. As perguntas são as mesmas. Como o estado mudou, a próxima execução de `dividendos.py`
**com** Jev não reaproveita `cache/jev.json`.

## Comparativo com 26/10

```bash
python comparacoes/comparar_ranking.py
```

O script monta dois estados do Jev para as 80 empresas de 26/10: o **antigo**, com o payout DY × P/L
publicado, e o **novo**, com o payout da CVM. Tudo o mais é igual nos dois lados, inclusive a oscilação
de 12 meses. Chama o Jev três vezes em cada lado e decide pela maioria. Assim, toda decisão que muda é
efeito do payout. O resultado fica em `comparacoes/ranking_2026_10_26.md`.

## Fora deste PR

Ficam para um PR separado, que só deve entrar com critério de aceite definido antes:

- **Natureza do provento** (recorrente, extraordinário, retomada, antecipação de nov/dez de 2025) e os
  campos que a alimentam no estado do Jev.
- **Mudanças nas perguntas do Jev** sobre extraordinário e sustentabilidade.
- **DY líquido de IR sobre JCP** no score e no painel. O parser ainda calcula `valor_liquido` por
  evento (15% até 2025, 17,5% a partir de 2026, LC 224/2025), mas nada usa esse valor aqui.

Proposta para esse PR:

1. Um conjunto de 12 a 15 papéis rotulado à mão (o DY está inflado ou não), escrito antes de qualquer
   mudança. O aceite é acertar esse conjunto e ser estável nas 3 chamadas, não a contagem de decisões.
2. Uma variável por rodada: código ou prompt, nunca os dois juntos.
3. Uma trava determinística na regra de decisão: provento de 12 meses abaixo da mediana dos anos
   anteriores, sem queda forte de preço, não descarta o papel só pela P(extra) do Jev.
