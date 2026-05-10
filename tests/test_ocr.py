from app.services.ocr import (
    TesseractOcrProvider,
    UnavailableOcrProvider,
    build_ocr_provider_from_env,
)


def test_build_ocr_provider_returns_none_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("OCR_ENGINE", raising=False)

    assert build_ocr_provider_from_env() is None


def test_build_ocr_provider_returns_tesseract_provider(monkeypatch) -> None:
    monkeypatch.setenv("OCR_ENGINE", "tesseract")
    monkeypatch.setenv("OCR_DPI", "180")
    monkeypatch.setenv("OCR_LANG", "chi_sim")

    provider = build_ocr_provider_from_env()

    assert isinstance(provider, TesseractOcrProvider)
    assert provider.dpi == 180
    assert provider.lang == "chi_sim"


def test_build_ocr_provider_returns_unavailable_for_unknown_engine(monkeypatch) -> None:
    monkeypatch.setenv("OCR_ENGINE", "unknown")

    provider = build_ocr_provider_from_env()

    assert isinstance(provider, UnavailableOcrProvider)
    assert "不支持的 OCR_ENGINE" in provider.message

