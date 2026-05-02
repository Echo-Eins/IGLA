"""CLI settings and LLM backend selection."""
from __future__ import annotations

from pathlib import Path

from igla.cli import _build_settings, _make_llm, build_parser
from igla.config import load_settings
from igla.planner.llm_client import LMStudioClient, OllamaClient


def test_cli_can_select_ollama_backend_and_model(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--workspace",
            str(tmp_path),
            "--llm-provider",
            "ollama",
            "--ollama-url",
            "http://localhost:11434",
            "--ollama-model",
            "qwen2.5-coder:32b",
            "--ollama-key",
            "secret",
            "--ollama-keep-alive",
            "2h",
            "run",
            "hello",
        ]
    )

    settings = _build_settings(args)

    assert settings.llm_provider == "ollama"
    assert settings.ollama.base_url == "http://localhost:11434"
    assert settings.ollama.model == "qwen2.5-coder:32b"
    assert settings.ollama.api_key == "secret"
    assert settings.ollama.keep_alive == "2h"
    llm = _make_llm(settings)
    try:
        assert isinstance(llm, OllamaClient)
    finally:
        llm.close()


def test_cli_model_alias_targets_selected_provider(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--workspace",
            str(tmp_path),
            "--llm-provider",
            "ollama",
            "--model",
            "mistral-small3.2:latest",
            "chat",
        ]
    )

    settings = _build_settings(args)

    assert settings.ollama.model == "mistral-small3.2:latest"


def test_load_settings_reads_ollama_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("IGLA_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("IGLA_OLLAMA_URL", "http://ollama.local:11434")
    monkeypatch.setenv("IGLA_OLLAMA_MODEL", "gpt-oss:120b")
    monkeypatch.setenv("IGLA_OLLAMA_KEY", "cloud-key")

    settings = load_settings(tmp_path)

    assert settings.llm_provider == "ollama"
    assert settings.ollama.base_url == "http://ollama.local:11434"
    assert settings.ollama.model == "gpt-oss:120b"
    assert settings.ollama.api_key == "cloud-key"


def test_default_llm_backend_remains_lmstudio(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--workspace", str(tmp_path), "chat"])
    settings = _build_settings(args)

    llm = _make_llm(settings)
    try:
        assert settings.llm_provider == "lmstudio"
        assert isinstance(llm, LMStudioClient)
    finally:
        llm.close()
