"""O painel do Grafana e a configuração do Prometheus usam métricas que existem."""

import json
import re
from pathlib import Path

import yaml

from monitoramento import metricas
from monitoramento.metricas import NOMES, REGISTRO

RAIZ = Path(__file__).parents[2]


def _metricas_do_registro() -> set[str]:
    nomes = set()
    for familia in REGISTRO.collect():
        for amostra in familia.samples:
            nomes.add(amostra.name)
        nomes.add(familia.name)
    return nomes


def test_nomes_documentados_existem() -> None:
    metricas.CONSULTAS.labels("T", "s", "documento", "ok")  # cria séries para coletar
    metricas.DURACAO.labels("T", "s", "documento").observe(0.1)
    metricas.ERROS.labels("T", "s", "X")
    metricas.PROCESSOS_NOVOS.labels("T", "s")
    metricas.ALERTAS.labels("email", "imediato", "enviado")
    existentes = _metricas_do_registro() | {n.removesuffix("_total") for n in NOMES}
    for nome in NOMES:
        assert nome in existentes or f"{nome}_bucket" in existentes, nome


def test_dashboard_referencia_so_metricas_conhecidas() -> None:
    painel = json.loads((RAIZ / "docker/grafana/dashboards/monitor-processual.json").read_text())
    exprs = [t["expr"] for p in painel["panels"] for t in p.get("targets", [])]
    assert len(exprs) >= 7
    conhecidas = set(NOMES) | {f"{n}_bucket" for n in NOMES} | {f"{n}_count" for n in NOMES}
    for expr in exprs:
        usadas = set(re.findall(r"\bmonitor_[a-z_]+", expr))
        assert usadas, expr
        assert usadas <= conhecidas, (expr, usadas - conhecidas)
    for p in painel["panels"]:
        assert p["datasource"]["uid"] == "prometheus"


def test_prometheus_coleta_o_agendador() -> None:
    config = yaml.safe_load((RAIZ / "docker/prometheus/prometheus.yml").read_text())
    alvos = [
        a for job in config["scrape_configs"] for g in job["static_configs"] for a in g["targets"]
    ]
    assert "agendador:9100" in alvos
    fonte = yaml.safe_load(
        (RAIZ / "docker/grafana/provisioning/datasources/prometheus.yml").read_text()
    )
    assert fonte["datasources"][0]["uid"] == "prometheus"
    assert fonte["datasources"][0]["url"] == "http://prometheus:9090"
