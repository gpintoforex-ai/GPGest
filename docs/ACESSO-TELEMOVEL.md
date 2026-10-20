# Acesso pelo telemóvel na mesma rede

No computador, abra `Iniciar-Rede.bat`. Também pode executar `Iniciar-Rede.ps1` ou, se a aplicação já estiver aberta, `Iniciar-Rede.ps1 -Reiniciar`. O script mostra o endereço atual do computador; o telemóvel deve estar no mesmo Wi-Fi, com acesso aos restantes dispositivos dessa rede.

Abra no navegador do telemóvel o endereço apresentado, por exemplo `http://192.168.1.70:5174`. Para administração, acrescente `/plataforma`. Utilize as credenciais já existentes; não é criada uma nova conta nem copiada a base de dados. O endereço pode mudar quando a rede atribuir outro IP.

Apenas a interface é disponibilizada na rede. A API na porta 8001 e o PostgreSQL na 55432 continuam ligados a `127.0.0.1`. A origem LAN exata é acrescentada ao processo da API sem alterar `.env`; as restantes verificações de origem e sessão mantêm-se. Perfis que exijam cookies HTTPS são recusados neste arranque HTTP, sem desativar essa proteção.

Se a página não abrir, confirme o Wi-Fi, a ausência de isolamento de dispositivos/rede de convidados e a Firewall do Windows. Foi preparado `Permitir-Rede.ps1`: execute-o num PowerShell como administrador, a partir da pasta do projeto, se precisar de autorizar a porta 5174. A regra permite apenas origens da sub-rede local e o executável Node. Não abra portas no router.

Este é o servidor de desenvolvimento para utilização na rede de confiança. Para acesso fora dessa rede, utilize o endereço HTTPS da instalação publicada. A autorização inicial Google mantém os requisitos do callback configurado; abrir a aplicação no telemóvel não altera as credenciais nem o callback OAuth.

A interface inclui uma alternativa criptográfica para gerar UUIDs em HTTP local, onde os navegadores não disponibilizam `crypto.randomUUID`. Continua a utilizar `crypto.getRandomValues`, sem recorrer a `Math.random`.
