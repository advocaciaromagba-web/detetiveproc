"""Configuração da aplicação, lida de variáveis de ambiente e do arquivo .env."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://monitor:monitor@localhost:5432/monitor"
    redis_url: str = "redis://localhost:6379/0"
    opensearch_url: str = "http://localhost:9200"
    # Armazenamento de objetos S3 (SeaweedFS no compose) para o HTML bruto.
    s3_endpoint: str = "localhost:8333"
    s3_access_key: str = "monitor"
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket_bruto: str = "bruto"
    s3_tls: bool = False  # True quando o armazenamento estiver fora da rede interna
    hash_documento_chave: SecretStr | None = None

    smtp_host: str = "localhost"
    smtp_porta: int = 587
    smtp_usuario: str | None = None
    smtp_senha: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_remetente: str = "Detetiveproc <alertas@localhost>"
    # Recebe avisos de bloqueio de tribunal (DesafioHumano, LayoutAlterado).
    email_operacao: str | None = None

    # WhatsApp (API oficial da Meta): só o aviso de processo novo. Desligado sem as
    # credenciais do app (ID do número e token permanente de usuário do sistema).
    whatsapp_numero_id: str = ""
    whatsapp_token: SecretStr | None = None
    whatsapp_modelo: str = "novo_processo"  # nome do modelo aprovado na Meta
    whatsapp_idioma: str = "pt_BR"
    whatsapp_versao_api: str = "v21.0"

    # Cobrança pelo Asaas (Pix, boleto e cartão; assinaturas mensais/anuais). Desligada
    # sem a chave. Testes: ASAAS_URL=https://api-sandbox.asaas.com/v3 e chave do sandbox.
    asaas_url: str = "https://api.asaas.com/v3"
    asaas_api_key: SecretStr | None = None
    # Token que o Asaas envia no cabeçalho asaas-access-token do webhook (definido ao
    # cadastrar o webhook no painel do Asaas). Sem ele, o webhook fica desligado.
    asaas_webhook_token: SecretStr | None = None

    # Cadastro pelo próprio cliente (página pública do painel).
    painel_url_publica: str = "http://localhost:3100"  # base dos links enviados por e-mail
    cadastro_validade_horas: int = 48  # validade do link de confirmação
    # Só True quando a API NÃO for exposta diretamente (atrás do painel/proxy): aí o IP
    # do visitante vem em X-Forwarded-For e é usado no limite de tentativas.
    confiar_x_forwarded_for: bool = False
    # Razão social pelo CNPJ (dados abertos da Receita Federal via BrasilAPI).
    brasilapi_url: str = "https://brasilapi.com.br"

    # Assinaturas: dias após o vencimento em que o item segue monitorado (carência)
    # até ser suspenso por falta de pagamento.
    assinatura_carencia_dias: int = 7

    # Coleta nos tribunais (seção 5). O contato técnico vai no User-Agent do robô.
    coletor_contato: str = ""
    coletor_timeout: float = 30.0
    coletor_max_paginas: int = 20
    coletor_cache_horas: float = 24.0
    esaj_tjsp_url: str = "https://esaj.tjsp.jus.br"
    # O eproc só é registrado quando o leitor de páginas existir (fase 0).
    eproc_tjsp_ativo: bool = False
    eproc_tjsp_url: str = "https://eproc-consulta.tjsp.jus.br/consulta_1g"

    # Fuso do escritório: exibição e cálculo de prazos/audiências (dados guardados em UTC).
    fuso_escritorio: str = "America/Sao_Paulo"
    # Publicações do DJEN/Comunica (API pública do PJe). Descoberta sem acesso ao tribunal.
    djen_url: str = "https://comunicaapi.pje.jus.br"
    djen_itens_por_pagina: int = 100
    djen_max_paginas: int = 50
    djen_req_min: float = 20.0  # limite de requisições por minuto à API do DJEN
    djen_varredura_minutos: int = 60  # intervalo da varredura nacional
    djen_historico_dias: int = 365  # carga inicial ao cadastrar um nome/OAB
    djen_janela_dias: int = 30  # período máximo por consulta (buscas longas são fatiadas)

    # Análise das publicações por IA (Claude). Extrai tipo do ato, prazo e audiência.
    # DataJud (CNJ): classe, assunto, data de ajuizamento e grau pelo número do processo.
    # A chave é PÚBLICA (a mesma para todos, divulgada pelo CNJ); troque se o CNJ mudar.
    datajud_url: str = "https://api-publica.datajud.cnj.jus.br"
    datajud_api_key: SecretStr = SecretStr(
        "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="
    )
    datajud_req_min: float = 30.0

    # Opcional: o produto entrega a lista de processos; o resumo por IA fica desligado.
    ia_analise_ativa: bool = False
    anthropic_api_key: SecretStr | None = None
    ia_modelo: str = "claude-sonnet-5"
    ia_timeout: float = 60.0
    ia_max_tokens: int = 2000

    log_formato: Literal["json", "texto"] = "json"
    log_nivel: str = "INFO"
    metricas_porta: int = 9100  # servidor Prometheus do agendador (rede interna)


@lru_cache
def obter_settings() -> Settings:
    return Settings()
