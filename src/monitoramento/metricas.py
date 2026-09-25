"""Métricas Prometheus (seção 9). Registro próprio: só o agendador as expõe (porta
interna 9100), nunca a API pública."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRO = CollectorRegistry(auto_describe=True)

CONSULTAS = Counter(
    "monitor_consultas",
    "Chamadas aos adaptadores de tribunal",
    ["tribunal", "sistema", "operacao", "resultado"],
    registry=REGISTRO,
)
DURACAO = Histogram(
    "monitor_consulta_duracao_segundos",
    "Duração das chamadas aos adaptadores (inclui espera do rate limiter)",
    ["tribunal", "sistema", "operacao"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    registry=REGISTRO,
)
ERROS = Counter(
    "monitor_erros",
    "Erros nas chamadas aos adaptadores, por tipo de exceção",
    ["tribunal", "sistema", "excecao"],
    registry=REGISTRO,
)
PROCESSOS_NOVOS = Counter(
    "monitor_processos_novos",
    "Processos inéditos gravados na base",
    ["tribunal", "sistema"],
    registry=REGISTRO,
)
ALERTAS = Counter(
    "monitor_alertas",
    "Alertas despachados",
    ["canal", "modalidade", "resultado"],
    registry=REGISTRO,
)
SENTINELA_OK = Gauge(
    "monitor_sentinela_ok",
    "1 se a última conferência da sentinela passou, 0 se falhou",
    ["tribunal", "sistema"],
    registry=REGISTRO,
)
SENTINELA_ULTIMA = Gauge(
    "monitor_sentinela_ultima_execucao_timestamp",
    "Momento (epoch) da última conferência da sentinela",
    ["tribunal", "sistema"],
    registry=REGISTRO,
)
ALARMES_ABERTOS = Gauge(
    "monitor_alarmes_abertos",
    "Alarmes de operação abertos",
    ["tipo"],
    registry=REGISTRO,
)
TRIBUNAL_ESTADO = Gauge(
    "monitor_tribunal_estado",
    "Estado de cada tribunal (1 no estado atual, 0 nos demais)",
    ["tribunal", "sistema", "estado"],
    registry=REGISTRO,
)

TIPOS_ALARME = ("sentinela", "taxa_erro", "volume_baixo", "sem_unidades")

NOMES = (
    "monitor_consultas_total",
    "monitor_consulta_duracao_segundos",
    "monitor_erros_total",
    "monitor_processos_novos_total",
    "monitor_alertas_total",
    "monitor_sentinela_ok",
    "monitor_sentinela_ultima_execucao_timestamp",
    "monitor_alarmes_abertos",
    "monitor_tribunal_estado",
)
