# Modos de funcionamento por empresa

## Utilização

Abra **Configurações > Integrações** com um utilizador administrador da empresa.
O modo de funcionamento é independente da escolha demonstração/real. Uma nova
empresa começa em **Modo local autónomo**, sem precisar de chave ERP. O modo
local define quem gere os dados; não significa funcionamento sem Internet.

1. Escolha **Modo local** ou **Modo integrado com ERP**.
2. No modo integrado, configure Wintouch, URL autorizada, empresa externa e
   chave API. Uma chave em branco conserva o segredo cifrado que já existe.
3. Escolha os recursos e a origem principal. Entidades com origem principal ERP
   têm os dados principais de leitura; a rubrica associada continua local.
   Números, montantes e liquidações de documentos externos são sempre da origem.
4. Desative **Permitir operações de escrita** para uma ligação apenas de leitura.
5. Introduza uma justificação, carregue em **Pré-visualizar alteração**, reveja
   correspondências e operações pendentes e confirme os efeitos antes de aplicar.
6. Use **Testar ligação** e importe os recursos selecionados. Os documentos
   financeiros têm formulários próprios com estabelecimento, período e confirmação.

Não há envio automático dos registos locais ao ativar o modo integrado. Pode
manter documentos de gestão, anexos, observações, cenários e despesas locais.
Uma falha de ligação mantém o modo integrado e os dados já guardados.

## Trabalho local

Reutilizam-se clientes/fornecedores, rubricas com ícone e cor, contas/caixas,
documentos de gestão, recebimentos/pagamentos parciais, movimentos reais,
depósitos, transferências previstas, compromissos recorrentes, anexos,
conferência documental, dossiê contabilístico e previsão de liquidez.
Os documentos de gestão não constituem emissão fiscal certificada.

Em **Saldos iniciais locais**, indique a conta ou caixa, o saldo no início do dia
de referência e, nas caixas, o fundo a manter incluído nesse saldo. É possível
calcular o saldo acumulado e preparar um depósito sem abrir uma folha de caixa.
O fundo fica retido na caixa, sem nova entrada a cada abertura.

Um saldo inicial substitui os movimentos anteriores ao dia de referência.
Movimentos do próprio dia contam depois desse saldo. Para bancos, um extrato da
mesma data ou posterior tem prioridade: não se somam saldo inicial e extrato.
Liquidações locais ainda por reconciliar continuam identificadas como estimativa.

Em **Movimentos previstos**, administradores e gestores podem editar documentos
locais. A alteração não pode reduzir o total abaixo do já liquidado ou trocar o
sentido de um documento com pagamentos. Pagamentos e recebimentos não voltam a
contar como despesas ou vendas; depósitos são transferências internas.

As importações existentes mantêm a pré-visualização e as suas validações:
e-Fatura/ficheiros de compras nos formatos suportados pelo importador e extratos
BPI no formato implementado. Não foi acrescentado um importador genérico para
ficheiros arbitrários.

## Correspondências e mudança de modo

Associe uma entidade local a uma entidade ERP, ou uma caixa a uma conta de caixa
ERP, em **Associações de contas e entidades**. NIFs coincidentes são propostas
de correspondência, não associações automáticas. Associações incompatíveis são
recusadas. Documentos de compra usam a conferência documental existente, que
compara identidade e valores antes de confirmar.

Ao passar para modo local, os documentos externos mantêm o original de leitura.
Pode **Criar cópia local do pendente**: a cópia começa pelo valor em aberto,
passa a ser editável em Movimentos previstos e substitui o original na previsão.
Repetir a conversão devolve a mesma cópia. Não há escrita na origem.
Liquidações locais já existentes exigem conferência antes desta conversão.

A mudança é bloqueada enquanto existem sincronizações em curso. Envios incertos
exigem verificação ou, na passagem para local ou leitura da mesma origem, revisão e aceitação
explícita da lista de resultados ainda por verificar. Os estados são preservados
e não há novos envios. Rubricas usam **Verificar envio** em Tabelas > Rubricas.
Liquidações dispõem de **Verificar envio no ERP** nas operações pendentes: apenas
consulta o identificador enviado e o saldo atual. Se a origem não confirmar,
mantém-se o resultado por verificar; não se repete o pagamento.

## Capacidades e limitações dos conectores

| Capacidade | Wintouch |
|---|---|
| Artigos, entidades e contas de caixa | Consulta suportada |
| Documentos de venda/compra, pendentes e pagamentos | Consulta suportada |
| Criar rubricas | Escrita suportada, com confirmação na origem |
| Liquidar pendentes | Escrita suportada, com confirmação na origem |
| Criar documentos de despesas no ERP | Indisponível; proposta local para revisão/exportação |
| Quantidades em stock | Indisponível neste adaptador |
| Cursor incremental | Indisponível; snapshots idempotentes |

Eticadata e ZoneSoft aparecem identificados como **Não implementado**. Faltam,
para cada um, documentação de acesso/autenticação, contratos de entidades,
documentos e pagamentos, paginação, identificação/versão e mecanismo de escrita
com verificação. Não há endpoints inventados nem escrita direta em tabelas ERP.

A execução periódica é opcional, com intervalo mínimo de 15 minutos. Consulta
apenas os catálogos selecionados (artigos, entidades e contas de caixa). Em
demonstração exige confirmação explícita de importação de dados reais. Intervalo
0 desativa-a. Não envia lançamentos. Documentos financeiros continuam manuais,
por período e estabelecimento; o histórico guarda as execuções e erros.

Os estados do fluxo existente distinguem proposta/rascunho local, preparação
para revisão, envio, confirmação, rejeição/erro e resultado por verificar.
Uma proposta de despesas exportada não é um lançamento confirmado no ERP.
Não existe fila geral de escrita para conectores sem essa capacidade.

## Implementação e instalação

Migração **0032_company_operating_modes** sobre **0031**:

- Configuração explícita em `public.platform_companies`: modo, escrita,
  recursos, origem principal, revisão e periodicidade.
- `integration_bindings` em cada empresa: identidade por empresa/conector,
  referências internas/externas, versão, sincronização e dados locais.
- `account_balance_references` em cada empresa: saldo, fundo, moeda e data.

As empresas com API já ativa são preservadas como integradas; as restantes
começam locais. Não se apagam dados importados, credenciais, anexos ou histórico.
Para outra instalação: executar `alembic upgrade head` na pasta `backend` e
reiniciar a API. A configuração ERP e a chave de cifragem são exclusivamente
do servidor. O frontend nunca recebe a chave guardada nem o texto cifrado.

Ficheiros principais: `backend/app/operating_modes.py`,
`integration_scheduler.py`, `platform_models.py`, `models.py`, `db.py`,
`platform.py`, `main.py`, `documents.py`, `liquidity.py`, `deposits.py`;
`frontend/src/IntegrationSettings.tsx` e `.css`, `App.tsx`, `Entities.tsx`,
`SyncPanel.tsx`, `TenantIntegrations.tsx`, `Rubrics.tsx`,
`PlannedMovements.tsx`, `DocumentControl.tsx`, `DocumentAssistant.tsx` e `Liquidity.tsx`.

A configuração mantém uma ligação ERP por empresa. A camada comum de compras
em `integrations/erp.py` e os metadados/associações comuns isolam as identidades;
os snapshots específicos Wintouch continuam preservados. Adicionar outro
conector operacional requer o seu adaptador, configuração e testes de contratos.

Verificação: `backend/tests/test_operating_modes.py` cobre isolamento, leitura,
mudança explícita, conflitos de associação, dados locais associados,
cópia idempotente, saldo sem folha e falha sem troca automática de modo.
`scripts/test-operating-modes-ui.py` verifica o assistente com dados fictícios,
confirmação, leitura, falha e vista móvel. As verificações não enviam operações
para o ERP real.
