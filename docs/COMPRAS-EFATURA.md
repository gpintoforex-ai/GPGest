# Compras, e-Fatura e reconciliação

Em **Bancos → Reconciliação automática → Faturas e documentos**, o pagamento e o documento têm estados independentes:

- Pagamento: por reconciliar, parcial, reconciliado.
- Documento: por anexar, anexado (PDF, JPG ou PNG, até 5 MB; máximo de cinco ficheiros).
- Validação interna: por validar, validada. Não altera a classificação fiscal no Portal das Finanças.

Pode registar uma compra manualmente, abrir o detalhe, anexar a fatura e confirmar a associação a movimentos dos extratos importados em Bancos. Uma compra pode ter vários pagamentos, e um pagamento pode ser repartido por várias compras. O servidor impede exceder o montante de qualquer lado, associar movimentos de outra loja ou usar uma entrada para pagar uma fatura de saída. Notas de crédito são negativas e podem ser associadas a entradas bancárias. Desfazer uma associação devolve o montante disponível e fica na auditoria.

O separador **Pagamentos sem associação completa** mostra saídas com montante ainda por associar. Esta lista é um alerta para conferência, não uma conclusão de que falta uma fatura.

## Ligação à AT

Enquanto não houver certificado da AT, pode usar **Importar ficheiro**, independente dos acessos ao e-Fatura. Aceita CSV (UTF-8 ou Windows-1252) e XLSX, até 5 MB e 10 000 compras. O modelo CSV pode ser descarregado na janela de importação; a linha de exemplo é fictícia e deve ser substituída.

Colunas obrigatórias: NIF do fornecedor/emitente, número de documento/fatura, data e total. Fornecedor, tipo e IVA são opcionais. O leitor aceita nomes equivalentes em português, valores portugueses e datas ISO ou dia/mês/ano. Folhas com fórmulas são recusadas. O suporte às colunas reais de um export do e-Fatura deve ser confirmado com um ficheiro de exemplo; um formato não reconhecido produz uma mensagem e não importa dados.

A pré-visualização identifica compras novas, duplicados e conflitos de data, valor ou IVA informado. Só importa após confirmação; conflitos bloqueiam a importação completa. Duplicados não alteram anexos, validações ou pagamentos existentes. As compras importadas ficam com origem **Ficheiro**, documento **Por anexar** e pagamento **Por reconciliar**.

Foi implementada exclusivamente a operação SOAP de consulta **Invoices**, com **CustomerTaxID** (adquirente). O contrato oficial está guardado em `backend/app/integrations/fatshareInvoices.wsdl`. Não comunica faturas, não emite documentos e não altera dados da AT.

Documentação oficial:

- https://info.portaldasfinancas.gov.pt/pt/apoio_ao_contribuinte/Outras_entidades/Suporte_tecnologico/Webservice/e_Fatura/Paginas/default.aspx
- https://info.portaldasfinancas.gov.pt/pt/apoio_ao_contribuinte/Outras_entidades/Suporte_tecnologico/Webservice/e_Fatura/Documents/Comunicacao_dos_elementos_dos_documentos_de_faturacao_aspetos_gerais.pdf
- https://info.portaldasfinancas.gov.pt/pt/apoio_ao_contribuinte/Outras_entidades/Suporte_tecnologico/Webservice/e_Fatura/Documents/Comunicacao_dos_elementos_dos_documentos_de_faturacao.pdf

O administrador configura NIF, subutilizador, senha e ambiente na janela **Configurar e-Fatura**, para a loja selecionada. A senha é cifrada com a chave da plataforma já configurada e nunca é devolvida ao navegador.

No servidor, configurar os caminhos para os ficheiros fornecidos/assinados pela AT. Certificado e chave privada em PEM; chave pública de autenticação em PEM ou certificado PEM/DER:

```dotenv
EFATURA_CERT_TEST=/caminho/seguro/certificado-testes.pem
EFATURA_KEY_TEST=/caminho/seguro/chave-testes.pem
EFATURA_PUBLIC_KEY_TEST=/caminho/seguro/ChavePublicaAT-testes.cer
# Opcional, se a chave privada tiver senha:
EFATURA_KEY_PASSWORD_TEST=

EFATURA_CERT_PROD=/caminho/seguro/certificado-producao.pem
EFATURA_KEY_PROD=/caminho/seguro/chave-producao.pem
EFATURA_PUBLIC_KEY_PROD=/caminho/seguro/ChavePublicaAT-producao.cer
EFATURA_KEY_PASSWORD_PROD=
```

Guardar certificados, chaves e senhas fora do repositório. Reiniciar a API depois de alterar o ambiente. A consulta usa HTTPS com certificado de cliente, senha e data cifradas conforme WS-Security da AT, e uma chave aleatória nova em cada pedido.

**Sincronizar e-Fatura** consulta um intervalo dentro do mesmo mês e percorre as páginas (300 documentos por página; limite de 100 páginas por consulta). Só guarda alterações depois de receber todas as páginas com sucesso. O NIF do fornecedor, o tipo e o número do documento identificam a compra dentro da empresa. Repetir a sincronização não duplica compras nem elimina anexos ou associações. Alterações de data/valores obrigam a validar novamente; uma redução incompatível com pagamentos associados bloqueia a sincronização para revisão.

As compras do ambiente de testes são identificadas como **e-Fatura (teste)** e não podem ser reconciliadas com movimentos bancários. Usar uma empresa de demonstração separada para os testes da AT. Não se misturam documentos com a mesma identidade entre testes e produção.

O serviço de consulta fornece dados das faturas. O comprovativo é anexado separadamente. Quando o nome do fornecedor não vem na resposta, é usado o nome de uma entidade Wintouch com o mesmo NIF, se disponível; caso contrário, mostra-se o NIF.

## Validação

Testes cobrem os estados independentes, anexos e permissões, pagamentos parciais e repartidos, prevenção de excesso e duplicados, configuração cifrada, sincronização repetida, invalidação após alteração, notas de crédito, pedido SOAP e cifra das credenciais.

A ligação real à AT necessita de credenciais autorizadas e certificados válidos; não foi validada com uma conta real durante a implementação.
