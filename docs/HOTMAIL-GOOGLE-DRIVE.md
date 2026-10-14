# Anexos de faturas: Hotmail e Google Drive

Estado: plano inicial Hotmail/Drive adiado pelo utilizador. A integração implementada usa Gmail e Drive; consultar [GMAIL-DRIVE-ANEXOS.md](GMAIL-DRIVE-ANEXOS.md). Este documento conserva os requisitos iniciais e não descreve o fluxo atualmente ativo.

Origem: `desafiosalutar@hotmail.com`.

Pasta de destino: https://drive.google.com/drive/folders/1OV1fH8lEFrvlTa7P7-OzgkVxgFmMZ5pJ

## Fluxo definido

1. O administrador da empresa autoriza a leitura da conta Microsoft e o acesso à pasta Google Drive.
2. Escolhe a pasta de email e o período a consultar; a primeira importação exige uma pré-visualização dos anexos selecionados.
3. O GPGest guarda os anexos selecionados na pasta de destino, conservando origem, remetente, data, identificadores da mensagem e anexo, e hash do ficheiro.
4. A mesma mensagem/anexo não volta a produzir uma cópia em novas sincronizações. Um reenvio com conteúdo igual é sinalizado para revisão.
5. Os ficheiros dão origem a sugestões de associação às faturas, com motivos e confiança. A associação exige confirmação; um anexo não cria automaticamente uma compra, pagamento ou reconciliação.
6. Associar um anexo não altera o estado de pagamento, entrega à contabilidade ou presença no ERP. Os ficheiros podem permanecer por associar.

## Autorizações e configuração pendentes

- Identificar a conta Google com permissão de escrita na pasta indicada.
- Registar a aplicação GPGest na Microsoft, com suporte a contas pessoais e autorização delegada para ler email (`Mail.Read`).
- Registar a aplicação GPGest na Google e configurar OAuth, a API Drive e seleção/autorização da pasta.
- Configurar credenciais das aplicações no servidor, sem as colocar no frontend, no repositório ou no chat.
- Guardar tokens cifrados por empresa; proteger o retorno OAuth com estado de utilização única, expiração e validação da empresa/utilizador.
- Desligar uma conta revoga o uso dos tokens no GPGest e não elimina anexos existentes.
- Não marcar mensagens como lidas, mover ou apagar emails durante a recolha.

As ligações de Outlook/Drive no assistente podem ajudar a inspecionar as contas, mas não substituem a autorização OAuth própria do GPGest.

Documentação oficial:

- Microsoft: https://learn.microsoft.com/en-us/graph/permissions-reference
- Google: https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- Pastas Drive: https://developers.google.com/workspace/drive/api/guides/folder
