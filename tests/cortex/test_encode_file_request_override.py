"""Regression coverage for multi-experiment ENCODE file request routing."""

from unittest.mock import patch

import pytest

from cortex.chat_context import ChatContext
from cortex.chat_stages.overrides import OverrideDetectionStage
from cortex.chat_stages.tag_parsing import TagParsingStage
from cortex.tag_parser import DATA_CALL_PATTERN


_MESSAGE = (
    "Download all released FASTQ files from these ENCODE experiments: "
    "ENCSR448TJV ENCSR432YKA ENCSR476STT. "
    "Show the file list and total size first, then download them into this project."
)


class TestEncodeFileRequestOverride:
    @pytest.mark.asyncio
    async def test_keeps_fastq_file_call_when_only_experiments_are_injected(self):
        ctx = ChatContext(
            message="What are the FASTQs for these experiments?",
            active_skill="ENCODE_Search",
            raw_response=(
                "I will retrieve the file list.\n"
                "[[DATA_CALL: consortium=encode, tool=get_files_by_type, "
                "accession=ENCSR432YKA]]"
            ),
            injected_dfs={
                "kidney experiments": {
                    "columns": ["Accession", "Assay"],
                    "data": [{"Accession": "ENCSR432YKA", "Assay": "long read RNA-seq"}],
                    "metadata": {},
                },
            },
        )

        await TagParsingStage().run(ctx)

        assert [match.group(3) for match in ctx.data_call_matches] == ["get_files_by_type"]
        assert "suppressed_calls" not in ctx.inject_debug

    @pytest.mark.asyncio
    async def test_suppresses_fastq_file_call_when_file_rows_are_injected(self):
        ctx = ChatContext(
            message="Which of these FASTQs are paired-end?",
            active_skill="ENCODE_Search",
            raw_response=(
                "I will retrieve the file list.\n"
                "[[DATA_CALL: consortium=encode, tool=get_files_by_type, "
                "accession=ENCSR432YKA]]"
            ),
            injected_dfs={
                "ENCSR432YKA fastq files": {
                    "columns": ["Accession", "File Type"],
                    "data": [{"Accession": "ENCFF001ABC", "File Type": "fastq"}],
                    "metadata": {"file_type": "fastq"},
                },
            },
        )

        await TagParsingStage().run(ctx)

        assert ctx.data_call_matches == []
        assert ctx.inject_debug["suppressed_calls"] == ["get_files_by_type"]

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
            return_value=generated_calls,
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
            return_value=generated_calls,
        ):
            await OverrideDetectionStage().run(ctx)

        assert ctx.data_call_matches == []
        assert {call["params"]["accession"] for call in ctx.auto_calls} == {
            "ENCSR432YKA",
            "ENCSR476STT",
            "ENCSR448TJV",
        }