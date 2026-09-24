import pytest

from core.seguranca import VARIAVEL_CHAVE, ChaveHashAusente, hash_documento

CHAVE = "chave-de-teste"


def test_hash_estavel_e_independente_de_pontuacao() -> None:
    a = hash_documento("529.982.247-25", CHAVE)
    assert a == hash_documento("52998224725", CHAVE)
    assert len(a) == 64
    assert "52998224725" not in a


def test_hash_depende_da_chave_e_do_documento() -> None:
    assert hash_documento("52998224725", CHAVE) != hash_documento("52998224725", "outra")
    assert hash_documento("52998224725", CHAVE) != hash_documento("11144477735", CHAVE)
    assert hash_documento("52998224725", CHAVE) == hash_documento("52998224725", CHAVE.encode())


def test_hash_le_chave_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VARIAVEL_CHAVE, CHAVE)
    assert hash_documento("52998224725") == hash_documento("52998224725", CHAVE)


def test_hash_sem_chave_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VARIAVEL_CHAVE, raising=False)
    with pytest.raises(ChaveHashAusente):
        hash_documento("52998224725")
    with pytest.raises(ChaveHashAusente):
        hash_documento("52998224725", "")
