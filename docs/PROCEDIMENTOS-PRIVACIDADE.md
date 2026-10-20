# Procedimentos de privacidade e segurança — para completar e aprovar

Preparado em 19/10/2026. Minuta operacional interna, não publicada como promessa de serviço. Responsáveis, contactos, prazos contratuais e procedimentos de suporte têm de ser atribuídos e ensaiados.

## Registo por finalidade

Manter registos distintos para a entidade enquanto responsável e enquanto subcontratante, conforme [orientação da CNPD](https://www.cnpd.pt/organizacoes/outras-obrigacoes/registo-de-atividades-de-tratamento/). Para cada linha registar titular responsável, finalidade concreta, categorias, fonte, dados mínimos, fundamento e avaliação quando necessária, destinatários, localizações, garantias, acessos, prazo/critério e forma de eliminar/devolver.

| Finalidade real | Dados mínimos e papel a validar | Fundamento a avaliar — não usar consentimento genérico | Conservação a decidir |
|---|---|---|---|
| Acesso, administração e suporte da conta | Nome, email, função, empresa; prestador responsável por conta/relação própria | Contrato quando o titular é parte; interesse legítimo e ponderação para representantes; obrigações específicas quando existam | Vida da conta, obrigações e defesa de direitos com critérios e restrições, sem prazo único indiscriminado |
| Segurança e operações | Identificadores, ações, datas e informação estritamente necessária | Interesse legítimo devidamente documentado e/ou obrigações concretas | Sessões técnicas: 8h empresa, 2h plataforma; definir retenção de logs/auditoria com avaliação do risco |
| Documentos de empresas clientes | Dados de colaboradores, fornecedores/clientes, caixa, contas e anexos | Instruções documentadas do cliente e fundamento determinado pelo responsável, segundo o papel efetivo | Contrato e instruções, retenção fiscal/legal concreta e exportação; nunca apagar só por pedido de fecho |
| Ligação Google/ERP/SMTP | Tokens e credenciais cifrados, documentos/recursos e destinatários selecionados | Execução/instruções e autorização técnica; OAuth não substitui por si só a base legal dos dados de terceiros | Tokens locais removidos na desconexão; confirmar ciclo dos documentos, prestadores e logs |
| Pedidos de direitos | Email, referência, tipo, descrição mínima, histórico de tratamento | Cumprimento da obrigação de tramitar direitos e demonstrar a resposta, conforme o papel | Definir prazo de prova e defesa, restrições e eliminação; não exigir cópia de identificação por defeito |
| Prova contratual | Empresa, utilizador, versão, hash e data de aceitação | Execução/defesa de direitos e conservação exigível a validar | Prazo de prova definido e acesso reservado; preservar a versão correspondente |

Não existe finalidade de publicidade ou treino externo no código analisado. Se for criada, exige análise própria, comunicação, mecanismo de oposição/consentimento quando exigível e atualização dos destinatários. [A CNPD distingue consentimento de outros fundamentos](https://www.cnpd.pt/organizacoes/areas-tematicas/consentimento/).

## Anexos ao acordo do artigo 28.º

A base em `legal_content.py` só deve ser concluída com: (A) objeto/duração/instruções, categorias e papéis efetivos; (B) medidas técnicas/organizativas, perfis, gestão de chaves, restauro e assistência; (C) lista de subcontratantes, entidades legais, locais e transferências, autorização e aviso de mudanças; (D) contactos, incidentes, exportação/devolução, retenções obrigatórias, eliminação das cópias e condições de auditoria. As obrigações em cadeia devem ser equivalentes e não uma autorização aberta indeterminada. Aprovar e celebrar com a empresa cliente antes do tratamento abrangido; a caixa de aceitação dos termos não substitui automaticamente a negociação necessária dos anexos.

## Pedidos de direitos

1. Rever a fila na administração com uma frequência que permita cumprir os prazos; atribuir responsável e registar a receção. O formulário não envia emails automaticamente.
2. Distinguir dados da conta, dados que o prestador trata por instrução do cliente, exportação contratual e encerramento. Encaminhar ao responsável competente quando necessário, preservando os deveres próprios e informando o titular.
3. Verificar a identidade apenas na medida necessária: sessão autenticada/contexto e confirmação por canal conhecido podem bastar; em caso de dúvida razoável justificar informação adicional. Não pedir documentos de identificação indiscriminadamente nem carregar esses documentos no campo livre.
4. Identificar dados em aplicação, integrações, anexos, auditoria, arquivos e backups; proteger terceiros e segredos. A exportação de perfil disponível não é resposta completa automática ao direito de acesso.
5. Responder em regra num mês de calendário. Extensão por complexidade/número de pedidos pode ir até dois meses adicionais e requer comunicação fundamentada dentro do primeiro mês. O painel não suspende o prazo por estar a verificar identidade e não regista retroativamente uma extensão como tempestiva.
6. Efetuar a comunicação por canal verificado, incluindo fundamento e vias aplicáveis se houver recusa. Só depois assinalar no painel que foi enviada e guardar a descrição mínima. O comprovativo de receção não é uma promessa de deferimento.
7. Revisão do estado após encaminhamento: “encaminhado” não equivale a resposta completa. Tratar atrasos como incidentes de processo, sem alterar datas de receção.

Fonte consultada em 19/10/2026: [CNPD — direitos](https://www.cnpd.pt/cidadaos/direitos/).

## Conservação e encerramento

Definir prazos por categoria e manter um mapa de cópias: base ativa, anexos locais/Drive, email enviado, esquemas arquivados das funções administrativas, logs, exportações e backups. As digitalizações preparatórias têm expiração de 24h e limpeza do PDF temporário no código; isso não elimina o documento guardado no Drive, as referências nem todas as cópias.

A obrigação de conservar documentos fiscais pertence ao sujeito passivo no âmbito legal aplicável. O CIVA e o DL 28/2019 não justificam guardar todos os dados pessoais durante um único prazo. Confirmar com o contabilista o tipo documental, os marcos de contagem, as exceções e os requisitos do arquivo. Não introduzir uma purga generalizada antes dessa decisão. Exportação/dossiê contabilístico deve ser validada contra o período e tipos selecionados; JSONs de índice não são prova de que todos os originais foram incluídos.

No encerramento, acordar quem pode pedir e autorizar, exportação/devolução, período de acesso, documentos sob obrigação legal, restrição dos acessos, revogação das integrações e eliminação verificável após o prazo aplicável. A desativação de conta não apaga documentos; as funções atuais de reset/remoção preservam arquivos e requerem política própria. Se um backup for restaurado, reaplicar eliminações/restrições registadas; não prometer apagar imediatamente uma cópia imutável sem capacidade demonstrada.

## Avaliação e resposta a violações de dados

Procedimento baseado nas [diretrizes publicadas pela CNPD](https://www.cnpd.pt/media/vfhpxp3p/edpb_guidelines_202209_personal_data_breach_notification_v2-0_pt.pdf), consulta 19/10/2026:

1. Registar quando houve conhecimento, quem detetou, sistemas/empresas afetados e factos conhecidos. Acionar os contactos designados, preservar evidência mínima e conter o acesso sem destruir prova.
2. Avaliar se houve perda de confidencialidade, integridade ou disponibilidade de dados pessoais; categorias, número aproximado, titulares, consequências, duração, proteção efetiva e medidas. Separar incidente técnico de violação notificável.
3. Enquanto subcontratante, informar o responsável sem demora injustificada, segundo o acordo, e fornecer atualizações úteis; não esperar fechar a investigação para o primeiro aviso. Não assumir que o prazo de 72h do responsável permite esperar 72h para avisar o cliente.
4. Enquanto responsável, avaliar notificação à autoridade competente sem demora injustificada e, quando aplicável, até 72h após conhecimento; se ultrapassado, justificar. Se a informação estiver incompleta, prever comunicação faseada nos pressupostos legais. A exceção depende de ser improvável o risco, não do tamanho da empresa.
5. Avaliar comunicação aos titulares sem demora injustificada quando for suscetível de resultar elevado risco, considerando as exceções legais. Usar linguagem clara, consequências, medidas e contacto efetivo.
6. Documentar decisões, inclusive a de não notificar, e razões; responsáveis, comunicações, contenção, restauro e prevenção. Usar apenas os canais oficiais adequados: [CNPD — outras obrigações](https://www.cnpd.pt/organizacoes/outras-obrigacoes/).
7. Ensaiar recuperação de backup em ambiente isolado, medir tempos e integridade e corrigir causas. Não foram comprovados RPO/RTO, cifragem de backups nem uma periodicidade de restauro; não os publicar como garantidos.

Este documento não envia notificações, não designa um encarregado de proteção de dados e não prova que os procedimentos tenham sido exercitados. Nomeação de responsáveis, contactos e ensaio são pendências concretas.
