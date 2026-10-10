# Validação da Fase 1 — 10/10/2026

Ambiente local Windows, Python 3.13, React/TypeScript, FastAPI e PostgreSQL 16.15. Valores monetários em cêntimos inteiros. Base de demonstração própria, sem dados do site Desafio Salutar.

## Verificações concluídas

- `npm run build`: TypeScript e compilação de produção passaram.
- `pytest -q`: **11 testes passaram**, usando esquemas PostgreSQL temporários isolados.
- Teste de navegador: login, abertura, venda em cartão sem alterar numerário, contagem, fecho, ligação a PDF, menu móvel e ausência de erros JavaScript passaram.
- Imagens de computador e telemóvel verificadas; não existe deslocamento horizontal da página no teste a 390 px.
- Relatório de fecho validado como PDF no teste do backend.
- Anexos: acesso autenticado, validação de tipo, rejeição de conteúdo sem assinatura e bloqueio após fecho.
- Backup PostgreSQL criado e restaurado numa base temporária; lista e contagem de todas as tabelas da aplicação comparadas.

## Regras financeiras e de acesso testadas

1. Numerário e cartão separados; 0,10 € + 0,20 € calculados exatamente.
2. Montantes com mais de duas casas decimais e valores não finitos rejeitados.
3. Diferença no fecho exige justificação; caixa fechada bloqueia movimentos e anexos.
4. Reabertura autorizada e estorno preservam os registos originais; estorno repetido rejeitado.
5. Duas aberturas concorrentes: uma aceite, outra rejeitada.
6. Saídas concorrentes superiores ao fundo disponível: apenas uma aceite.
7. Chave duplicada de movimento e transferência rejeitada sem duplicar saldo.
8. Transferência entre caixas conserva o total; não cria resultado de gestão.
9. Consulta não pode abrir caixa; operador não acede a lojas sem autorização.
10. Operador só movimenta uma sessão sob a sua responsabilidade; passagem de caixa altera essa responsabilidade com histórico.
11. Operador não reabre caixa fechada.
12. Alteração de palavra-passe exige a atual e termina outras sessões.
13. Pedidos de origem externa não autorizada são rejeitados.

Os testes não deixam sessões ou movimentos na caixa da aplicação. O ensaio de recuperação remove apenas a base temporária criada para o teste.

## Limites da entrega

A administração central em `/plataforma` foi validada no navegador: autenticação separada, formulários de criação/configuração, saída de sessão e largura móvel. Os novos testes verificam isolamento dos esquemas, rejeição de uma sessão de outra empresa, impossibilidade de administradores das lojas acederem à plataforma, desativação imediata de empresas, cifragem das chaves e teste Wintouch exclusivamente de leitura com transporte simulado. Nenhuma chamada real ao Wintouch foi feita; são necessárias as credenciais de cada cliente.

O backup inclui agora a plataforma e os esquemas das empresas; a recuperação compara tabelas e contagens. A empresa inicial e os acessos anteriores foram preservados. Consulte [ADMINISTRACAO-PLATAFORMA.md](ADMINISTRACAO-PLATAFORMA.md) para configuração e atualização dos esquemas.

Esta entrega corresponde à **Fase 1 funcional**, com PDF de fecho, anexos e exportação CSV de movimentos, mais gestão central de empresas e configuração Wintouch. Os módulos das fases 2–5 estão identificados como pendentes. A análise Wintouch usa o Swagger guardado no projeto existente; a configuração e o teste de leitura estão disponíveis, mas ainda não há sincronização de dados. A integração bancária continua sem prestador confirmado.

A importação CSV/Excel, depósitos com talões e trânsito, receitas/custos reconhecidos, lotes e reconciliação ainda não estão implementados. O ambiente foi validado localmente; ainda não foi publicado nem auditado para utilização em produção.

O runner de testes apresenta um aviso de descontinuação do transporte httpx no Starlette instalado. Os testes passam; o aviso não afeta os pedidos da aplicação. As versões usadas estão guardadas em `backend/requirements.lock.txt`.
