from core.config import Settings
from entrega.email import Email, EnviadorMemoria, EnviadorSMTP

EMAIL = Email("destino@exemplo.com", "Assunto çã", "texto", "<p>html</p>")


def test_monta_mensagem_multipart() -> None:
    enviador = EnviadorSMTP("smtp.exemplo.com", 587, "Monitor <alertas@exemplo.com>")
    msg = enviador.montar(EMAIL)
    assert msg["To"] == "destino@exemplo.com"
    assert msg["From"] == "Monitor <alertas@exemplo.com>"
    assert msg["Subject"] == "Assunto çã"
    assert msg.get_content_type() == "multipart/alternative"
    tipos = [p.get_content_type() for p in msg.iter_parts()]
    assert tipos == ["text/plain", "text/html"]


def test_de_settings() -> None:
    settings = Settings(_env_file=None, smtp_host="h", smtp_porta=25, smtp_senha="s")  # type: ignore[call-arg]
    enviador = EnviadorSMTP.de_settings(settings)
    assert (enviador.host, enviador.porta) == ("h", 25)


async def test_enviador_memoria() -> None:
    enviador = EnviadorMemoria(falhar_para={"falha@x.com"})
    await enviador.enviar(EMAIL)
    assert enviador.enviados == [EMAIL]
    try:
        await enviador.enviar(Email("falha@x.com", "a", "b", "c"))
    except ConnectionError:
        pass
    else:
        raise AssertionError("deveria falhar")
