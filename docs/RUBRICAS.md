# Rubricas e sincronização ERP

Em Tabelas > Rubricas, crie e edite nome, ícone e cor. A criação sugere uma cor livre; editar mantém a cor existente. Ícones e cores permanecem no GPGest.

## Configuração

Escolha o ERP e o Prefixo dos códigos de rubricas no ERP nas configurações da empresa. O prefixo inicial é RUB; pode conter letras, números, hífen e sublinhado ou ficar vazio. Apenas o adaptador Wintouch está atualmente implementado. Sem ligação ativa, a gestão local mantém-se disponível.

## Importar e enviar

Sincronizar ERP > Importar do ERP permite selecionar até 100 rubricas, sem duplicar associações nem substituir a aparência local. Administradores e gestores podem importar.

Sincronizar ERP > Enviar para o ERP consulta a origem e exclui as rubricas existentes pela identidade guardada ou pelo nome exato, ignorando maiúsculas e espaços exteriores. Não exige correspondências manuais ou escolher uma rubrica de referência. O administrador revê e confirma as rubricas em falta.

Os códigos são sequenciais (RUB0001, RUB0002), após o maior sufixo existente com o prefixo configurado. Incluem-se os códigos de envios pendentes. A proposta é indicativa; a atribuição definitiva ocorre no envio, sob bloqueio local, consultando novamente o ERP. Ao enviar só parte da lista, os códigos são atribuídos aos elementos efetivamente enviados.

A Wintouch recebe uma rubrica de caixa Type=M e CashAccountDocumentsNature=2, conforme a definição por defeito do objeto Item na documentação da API. Não se copia a configuração fiscal de outra rubrica nem se associa um IVA ou motivo de isenção. Ligações existentes preservam os dados devolvidos pelo ERP.

Os identificadores são guardados antes da escrita remota. Respostas ambíguas ficam por verificar: Verificar envio pendente consulta o ERP sem repetir a escrita. Numa falha parcial, os envios concluídos mantêm-se guardados e a proposta é atualizada. Guardar uma rubrica local não a envia automaticamente.

## Instalação e testes

Execute alembic upgrade head no backend. Migrações: 0023 (aparência e associações), 0024 (ERP configurado), 0025 (prefixo).

As rotas categories/erp/settings, items, proposals, import e categories/{id}/erp/send usam a configuração da empresa. Novos ERP precisam de um adaptador próprio. As rotas anteriores são mantidas por compatibilidade. Os testes usam API simulada, sem criar rubricas em empresas reais.