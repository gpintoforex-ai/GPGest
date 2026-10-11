# Imagens da contagem de numerário

As imagens em `frontend/public/euro` são ficheiros do Banco Central Europeu, descarregados em 11 de outubro de 2026. São servidas pela aplicação, sem pedidos externos durante a contagem.

Notas: anversos da série Europa com assinatura de Lagarde (5 a 200 euros) e da primeira série (500 euros), com a marca **SPECIMEN**, sem alterações aos ficheiros do [pacote de baixa resolução do BCE](https://www.ecb.europa.eu/euro/shared/img/euro_banknotes_specimen_72dpi.zip). Os nomes locais correspondem ao valor em cêntimos. [Fonte e regras de reprodução](https://www.ecb.europa.eu/euro/banknotes/images/html/index.en.html).

Moedas: [faces comuns publicadas pelo BCE](https://www.ecb.europa.eu/euro/coins/common/html/index.en.html), conservadas sem alterações:

| Ficheiro local | Ficheiro na pasta `/euro/coins/common/shared/img/` do BCE |
| --- | --- |
| 200.jpg | newcommon_2euro_800.jpg |
| 100.jpg | newcommon_1euro_800.jpg |
| 50.jpg | newcommon_50cent_800.jpg |
| 20.jpg | newcommon_20cent_800.jpg |
| 10.png | new10common.png |
| 5.jpg | common_5cent_800.jpg |
| 2.jpg | common_2cent_800.jpg |
| 1.jpg | common_1cent_800.jpg |

A janela apresenta imagens proporcionais, quantidades inteiras e subtotais por denominação. No computador, as sete notas ocupam uma linha e as oito moedas outra; no telemóvel, duas listas compactas mostram notas e moedas lado a lado. O total e os botões ficam sempre disponíveis; ecrãs muito baixos ou ampliação de texto mantêm a possibilidade de deslocar o conteúdo.

**Aplicar contagem** atualiza a folha; **Guardar contagem** grava na base de dados. Cancelar ou premir Escape descarta apenas as alterações feitas nessa janela.

Clicar num campo de quantidade ou no valor total abre um teclado numérico com limpar, apagar e confirmar. O valor total inclui vírgula decimal; as quantidades aceitam apenas inteiros até 100000. O primeiro dígito substitui o valor anterior. Cancelar ou Escape fecha apenas o teclado e mantém o campo anterior; confirmar atualiza o cálculo da contagem. Também aceita os números, Backspace, Delete e Enter do teclado físico.
