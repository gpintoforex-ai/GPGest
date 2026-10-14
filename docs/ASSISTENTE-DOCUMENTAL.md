# Assistente de conferência documental

O fluxo principal do assistente e-Fatura foi simplificado para uma lista com verificações independentes, associações seguras e revisão conjunta. Consulte o [guia atualizado](ASSISTENTE-EFATURA.md). O fluxo individual e as ferramentas anteriores descritos abaixo permanecem disponíveis.

Em **Conferência documental**, abrir a fatura em **Conferir** e escolher **Abrir assistente passo a passo**.

1. **Anexo no Drive:** consultar a pasta configurada, escolher um ficheiro e analisar as sugestões por referência, fornecedor/NIF, montante e data. A análise lê temporariamente o texto extraível de PDFs; não existe OCR. Confirmar que o ficheiro pertence à fatura antes de guardar a referência.
2. **Fornecedor:** verificar os registos locais e importados da Wintouch pelo NIF. Se faltar um fornecedor ativo, o assistente propõe criar um registo no GPGest ou ativar/classificar uma entidade existente. A ação exige confirmação e não cria automaticamente fornecedores no ERP.
3. **ERP:** mostrar correspondências do catálogo importado. Um administrador pode consultar o ERP para a data da fatura. Sem consulta atual, a ausência no catálogo não prova que o documento não está lançado. Correspondências e divergências exigem revisão antes de confirmar uma associação.
4. **Revisão:** apresentar anexos, fornecedor e estado ERP. Se não houver correspondência, sugerir a preparação de uma proposta de despesa na área existente. O utilizador revê os dados e confirma as ações; o GPGest não emite automaticamente documentos na Wintouch.

## Documentos no Drive

Os novos anexos Drive e novas importações Gmail guardam apenas referências e metadados na base de dados (IDs, conta, nome, tipo e hash). Não guardam PDF, imagem ou texto integral extraído do documento. O ficheiro permanece no Drive. Anexos locais e conteúdos guardados em versões anteriores permanecem intactos; não se eliminam dados anteriores durante esta alteração.

Visualizar um anexo ou exportar o pacote da contabilidade lê o ficheiro do Drive para memória, sem o gravar na base de dados. É necessário manter a conta autorizada e o ficheiro disponível. Remover a associação elimina apenas a referência no GPGest, nunca o ficheiro Drive. A exportação falha com mensagem se um ficheiro remoto não puder ser obtido, em vez de entregar um pacote incompleto.

As associações estão limitadas à empresa e às lojas permitidas ao utilizador. O mesmo ficheiro não pode ser associado a outra fatura sem remover a associação anterior. Criação de fornecedores, associação e reversão ficam no histórico. O assistente mantém separados disponibilidade, registo ERP, pagamento, reconciliação e entrega à contabilidade.
