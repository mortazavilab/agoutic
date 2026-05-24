import sys
import types
from types import SimpleNamespace

import pytest

if "pandas" not in sys.modules:
    sys.modules["pandas"] = types.SimpleNamespace(DataFrame=object, Series=object, read_csv=lambda *_args, **_kwargs: None)


class _FakeEncoding:
    def encode(self, text: str):
        return list(text or "")


if "tiktoken" not in sys.modules:
    sys.modules["tiktoken"] = types.SimpleNamespace(get_encoding=lambda _name: _FakeEncoding(), Encoding=_FakeEncoding)

from cortex.chat_context import ChatContext
from cortex.chat_stages.quick_exits import CapabilitiesStage, HelpCommandStage


def _make_ctx(message: str, *, active_skill: str = "welcome") -> ChatContext:
    return ChatContext(
        project_id="proj-test",
        message=message,
        skill=active_skill,
        active_skill=active_skill,
        model="default",
        user=SimpleNamespace(id="user-1"),
        user_msg_lower=message.lower(),
        user_block=SimpleNamespace(id="block-1"),
        session=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_capabilities_stage_no_longer_captures_bare_help_or_help_slash_command():
    stage = CapabilitiesStage()

    assert await stage.should_run(_make_ctx("help")) is False
    assert await stage.should_run(_make_ctx("/help /list files")) is False
    assert await stage.should_run(_make_ctx("How do I stage a sample on hpc3?")) is False


@pytest.mark.asyncio
async def test_help_stage_handles_bare_help_overview(monkeypatch):
    async def _fake_create_prompt_response(
        _session,
        _req,
        _user_block,
        _user_id,
        _active_skill,
        _model_name,
        markdown,
        *,
        prompt_type,
    ):
        return {
            "status": "ok",
            "agent_block": {
                "payload": {
                    "markdown": markdown,
                    "_debug": {"prompt_type": prompt_type},
                }
            },
        }

    monkeypatch.setattr("cortex.chat_stages.quick_exits._create_prompt_response", _fake_create_prompt_response)

    stage = HelpCommandStage()
    ctx = _make_ctx("help")

    assert await stage.should_run(ctx) is True

    await stage.run(ctx)

    payload = ctx.response["agent_block"]["payload"]
    assert "### Help: Prompting AGOUTIC" in payload["markdown"]
    assert payload["_debug"]["prompt_type"] == "help_command"


@pytest.mark.asyncio
async def test_help_stage_handles_slurm_help_command(monkeypatch):
    async def _fake_create_prompt_response(
        _session,
        _req,
        _user_block,
        _user_id,
        _active_skill,
        _model_name,
        markdown,
        *,
        prompt_type,
    ):
        return {
            "status": "ok",
            "agent_block": {
                "payload": {
                    "markdown": markdown,
                    "_debug": {"prompt_type": prompt_type},
                }
            },
        }

    monkeypatch.setattr("cortex.chat_stages.quick_exits._create_prompt_response", _fake_create_prompt_response)

    stage = HelpCommandStage()
    ctx = _make_ctx("/help remote slurm")

    assert await stage.should_run(ctx) is True

    await stage.run(ctx)

    assert ctx.response is not None
    payload = ctx.response["agent_block"]["payload"]
    assert "Remote SLURM Stage, Run, And Sync" in payload["markdown"]
    assert payload["_debug"]["prompt_type"] == "help_command"


@pytest.mark.asyncio
async def test_help_stage_handles_natural_language_stage_question(monkeypatch):
    async def _fake_create_prompt_response(
        _session,
        _req,
        _user_block,
        _user_id,
        _active_skill,
        _model_name,
        markdown,
        *,
        prompt_type,
    ):
        return {
            "status": "ok",
            "agent_block": {
                "payload": {
                    "markdown": markdown,
                    "_debug": {"prompt_type": prompt_type},
                }
            },
        }

    monkeypatch.setattr("cortex.chat_stages.quick_exits._create_prompt_response", _fake_create_prompt_response)

    stage = HelpCommandStage()
    ctx = _make_ctx("How do I stage a sample on hpc3?")

    assert await stage.should_run(ctx) is True

    await stage.run(ctx)

    payload = ctx.response["agent_block"]["payload"]
    assert "Staging A Sample On SLURM" in payload["markdown"]
    assert "Stage tumor-a on hpc3" in payload["markdown"]
    assert payload["_debug"]["prompt_type"] == "help_command"


@pytest.mark.asyncio
async def test_help_stage_handles_command_specific_question(monkeypatch):
    async def _fake_create_prompt_response(
        _session,
        _req,
        _user_block,
        _user_id,
        _active_skill,
        _model_name,
        markdown,
        *,
        prompt_type,
    ):
        return {
            "status": "ok",
            "agent_block": {
                "payload": {
                    "markdown": markdown,
                    "_debug": {"prompt_type": prompt_type},
                }
            },
        }

    monkeypatch.setattr("cortex.chat_stages.quick_exits._create_prompt_response", _fake_create_prompt_response)

    stage = HelpCommandStage()
    ctx = _make_ctx("/help /list files")

    assert await stage.should_run(ctx) is True

    await stage.run(ctx)

    payload = ctx.response["agent_block"]["payload"]
    assert "### Help: /list files" in payload["markdown"]
    assert "project root" in payload["markdown"].lower()
    assert payload["_debug"]["prompt_type"] == "help_command"


@pytest.mark.asyncio
async def test_help_stage_handles_generic_help_phrase(monkeypatch):
    async def _fake_create_prompt_response(
        _session,
        _req,
        _user_block,
        _user_id,
        _active_skill,
        _model_name,
        markdown,
        *,
        prompt_type,
    ):
        return {
            "status": "ok",
            "agent_block": {
                "payload": {
                    "markdown": markdown,
                    "_debug": {"prompt_type": prompt_type},
                }
            },
        }

    monkeypatch.setattr("cortex.chat_stages.quick_exits._create_prompt_response", _fake_create_prompt_response)

    stage = HelpCommandStage()
    ctx = _make_ctx("help me!")

    assert await stage.should_run(ctx) is True

    await stage.run(ctx)

    payload = ctx.response["agent_block"]["payload"]
    assert "### Help: Prompting AGOUTIC" in payload["markdown"]
    assert payload["_debug"]["prompt_type"] == "help_command"