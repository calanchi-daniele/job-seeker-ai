"""The API client: configuration checks and the single request path.

There is no server to test - the endpoint is external - so what matters is that a
request carries the configured model and nothing endpoint-specific, that a host
without strict structured outputs still yields a verdict, and that a missing
setting is reported clearly rather than as a bare 401.

No model is called: the client is replaced with a recorder that keeps the kwargs
it would have sent.
"""

import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest
from openai import BadRequestError
from pydantic import BaseModel

from job_seeker_ai import config, llm_clients
from job_seeker_ai.exceptions import LLMConfigError


class _Verdict(BaseModel):
    ok: bool = False


class _Message:
    """A reply, in whichever shape the endpoint produced it."""

    def __init__(self, parsed=None, content=None, reasoning_content=None):
        self.parsed = parsed
        self.content = content
        self.reasoning_content = reasoning_content
        self.refusal = None


class _Response:
    def __init__(self, message):
        self.choices = [type("_Choice", (), {"message": message})()]


class _Completions:
    def __init__(self, recorder):
        self._recorder = recorder

    async def parse(self, **kwargs):
        self._recorder.calls.append(("parse", kwargs))
        if self._recorder.reject_schema:
            raise _bad_request()
        return _Response(self._recorder.reply())

    async def create(self, **kwargs):
        self._recorder.calls.append(("create", kwargs))
        return _Response(self._recorder.reply())


class _Recorder:
    """Stands in for ``aclient`` and remembers what would have been sent.

    ``reject_schema`` makes ``parse`` fail the way a host without strict
    structured outputs does, so the fallback can be exercised.
    """

    def __init__(self, parsed=None, content=None, reasoning_content=None, reject_schema=False):
        self.calls = []
        self.chat = type("_Chat", (), {"completions": _Completions(self)})()
        self.reject_schema = reject_schema
        self._reply = _Message(parsed=parsed, content=content, reasoning_content=reasoning_content)

    def reply(self):
        return self._reply


def _bad_request():
    """A 400 shaped like the one a host returns for an unsupported field."""
    request = httpx.Request("POST", "https://api.example.test/v1/chat/completions")
    return BadRequestError("response_format is not supported", response=httpx.Response(400, request=request), body=None)


def _record(monkeypatch, recorder):
    monkeypatch.setattr(llm_clients, "aclient", recorder)
    return recorder


async def _send(schema=_Verdict):
    return await llm_clients.complete_structured(
        schema,
        [{"role": "user", "content": "hello"}],
        temperature=0.1,
        max_tokens=200,
    )


# --- what goes on the wire ----------------------------------------------


def test_the_configured_model_is_forwarded(monkeypatch):
    monkeypatch.setattr(config, "LLM_MODEL", "chosen-model")
    recorder = _record(monkeypatch, _Recorder(parsed=_Verdict(ok=True)))

    asyncio.run(_send())

    assert recorder.calls[0][1]["model"] == "chosen-model"


def test_requests_carry_no_endpoint_specific_extras(monkeypatch):
    """Only standard OpenAI fields: there is no local runtime left to please."""
    recorder = _record(monkeypatch, _Recorder(parsed=_Verdict(ok=True)))

    asyncio.run(_send())

    assert "extra_body" not in recorder.calls[0][1]


def test_the_temperature_and_token_limit_are_passed_through(monkeypatch):
    recorder = _record(monkeypatch, _Recorder(parsed=_Verdict(ok=True)))

    asyncio.run(_send())

    kwargs = recorder.calls[0][1]
    assert kwargs["temperature"] == 0.1
    assert kwargs["max_tokens"] == 200


# --- the structured-output fallback --------------------------------------


def test_a_rejected_schema_falls_back_to_plain_json(monkeypatch):
    recorder = _record(monkeypatch, _Recorder(reject_schema=True, content='{"ok": true}'))

    verdict = asyncio.run(_send())

    assert [kind for kind, _ in recorder.calls] == ["parse", "create"]
    assert recorder.calls[1][1]["response_format"] == {"type": "json_object"}
    assert verdict == _Verdict(ok=True)


def test_the_fallback_shows_the_shape_by_example(monkeypatch):
    """json_object names no fields, and DeepSeek additionally requires the word
    json in the prompt, so the retry carries a worked example of the shape."""
    recorder = _record(monkeypatch, _Recorder(reject_schema=True, content='{"ok": true}'))

    asyncio.run(_send())

    instruction = recorder.calls[1][1]["messages"][-1]
    assert instruction["role"] == "user"
    assert "desired output json" in instruction["content"]
    assert '{"ok": false}' in instruction["content"]


def test_the_fallback_leaves_the_first_attempt_alone(monkeypatch):
    """The schema attempt keeps the caller's messages verbatim, and the caller's
    list is never mutated by the retry."""
    recorder = _record(monkeypatch, _Recorder(reject_schema=True, content='{"ok": true}'))
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}]

    asyncio.run(llm_clients.complete_structured(_Verdict, messages, temperature=0.1, max_tokens=2000))

    assert recorder.calls[0][1]["messages"] == messages
    assert len(messages) == 2


def test_the_example_is_derived_from_the_schema():
    """Not hand-written: a nested list of models has to come out right, so a new
    evaluator gets a correct example without anyone maintaining one."""

    class _Item(BaseModel):
        name: str = ""
        score: int = 0

    class _Report(BaseModel):
        items: list[_Item] = []

    assert llm_clients._json_example(_Report) == {"items": [{"name": "...", "score": 0}]}


def test_a_connection_failure_is_not_retried(monkeypatch):
    """Only a rejected schema falls back; a dead endpoint must fail fast."""

    class _Broken:
        calls = []

        class chat:
            class completions:
                @staticmethod
                async def parse(**kwargs):
                    raise ConnectionError("connection refused")

    _record(monkeypatch, _Broken())

    with pytest.raises(ConnectionError):
        asyncio.run(_send())


# --- reading the reply ---------------------------------------------------


def test_an_already_parsed_reply_is_returned_as_is(monkeypatch):
    _record(monkeypatch, _Recorder(parsed=_Verdict(ok=True)))

    assert asyncio.run(_send()) == _Verdict(ok=True)


def test_json_in_content_is_accepted(monkeypatch):
    """The json_object fallback leaves the object in ``content``."""
    _record(monkeypatch, _Recorder(content='{"ok": true}'))

    assert asyncio.run(_send()) == _Verdict(ok=True)


def test_json_in_reasoning_content_is_accepted(monkeypatch):
    """A reasoning model can leave ``content`` empty and put it there instead."""
    _record(monkeypatch, _Recorder(reasoning_content='{"ok": true}'))

    assert asyncio.run(_send()) == _Verdict(ok=True)


def test_an_empty_reply_raises(monkeypatch):
    _record(monkeypatch, _Recorder())

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(_send())

    assert "no usable object" in str(excinfo.value)


# --- configuration errors ------------------------------------------------


def test_every_missing_setting_is_named(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "LLM_MODEL", "")

    with pytest.raises(LLMConfigError) as excinfo:
        llm_clients.check_config()

    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        assert name in str(excinfo.value)


def test_a_complete_configuration_is_accepted(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_MODEL", "test-model")

    llm_clients.check_config()


# --- the resolved configuration -----------------------------------------


def _fresh_env(**env):
    """The current environment with every LLM_/DOTENV_ override removed."""
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "DOTENV_"))}
    return {**inherited, **env}


def _run_in_a_fresh_process(code, **env):
    """Run one snippet in a subprocess, where config resolves the env at import."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=_fresh_env(**env),
        check=True,
    ).stdout


def _config_in_a_fresh_process(**env):
    """Read the three client values in a subprocess.

    JSON rather than a joined string: all three are legitimately empty when
    nothing is set, and a joined string cannot tell an empty field from a missing
    one.
    """
    code = (
        "import json; from job_seeker_ai import config; "
        "print(json.dumps([config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL]))"
    )
    return json.loads(_run_in_a_fresh_process(code, **env))


def test_there_is_no_default_endpoint():
    """Aiming the pipeline at a provider has to be an explicit request."""
    assert _config_in_a_fresh_process() == ["", "", ""]


def test_the_configured_endpoint_is_used():
    resolved = _config_in_a_fresh_process(
        LLM_BASE_URL="https://api.example.test/v1",
        LLM_API_KEY="sk-test",
        LLM_MODEL="test-model",
    )

    assert resolved == ["https://api.example.test/v1", "sk-test", "test-model"]


def test_an_unconfigured_client_reports_itself_clearly():
    """The SDK raises on an empty key at construction, so building the client
    must not be what reports the problem: check_config has to name the knob."""
    code = (
        "from job_seeker_ai import llm_clients\n"
        "try:\n"
        "    llm_clients.check_config()\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__, exc)\n"
    )

    output = _run_in_a_fresh_process(code)

    assert output.startswith("LLMConfigError")
    assert "LLM_API_KEY" in output


# --- the .env file -------------------------------------------------------


def _dotenv_file(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_a_dotenv_file_supplies_the_overrides(tmp_path):
    """The file has to be real, or "copy to .env" in the docs is a lie."""
    path = _dotenv_file(tmp_path, "LLM_MODEL=from-dotenv\nLLM_API_KEY=from-dotenv\n")

    resolved = _config_in_a_fresh_process(DOTENV_PATH=path)

    assert resolved[1] == "from-dotenv"
    assert resolved[2] == "from-dotenv"


def test_the_real_environment_beats_the_dotenv_file(tmp_path):
    """An explicit export must win over a stale checkout of .env."""
    path = _dotenv_file(tmp_path, "LLM_MODEL=from-dotenv\n")

    resolved = _config_in_a_fresh_process(DOTENV_PATH=path, LLM_MODEL="from-shell")

    assert resolved[2] == "from-shell"


def test_a_missing_dotenv_file_is_not_an_error(tmp_path):
    """A repository with no .env must still import."""
    assert _config_in_a_fresh_process(DOTENV_PATH=str(tmp_path / "absent.env")) == ["", "", ""]
