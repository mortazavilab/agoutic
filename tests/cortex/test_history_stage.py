import json
import sys
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.chat_context import ChatContext
from cortex.conversation_state import _build_conversation_state
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock


def _load_history_stage_class():
    module_path = Path(__file__).resolve().parents[2] / "cortex" / "chat_stages" / "history.py"
    spec = spec_from_file_location("tests._history_stage_module", module_path)
    module = module_from_spec(spec)
    original_chat_stages = sys.modules.get("cortex.chat_stages")
    stub_chat_stages = ModuleType("cortex.chat_stages")
    stub_chat_stages.register_stage = lambda _stage: None
    sys.modules["cortex.chat_stages"] = stub_chat_stages
    try:
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module.HistoryStage
    finally:
        if original_chat_stages is not None:
            sys.modules["cortex.chat_stages"] = original_chat_stages
        else:
            del sys.modules["cortex.chat_stages"]


HistoryStage = _load_history_stage_class()


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _add_block(session, *, seq: int, block_type: str, payload: dict, status: str = "DONE"):
    block_id = str(uuid.uuid4())
    session.add(
        ProjectBlock(
            id=block_id,
            project_id="proj-history",
            owner_id="user-history",
            seq=seq,
            type=block_type,
            status=status,
            payload_json=json.dumps(payload),
        )
    )
    return block_id


class TestHistoryStage:
    async def test_preserves_payload_json_for_dataframe_blocks(self):
        SL = _session_factory()
        session = SL()
        try:
            _add_block(session, seq=1, block_type="USER_MESSAGE", payload={"text": "how many HepG2 assays does ENCODE have?"})
            _add_block(
                session,
                seq=2,
                block_type="AGENT_PLAN",
                payload={
                    "markdown": "Found 1767 result(s).",
                    "_dataframes": {
                        "HepG2 (1767 results)": {
                            "columns": ["Assay", "Accession"],
                            "data": [
                                {"Assay": "long read RNA-seq", "Accession": "ENCSR1"},
                                {"Assay": "TF ChIP-seq", "Accession": "ENCSR2"},
                            ],
                            "metadata": {
                                "df_id": 1,
                                "label": "HepG2 (1767 results)",
                                "row_count": 1767,
                                "visible": True,
                            },
                        }
                    },
                },
            )
            _add_block(session, seq=3, block_type="USER_MESSAGE", payload={"text": "how many of these are long read RNA-seq ?"})
            session.commit()

            ctx = ChatContext(project_id="proj-history", session=session)
            await HistoryStage().run(ctx)

            assert len(ctx.history_blocks) == 3
            payload = get_block_payload(ctx.history_blocks[1])
            assert payload.get("_dataframes", {}).get("HepG2 (1767 results)", {}).get("metadata", {}).get("df_id") == 1
            assert ctx.conversation_history == [
                {"role": "user", "content": "how many HepG2 assays does ENCODE have?"},
                {"role": "assistant", "content": "Found 1767 result(s)."},
            ]
        finally:
            session.close()

    async def test_keeps_most_recent_history_rows_when_limit_applies(self):
        SL = _session_factory()
        session = SL()
        try:
            for seq in range(1, 76):
                _add_block(
                    session,
                    seq=seq,
                    block_type="USER_MESSAGE",
                    payload={"text": f"msg-{seq}"},
                )
            session.commit()

            ctx = ChatContext(project_id="proj-history", session=session)
            await HistoryStage().run(ctx)

            assert len(ctx.history_blocks) == 60
            assert ctx.history_blocks[0].seq == 16
            assert ctx.history_blocks[-1].seq == 75
        finally:
            session.close()

    async def test_rows_preserve_id_status_for_conversation_state_fast_path(self):
        SL = _session_factory()
        session = SL()
        try:
            _add_block(session, seq=1, block_type="USER_MESSAGE", payload={"text": "search k562"})
            plan_id = _add_block(
                session,
                seq=2,
                block_type="AGENT_PLAN",
                payload={
                    "state": {
                        "active_skill": "ENCODE_Search",
                        "known_dataframes": [],
                        "latest_dataframe": None,
                    },
                    "markdown": "Plan cached",
                },
            )
            pending_id = _add_block(
                session,
                seq=3,
                block_type="PENDING_ACTION",
                status="PENDING",
                payload={"summary": "Confirm next step"},
            )
            session.commit()

            ctx = ChatContext(project_id="proj-history", session=session)
            await HistoryStage().run(ctx)

            assert any(getattr(row, "id", "") == plan_id for row in ctx.history_blocks)
            assert any(
                getattr(row, "id", "") == pending_id and getattr(row, "status", "") == "PENDING"
                for row in ctx.history_blocks
            )

            # Regression guard: conversation state fast-path should no longer
            # crash on lightweight history rows missing identity/status fields.
            state = _build_conversation_state("ENCODE_Search", [], history_blocks=ctx.history_blocks)
            assert state.pending_action_id == pending_id
            assert state.pending_action_summary == "Confirm next step"
        finally:
            session.close()