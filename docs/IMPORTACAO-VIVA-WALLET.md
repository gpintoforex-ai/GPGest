# Importação de extratos Viva Wallet

Em **Tesouraria → Extratos bancários**, selecione a conta Viva Wallet em euros,
carregue o ficheiro `.xls` original exportado pela Viva Wallet e escolha
**Pré-visualizar extrato**. Verifique os movimentos e a conta antes de escolher
**Confirmar importação**. Não é necessário abrir ou converter o ficheiro no Excel.

O formato suportado contém uma tabela HTML com as colunas **Data da Transação**,
**Data Valor**, **Descrição**, **Montante** e **Saldo**, e os resumos **Saldo Anterior**
e **Saldo a Transitar**. A extensão `.xls` deste exportador não corresponde a um
ficheiro Excel binário. Outros formatos `.xls` não são aceites por este leitor.

- Importa entradas e saídas com o sinal original, incluindo liquidações de vendas,
  comissões e transferências. Os saldos inicial e final não criam movimentos.
- Confere os saldos linha a linha e o saldo final. Um ficheiro incompleto ou com
  valores inválidos é rejeitado por inteiro, sem importação parcial.
- A deteção de duplicados usa datas, descrição, montante e saldo, por conta.
  Reimportar o mesmo extrato ou períodos sobrepostos ignora movimentos já importados.
- O ficheiro não inclui um identificador da conta de origem nem uma indicação
  explícita da moeda: confirme estes elementos na pré-visualização. A aplicação
  não consegue distinguir automaticamente dois extratos de contas Viva diferentes.
- Internamente, a última data dos movimentos serve como referência de ordenação;
  o ficheiro não fornece uma data de exportação. Dentro do mesmo extrato, o último
  movimento tem prioridade para apresentar o saldo final.

Os movimentos ficam disponíveis nos extratos bancários. A reconciliação e a
criação de movimentos de gestão continuam a ser operações separadas.

O BPI Net Empresas em `.xlsx` continua disponível no mesmo seletor. O novo
endpoint é `/api/accounts/{account_id}/import-statement`; o endpoint anterior
`/import-bpi` permanece compatível. Não há alterações ao esquema da base de dados
nem dependências adicionais para ler o formato Viva.
