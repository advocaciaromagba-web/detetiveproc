"""Os arquivos do Railway apontam para Dockerfiles que existem e mantêm o essencial."""

import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
# Serviço -> Root Directory configurado no Railway (docs/RAILWAY.md).
RAIZES = {"api": RAIZ, "agendador": RAIZ, "painel": RAIZ / "painel"}


def _config(servico: str) -> dict:  # type: ignore[type-arg]
    return tomllib.loads((RAIZ / "railway" / f"{servico}.toml").read_text(encoding="utf-8"))


def test_dockerfiles_existem() -> None:
    for servico, raiz in RAIZES.items():
        build = _config(servico)["build"]
        assert build["builder"] == "DOCKERFILE"
        assert (raiz / build["dockerfilePath"]).is_file(), servico


def test_api_migra_antes_e_usa_o_comando_da_imagem() -> None:
    deploy = _config("api")["deploy"]
    assert deploy["preDeployCommand"] == ["alembic upgrade head"]
    assert "startCommand" not in deploy  # host/porta vêm de UVICORN_* (ver Dockerfile)
    assert deploy["healthcheckPath"] == "/healthz"
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert "UVICORN_PORT=8000" in dockerfile


def test_agendador_e_painel() -> None:
    assert _config("agendador")["deploy"]["startCommand"] == "python -m agendador"
    assert _config("painel")["deploy"]["healthcheckPath"] == "/login"
