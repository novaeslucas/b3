# Fontes gratuitas de proventos da B3 em 2026: onde consultar, baixar e integrar

A melhor fonte gratuita e mais completa de proventos da B3 em 2026 é a própria B3: a página de Empresas Listadas (aba "Eventos Corporativos") e o endpoint JSON que a alimenta (`GetListedCashDividends`), que traz o histórico de dividendos, JCP e rendimentos desde 1996. Para quem programa, o caminho recomendado é consumir esse endpoint diretamente e validar com a CVM e com um site agregador; as APIs comerciais "grátis" (brapi, bolsai, Dados de Mercado) limitam ou bloqueiam justamente os dados de proventos no plano gratuito.

## TL;DR

- **Consulta manual rápida:** Status Invest, Investidor10, Fundamentus e Dados de Mercado seguem gratuitos em 2026 e mostram tipo, data-com/data ex, pagamento e valor por ação; a B3 é a referência oficial para tirar dúvidas.
- **Download em lote / histórico completo:** o endpoint não documentado da B3 (`listedCompaniesProxy/CompanyCall/GetListedCashDividends`, histórico desde 1996, JCP em valor bruto) combinado com o pacote R `rb3` e com os arquivos IPE da CVM (avisos aos acionistas e fatos relevantes, em ZIP anual desde 2003) é a única combinação 100% gratuita que cobre o mercado inteiro.
- **Integração via API:** não existe API gratuita, estável e completa de proventos. A brapi exclui dividendos do plano gratuito (exceto 4 tickers de teste); a bolsai oferece 200 requisições/dia, mas suas próprias páginas se contradizem sobre incluir dividendos no plano grátis; o yfinance funciona, mas tem consistência limitada. Para produção, construa seu próprio ETL sobre a B3 e a CVM.

## Key Findings

1. **A B3 é a fonte primária de fato.** Os sites agregadores (Status Invest, Meus Dividendos, Dados de Mercado, bolsai etc.) declaram que seus dados vêm da B3 e/ou dos comunicados à CVM.\[1\]\[2\] Desde a entrada em produção em 30/09/2022, a B3 também publica o "Formulário de Proventos em Dinheiro Aprovados", documento padronizado que as companhias enviam sempre que aprovam dividendos, JCP ou restituição de capital.\[3\]
2. **O endpoint da B3 funciona em setembro de 2026, mas tem pegadinhas.** Um projeto aberto no GitHub (PedroDnT/SILO-BZ, PRs mesclados em 28–29/09/2026) confirmou que o endpoint retorna histórico desde 1996, sem ISIN. Os registros foram casados com ISIN via nome de pregão + tipo de ação + preço de fechamento, com "1.054 de 1.054 chaves" confirmadas desde 2019 e zero erros.\[4\] O mesmo trabalho documentou que parcelas aparecem como linhas idênticas repetidas, que o JCP é publicado apenas em valor bruto e que nomes com barra (ex.: "KLABIN S/A") retornam zero resultados silenciosamente.\[4\]\[5\]
3. **Os planos gratuitos das APIs comerciais cortam justamente os proventos.** A brapi (plano gratuito: 15.000 requisições/mês, 1 ticker por requisição, histórico de 3 meses) informa em seu FAQ que dividendos "não estão incluídos" no plano grátis; eles começam no Startup\[6\] (R$ 119,99/mês).\[7\] A bolsai anuncia 200 requisições/dia grátis, mas sua própria página de MCP diz que dividendos exigem o plano Pro.\[8\]\[9\]
4. **O yfinance inclui JCP, aparentemente pelo valor bruto, e não distingue o tipo.** No caso de B3SA3 (ex-date 23/09/2026), o Yahoo exibiu R$ 0,227. Esse valor coincide com a soma bruta de dois JCPs (R$ 0,075947 + R$ 0,150688), não com a líquida (~R$ 0,187).\[10\]\[11\] Ou seja, o Yahoo juntou dois eventos numa linha e não marcou que era JCP. A documentação do pacote yfR avisa que "YF does not provide a consistent dividend database".\[12\]
5. **Atenção à tributação de 2026 ao converter bruto em líquido.** A Lei Complementar nº 224/2025, publicada em 26/12/2025, elevou a retenção de IR sobre JCP de 15% para 17,5% (Client Alert do escritório Demarest, jan/2026: "JCP: aumento de 15% para 17,5% de IRRF sobre JCPs distribuídos"); segundo a Receita Federal, em nota ao Investidor10, "Não há, nesse caso, previsão para excepcionar a nova alíquota de tributação para JCP apurado em 2025 e pago em 2026" — ou seja, vale a data de pagamento ou crédito. Também passou a valer uma retenção de 10% sobre dividendos acima de R$ 50 mil/mês por empresa pagadora (Lei 15.270/2025).\[13\] Qualquer cálculo de valor líquido precisa de regra por data.

## Details

### 1. Fontes oficiais

| Fonte | URL / acesso | O que cobre | Campos | Lote? | Status 2026 |
|---|---|---|---|---|---|
| **B3 – Empresas Listadas → Eventos Corporativos** | b3.com.br → Produtos e Serviços → Renda Variável → Empresas Listadas → [empresa] → Eventos Corporativos | Proventos em Dinheiro (dividendo, JCP, rendimento, restituição de capital), Proventos em Ações (bonificação, desdobramento, grupamento) e Subscrições; ações, units, FIIs, BDRs | Tipo, valor por ação, data de aprovação, "último dia com", ISIN na tela (o JSON histórico não traz ISIN) | Não pela interface; sim via endpoint JSON | Ativo |
| **B3 – endpoint JSON `GetListedCashDividends`** | `https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetListedCashDividends/{params base64}`; nome de pregão obtido via `GetInitialCompanies` | Proventos em dinheiro desde 1996\[14\] | Tipo, valor (JCP bruto), data de aprovação, data-com; um leitor relatou que a data de pagamento não vem nesse JSON | Sim, paginado (tamanho de página limitado; o tutorial de referência menciona 120 por página) | Funcionando em set/2026, não documentado oficialmente |\[14\]
| **B3 – Formulário de Proventos em Dinheiro** | Empresas Listadas → Informações Relevantes → tipo "Formulário de Proventos em Dinheiro Aprovados" | Dividendo, JCP e restituição de capital, em formato padronizado | Todos os dados do evento (inclusive pagamento) | Não (documento por documento) | Ativo desde 30/09/2022 |\[3\]
| **CVM – Dados Abertos, IPE** | dados.cvm.gov.br → "Cias Abertas: Documentos: Periódicos e Eventuais (IPE)"; arquivos `ipe_cia_aberta_AAAA.zip` | Metadados e links de Aviso aos Acionistas, Fato Relevante, Comunicado ao Mercado, Assembleia, Política de Dividendos, Calendário de Eventos Corporativos | Empresa, categoria, assunto, data de entrega, link do documento; o valor do provento fica dentro do PDF, não é estruturado | Sim, ZIP anual (diretório com arquivos desde 2003; o de 2026 foi atualizado em 07/09/2026) | Ativo; licença ODbL |\[15\]\[16\]\[17\]
| **CVM – DFP/ITR** | dados.cvm.gov.br (DFP/ITR) | Dividendos e JCP declarados nas demonstrações (DFC/DMPL), por exercício | Totais em R$, não por ação/evento | Sim | Ativo; útil para conferência, não para calendário |
| **RI das empresas** | Site de RI de cada companhia (ex.: Santander RI publica tabela com data de pagamento, data da RCA, montante e R$/lote de mil por classe) | O evento completo, com a palavra final | Todos | Não (formato varia) | Ativo; serve para desempate |\[18\]

**Como usar a B3 programaticamente (Python/Java):** (1) chame `GetInitialCompanies` com `{"language":"pt-br","pageNumber":1,"pageSize":20,"company":"PETR"}` em base64 para obter o `tradingName`; (2) chame `GetListedCashDividends` com `{"language":"pt-br","pageNumber":n,"pageSize":…,"tradingName":…}` em base64, iterando páginas até `results` vir vazio. O tutorial de referência precisou de `verify=False` (problema de certificado). Normalize o nome do jeito que o site da B3 faz: remova "/" e "." do nome, senão a consulta volta vazia.\[5\]\[14\] Remova duplicatas de parcelas e trate o JCP como bruto. Em Java, basta `HttpClient` + `Base64.getEncoder()` + Jackson.

### 2. Sites gratuitos de consulta

| Site | O que mostra (grátis) | Campos | Exportação | Observações 2026 |
|---|---|---|---|---|
| **Status Invest** (statusinvest.com.br) | Histórico de proventos de ações, FIIs, BDRs, ETFs e stocks; opções "5 anos" e "Máx." | Tipo (Dividendo, JCP, Rend. Tributado), data com/ex, pagamento, valor com 8 casas | Sem exportação oficial | Grátis no plano Bear; proventos futuros por carteira e sincronização com a B3 ficam no plano pago (Bull/Essencial). DY calculado sobre valor bruto |\[19\]\[20\]\[21\]
| **Investidor10** (investidor10.com.br) | Histórico por ativo e agenda de dividendos (data-com, pagamento, tipo JSCP/dividendo/rend. tributado) | Data-com, pagamento, tipo, valor | Sem API/CSV públicos | O "Radar de Dividendos Inteligente" é projeção estatística, não anúncio oficial.\[22\]\[23\] O plano PRO libera até 30 anos de histórico de indicadores |\[24\]\[25\]\[26\]
| **Fundamentus** (fundamentus.com.br/proventos.php?papel=XXXX) | Tabela simples por ativo | Data ("último dia com"), valor, tipo (DIVIDENDO / JRS CAP PROPRIO), data de pagamento, "por quantas ações" | HTML estático, fácil de raspar | Histórico longo (ex.: CSNA3 desde 2012); algumas linhas sem data de pagamento |\[27\]\[28\]
| **Dados de Mercado** (dadosdemercado.com.br/acoes/xxxx/dividendos) | Histórico, DY anual e links das divulgações na CVM | Tipo, valor, registro (data-com), ex, pagamento | Site grátis; a API exige token | Um dos poucos sites que mostra data-com **e** data ex **e** pagamento |\[29\]\[30\]
| **Meus Dividendos** (meusdividendos.com) | Histórico de ações, FIIs, ETFs e BDRs; agenda semanal com fonte | Data-com, pagamento, valor, yield | — | Informa que usa dados da CVM e da B3 |\[1\]
| **ADVFN** | Página de dividendos por ticker | Tipo, data-com, pagamento, valor, DY | — | Parte do conteúdo exige login |\[31\]
| **OkaneBox** (okanebox.com.br) | Proventos em dinheiro de ações via página e API | Valores de origem B3 | API JSON em `/api/acoes/proventos/{TICKER}/` | Grátis com restrições (o plano grátis de cotações tem 12 meses com 15 dias de atraso; FIIs exigem premium); premium a partir de 12x R$ 59,99 |\[32\]\[33\]\[34\]\[35\]

Não verificados nesta pesquisa (ou sem evidência atual encontrada): Oceans14, TradingView, Investing.com e Funds Explorer. Cheque manualmente antes de depender deles.

### 3. APIs e bibliotecas

| Serviço | Grátis? | Proventos no grátis | Limites / token | Avaliação |
|---|---|---|---|---|
| **brapi.dev** | Sim | **Não** (só nos tickers de teste PETR4, MGLU3, VALE3, ITUB4, sem token) | 15.000 req/mês, 1 ticker/req, 3 meses de histórico, ~30 min de atraso; Startup R$ 119,99/mês (dividendos de até 1 ano), Pro com histórico completo | Boa para cotações; para proventos, só pago. Resposta `?dividends=true` traz `cashDividends` e `stockDividends` com data-com, pagamento e `rate` |\[6\]\[7\]\[36\]\[37\]
| **bolsai** (usebolsai.com) | Sim | Contraditório: o blog diz que o plano grátis cobre todos os endpoints, inclusive `/dividends/{ticker}`; a página de MCP diz que dividendos exigem Pro; outra página diz que o grátis é "snapshot atual" | 200 req/dia, login Google, até 5 chaves; Pro citado a R$ 29 **e** R$ 49/mês em páginas diferentes | Teste antes de adotar; os campos anunciados são ex_date, payment_date, value e type (dividendo, JCP, bonificação) |\[2\]\[8\]\[9\]\[26\]\[38\]\[39\]
| **Dados de Mercado API** (api.dadosdemercado.com.br/v1) | Token obrigatório; não há tabela de preços pública | Endpoints de Dividendos (empresas e FIIs), Desdobramentos e Bonificações | "Acesso completo" e limites via contato comercial | O modelo de dados é o melhor encontrado: amount, adj_amount, approval_date, record_date (data-com), ex_date, payable_date, type, notes |\[40\]\[41\]\[42\]
| **Fintz** | Chave de testes pública | Endpoint `/bolsa/b3/avista/proventos` (só ações; FIIs "em breve") | Acesso completo comercial | Útil para prototipar |\[43\]
| **Yahoo Finance / yfinance (Python), yfR (R)** | Sim, sem token | Sim, via `Ticker("PETR4.SA").dividends` | Sem SLA; limites de requisição não oficiais | Inclui JCP sem rotular o tipo, aparentemente pelo valor bruto, e pode juntar eventos da mesma data ex; não traz data-com nem pagamento. Serve para análise rápida, não como fonte contábil |\[12\]\[44\]
| **rb3 (R, rOpenSci/CRAN)** | Sim | Funções de proventos em dinheiro, proventos em ações e subscrições das empresas, que usam os endpoints da B3 | — | O caminho mais "pronto" para eventos corporativos da B3 em lote; o COTAHIST desde 1986 vem **sem ajuste** por proventos |\[45\]\[46\]\[47\]\[48\]\[49\]
| **Apify "B3 Scraper"** | Plataforma paga com créditos | Cadastro B3 + dividendos em dinheiro, proventos em ações e subscrições | Cobrança por uso | Alternativa terceirizada ao scraper próprio |\[50\]

Sobre bibliotecas citadas no escopo: `investpy` dependia do Investing.com e está inoperante — o próprio autor, Alvaro Bartolome, reconheceu na Issue #611 do GitHub (alvarobartt/investpy) que o Investing.com "has recently changed its APIs as well as the protection of those with Cloudflare, which means that the data is not accessible the same way as before", e a última versão estável é a v1.0.8, de out/2022. `GetDFPData`/`GetDFPData2` cobrem demonstrações da CVM, não o calendário de proventos. A HG Brasil Finance e a Alpha Vantage não foram verificadas nesta pesquisa. A Alpha Vantage é, em geral, fraca em proventos brasileiros com JCP, mas isso não foi confirmado aqui.

### 4. Repositórios e datasets abertos

- **PedroDnT/SILO-BZ (GitHub):** o trabalho mais recente e rigoroso encontrado (set/2026) sobre o endpoint da B3, com mapeamento para ISIN e documentação das armadilhas.\[5\]\[51\] Leitura obrigatória antes de escrever seu ETL.
- **nickmaglowsch/b3-pipeline-data-and-backtest-framework:** pipeline que puxa CASH_DIVIDEND e JCP do `listedCompaniesProxy`.\[52\]
- **Tutorial "Como obter o histórico de dividendos… direto do site da B3" (Fábio Minutti Teixeira, Medium, out/2022, com edição posterior):** o código de referência para a paginação em base64.\[14\]
- **MaikeRM/WS_Dividendos:** raspa o dividendobr.com e gera .xlsx; pequeno e sem sinais de manutenção.\[53\]
- **gustavomoers/BrazilianMarketDataCollector:** o próprio autor avisa que o coletor da B3 está quebrado e os dados de proventos, desatualizados.\[54\]
- **ramonpaolo/api-b3:** raspa o Status Invest e estava hospedado no Heroku; trate como abandonado.\[55\]
- **Kaggle:** não encontrei dataset público mantido com o histórico completo de proventos da B3. Datasets estáticos, quando existem, envelhecem rápido; prefira gerar o seu.

## Recommendations

**(a) Consulta manual rápida:** use o **Status Invest** ou o **Investidor10** no dia a dia. Use o **Dados de Mercado** quando precisar ver data-com, data ex e pagamento lado a lado, com link para o documento na CVM. Em caso de divergência, a palavra final é da **página da B3** (Eventos Corporativos) ou do aviso aos acionistas no **RI**.

**(b) Download em lote / histórico completo (grátis):**
1. Liste os emissores (cadastro da B3 ou cadastro de companhias da CVM).
2. Para cada emissor, pagine `GetListedCashDividends`, que dá o histórico desde 1996.\[14\] Para bonificações, desdobramentos, grupamentos e subscrições, use as funções equivalentes da B3 (as mesmas do `rb3`).\[46\]\[47\]
3. Remova duplicatas de parcelas, guarde o JCP como bruto e calcule o líquido por data (15% até 2025, 17,5% a partir de 2026).
4. Enriqueça com a data de pagamento e valide com os ZIPs IPE da CVM (Aviso aos Acionistas / Fato Relevante) ou raspando o Fundamentus.
5. Rode incrementalmente todo dia, com baixa taxa de requisições e cache, porque o endpoint não é oficial e pode mudar sem aviso.

**(c) Integração via API em aplicações (Java/Python):**
- **Protótipo sem custo:** yfinance (Python) para séries rápidas, ciente das limitações de JCP e de datas; bolsai grátis, se o teste confirmar que `/dividends` responde no seu plano.
- **Produção:** crie seu próprio serviço (Spring Boot ou FastAPI) que expõe o banco montado no item (b). É a única opção gratuita, completa e sob seu controle.
- **Se puder pagar:** a brapi Startup/Pro tem preço público e contrato simples; o Dados de Mercado tem o modelo de campos mais rico (data-com, ex, pagamento, valor ajustado), mas exige contato comercial.

## Caveats

- O endpoint da B3 não é uma API oficial nem documentada: pode mudar de formato, exigir novos parâmetros ou bloquear automação. Os planos B2B oficiais da B3 (UP2DATA / B3 for Developers) não têm plano gratuito.\[51\]\[56\]\[57\]
- A evidência de que o Yahoo usa JCP bruto vem de um único ticker (B3SA3, set/2026); trate-a como indício, não como regra.
- As informações sobre planos (brapi, bolsai) vêm das páginas dos próprios fornecedores, que se contradizem em alguns pontos e mudam com frequência; comparativos publicados por concorrentes (ex.: brapi vs. Yahoo, bolsai vs. brapi) são material de marketing.
- A alíquota de 17,5% sobre JCP tem base na Lei Complementar nº 224/2025, e a retenção de 10% sobre dividendos acima de R$ 50 mil/mês está confirmada no documento de Perguntas e Respostas da Receita Federal sobre a Lei 15.270/2025 (dez/2025): o IRRF "incidirá à alíquota de 10% sobre a totalidade dos dividendos distribuídos por uma mesma pessoa jurídica" à pessoa física que ultrapassar R$ 50.000,00 no mês, a partir de janeiro de 2026. A Mayer Brown (dez/2025) acrescenta que dividendos pagos a não residentes sofrem 10% "independentemente do valor". Ainda assim, confira os detalhes antes de usar em cálculo fiscal.
- As "projeções" de dividendos (Radar do Investidor10, estimativas de DY futuro) são estimativas estatísticas, não proventos anunciados.\[23\]\[24\]

## Fontes

1. [Meus Dividendos](https://www.meusdividendos.com/)
2. [bolsai — API de Dados Financeiros do Brasil](https://usebolsai.com/)
3. [Availability of Cash Payment Form of Listed Companies on B3 Portal](https://clientes.b3.com.br/en/w/disponibilizacao-no-site-b3-do-formulario-de-proventos-em-dinheiro-das-companhias-listadas)
4. [research: map GetListedCashDividends history to ISINs (#386) by PedroDnT · Pull Request #401 · PedroDnT/SILO-BZ](https://github.com/PedroDnT/SILO-BZ/pull/401)
5. [Land B3's full cash-distribution history (migration 51) by PedroDnT · Pull Request #405 · PedroDnT/SILO-BZ](https://github.com/PedroDnT/SILO-BZ/pull/405)
6. [A API da brapi é grátis mesmo?](https://brapi.dev/faq/api-e-gratis-mesmo)
7. [Planos da API da bolsa brasileira e dados financeiros](https://brapi.dev/pricing)
8. [MCP Server B3: dados da bolsa no Claude e ChatGPT](https://usebolsai.com/mcp)
9. [bolsai vs brapi: Comparação Honesta das APIs B3 (2026) — bolsai](https://usebolsai.com/blog/bolsai-vs-brapi-comparacao-api-b3-2026)
10. [B3SA3 ajusta JCP: veja valores e data de pagamento](https://seucreditodigital.com.br/b3sa3-ajusta-jcp-pagamento-outubro-2026)
11. [B3 S.A. - Brasil, Bolsa, Balcão (B3SA3.SA) Stock Price, News, Quote & History - Yahoo Finance](https://finance.yahoo.com/quote/B3SA3.SA/)
12. [Get Yahoo Finance Dividends from a single stock — yf\_get\_dividends • yfR](https://docs.ropensci.org/yfR/reference/yf_get_dividends.html)
13. [Dividend Yield (DY): o que é e como calcular passo a passo - Renova Invest](https://renovainvest.com.br/blog/o-que-e-e-como-calcular-o-dividend-yield-dy/)
14. [Como obter o histórico de dividendos e outros proventos com Python direto do site da B3](https://fabiomt.medium.com/como-obter-o-hist%C3%B3rico-de-dividendos-e-outros-proventos-com-python-direto-do-site-da-b3-90a507623418)
15. [Cias Abertas: Documentos: Periódicos e Eventuais (IPE) - Conjunto de dados - Portal Dados Abertos CVM](https://dados.cvm.gov.br/dataset/cia_aberta-doc-ipe)
16. [Cias Abertas: Documentos: Periódicos e Eventuais (IPE) - Documentos Periódicos e Eventuais de Cias Abertas (2026) - Portal Dados Abertos CVM](https://dados.cvm.gov.br/dataset/cia_aberta-doc-ipe/resource/1203dc1b-648c-41cc-a67f-454fe7204834)
17. [Index of /dados/CIA\_ABERTA/DOC/IPE/DADOS/](https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/)
18. [Tabela de JCP e Dividendos - Santander RI](https://www.santander.com.br/ri/conheca-o-santander-brasil/historico-de-dividendos/tabela-de-jcp-e-dividendos/)
19. [PETR4 - PETROBRAS PN: cotação e indicadores](https://statusinvest.com.br/acoes/petr4)
20. [Planos e preços Status Invest](https://lp.statusinvest.com.br/ao/planos/)
21. [Status Invest: como utilizar a plataforma?](https://www.mobills.com.br/blog/investimentos/status-invest/)
22. [Agenda de Dividendos de Ações - 2026 / 2027 - Investidor10](https://investidor10.com.br/acoes/dividendos/)
23. [Como acompanhar projeções de dividendos com o Radar de Dividendos Inteligente do Investidor10 - Investidor10](https://investidor10.com.br/conteudo/radar-de-dividendos-inteligente-121700/)
24. [Plataformas online de investimentos: compare as principais opções \[2026\] - Investidor10](https://investidor10.com.br/conteudo/ferramentas-controle-de-investimento/)
25. [Investidor10 PRO - Cursos, Ferramentas Avançadas, Gerenciador de Carteiras e muito mais](https://investidor10.com.br/assine-agora-mar-26-irpf/)
26. [bolsai vs Investidor10: dados da B3 para automação e código (2026)](https://usebolsai.com/blog/bolsai-vs-investidor10-dados-mercado-api-2026)
27. [FUNDAMENTUS - Invista consciente](https://www.fundamentus.com.br/proventos.php?papel=cple6&tipo=2)
28. [FUNDAMENTUS - Invista consciente](https://fundamentus.com.br/proventos.php?papel=CSNA3)
29. [Dividendos de BMEB4 (Banco Mercantil do Brasil) - Dados de Mercado](https://www.dadosdemercado.com.br/acoes/bmeb4/dividendos)
30. [Dividendos de POMO3 (Marcopolo) - Dados de Mercado](https://api.dadosdemercado.com.br/acoes/pomo3/dividendos)
31. [Dividendos B3 SA - Brasil Bolsa Bal... ON - B3SA3 - ADVFN](https://investorshub.advfn.com/stock-market/bovespa/B3SA3/dividends/more-dividends)
32. [API Dividendos Fundos Imobiliários - OkaneBox](https://www.okanebox.com.br/uncategorized/api-dividendos-fundos-imobiliarios/)
33. [Como Usar API de Ações Empresas Bovespa - OkaneBox](https://www.okanebox.com.br/como-usar/api-de-acoes-empresas-bovespa/)
34. [Como usar API de Dividendos e JCP - OkaneBox](https://www.okanebox.com.br/como-usar/api-dividendos-proventos/)
35. [Preços - OkaneBox](https://www.okanebox.com.br/precos/)
36. [API da bolsa brasileira e dados financeiros do Brasil](https://brapi.dev/)
37. [O plano gratuito tem limitações importantes?](https://brapi.dev/faq/o-plano-gratuito-tem-limitacoes-importantes)
38. [Como montar uma carteira de dividendos com Python — bolsai](https://usebolsai.com/blog/como-montar-carteira-dividendos-python-api)
39. [bolsai vs Status Invest: quando você precisa de uma API de dados da B3 (2026)](https://usebolsai.com/blog/bolsai-vs-status-invest-api-dados-b3-2026)
40. [Documentação da API - Dados de Mercado](https://www.dadosdemercado.com.br/api/docs)
41. [FIIs: Dividendos - Documentação da API - Dados de Mercado](https://www.dadosdemercado.com.br/api/docs/fiis/dividendos)
42. [Empresas: Dividendos - Documentação da API - Dados de Mercado](https://www.dadosdemercado.com.br/api/docs/empresas/dividendos)
43. [Bolsa -](https://docs.fintz.com.br/endpoints/bolsa/)
44. [Ticker — yfinance](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.html)
45. [Help for package rb3](https://cran.r-project.org/web/packages/rb3/refman/rb3.html)
46. [rb3 package - RDocumentation](https://www.rdocumentation.org/packages/rb3/versions/0.0.12)
47. [NEWS](https://cran.r-project.org/web/packages/rb3/news/news.html)
48. [Releases · ropensci/rb3](https://github.com/ropensci/rb3/releases)
49. [Changelog • rb3](https://ropensci.github.io/rb3/news/index.html)
50. [B3 Brazil Stock Tickers & Listed Company Scraper · Apify](https://apify.com/scrapers_lat/b3-scraper)
51. [Research seam: SILO as the data layer for external quant research · Issue #371 · PedroDnT/SILO-BZ](https://github.com/PedroDnT/SILO-BZ/issues/371)
52. [GitHub - nickmaglowsch/b3-pipeline-data-and-backtest-framework](https://github.com/nickmaglowsch/b3-pipeline-data-and-backtest-framework)
53. [GitHub - MaikeRM/WS\_Dividendos · GitHub](https://github.com/MaikeRM/WS_Dividendos)
54. [GitHub - gustavomoers/BrazilianMarketDataCollector: Extract brazilian market financial data from CVM, B3 and other sources. · GitHub](https://github.com/gustavomoers/BrazilianMarketDataCollector)
55. [GitHub - ramonpaolo/api-b3: API Simples que retorna dados sobre tal ação/empresa da B3 · GitHub](https://github.com/ramonpaolo/api-b3)
56. [API Gratuita da B3: Guia Completo em Python (2026) — bolsai](https://usebolsai.com/blog/api-gratuita-b3-guia-completo-python-2026)
57. [WebServices Bovespa · GitHub](https://gist.github.com/ThiagoAnunciacao/cc54b806d4a10bf3408f?permalink_comment_id=3497496)
