# Esquema oficial SAF-T (PT)

`SAFTPT1.04_01.xsd` é a cópia sem alterações do esquema publicado pela AT:
https://info.portaldasfinancas.gov.pt/apps/saft-pt04/saftpt1.04_01.xsd

Obtido em 17/10/2026 a partir da ligação oficial “Estrutura de dados, versão 1.04_01”:
https://info.portaldasfinancas.gov.pt/pt/apoio_ao_contribuinte/Negocios/Faturacao/SAF_T_PT/SAF_T_PT_Versao_PT/Paginas/default.aspx

SHA-256 dos bytes originais: `292c0ab4611e3f5a0cdf2abb4e62d9bd41254dc2e76a1fae4d35a8b132d79350`.

Codificação original Windows-1252; contém asserções XSD 1.1 e ATCUD. A aplicação
usa `xmlschema.XMLSchema11` com validação completa offline, incluindo asserções
e restrições de identidade. Não descarrega esquemas sugeridos pelo XML recebido.
Depois da validação, a extração usa `lxml.iterparse`, libertando registos já lidos.

O importador suporta este perfil atual da versão 1.04_01. Perfis antigos com o
mesmo número de versão e sem campos atualmente obrigatórios (por exemplo ATCUD)
são rejeitados com o campo e caminho incompatíveis, sem importar parcialmente.

Consultar também as FAQ técnicas da AT, em particular estados A/F/R/S,
notas de crédito com linhas a débito e sentidos dos recibos:
https://info.portaldasfinancas.gov.pt/pt/apoio_contribuinte/questoes_frequentes/pages/faqs-00276.aspx
