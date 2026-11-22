import stat

import pytest

from kassist import config, llm
from kassist.config import DEFAULTS, Settings, _merge


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    path = tmp_path / "secrets.env"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(llm, "load_secrets", lambda: config.load_secrets(path))
    return path


def test_set_secret_keeps_other_lines_and_mode(secrets):
    secrets.write_text("KASSIST_KA_USER=a@b\nMISTRAL_API_KEY=old\n")
    secrets.chmod(0o600)
    config.set_secret("MISTRAL_API_KEY", "  new-key  ")
    assert config.load_secrets(secrets) == {"KASSIST_KA_USER": "a@b", "MISTRAL_API_KEY": "new-key"}
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    for bad in ("", "a\nb"):
        with pytest.raises(ValueError):
            config.set_secret("MISTRAL_API_KEY", bad)


def test_mistral_chat_request_and_answer(secrets, monkeypatch):
    config.set_secret("MISTRAL_API_KEY", "k123")
    seen = {}

    def fake(path, key, body=None, timeout=60):
        seen.update(path=path, key=key, body=body)
        return {"choices": [{"message": {"content": " Hallo! "}}]}
    monkeypatch.setattr(llm, "_request", fake)
    settings = Settings(_merge(DEFAULTS, {"llm": {"provider": "mistral", "model": "mistral-medium-2508"}}))
    assert llm.complete(settings, "Frage", json_mode=True) == "Hallo!"
    assert seen["path"] == "/chat/completions" and seen["key"] == "k123"
    assert seen["body"]["model"] == "mistral-medium-2508" and seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"] == [{"role": "user", "content": "Frage"}]


def test_mistral_needs_key_and_model(secrets, monkeypatch):
    settings = Settings(_merge(DEFAULTS, {"llm": {"provider": "mistral", "model": "m"}}))
    with pytest.raises(llm.LLMError, match="MISTRAL_API_KEY"):
        llm.complete(settings, "x")
    config.set_secret("MISTRAL_API_KEY", "k")
    settings.raw["llm"]["model"] = ""
    with pytest.raises(llm.LLMError, match="model"):
        llm.complete(settings, "x")


def test_model_list_drops_latest_aliases(secrets, monkeypatch):
    monkeypatch.setattr(llm, "_request", lambda path, key, body=None, timeout=60: {"data": [
        {"id": "mistral-large-latest"}, {"id": "mistral-medium-2508"}, {"id": "mistral-small-2506"},
        {"id": "mistral-embed", "capabilities": {"completion_chat": False}}]})
    assert set(llm.mistral_models("k")) == {"mistral-medium-2508", "mistral-small-2506"}


def test_command_provider_runs_the_command():
    settings = Settings(_merge(DEFAULTS, {"llm": {"provider": "command", "command": ["cat"]}}))
    assert llm.complete(settings, "echo me") == "echo me"
    settings.raw["llm"]["command"] = ["false"]
    with pytest.raises(llm.LLMError):
        llm.complete(settings, "x")


def test_fallback_order_and_errors(secrets, monkeypatch):
    config.set_secret("MISTRAL_API_KEY", "k")
    settings = Settings(_merge(DEFAULTS, {"llm": {"provider": "command", "command": ["false"], "model": "m"}}))
    monkeypatch.setattr(llm, "mistral_chat", lambda prompt, model, key=None, json_mode=False, timeout=120: "von Mistral")
    assert llm.order(settings) == ["command", "mistral"]
    assert llm.complete(settings, "x") == "von Mistral"                  # claude failed, Mistral answered
    settings.raw["llm"]["fallback"] = False
    with pytest.raises(llm.LLMError, match="command"):
        llm.complete(settings, "x")
    settings.raw["llm"].update(fallback=True, command=["cat"])
    assert llm.complete(settings, "echo") == "echo"                       # the active one answers first
    settings.raw["llm"]["provider"] = "mistral"
    assert llm.complete(settings, "echo") == "von Mistral"


def test_fallback_skips_a_provider_that_is_not_set_up(secrets):
    settings = Settings(_merge(DEFAULTS, {"llm": {"provider": "command", "command": ["false"], "model": ""}}))
    assert llm.order(settings) == ["command"]                              # no Mistral key/model
    settings.raw["llm"].update(provider="mistral")
    assert llm.order(settings) == ["mistral", "command"]


def test_command_runs_outside_the_project_and_finds_user_installs(tmp_path, monkeypatch):
    import tempfile
    real = llm.find_program.real
    bin_dir = tmp_path / "nvm" / "v1" / "bin"
    bin_dir.mkdir(parents=True)
    prog = bin_dir / "kassist-fake-ai"
    prog.write_text('#!/bin/sh\nread x; echo "$x $(pwd) $PATH"\n')
    prog.chmod(0o755)
    monkeypatch.setattr(llm, "EXTRA_BIN", (str(tmp_path / "nvm" / "*" / "bin"),))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")                          # like a desktop start: nvm not on PATH
    assert real("kassist-fake-ai") == str(prog) and real("kassist-missing") is None
    monkeypatch.setattr(llm, "find_program", real)
    out = llm.run_command(["kassist-fake-ai"], "frage\n").split(" ")
    assert out[0] == "frage" and out[1] == tempfile.gettempdir() and out[2].startswith(str(bin_dir) + ":")
    with pytest.raises(llm.LLMError, match="not found"):
        llm.run_command(["kassist-missing"], "x")


def test_default_command_has_no_tools():
    cmd = DEFAULTS["llm"]["command"]
    assert cmd[0] == "claude" and cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "" and DEFAULTS["llm"]["provider"] == "command"


def test_example_config_keeps_the_locked_down_claude_command():
    from pathlib import Path
    import yaml
    example = yaml.safe_load((Path(__file__).parents[1] / "examples" / "config.yaml").read_text(encoding="utf-8"))
    settings = Settings(_merge(DEFAULTS, example))
    cmd = settings["llm"]["command"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd   # not overridden by the example
