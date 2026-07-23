"""Regression coverage for multi-experiment ENCODE file request routing."""

from unittest.mock import patch

import pytest

from cortex.chat_context import ChatContext
from cortex.chat_stages.overrides import OverrideDetectionStage
from cortex.tag_parser import DATA_CALL_PATTERN


_MESSAGE = (
    "Download all released FASTQ files from these ENCODE experiments: "
    "ENCSR448TJV ENCSR432YKA ENCSR476STT. "
    "Show the file list and total size first, then download them into this project."
)


class TestEncodeFileRequestOverride:
    @pytest.mark.asyncio
    async def test_replaces_partial_model_calls_with_all_requested_experiments(self):
        ctx = ChatContext(
            message=_MESSAGE,
            user_msg_lower=_MESSAGE.lower(),
            active_skill="download_files",
            has_any_tags=True,
        )
        ctx.data_call_matches = [
            DATA_CALL_PATTERN.search(
                "[[DATA_CALL: consortium=encode, tool=get_files_by_type, accession=ENCSR432YKA]]"
            )
        ]
        generated_calls = [
            {
                "source_type": "consortium",
                "source_key": "encode",
                "tool": "get_files_by_type",
                "params": {"accession": accession},
            }
            for accession in ("ENCSR432YKA", "ENCSR476STT", "ENCSR448TJV")
        ]

        with patch(
            "cortex.chat_stages.overrides._auto_generate_data_calls",
            side_effect=[[], generated_calls],
        ):
            await OverrideDetectionStage().run(ctx)

        assert ctx.data_call_matches == []
        assert {call["params"]["accession"] for call in ctx.auto_calls} == {
            "ENCSR432YKA",
            "ENCSR476STT",
            "ENCSR448TJV",
        }

    @pytest.mark.asyncio
    async def test_replaces_partial_calls_when_previous_encode_data_is_injected(self):
        ctx = ChatContext(
            message=_MESSAGE,
            user_msg_lower=_MESSAGE.lower(),
            active_skill="ENCODE_Search",
            has_any_tags=True,
            injected_previous_data=True,
            injected_was_capped=False,
        )
        ctx.data_call_matches = [
            DATA_CALL_PATTERN.search(
                "[[DATA_CALL: consortium=encode, tool=get_files_by_type, accession=ENCSR432YKA]]"
            )
        ]
        generated_calls = [
            {
                "source_type": "consortium",
                "source_key": "encode",
                "tool": "get_files_by_type",
                "params": {"accession": accession},
            }
            for accession in ("ENCSR432YKA", "ENCSR476STT", "ENCSR448TJV")
        ]

        with patch(
            "cortex.chat_stages.overrides._auto_generate_data_calls",
            side_effect=[[], generated_calls],
        ):
            await OverrideDetectionStage().run(ctx)

        assert ctx.data_call_matches == []
        assert {call["params"]["accession"] for call in ctx.auto_calls} == {
            "ENCSR432YKA",
            "ENCSR476STT",
            "ENCSR448TJV",
        }