# Assistente de configuração inicial

O painel apresenta **Vamos preparar o seu GPGest** às empresas que ainda não concluíram a configuração. O assistente também está em **Configurações → Parâmetros → Configuração inicial**. Apenas administradores podem alterar os dados. Gestores, operadores e utilizadores de consulta podem ver o estado, sem acesso às credenciais.

## Arquitetura e migração

A análise identificou a separação de dados por esquema PostgreSQL, a empresa central em `platform_companies`, os perfis e acessos por estabelecimento e os módulos existentes de caixas, bancos, pagamentos, saldos de referência, ERP, anexos e reconciliação. O assistente reutiliza esses registos e as identidades externas; não cria outro catálogo de caixas, contas ou fornecedores.

A migração aditiva **0041_company_setup**, após a **0040**, acrescenta:

- `company_setup`: progresso, rascunho, preferências, validação ERP e identificação de quem guardou e concluiu. Existe um registo por esquema de empresa.
- `payment_terminals`: identificação do TPA, estabelecimento, caixa, prestador, conta de destino e parâmetros de liquidação.
- Parâmetros nas caixas, contas e meios de pagamento existentes; IBAN cifrado nas contas.
- Referências ao terminal e ao meio de origem nos movimentos; fundo habitual a manter nas novas folhas.
- Associações dos meios ERP aos meios locais, incluindo MB WAY e meios personalizados.

Atualize a API e execute, com o perfil da base pretendida:

```powershell
Set-Location -LiteralPath 'C:\GPGest-API\backend'
..\.venv\Scripts\python.exe -m alembic upgrade head
```

Não execute seeds nem reinicialize a base para atualizar uma instalação existente. A migração não altera montantes, não abre folhas e não cria movimentos financeiros. Para uma instalação nova, os inicializadores habituais continuam a criar as estruturas do projeto.

## Percurso

1. **Empresa**: confirma nome, nome comercial, NIF e estabelecimento; sugere PT, EUR e Europe/Lisbon. Os parâmetros regionais e a moeda das novas contas e caixas são guardados. Os registos históricos conservam a sua moeda. Define também o **regime de IVA**, obrigatório para concluir: regime normal (periodicidade mensal ou trimestral), isenção do artigo 53.º do CIVA (sem declaração periódica; a periodicidade escolhida só organiza a entrega dos documentos à contabilidade) ou pequenos retalhistas (art. 60.º, sempre trimestral). Ao concluir, o regime e a periodicidade são gravados nas definições fiscais da empresa (Configurações → Regime de IVA), com registo na auditoria, e determinam os períodos da entrega à contabilidade. Uma alteração posterior nas Configurações prevalece quando se revê o assistente. Propõe ainda criar um **armazém de stock** no estabelecimento principal (por omissão, «Armazém principal»), que pode ser renomeado ou desmarcado; não é criado em duplicado e, se o estabelecimento já tiver armazém, a proposta vem desmarcada e os existentes são indicados. A criação fica na auditoria. A migração **0049_vat_regime** acrescenta o regime e classifica como regime normal as empresas que já tinham periodicidade definida.
2. **Modo de trabalho**: autónomo ou conector ERP operacional. Autónomo significa independente de ERP, sem promessa de funcionamento sem Internet. A ligação pode ficar pendente.
3. **Caixas**: reutiliza as existentes, permite acrescentar propostas, indicar responsável, data de início, fundo habitual, numerário inicial, contagem obrigatória e sugestão de transporte de saldo ou reposição de fundo fixo.
4. **Contas bancárias**: separa instituição financeira de conta, permite várias contas no mesmo banco, IBAN opcional validado e cifrado, moeda, saldo de referência e conta principal.
5. **Meios de pagamento**: confirma os meios utilizados, os destinos bancários e a disponibilidade em recebimentos ou pagamentos. Os meios retirados da seleção conservam o histórico. O numerário é obrigatório para a gaveta. Meios personalizados que movimentam numerário são conferidos em conjunto nessa gaveta, conservando a identificação de origem no movimento.
6. **TPA**: configura vários terminais com contas de destino diferentes, prestador independente do banco de destino, prazos, bruto/líquido, comissões opcionais, referência de extrato e MB WAY pelo terminal ou por conta própria.
7. **Preferências**: fecho diário, faturado manual ou obtido dos movimentos, justificação de diferenças, categorias reutilizadas, avisos de caixas antigas por fechar e arquivo nas integrações existentes. O Google Drive continua a usar a autorização e a pasta dos módulos de Anexos.
8. **Faturação SAF-T** (modo autónomo): associa os códigos de pagamento SAF-T aos meios de pagamento, com sugestões apenas quando o significado é inequívoco, e define se a importação de ficheiros com códigos por associar é **impedida** (predefinição) ou apenas avisada com confirmação. Indica como importar o primeiro ficheiro, incluindo a partir do Google Drive. A associação fica disponível depois de os meios de pagamento estarem guardados; até lá, o resumo apresenta-a como pendente, tal como a primeira importação. A associação é gravada com **Guardar e continuar** (ou ao recuar, sair ou guardar para mais tarde), sem botão próprio, e é guardada na empresa e não é apagada ao repetir a configuração.
9. **Resumo**: mostra os registos, associações e pendências antes de **Guardar e começar**. Depois permite abrir uma folha, registar um movimento quando existe uma folha aberta e importar um extrato; no modo autónomo, também permite importar SAF-T de vendas, enquanto no modo integrado permite configurar a ligação ERP.

O rodapé tem sempre os mesmos botões: **Anterior** (inativo na primeira etapa), **Guardar e continuar mais tarde** e **Guardar e continuar**. Guardar para mais tarde, avançar, recuar ou fechar pela cruz guardam o rascunho na empresa. Uma etapa opcional que fique por preencher aparece como pendente no resumo. Os botões que levam a outra página (por exemplo, **Configurar conta Google e pasta de arquivo**) guardam o rascunho, fecham o assistente e só depois abrem essa página. Outro administrador pode retomar noutra sessão ou computador. Alterações concorrentes são detetadas por uma revisão, evitando substituir silenciosamente as respostas de outra pessoa. Etapas opcionais adiadas conservam as respostas incompletas, sem criar os seus registos, e originam um lembrete no painel.

Retirar uma caixa ou conta existente do assistente conserva os seus registos e movimentos. Terminais retirados de uma configuração confirmada ficam inativos. Não se eliminam dados quando se muda de modo.

## Saldos e integridade financeira

O **fundo habitual** é o valor pretendido para manter na gaveta. O **saldo inicial** é todo o numerário efetivamente existente na data de início; pode ser diferente do fundo. Ambos são distintos do faturado.

Os saldos iniciais usam `account_balance_references`, sem movimentos de venda ou receita. A primeira abertura sugere o numerário inicial; o fundo habitual fica identificado separadamente para o valor a retirar ou repor. Repetir a conclusão ou retomar o rascunho reutiliza os registos e referências. Um saldo inicial histórico diferente é rejeitado, com indicação para o rever na área existente de saldos iniciais.

As contas mantêm a sua moeda e o saldo importado de um extrato passa a ser a referência bancária aplicável conforme a data. O saldo inicial não volta a ser somado como uma receita ao importar o extrato.

Ao registar um recebimento de cartão ou MB WAY, pode escolher o terminal. O movimento usa a conta desse terminal, validada no estabelecimento e caixa. Com vários destinos possíveis, um total agregado sem terminal não é atribuído por suposição a um banco.

A posterior liquidação do banco usa a reconciliação existente, que associa o extrato aos recebimentos já registados e permite confirmar as comissões. Não cria outra receita. Comissões desconhecidas permanecem vazias; os valores configurados no terminal são parâmetros de referência, sem lançamentos automáticos.

Uma contagem facultativa pode terminar sem valor contado; o sistema não inventa uma contagem igual ao esperado. A preferência de justificação aplica-se aos novos fechos. Os fechos concluídos conservam as preferências e os valores capturados no seu histórico.

## ERP e segurança

O conector operacional disponível é o **Wintouch**. O assistente reutiliza a validação de endereço, a cifra de credenciais e a normalização dos catálogos do projeto. Só marca uma nova ligação como ativa após um teste bem-sucedido da configuração exata. Alterar endereço, empresa ou chave invalida o resultado anterior.

A empresa ERP é escolhida pelo identificador da ligação existente. A pré-visualização apresenta quantidades e exemplos de caixas, entidades, artigos e meios de pagamento. Apenas **Confirmar importação dos catálogos** aplica o snapshot validado, dentro do prazo de dez minutos, em transação e por identidade ERP. Os documentos e movimentos mantêm os mecanismos próprios de importação em Integrações. A importação inicial não gera movimentos financeiros.

As chaves e os IBAN são cifrados. As respostas nunca devolvem as chaves guardadas, o IBAN completo ou os respetivos valores cifrados. A auditoria guarda etapas, revisões, resultados, montantes iniciais e contagens de registos; não guarda os segredos submetidos. As permissões e a empresa são validadas no servidor, incluindo todas as referências a caixas, bancos, contas, responsáveis e terminais.

## Validação

`backend/tests/test_company_setup.py` cobre o percurso autónomo, retoma, repetição, transações, permissões, isolamento entre empresas, ligação ERP pendente, teste e importação confirmada, IBAN, migração, etapas opcionais, preferências de contagem e dois TPA com destinos distintos. Confirma que uma liquidação bancária com comissão mantém uma única receita.

`scripts/test-company-setup-ui.py` e a variante `--erp` percorrem a interface real em computador e telemóvel, exclusivamente numa base local descartável e com respostas ERP fictícias. As regressões de caixa e os testes de desempenho verificam a compatibilidade com o comportamento anterior.
