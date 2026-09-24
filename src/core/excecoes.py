"""Erros padronizados dos adaptadores (seção 4). O orquestrador decide a ação de cada um."""


class ErroAdaptador(Exception):
    """Base de todos os erros levantados por adaptadores de tribunal."""

    def __init__(self, tribunal: str, detalhe: str = "") -> None:
        self.tribunal = tribunal
        self.detalhe = detalhe
        mensagem = f"[{tribunal}] {type(self).__name__}"
        super().__init__(f"{mensagem}: {detalhe}" if detalhe else mensagem)


class TribunalIndisponivel(ErroAdaptador):
    """Timeout, 5xx ou manutenção. Ação: retry com backoff exponencial (1, 5, 15, 60 min)."""


class LimiteAtingido(ErroAdaptador):
    """429 ou bloqueio temporário. Ação: pausar o tribunal inteiro e reduzir a taxa."""

    def __init__(self, tribunal: str, detalhe: str = "", retry_after: float | None = None) -> None:
        super().__init__(tribunal, detalhe)
        self.retry_after = retry_after


class DesafioHumano(ErroAdaptador):
    """Página exige CAPTCHA ou verificação. Ação: parar o adaptador; NUNCA contornar."""


class LayoutAlterado(ErroAdaptador):
    """Seletores esperados não encontrados. Ação: parar o adaptador e abrir incidente."""


class ProcessoSigiloso(ErroAdaptador):
    """Segredo de justiça. Ação: registrar só o número, sem partes."""

    def __init__(self, tribunal: str, numero_cnj: str, detalhe: str = "") -> None:
        super().__init__(tribunal, detalhe or f"processo {numero_cnj} em segredo de justiça")
        self.numero_cnj = numero_cnj
