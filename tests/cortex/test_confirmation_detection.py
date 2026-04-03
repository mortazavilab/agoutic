import json
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cortex.chat_context import ChatContext


def _load_confirmation_stage_class():
    module_name = "tests._confirmation_detection_stage"
    module_path = Path(__file__).resolve().parents[2] / "cortex" / "chat_stages" / "confirmation_detection.py"

    stub_stage_module = types.ModuleType("cortex.chat_stages")
    stub_stage_module.register_stage = lambda stage: None
    stub_logging_module = types.ModuleType("common.logging_config")
    stub_logging_module.get_logger = lambda name: SimpleNamespace(info=lambda *args, **kwargs: None)
    stub_models_module = types.ModuleType("cortex.models")

    class _Field:
        def __eq__(self, _other):
            return self

        def desc(self):
            return self

    stub_models_module.ProjectBlock = type(
        "ProjectBlock",
        (),
        {
            "project_id": _Field(),
            "owner_id": _Field(),
            "type": _Field(),
            "status": _Field(),
            "seq": _Field(),
        },
    )

    original_modules = {
        "cortex.chat_stages": sys.modules.get("cortex.chat_stages"),
        "common.logging_config": sys.modules.get("common.logging_config"),
        "cortex.models": sys.modules.get("cortex.models"),
    }
    sys.modules["cortex.chat_stages"] = stub_stage_module
    sys.modules["common.logging_config"] = stub_logging_module
    sys.modules["cortex.models"] = stub_models_module
    try:
        spec = spec_from_file_location(module_name, module_path)
        module = module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module.ConfirmationDetectionStage, module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.mark.asyncio
async def test_confirmation_stage_resumes_pending_dataframe_action():
    ConfirmationDetectionStage, stage_module = _load_confirmation_stage_class()

    pending_block = SimpleNamespace(
        id="pending-1",
        payload_json=json.dumps({
            "summary": "Melt DF1 into long format",
            "action_call": {
                "source_type": "service",
                "source_key": "cortex",
                "tool": "melt_dataframe",
                "params": {"df_id": 1, "id_vars": ["sample"], "var_name": "modification", "value_name": "reads"},
            },
        }),
        status="PENDING",
    )
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = pending_block
    session = MagicMock()
    session.execute.return_value = scalar_result

    class _Query:
        def where(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

    stage_module.select = lambda *_args, **_kwargs: _Query()

    ctx = ChatContext(
        project_id="proj-1",
        message="yes",
        user=SimpleNamespace(id="user-1"),
        session=session,
    )

    stage = ConfirmationDetectionStage()
    assert await stage.should_run(ctx) is True
    await stage.run(ctx)

    assert ctx.auto_calls[0]["tool"] == "melt_dataframe"
    assert ctx.skip_llm_first_pass is True
    assert ctx.skip_tag_parsing is True
    assert ctx.skip_second_pass is True
    assert pending_block.status == "CONFIRMED"