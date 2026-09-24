import core
from adaptadores import base


def test_base_reexporta_contrato_do_core() -> None:
    assert base.AdaptadorTribunal is core.AdaptadorTribunal
    assert base.ProcessoDTO is core.ProcessoDTO
    assert base.DesafioHumano is core.DesafioHumano
