"""
Cortex-specific test fixtures.

Provides pre-built conversation histories, block lists, and helper factories
for testing cortex/app.py functions.
"""

import json
import pytest

from tests.conftest import FakeBlock


# ---------------------------------------------------------------------------
# Conversation history fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def encode_conversation_history():
    """A conversation history that includes ENCODE experiment data."""
    return [
        {"role": "user", "content": "Search ENCODE for K562 experiments"},
        {"role": "assistant", "content": (
            "Found 150 experiments for K562. Here are the results:\n"
            "ENCSR123ABC - ChIP-seq - K562\n"
            "ENCSR456DEF - RNA-seq - K562\n"
            "Files include ENCFF789GHI and ENCFF012JKL from experiment ENCSR123ABC."
        )},
    ]


@pytest.fixture()
def dogme_conversation_history():
    """A conversation history referencing a Dogme DNA job."""
    return [
        {"role": "user", "content": "Run a DNA analysis on my POD5 files"},
        {"role": "assistant", "content": (
            "I'll set up a Dogme DNA pipeline for your sample.\n"
            "Sample name: C2C12r1\n"
            "Input: /data/pod5/\n"
            "Reference genome: mm39"
        )},
    ]


@pytest.fixture()
def history_blocks_with_job(make_block):
    """History blocks that include a completed EXECUTION_JOB block."""
    return [
        make_block(
            type="AGENT_PLAN",
            status="DONE",
            payload={
                "markdown": "Starting DNA analysis",
                "skill": "run_dogme_dna",
                "state": {
                    "active_skill": "run_dogme_dna",
                    "sample_name": "C2C12r1",
                    "work_dir": "/tmp/agoutic/users/testuser/testproject/workflow1",
                    "sample_type": "DNA",
                    "reference_genome": "mm39",
                    "workflows": [
                        {"work_dir": "/tmp/agoutic/users/testuser/testproject/workflow1",
                         "sample_name": "C2C12r1"}
                    ],
                },
            },
        ),
        make_block(
            type="EXECUTION_JOB",
            status="DONE",
            payload={
                "run_uuid": "test-run-uuid-123",
                "sample_name": "C2C12r1",
                "mode": "DNA",
                "work_dir": "/tmp/agoutic/users/testuser/testproject/workflow1",
                "job_status": {"status": "COMPLETED"},
            },
        ),
    ]


@pytest.fixture()
def history_blocks_running_job(make_block):
    """History blocks with a RUNNING job (not yet complete)."""
    return [
        make_block(
            type="EXECUTION_JOB",
            status="RUNNING",
            payload={
                "run_uuid": "running-job-uuid",
                "sample_name": "sample1",
                "mode": "DNA",
                "work_dir": "/tmp/work",
                "job_status": {"status": "RUNNING"},
            },
        ),
    ]
