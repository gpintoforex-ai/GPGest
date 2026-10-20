# Importar SAF-T (PT) de vendas

Em **Tesouraria → Importar SAF-T de vendas**, um administrador da empresa em
modo **local autónomo** pode carregar um XML, selecionar o estabelecimento,
rever os dados e escolher **Confirmar importação**. O NIF é sempre comparado com
o da empresa selecionada no servidor. O ficheiro não determina automaticamente
a loja ou a gaveta; confirme o estabelecimento de destino.

Com o Google Drive configurado, a página lista também os XML encontrados na pasta
configurada e subpastas, para importar sem descarregar o ficheiro; consulte
[SAF-T e extratos bancários no Drive](GMAIL-DRIVE-ANEXOS.md#saf-t-e-extratos-bancários-no-drive).

## Códigos de pagamento por associar

Na pré-visualização, a aplicação compara os códigos de pagamento do ficheiro (NU, CC, MB, TB…) com a associação em **Associar códigos SAF-T aos meios de pagamento**. Por predefinição, a confirmação é **impedida** enquanto existirem códigos por associar. Se a empresa escolher apenas avisar (configuração inicial → Faturação SAF-T), é preciso confirmar explicitamente que esses pagamentos ficam por classificar na conferência. Pagamentos sem código não bloqueiam. **Verificar novamente** atualiza o aviso depois de guardar a associação.

## Artigos do SAF-T

Depois de importar, **Artigos do SAF-T por criar ou associar** propõe uma ficha para cada código de artigo do ficheiro ainda sem correspondência: associa a um artigo existente com o mesmo código ou código de barras, ou cria um novo com nome, unidade, taxa de IVA e último preço sem IVA lidos das linhas das faturas. Serviços, outros e impostos aparecem desmarcados. Nada é criado sem seleção e confirmação; nunca se cria um segundo artigo com o mesmo código. Criar ou associar artigos não movimenta stock: as saídas por vendas SAF-T continuam a exigir a política de stock. O preço só é guardado por quem tem permissão para alterar preços.

## Compatibilidade efetiva

Suporta o perfil atual **1.04_01**, namespace
`urn:OECD:StandardAuditFile-Tax:PT_1.04_01`, com cabeçalho e totais em EUR.
É validado integralmente com a cópia oficial do XSD da AT, incluindo asserções
XSD 1.1 e campos atualmente obrigatórios, como ATCUD. Perfis antigos que indicam
1.04_01 mas omitem esses campos, outras versões, ficheiros de comunicação
simplificada e ficheiros sem `SourceDocuments/SalesInvoices` são rejeitados.
A mensagem identifica o campo/caminho incompatível; não há importação parcial.

Fontes oficiais consultadas:

- [Estruturas SAF-T (PT) da Autoridade Tributária](https://info.portaldasfinancas.gov.pt/pt/apoio_ao_contribuinte/Negocios/Faturacao/SAF_T_PT/SAF_T_PT_Versao_PT/Paginas/default.aspx).
- [XSD 1.04_01](https://info.portaldasfinancas.gov.pt/apps/saft-pt04/saftpt1.04_01.xsd).
- [FAQ técnicas da AT](https://info.portaldasfinancas.gov.pt/pt/apoio_contribuinte/questoes_frequentes/pages/faqs-00276.aspx): estados, notas de crédito e sentidos de débito/crédito dos recibos.

A origem e a impressão digital do esquema encontram-se em
`backend/app/saft_schemas/README.md`. A IAZELA não certifica assinaturas fiscais,
nem submete o ficheiro à AT.

## Secções e dados

- `Header`: empresa, NIF, versão, moeda, período e aplicação de origem.
- `MasterFiles/Customer`: código original, identificação e contactos, associados
  aos clientes existentes quando há correspondência única de NIF real. O código
  `CustomerID` mantém distintos os consumidores finais com NIF genérico.
  Os dados editados manualmente nos clientes não são substituídos.
- `MasterFiles/Product`: produtos/serviços e todos os campos originais, como
  referência de origem consultável no detalhe da importação. Não altera o stock
  nem os preços/artigos manuais da aplicação.
- `SourceDocuments/SalesInvoices/Invoice`: cabeçalhos, linhas, códigos, série e
  número, datas, estados, quantidades, descontos, impostos, totais e referências.
- `SourceDocuments/Payments/Payment`: recibos, linhas de origem e informação
  explícita dos pagamentos. Não são tratados como nova faturação.

`MovementOfGoods`, `WorkingDocuments`, fornecedores, plano de contas e movimentos
contabilísticos são estruturalmente validados, mas não importados como vendas.
Encomendas e transportes nunca contribuem para os totais de faturação.

Na análise normal de faturação entram FT, FS, FR e ND com sentido positivo e NC
com sentido negativo. Os montantes originais e as linhas são conservados.
Os estados A (anulado), F, R e S ficam consultáveis, excluídos dessa análise para
evitar duplicações ou interpretar autofaturação/resumos como vendas normais.
Tipos setoriais especiais, incluindo RP/RE do setor segurador, são conservados
para consulta e explicitamente excluídos da análise normal.

## Faturação, recebimentos e folha de caixa

Uma fatura não prova que houve recebimento. A referência usa pagamentos com
montante e data explícitos; não copia `InvoiceDate` para uma data de recebimento
em falta e não inventa numerário, TPA ou transferência. Ausência de informação
é apresentada como **Informação de recebimento indisponível**.

As associações dos códigos SAF-T aos meios ativos da IAZELA são manuais. A
interface apresenta sugestões apenas para correspondências conhecidas; estas
nunca são aplicadas automaticamente. Sem associação, ou se o meio for
inativado, o valor fica **Por classificar**. MB não significa automaticamente
TPA, nem DE significa automaticamente MB WAY.
Compensações e permutas também não provam entrada física de dinheiro.

Pagamentos de faturas que estejam representados de forma inequívoca em recibos
associados são contados apenas no recibo. Sobreposições parciais ou ambíguas são
excluídas e identificadas para revisão, sem distribuir montantes por suposição.
Recibos sem meios/pagamentos identificáveis não são transformados em numerário.

Nas folhas de caixa locais, abrir **Referência SAF-T do estabelecimento**:

- Faturação por data do documento, estabelecimento e tipo.
- Recebimentos por data efetiva do pagamento e código/meio associado.
- Documentos e importações que sustentam os valores, informações indisponíveis
  e duplicações excluídas.

É uma referência atual do estabelecimento, sem identificação da gaveta, e pode
mudar após uma substituição explícita de versões. Não é o retrato da informação
existente à data de um fecho anterior. Não altera o realizado manual, o saldo,
os movimentos reais, os fechos nem os cálculos existentes.

## Confirmação, conflitos e histórico

O processamento prepara uma área de revisão, sem aplicar documentos. A janela
mostra quantidades por tipo, totais, NC, anulados, registos novos, existentes e
em conflito. A amostra tem páginas de 30 registos; o detalhe apresenta as linhas,
os dados originais e a versão anterior quando existe conflito.

Cada conflito exige uma decisão **Manter versão existente** ou **Aplicar esta
versão**. A confirmação exige também assinalar a empresa, período e decisões.
Alterações concorrentes detetadas depois da pré-visualização impedem a aplicação;
atualize a pré-visualização para rever novamente.

A identidade dos documentos usa o NIF da empresa e tipo/número fiscal completo,
incluindo série e número originais. Catálogos usam os códigos originais por empresa.
São guardadas impressões digitais do ficheiro e do conteúdo normalizado. Ficheiros
repetidos e períodos sobrepostos não duplicam os documentos ou movimentos.
Documentos ausentes num ficheiro posterior permanecem disponíveis.

O histórico mantém ficheiro, empresa, período, versão, utilizador, datas, estado,
quantidades aplicadas/ignoradas/atualizadas e erros. As áreas de revisão anteriores
conservam os conteúdos das versões recebidas; o registo de origem aponta para a
última versão explicitamente aplicada. Os XML temporários são eliminados depois
do processamento; os dados originais normalizados e a impressão digital ficam
na base de dados privada da empresa.

## Processamento e limites

O upload é limitado no servidor, escrito num ficheiro temporário e processado em
segundo plano. A extração XML liberta os registos lidos e a preparação/gravação usa
lotes de 250. A validação XSD usa leitura diferida por secção para conservar as
restrições de identidade; uma secção grande pode requerer memória proporcional
ao seu tamanho. Existem no máximo dois trabalhadores por processo da API.

Limites: `SAFT_MAX_FILE_BYTES` (100 000 000 bytes por defeito, configurável entre
64 KB e 500 MB), 200 000 registos por ficheiro, 10 000 linhas e 5 MB normalizados
por documento. Exporte períodos menores quando um limite for excedido.
DTD, entidades externas, resolução remota e esquemas sugeridos pelo ficheiro
estão desativados. UTF-8 e codificações XML declaradas, incluindo Windows-1252,
são interpretadas no servidor.

As tarefas são internas ao processo, sem fila externa persistente. Se a API for
reiniciada, a aplicação dos documentos é revertida pela transação. Após 30 minutos
é possível recuperar um processamento interrompido; uma tarefa ainda ativa não
pode ser recuperada. Depois atualize a pré-visualização, ou volte a carregar o
ficheiro se a validação ainda não tinha terminado. Não há repetição automática.

## Instalação e testes

Instalar as dependências de `backend/requirements.txt`, aplicar `alembic upgrade
head` no perfil correto e reiniciar a API. A migração **0043** acrescenta as tabelas
de preparação/histórico; os snapshots, clientes, associações e auditoria reutilizam
as tabelas existentes. Novas empresas recebem as tabelas automaticamente.

Testes com dados fictícios: `test_saft_sales.py`, `test_saft_tenants.py` e
`scripts/test-saft-sales-ui.py`. Cobrem validação XSD, importação repetida,
sobreposições, notas de crédito, anulações, pagamentos/deduplicação, conflitos,
falhas atómicas, limites, Windows-1252, isolamento e permissões. A interface é
verificada em computador e telemóvel. Nenhum SAF-T real é enviado durante os testes.
