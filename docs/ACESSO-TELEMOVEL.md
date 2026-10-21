# Acesso pelo telemóvel na mesma rede

No computador, abra `Iniciar-Rede.bat`. Também pode executar `Iniciar-Rede.ps1` ou, se a aplicação já estiver aberta, `Iniciar-Rede.ps1 -Reiniciar`. O script mostra o endereço atual do computador; o telemóvel deve estar no mesmo Wi-Fi, com acesso aos restantes dispositivos dessa rede.

Abra no navegador do telemóvel o endereço apresentado, por exemplo `http://192.168.1.70:5174`. Para administração, acrescente `/plataforma`. Utilize as credenciais já existentes; não é criada uma nova conta nem copiada a base de dados. O endereço pode mudar quando a rede atribuir outro IP.

Apenas a interface é disponibilizada na rede. A API na porta 8001 e o PostgreSQL na 55432 continuam ligados a `127.0.0.1`. A origem LAN exata é acrescentada ao processo da API sem alterar `.env`; as restantes verificações de origem e sessão mantêm-se. Perfis que exijam cookies HTTPS são recusados neste arranque HTTP, sem desativar essa proteção.

Se a página não abrir, confirme o Wi-Fi, a ausência de isolamento de dispositivos/rede de convidados e a Firewall do Windows. Foi preparado `Permitir-Rede.ps1`: execute-o num PowerShell como administrador, a partir da pasta do projeto, se precisar de autorizar a porta 5174. A regra permite apenas origens da sub-rede local e o executável Node. Não abra portas no router.

## Câmara com deteção de margens (HTTPS na rede local)

Os navegadores só permitem a câmara em direto em contexto seguro (HTTPS ou `localhost`). Por `http://IP:5174`, a digitalização usa a aplicação da câmara do sistema e a galeria, sem o contorno em direto. Para a experimentar no telemóvel:

1. No computador, abra `Iniciar-Rede-HTTPS.bat` (ou `Iniciar-Rede.ps1 -Https -Reiniciar`).
2. O script cria um certificado local autoassinado para o IP do computador, válido por 365 dias, em `.local/https`. Esta pasta está excluída do Git e o certificado não é instalado como autoridade de certificação. Depois arranca a interface em `https://IP:5174`.
3. No telemóvel, abra esse endereço e aceite uma vez o aviso de segurança:
   - iPhone: «Mostrar detalhes» › «visitar este site»;
   - Android/Chrome: «Avançadas» › «Continuar».
4. A API passa a aceitar apenas a origem HTTPS exata desse endereço. As restantes verificações de origem e de sessão mantêm-se.

O aviso aparece porque o certificado não é emitido por uma entidade pública. É adequado para testes na rede de confiança. Na instalação publicada (Cloudflare), o HTTPS é válido e não há aviso.

Este é o servidor de desenvolvimento para utilização na rede de confiança. Para acesso fora dessa rede, utilize o endereço HTTPS da instalação publicada. A autorização inicial Google mantém os requisitos do callback configurado; abrir a aplicação no telemóvel não altera as credenciais nem o callback OAuth.

A interface inclui uma alternativa criptográfica para gerar UUIDs em HTTP local, onde os navegadores não disponibilizam `crypto.randomUUID`. Continua a utilizar `crypto.getRandomValues`, sem recorrer a `Math.random`.
