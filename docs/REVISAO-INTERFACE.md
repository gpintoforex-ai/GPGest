# Janelas, ações e mensagens

A revisão mantém o tema claro atual e as operações do GPGest. Não altera contratos financeiros, permissões ou dados existentes, nem necessita de uma nova migração de base de dados. As alterações anteriores do assistente de configuração inicial são preservadas.

## Componentes comuns

`frontend/src/components/InteractionUI.tsx` e `InteractionUI.css` passam a reunir:

- **AppDialog**: janela nativa com X de 40 px, nome acessível e tooltip «Fechar», Escape, foco inicial, reposição do foco, bloqueio do fundo e fecho protegido durante operações. O X mantém-se acessível ao deslocar o conteúdo. Clicar no fundo não descarta formulários.
- **confirmAction / FeedbackHost**: confirmações flutuantes, sem `window.confirm` ou `alert`. Identificam o registo e o efeito real. As confirmações destrutivas começam no botão seguro «Cancelar» ou «Continuar a editar».
- **Proteção do rascunho**: compara os campos com os valores iniciais ou confirmados pelo servidor. Ao sair, pergunta «Tem alterações por guardar. Pretende sair sem guardar?» e oferece «Continuar a editar» e «Descartar alterações». Campos de pesquisa e preferências de leitura não são tratados como alterações ao registo. Calculadora, contagem, fornecedores e configuração inicial também acompanham alterações feitas através de botões.
- **ActionButton / ActionLink / CloseButton**: ícones Lucide coerentes, ações destrutivas vermelhas, avisos laranja, operações concluídas verdes e ações secundárias neutras. As operações de caixa conservam ícones próprios. Botões apenas com ícone têm nome acessível e tooltip. Nas páginas normais, o regresso usa «Voltar».
- **ManagedForm e Field**: impedem submissões repetidas, mostram o estado de gravação e erros junto dos campos, incluindo os formulários com etiquetas nativas anteriores à revisão. Os erros estão associados aos campos por `aria-invalid` e `aria-describedby`; o nome acessível do campo mantém-se estável. Erros estruturados da API são apresentados em português; falhas de rede e demora do servidor têm instruções de continuação. Os dados não são limpos em caso de erro.
- **Notice**: mensagens com texto e ícone; erros persistentes e sucessos discretos, dispensáveis e com duração limitada. Dentro de janelas, as mensagens ficam na janela para não desaparecerem atrás da camada modal.
- **Menu móvel**: X, Escape, navegação por Tab contida no menu e reposição do foco no botão de abertura.

A gravação de uma parte de uma janela atualiza apenas a referência dos campos desse formulário. Não deve fazer desaparecer o aviso relativo a alterações ainda não guardadas noutros formulários da mesma janela. Credenciais e valores dos campos usados na comparação ficam apenas na memória da interface; não são escritos em armazenamento do navegador nem em logs.

A navegação principal e a mudança do filtro de loja também verificam os formulários da página. «Continuar a editar» conserva o formulário e a loja atual; «Descartar alterações» permite a navegação pretendida. Uma submissão em curso impede a saída até à resposta da operação.

Os filtros de consulta por intervalo de datas têm **períodos rápidos** (componente `DatePresets`): Hoje, Esta semana (segunda a domingo), lista **Mês** (qualquer mês do ano de referência), 1.º a 4.º trimestre e lista **Ano** (ano atual e nove anteriores), com datas em Europe/Lisbon. Escolher um ano aplica o ano completo e torna-o a referência dos meses e trimestres; sem escolha, a referência é o ano da data inicial do filtro. Os trimestres de outro ano indicam o ano no botão. O período escolhido fica destacado enquanto as datas lhe corresponderem; as datas continuam editáveis. Nas consultas que não aceitam datas futuras (saldos bancários, faturação e recebimentos), o fim é limitado ao dia atual e os trimestres futuros não aparecem. Estão disponíveis em movimentos e listagens, compras, compras e stock, inventário, conferência documental (filtros, assistente e exportação), reconciliação, saldos bancários, faturação e recebimentos, análise SAF-T, vendas Wintouch, email da contabilidade e consulta de emails Gmail. A previsão de liquidez mantém os seus horizontes próprios, por ser apenas futura.

## Áreas abrangidas

Foram aplicados os componentes às janelas principais e secundárias de caixas, faturado, calculadora, contagem, fornecedores, bancos, contas, entidades, funcionários, rubricas, extratos, reconciliação, depósitos, movimentos previstos e reais, compras, conferência documental, digitalização, pré-visualização de anexos, assistentes, liquidez, integrações, contabilidade, configuração inicial e administração da plataforma.

O fecho de caixa sem débitos pede agora confirmação antes da gravação. Com débitos, mantém-se a confirmação que identifica os fornecedores. A reabertura e o estorno explicam o efeito, identificam a caixa ou o movimento e mantêm a justificação e a auditoria existentes. A retirada de associações de pagamentos/documentos e a substituição de fotografias/PDFs têm confirmações específicas. Não foram criadas operações de eliminação onde o módulo apenas suporta inativação ou conservação do histórico.

## Verificação

```powershell
npm.cmd --prefix frontend run check:ui
npm.cmd --prefix frontend run build
```

`scripts/check-interactions.cjs` verifica a utilização dos componentes, ações de fecho explícitas, nomes dos botões apenas com ícone e ausência de confirmações nativas. O inventário atual inclui 26 janelas, 380 botões, 64 formulários e 98 mensagens. Links de ação também usam a biblioteca partilhada. A identidade atual segue o [sistema visual IAZELA](IDENTIDADE-IAZELA.md).

`scripts/test-interactions-ui.py` testa no navegador real:

- X, tooltip, Escape, clique no fundo e reposição do foco;
- continuar a editar ou descartar alterações;
- erros de validação local e do servidor, mantendo os dados, tanto em Field como em etiquetas nativas, incluindo a descrição acessível;
- duas submissões imediatas com uma única operação no backend;
- campos e fecho bloqueados durante uma gravação em curso;
- calculadora sobreposta à contagem e respetivo rascunho;
- criação e remoção confirmada de entidades, cancelamento de remoção de rubricas e de fecho contabilístico;
- conservação das fotografias na digitalização ao escolher continuar a editar;
- proteção de Cancelar/Voltar em formulários de página, navegação principal e mudança de loja; gravação de movimentos previstos;
- confirmação da importação e-Fatura independente da atualização da lista: uma falha de leitura posterior não é apresentada como falha da gravação;
- confirmação/cancelamento do fecho e abertura da janela de reabertura;
- largura de telemóvel, menu móvel, Tab e Escape.

O teste usa exclusivamente uma base local descartável (`.local/test-cash.env`, validada para `127.0.0.1:55432` e nome `gpgest_test_cash_*`) e esquemas temporários. Não deve ser apontado à produção. O assistente inicial mantém os testes de interface autónomo e ERP simulado em `scripts/test-company-setup-ui.py` e na variante `--erp`.

## Importação e-Fatura

A leitura e gravação do ficheiro correm numa tarefa síncrona do servidor, fora do ciclo de eventos da API. A pré-visualização não bloqueia a linha de controlo usada para serializar gravações. Mantêm-se a transação, a identificação de duplicados e a rejeição de conflitos.

Depois da confirmação do backend, a janela termina a importação e atualiza a lista em segundo plano. O utilizador vê separadamente a gravação concluída e a atualização da lista. Há proteção contra duplo clique, indicação de progresso, limite de espera e conservação do ficheiro em caso de erro. Uma resposta perdida não desencadeia nova importação automática: a mensagem pede para verificar a lista antes de repetir.

`backend/tests/test_purchase_import.py` verifica também que uma importação demorada não impede a resposta de outras rotas da API.

## Limites da verificação

O projeto disponibiliza atualmente o tema claro; esta revisão usa os seus tokens e não introduz um tema escuro. Os testes automatizados de interface foram realizados em Chromium, com teclado e dimensões de computador/telemóvel. Não substituem uma avaliação manual com leitores de ecrã, dispositivos físicos e outros navegadores. As operações reais com ERP, Google e bancos não foram executadas sobre dados de produção.
