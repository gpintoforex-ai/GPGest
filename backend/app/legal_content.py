"""Reviewable texts based on implemented flows, not a claim of legal certification."""

COOKIE_VERSION = "2026-10-19.1"
COOKIES = [
    {
        "name": "gpgest_session",
        "storage": "Cookie HttpOnly",
        "purpose": "Autenticação na empresa",
        "provider": "IAZELA",
        "duration": "8 horas",
    },
    {
        "name": "gpgest_company",
        "storage": "Cookie HttpOnly",
        "purpose": "Seleção da empresa e isolamento da sessão",
        "provider": "IAZELA",
        "duration": "8 horas",
    },
    {
        "name": "gpgest_platform",
        "storage": "Cookie HttpOnly; /api/platform",
        "purpose": "Autenticação da administração",
        "provider": "IAZELA",
        "duration": "2 horas",
    },
    {
        "name": "gpgest_google_nonce",
        "storage": "Cookie HttpOnly",
        "purpose": "Segurança da ligação Google solicitada pelo utilizador",
        "provider": "IAZELA",
        "duration": "10 minutos",
    },
    {
        "name": "iazela-theme",
        "storage": "localStorage",
        "purpose": "Tema escolhido pelo utilizador",
        "provider": "IAZELA",
        "duration": "Até limpar as preferências ou o armazenamento do navegador",
    },
    {
        "name": "gpgest.cash.preferences.*",
        "storage": "localStorage",
        "purpose": "Opções de contagem e paginação por utilizador/empresa",
        "provider": "IAZELA",
        "duration": "Até limpar as preferências ou o armazenamento do navegador",
    },
    {
        "name": "gpgest.read-aloud.preferences",
        "storage": "localStorage",
        "purpose": "Voz local e velocidade escolhidas para leitura acessível",
        "provider": "IAZELA",
        "duration": "Até limpar as preferências ou o armazenamento do navegador",
    },
    {
        "name": "iazela-bank-reconciliation-account",
        "storage": "sessionStorage",
        "purpose": "Conta selecionada no percurso de reconciliação",
        "provider": "IAZELA",
        "duration": "Até fechar o separador ou limpar as preferências",
    },
]

BASE_TERMS = [
    {
        "title": "Objeto e limites",
        "text": "A IAZELA apoia a gestão empresarial de tesouraria, documentos, compras e stock, em modo autónomo ou integrado. Não emite documentos fiscais nem se apresenta como software de faturação certificado, arquivo fiscal certificado, banco ou substituto do contabilista. A demonstração do site usa exemplos ilustrativos e não executa operações.",
    },
    {
        "title": "Acesso e responsabilidade da equipa",
        "text": "Cada pessoa deve usar uma conta individual e apenas as empresas e estabelecimentos autorizados. O administrador gere os acessos e os destinatários dos envios. Proteja as credenciais e reveja anexos, propostas e destinatários antes de confirmar operações. Não carregue dados que não sejam necessários para a finalidade empresarial.",
    },
    {
        "title": "Automatização e revisão",
        "text": "Os assistentes são funcionalidades automatizadas de apoio, sujeitas a erros e à qualidade dos dados. Extrações por QR, texto PDF e OCR local, correspondências e propostas devem ser revistas por uma pessoa. Uma sugestão não confirma uma compra, reconciliação ou lançamento no ERP. As capacidades efetivas dependem do conector configurado.",
    },
    {
        "title": "Integrações e comunicações",
        "text": "A ligação Google permite consultar emails e ficheiros autorizados e guardar digitalizações no Drive. O ERP consulta ou sincroniza os recursos configurados; os pagamentos integrados dependem de confirmação no respetivo prestador. O SMTP envia documentação aos destinatários revistos pela equipa. Abrir ligações externas implica contactar o respetivo fornecedor. A desconexão local interrompe novos acessos pela plataforma, mas não elimina documentos já guardados nesses serviços.",
    },
    {
        "title": "Encerramento, exportação e direitos",
        "text": "O encerramento contratual, a exportação da empresa e os direitos de proteção de dados são pedidos distintos. Pode exportar o seu perfil e solicitar exportação contratual ou encerramento no canal de direitos. O dossiê contabilístico e os relatórios têm o âmbito, período e permissões selecionados; não são uma exportação universal. Não há eliminação automática de documentos sujeitos a conservação obrigatória nem garantia de conservação ilimitada.",
    },
    {
        "title": "Direitos imperativos",
        "text": "As condições não excluem direitos imperativos, responsabilidade que não possa ser limitada por lei, nem os meios legais de reclamação. Alterações contratuais devem ser comunicadas nos termos aplicáveis; uma nova versão não altera retroativamente uma aceitação anterior.",
    },
]

DPA = [
    {
        "title": "Objeto, duração e instruções",
        "text": "O cliente determina as finalidades do tratamento dos seus documentos e os acessos da sua equipa; o prestador trata-os por instruções documentadas para executar o serviço durante o contrato e a fase acordada de exportação/devolução. Categorias: utilizadores e representantes, colaboradores, clientes e fornecedores; identificação, contactos, documentos financeiros, movimentos, anexos e registos de operações. Categorias especiais não são uma finalidade do serviço; evite a sua inclusão desnecessária.",
    },
    {
        "title": "Obrigações do subcontratante",
        "text": "Tratar apenas segundo instruções lícitas documentadas, incluindo transferências; assegurar confidencialidade das pessoas autorizadas, medidas de segurança adequadas ao risco, assistência aos direitos e às obrigações dos artigos 32.º a 36.º do RGPD; alertar o cliente sobre instruções que considere contrárias à proteção de dados. Notificar o cliente de violações sem demora injustificada e prestar a informação necessária à avaliação e comunicação.",
    },
    {
        "title": "Subcontratantes e transferências",
        "text": "O recurso a subcontratantes ulteriores exige autorização escrita específica ou geral do cliente, obrigações equivalentes e informação prévia de mudanças, permitindo oposição nos termos acordados. A lista publicada identifica os prestadores e localizações confirmadas. Uma localização de servidor, por si só, não prova ausência de transferências ou acessos de países terceiros. Aplicam-se as garantias acordadas e verificadas para cada fluxo.",
    },
    {
        "title": "Fim do serviço e demonstração",
        "text": "Na cessação, devolver ou eliminar os dados à escolha do cliente, salvo conservação legal aplicável, incluindo o tratamento das cópias e o ciclo das cópias de segurança acordado. Disponibilizar informação e permitir auditorias/inspeções nas condições do artigo 28.º. A demonstração deve usar dados fictícios; introduzir documentos reais exige o enquadramento e as instruções aplicáveis.",
    },
]


def documents(config):
    """Generated before approval, then archived verbatim as an immutable publication."""
    identity = config["identity"]
    contact = f"{identity['legal_name']} · NIF {identity['nif']} · {identity['address']} · {identity['registry']} · {identity['email']}"
    privacy = [
        {
            "title": "Quem trata os dados",
            "text": f"IAZELA é a marca utilizada por {contact}. Contacto de privacidade: {identity['privacy_email']}. O prestador é responsável pelos dados das suas contas, segurança e relação comercial. Nos documentos tratados por instrução da empresa cliente, esta determina as finalidades e o prestador atua como subcontratante conforme o acordo aplicável.",
        },
        {"title": "Finalidades, dados e fundamento", "text": config["processing_notice"]},
        {"title": "Destinatários e prestadores", "text": config["providers_notice"]},
        {"title": "Localizações e transferências", "text": config["transfers_notice"]},
        {"title": "Conservação", "text": config["retention_policy"]},
        {
            "title": "Direitos e contacto",
            "text": f"Pode pedir acesso, retificação, apagamento, limitação, oposição e, quando aplicável, portabilidade. Use o formulário de direitos ou {identity['privacy_email']}. A resposta é devida, em regra, num mês; a eventual prorrogação até dois meses adicionais exige justificação e comunicação no primeiro mês. A identidade é verificada proporcionalmente antes da divulgação ou execução. Não envie cópia de documento de identificação por defeito. Pode reclamar à CNPD em https://www.cnpd.pt/. Nos dados da empresa cliente, o pedido será encaminhado ao responsável competente, sem afastar os deveres próprios do prestador.",
        },
        {
            "title": "Consentimento e comunicações",
            "text": "A informação de privacidade não é um pedido de consentimento genérico. A aceitação dos termos é separada de consentimentos opcionais. Não existe inscrição em marketing nem envio de campanhas neste produto. As mensagens SMTP, pedidos de documentos e comunicações à contabilidade são operacionais e exigem revisão dos destinatários e dos anexos. O código do serviço não envia documentos para publicidade ou treino de modelos; as condições dos prestadores constam da informação acima.",
        },
    ]
    consumer_sections = []
    if config.get("audience") in ("consumers", "both"):
        consumer_sections = [
            {"title": "Condições para consumidores", "text": config.get("consumer_terms", "")}
        ]
    return {
        "prestador": {
            "title": "Prestador e contactos",
            "sections": [
                {
                    "title": "Marca e entidade contratante",
                    "text": contact + ". Marca: IAZELA. " + config["audience_notice"],
                }
            ],
        },
        "termos": {
            "title": "Termos e condições do serviço",
            "sections": BASE_TERMS
            + consumer_sections
            + [
                {
                    "title": "Âmbito e condições comerciais",
                    "text": config["audience_notice"] + "\n" + config["commercial_terms"],
                }
            ],
        },
        "privacidade": {"title": "Política de privacidade", "sections": privacy},
        "subcontratacao": {
            "title": "Acordo de tratamento de dados",
            "sections": DPA
            + [{"title": "Instruções e condições acordadas", "text": config["dpa_instructions"]}],
        },
        "reclamacoes": {
            "title": "Reclamações e resolução de litígios",
            "sections": [{"title": "Canais e âmbito", "text": config["complaints_notice"]}],
        },
        "acessibilidade": {
            "title": "Informação de acessibilidade",
            "sections": [{"title": "Estado e contacto", "text": config["accessibility_notice"]}],
        },
    }
