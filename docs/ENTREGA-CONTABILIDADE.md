# Entrega à contabilidade por período

Abra **Contabilidade → Entrega à contabilidade**, com um perfil administrador.
O módulo usa o regime de IVA guardado em **Configurações → Regime de IVA**:
mensal apresenta 12 períodos por ano; trimestral apresenta quatro.

1. Configure o regime de IVA da empresa.
2. Escolha o ano e confira os documentos do mês/trimestre na Conferência documental.
3. Depois de o período terminar no calendário, clique em **Encerrar período**.
4. Reveja a contagem de documentos sem anexo, escreva a justificação e confirme
   a ação para toda a empresa. A data, o administrador e a justificação ficam
   guardados e auditados.
5. O botão **Exportar dossiê** fica disponível apenas após esse fecho explícito.
6. Se precisar de corrigir o fecho, **Reabrir** exige justificação. A exportação
   do período volta a ficar indisponível até novo fecho.

O ZIP abrange todas as lojas da empresa e todos os documentos locais com data
no período, sem o limite de 200 documentos da seleção manual. Inclui:

- `controlo.csv`: relação documental e estados independentes de ERP, anexos,
  tratamento, entrega, pagamento e banco.
- `documentos.json`: dados e estados documentais disponíveis no GPGest.
- `documentos/`: todos os anexos locais e os anexos associados do Google Drive
  que possam ser obtidos no momento da exportação.
- `anexos.csv`: nomes originais e localização dos ficheiros no ZIP.
- `documentos-erp.csv`: documentos ERP importados com data no período, incluindo
  os que ainda não estão relacionados com um documento local.
- `pendencias.csv`: documentos sem anexo ou com conferência/entrega pendente.
- `periodo.json`: empresa, período, regime, confirmação do fecho e âmbito da exportação.

O fecho é uma confirmação administrativa da preparação do período. Não bloqueia
alterações aos documentos, não marca faturas como pagas, não fecha obrigações
fiscais e não envia ficheiros. Cada exportação reúne o conteúdo atualmente
disponível. Originais em falta devem ser obtidos; a listagem não os substitui.
Documentos apenas existentes no ERP entram na listagem, sem inventar uma cópia
digital que não esteja guardada ou associada no GPGest.

Uma falha ao obter um anexo remoto impede a exportação; não é omitido silenciosamente.
Mantém-se o limite de 100 MB de anexos por pacote. Extratos originais, documentos
não importados e outros elementos externos à conferência documental não são
inventados nem incluídos automaticamente.

Fecho, reabertura e dossiê completo estão reservados ao administrador. O acesso
usa o esquema PostgreSQL da empresa selecionada. Períodos já encerrados continuam
acessíveis após alterar o regime; um novo fecho com datas sobrepostas é bloqueado
até reabrir o período anterior.

## Instalação

A migração `0022_accounting_periods.py` cria `accounting_periods` em cada empresa.
Foi aplicada na base local. Noutro ambiente:

```powershell
cd backend
..\.venv\Scripts\alembic.exe upgrade head
```

Reinicie a API para carregar as novas rotas, sem alterar as credenciais existentes.
