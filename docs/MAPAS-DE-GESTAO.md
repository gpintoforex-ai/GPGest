# Mapas de gestão: custos, proveitos e IVA

Em **Contabilidade → Mapas de gestão** (gestores e administradores) há dois mapas indicativos, por período e loja. São mapas de gestão: não substituem a contabilidade nem a declaração periódica de IVA.

## Custos e proveitos

- **Proveitos:** faturação do SAF-T de vendas, líquida de IVA, pela data do documento (documentos normais FT, FS, FR, ND; notas de crédito subtraem).
- **Custos:** todos os documentos de compra pela data do documento (e-Fatura, compras confirmadas em Compras e registos manuais), uma vez por documento, mais as compras do ERP sem documento correspondente. Inclui faturas do e-Fatura ainda não lançadas.
- **Rubrica de cada custo:** a das linhas da compra lançada; senão, a definida no documento ERP; senão, a do fornecedor; sem nenhuma, *Por classificar*.
- **Valor do custo:** no regime normal, base tributável mais o IVA não dedutível estimado; nos restantes regimes, o total (o IVA também é custo).
- Agrupamento por mês ou trimestre, com total de custos e margem (proveitos − custos). Não inclui amortizações, acréscimos e diferimentos nem salários que não estejam registados como compras.

## Mapa de IVA

- Segue a periodicidade do **regime de IVA** configurado (mensal ou trimestral). Na isenção do artigo 53.º não há mapa.
- **IVA liquidado:** IVA do SAF-T de vendas (notas de crédito subtraem).
- **IVA suportado:** IVA dos documentos de compra, incluindo os ainda não lançados.
- **IVA dedutível (estimado):** IVA suportado × percentagem de dedução da rubrica (Tabelas → Rubricas → «IVA dedutível estimado nas compras»; vazio = 100%). Exemplos: 50% no gasóleo de viaturas ligeiras, 0% em despesas não dedutíveis. A dedutibilidade real é confirmada pelo contabilista.
- **Saldo estimado:** liquidado − dedutível, como *a entregar* ou *a recuperar*. No regime dos pequenos retalhistas é apenas uma referência (regras próprias do art. 60.º do CIVA).
- O detalhe por rubrica mostra o IVA suportado e o dedutível estimado de cada rubrica.

## Relação com os outros ecrãs

- **Despesas por rubrica** conta apenas compras lançadas (ERP e Compras) e indica quantas faturas do e-Fatura estão por lançar.
- As faturas do e-Fatura lançam-se na **Conferência documental → Rever propostas** (propostas automáticas, ver [assistente e-Fatura](ASSISTENTE-EFATURA.md)).
