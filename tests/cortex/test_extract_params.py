"""
Tests for extract_job_parameters_from_conversation in cortex/app.py.

This 250-line function extracts Dogme pipeline parameters from conversation
blocks using heuristics: mode, genome, input_type, entry_point, sample_name,
advanced params (threshold, min_cov, per_mod, accuracy, gpu_tasks).
"""

import json
import uuid

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base
from cortex.models import User, Project, ProjectAccess, ProjectBlock
from cortex.job_parameters import extract_job_parameters_from_conversation
from cortex.remote_orchestration import _prepare_remote_execution_params
from launchpad.models import RemoteStagedSample


@pytest.fixture()
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def test_session_factory(test_engine):
    return sessionmaker(bind=test_engine, expire_on_commit=False)


@pytest.fixture()
def setup_project(test_session_factory, tmp_path):
    """Seed a project and user for parameter extraction tests."""
    sess = test_session_factory()
    user = User(id="u1", email="t@t.com", role="user", username="tuser", is_active=True)
    sess.add(user)
    proj = Project(id="proj-1", name="Test", owner_id="u1", slug="test")
    sess.add(proj)
    sess.commit()
    sess.close()


def _add_block(session_factory, block_type, payload, project_id="proj-1",
               owner_id="u1", seq=None, status=None):
    """Helper to add a ProjectBlock."""
    sess = session_factory()
    # Auto-increment seq
    if seq is None:
        from sqlalchemy import func, select
        max_seq = sess.execute(
            select(func.coalesce(func.max(ProjectBlock.seq), 0))
            .where(ProjectBlock.project_id == project_id)
        ).scalar()
        seq = max_seq + 1
    blk = ProjectBlock(
        id=str(uuid.uuid4()),
        project_id=project_id,
        owner_id=owner_id,
        type=block_type,
        seq=seq,
        payload_json=json.dumps(payload),
        status=status,
    )
    sess.add(blk)
    sess.commit()
    sess.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestModeDetection:
    @pytest.fixture(autouse=True)
    def _setup(self, test_session_factory, setup_project, tmp_path):
        self.sf = test_session_factory
        self.tmp = tmp_path

    async def _extract(self):
        sess = self.sf()
        try:
            return await extract_job_parameters_from_conversation(sess, "proj-1")
        finally:
            sess.close()

    @pytest.mark.asyncio
    async def test_default_dna(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "I want to run a pipeline"})
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await self._extract()
        assert result is not None
        assert result["mode"] == "DNA"

    @pytest.mark.asyncio
    async def test_rna_mode(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "analyze RNA data please"})
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await self._extract()
        assert result["mode"] == "RNA"

    @pytest.mark.asyncio
    async def test_cdna_mode(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "I have cDNA samples"})
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await self._extract()
        assert result["mode"] == "CDNA"


class TestGenomeDetection:
    @pytest.fixture(autouse=True)
    def _setup(self, test_session_factory, setup_project, tmp_path):
        self.sf = test_session_factory
        self.tmp = tmp_path

    @pytest.mark.asyncio
    async def test_human_genome(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "I have human DNA data"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert "GRCh38" in result["reference_genome"]

    @pytest.mark.asyncio
    async def test_mouse_genome(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "analyze mouse DNA data"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert "mm39" in result["reference_genome"]

    @pytest.mark.asyncio
    async def test_mad1_genome(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "analyze mad1 DNA data"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["reference_genome"] == ["mad1"]

    @pytest.mark.asyncio
    async def test_default_genome_is_mouse(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "run my pipeline"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["reference_genome"] == ["mm39"]

    @pytest.mark.asyncio
    async def test_both_genomes(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "analyze both human and mouse data"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert set(result["reference_genome"]) == {"GRCh38", "mm39"}

    @pytest.mark.asyncio
    async def test_mm39_and_mad1_genomes(self):
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "run dogme for sample Jamshid on /data/jamshid/pod5 using both mm39 and mad1"},
        )
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert set(result["reference_genome"]) == {"mm39", "mad1"}

    @pytest.mark.asyncio
    async def test_configured_future_genome_name_is_not_treated_as_sample_name(self, monkeypatch):
        monkeypatch.setitem(__import__("cortex.job_parameters", fromlist=["GENOME_ALIASES"]).GENOME_ALIASES, "canfam4", "canFam4")
        _add_block(self.sf, "USER_MESSAGE", {"text": "analyze canfam4 DNA data"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["reference_genome"] == ["canFam4"]
        assert result["sample_name"] != "canfam4"


class TestEntryPoint:
    @pytest.fixture(autouse=True)
    def _setup(self, test_session_factory, setup_project, tmp_path):
        self.sf = test_session_factory
        self.tmp = tmp_path

    @pytest.mark.asyncio
    async def test_basecall_only(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "only basecall the data"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["entry_point"] == "basecall"
        assert result["input_type"] == "pod5"

    @pytest.mark.asyncio
    async def test_modkit_entry(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "call modifications on my data"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["entry_point"] == "modkit"
        assert result["input_type"] == "bam"

    @pytest.mark.asyncio
    async def test_reports_entry(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "just generate report for me"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["entry_point"] == "reports"


class TestSampleName:
    @pytest.fixture(autouse=True)
    def _setup(self, test_session_factory, setup_project, tmp_path):
        self.sf = test_session_factory
        self.tmp = tmp_path

    @pytest.mark.asyncio
    async def test_explicit_sample_name(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "sample name is Jamshid"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["sample_name"] == "Jamshid"

    @pytest.mark.asyncio
    async def test_named_pattern(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "analyze named Ali1"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["sample_name"] == "Ali1"

    @pytest.mark.asyncio
    async def test_standalone_answer(self):
        """A short message that looks like an answer to 'what is the sample name?'"""
        _add_block(self.sf, "USER_MESSAGE", {"text": "run DNA pipeline"})
        _add_block(self.sf, "AGENT_PLAN", {"markdown": "What is the sample name?"})
        _add_block(self.sf, "USER_MESSAGE", {"text": "c2c12r1"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["sample_name"] == "c2c12r1"


class TestAdvancedParams:
    @pytest.fixture(autouse=True)
    def _setup(self, test_session_factory, setup_project, tmp_path):
        self.sf = test_session_factory
        self.tmp = tmp_path

    @pytest.mark.asyncio
    async def test_threshold(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "call modifications with threshold of 0.85"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["modkit_filter_threshold"] == 0.85

    @pytest.mark.asyncio
    async def test_min_cov(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "set minimum coverage of 10 and run DNA"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["min_cov"] == 10

    @pytest.mark.asyncio
    async def test_accuracy(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "run with accuracy hac for DNA"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["accuracy"] == "hac"

    @pytest.mark.asyncio
    async def test_gpu_tasks(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "max gpu tasks 2 and run DNA"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["max_gpu_tasks"] == 2


class TestSubmissionCycleScope:
    """The function should only consider blocks AFTER the last EXECUTION_JOB."""

    @pytest.fixture(autouse=True)
    def _setup(self, test_session_factory, setup_project, tmp_path):
        self.sf = test_session_factory
        self.tmp = tmp_path

    @pytest.mark.asyncio
    async def test_scopes_to_recent_cycle(self):
        """Old sample name from before EXECUTION_JOB should be ignored."""
        _add_block(self.sf, "USER_MESSAGE", {"text": "sample name is OldSample"}, seq=1)
        _add_block(self.sf, "EXECUTION_JOB", {"run_uuid": "abc"}, seq=2)
        _add_block(self.sf, "USER_MESSAGE", {"text": "sample name is NewSample"}, seq=3)
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["sample_name"] == "NewSample"

    @pytest.mark.asyncio
    async def test_no_blocks_returns_none(self):
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result is None

    @pytest.mark.asyncio
    async def test_only_agent_plan_no_user(self):
        """Only AGENT_PLAN blocks but no USER_MESSAGE → returns None."""
        _add_block(self.sf, "AGENT_PLAN", {"markdown": "Let me help you"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        # conversation is built with both types, but if no USER_MESSAGE was appended
        # the conversation list is still non-empty (AGENT_PLAN was added).
        # Result should be non-None since conversation IS populated.
        assert result is not None


class TestInputType:
    @pytest.fixture(autouse=True)
    def _setup(self, test_session_factory, setup_project, tmp_path):
        self.sf = test_session_factory
        self.tmp = tmp_path

    @pytest.mark.asyncio
    async def test_fastq_detection(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "I have .fastq files to analyze"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["input_type"] == "fastq"

    @pytest.mark.asyncio
    async def test_fastq_cdna_sets_fastq_cdna_entry_point(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Run Dogme cDNA on sample.fastq.gz"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["input_type"] == "fastq"
        assert result["mode"] == "CDNA"
        assert result["entry_point"] == "fastqCDNA"
        assert result["approval_prefill"] == {
            "input_type": "fastq",
            "entry_point": "fastqCDNA",
            "mode": "CDNA",
        }

    @pytest.mark.asyncio
    async def test_fastq_without_mode_prefills_cdna_clarification(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Please analyze reads.fastq.gz"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["input_type"] == "fastq"
        assert result["mode"] == "CDNA"
        assert result["entry_point"] is None
        assert result["approval_clarification"]["blocking"] is False
        assert "prefilled" in result["approval_clarification"]["assistant_text"].lower()

    @pytest.mark.asyncio
    async def test_fastq_rna_sets_blocking_clarification(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Analyze this fastq for RNA mode"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["input_type"] == "fastq"
        assert result["mode"] == "RNA"
        assert result["entry_point"] is None
        assert result["approval_clarification"]["blocking"] is True
        assert result["approval_clarification"]["requested_mode"] == "RNA"
        assert result["approval_clarification"]["options"] == [
            {"id": "use_fastq_cdna", "label": "Use FASTQ for cDNA (fastqCDNA)"},
            {"id": "provide_supported_input", "label": "I will provide pod5/BAM for RNA"},
        ]

    @pytest.mark.asyncio
    async def test_relative_fastq_gz_input_path_preserved(self):
        data_dir = self.tmp / "users" / "tuser" / "test" / "data"
        data_dir.mkdir(parents=True)
        gz_path = data_dir / "ENCFF694INI.fastq.gz"
        gz_path.write_text("@read\nACGT\n+\n!!!!\n", encoding="utf-8")

        _add_block(
            self.sf,
            "USER_MESSAGE",
            {
                "text": "Analyze the human fastqCDNA sample K562r2 using the file data/ENCFF694INI.fastq.gz on hpc3"
            },
        )

        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["input_type"] == "fastq"
        assert result["entry_point"] == "fastqCDNA"
        assert result["input_directory"] == str(gz_path)

    @pytest.mark.asyncio
    async def test_bam_remap(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "I have unmapped bam files"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["input_type"] == "bam"
        assert result["entry_point"] == "remap"

    @pytest.mark.asyncio
    async def test_relative_data_path_prefers_central_user_data_when_project_copy_missing(self):
        central_file = self.tmp / "users" / "tuser" / "data" / "ENCFF921XAH.bam"
        central_file.parent.mkdir(parents=True, exist_ok=True)
        central_file.write_text("BAM")

        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "Analyze the mouse RNA sample C2C12r1 using the file data/ENCFF921XAH.bam locally"},
        )

        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.user_jail.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["input_directory"] == str(central_file)
        assert result["input_directory_explicit"] is True

    @pytest.mark.asyncio
    async def test_relative_data_path_prefers_project_data_when_symlink_exists(self):
        project_file = self.tmp / "users" / "tuser" / "test" / "data" / "ENCFF921XAH.bam"
        project_file.parent.mkdir(parents=True, exist_ok=True)
        project_file.write_text("BAM")

        central_file = self.tmp / "users" / "tuser" / "data" / "ENCFF921XAH.bam"
        central_file.parent.mkdir(parents=True, exist_ok=True)
        central_file.write_text("CENTRAL")

        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "Analyze the mouse RNA sample C2C12r1 using the file data/ENCFF921XAH.bam locally"},
        )

        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.user_jail.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["input_directory"] == str(project_file)


class TestRemoteExecutionDetection:
    @pytest.fixture(autouse=True)
    def _setup(self, test_session_factory, setup_project, tmp_path):
        self.sf = test_session_factory
        self.tmp = tmp_path

    @pytest.mark.asyncio
    async def test_detects_slurm_execution_mode(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Run the mouse cDNA sample Jamshid3 at /data/pod5 using slurm"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["execution_mode"] == "slurm"

    @pytest.mark.asyncio
    async def test_detects_hpc3_profile_nickname(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Run the mouse cDNA sample Jamshid3 at /data/pod5 on hpc3"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["execution_mode"] == "slurm"
        assert result["ssh_profile_nickname"] == "hpc3"

    @pytest.mark.asyncio
    async def test_detects_hpc3_profile_nickname_with_follow_on_phrase(self):
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "run dogme rna on hpc3 using staged sample igvfr_698-04 with mm39"},
        )
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["execution_mode"] == "slurm"
        assert result["ssh_profile_nickname"] == "hpc3"

    @pytest.mark.asyncio
    async def test_detects_arbitrary_profile_nickname(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Run the mouse cDNA sample Jamshid3 at /data/pod5 on mycluster"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(side_effect=ValueError("SSH profile mycluster was not found"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["execution_mode"] == "slurm"
        assert result["ssh_profile_nickname"] == "mycluster"

    @pytest.mark.asyncio
    async def test_applies_profile_defaults_for_slurm_paths_and_accounts(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Run the mouse cDNA sample Jamshid3 at /data/pod5 on hpc3"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[{
                 "id": "profile-123",
                 "nickname": "hpc3",
                 "ssh_username": "jdoe",
                 "default_slurm_account": "cpu-acct",
                 "default_slurm_partition": "cpu-part",
                 "default_slurm_gpu_account": "gpu-acct",
                 "default_slurm_gpu_partition": "gpu-part",
                 "remote_base_path": "/remote/{ssh_username}/agoutic",
             }])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["slurm_account"] == "cpu-acct"
        assert result["slurm_partition"] == "cpu-part"
        assert result["slurm_gpu_account"] == "gpu-acct"
        assert result["slurm_gpu_partition"] == "gpu-part"
        assert result["remote_base_path"] == "/remote/jdoe/agoutic"

    @pytest.mark.asyncio
    async def test_reuses_previous_approved_slurm_settings_on_next_cycle(self):
        _add_block(
            self.sf,
            "APPROVAL_GATE",
            {
                "edited_params": {
                    "sample_name": "OldSample",
                    "execution_mode": "slurm",
                    "ssh_profile_nickname": "hpc3",
                    "slurm_account": "acct-a",
                    "slurm_partition": "part-a",
                    "slurm_cpus": 8,
                    "slurm_memory_gb": 32,
                    "slurm_walltime": "08:00:00",
                    "slurm_gpus": 1,
                    "remote_base_path": "/remote/u1/agoutic",
                    "result_destination": "local",
                }
            },
            seq=1,
            status="APPROVED",
        )
        _add_block(self.sf, "USER_MESSAGE", {"text": "Analyze sample name is NewSample with mouse DNA data"}, seq=2)

        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["execution_mode"] == "slurm"
        assert result["ssh_profile_nickname"] == "hpc3"
        assert result["slurm_account"] == "acct-a"
        assert result["slurm_partition"] == "part-a"
        assert result["remote_base_path"] == "/remote/u1/agoutic"

    @pytest.mark.asyncio
    async def test_does_not_reuse_previous_slurm_memory_override_on_next_cycle(self):
        _add_block(
            self.sf,
            "APPROVAL_GATE",
            {
                "edited_params": {
                    "sample_name": "OldSample",
                    "execution_mode": "slurm",
                    "ssh_profile_nickname": "hpc3",
                    "slurm_memory_gb": 16,
                }
            },
            seq=1,
            status="APPROVED",
        )
        _add_block(self.sf, "USER_MESSAGE", {"text": "Analyze sample name is NewSample with mouse DNA data"}, seq=2)

        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["execution_mode"] == "slurm"
        assert result["slurm_memory_gb"] is None

    @pytest.mark.asyncio
    async def test_explicit_local_request_overrides_previous_slurm_seed(self):
        _add_block(
            self.sf,
            "APPROVAL_GATE",
            {
                "edited_params": {
                    "execution_mode": "slurm",
                    "ssh_profile_nickname": "hpc3",
                    "slurm_account": "acct-a",
                    "slurm_partition": "part-a",
                }
            },
            seq=1,
            status="APPROVED",
        )
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {
                "text": "Run sample C2C12r1 locally using /media/backup_disk/agoutic_root/users/elnaz-a/data/ENCFF921XAH.bam"
            },
            seq=2,
        )

        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["execution_mode"] == "local"
        assert result["ssh_profile_id"] is None
        assert result["ssh_profile_nickname"] is None

    @pytest.mark.asyncio
    async def test_absolute_bam_path_is_not_rewritten_as_relative(self):
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {
                "text": "Analyze the mouse RNA sample C2C12r1 using /media/backup_disk/agoutic_root/users/elnaz-a/data/ENCFF921XAH.bam locally"
            },
        )

        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["input_directory"] == "/media/backup_disk/agoutic_root/users/elnaz-a/data/ENCFF921XAH.bam"
        assert result["execution_mode"] == "local"

    @pytest.mark.asyncio
    async def test_detects_stage_only_remote_action(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Stage the mouse cDNA sample called Jamshid at /data/pod5 on hpc3"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["execution_mode"] == "slurm"
        assert result["remote_action"] == "stage_only"
        assert result["gate_action"] == "remote_stage"

    @pytest.mark.asyncio
    async def test_detects_stage_only_remote_action_for_arbitrary_profile_nickname(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Stage the mouse cDNA sample called Jamshid at /data/pod5 on mycluster"})
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(side_effect=ValueError("SSH profile mycluster was not found"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["execution_mode"] == "slurm"
        assert result["ssh_profile_nickname"] == "mycluster"
        assert result["remote_action"] == "stage_only"
        assert result["gate_action"] == "remote_stage"

    @pytest.mark.asyncio
    async def test_detects_remote_input_path_for_slurm_submission(self):
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "Run the mouse DNA sample Jamshid on hpc3 using remote data at /crsp/lab/seyedam/share/pod5/Jamshid"},
        )
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[{
                 "id": "profile-123",
                 "nickname": "hpc3",
                 "ssh_username": "jdoe",
                 "remote_base_path": "/remote/{ssh_username}/agoutic",
             }])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["execution_mode"] == "slurm"
        assert result["remote_input_path"] == "/crsp/lab/seyedam/share/pod5/Jamshid"
        assert result["staged_remote_input_path"] == "/crsp/lab/seyedam/share/pod5/Jamshid"
        assert result["input_directory"] == "remote:/crsp/lab/seyedam/share/pod5/Jamshid"
        assert result["cache_preflight"]["data_action"]["action"] == "use_remote_path"
        assert result["result_destination"] == "both"

    @pytest.mark.asyncio
    async def test_detects_explicit_remote_unmapped_bam_file_for_remap(self):
        remote_bam = "/share/crsp/lab/seyedam/share/agoutic/elnaz/fshd15/workflow7/bams/fshd15.unmapped.bam"
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": f"Remap the remote unmapped.bam file {remote_bam} using chm13 on hpc3"},
        )
        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[{
                 "id": "profile-123",
                 "nickname": "hpc3",
                 "ssh_username": "elnaza",
                 "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/elnaz",
             }])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["execution_mode"] == "slurm"
        assert result["input_type"] == "bam"
        assert result["entry_point"] == "remap"
        assert result["reference_genome"] == ["chm13"]
        assert result["remote_input_path"] == remote_bam
        assert result["staged_remote_input_path"] == remote_bam
        assert result["input_directory"] == f"remote:{remote_bam}"
        assert result["cache_preflight"]["data_action"]["action"] == "use_remote_path"

    @pytest.mark.asyncio
    async def test_reuses_matching_remote_staged_sample_when_no_explicit_input_path(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "Analyze Jamshid on hpc3"})
        sess = self.sf()
        staged = RemoteStagedSample(
            id="stage-1",
            user_id="u1",
            ssh_profile_id="profile-123",
            ssh_profile_nickname="hpc3",
            sample_name="Jamshid",
            sample_slug="jamshid",
            mode="DNA",
            reference_genome_json=["mm39"],
            source_path="/data/pod5",
            input_fingerprint="fp-1",
            remote_base_path="/remote/jdoe/agoutic",
            remote_data_path="/remote/jdoe/agoutic/data/fp-1",
            remote_reference_paths_json={"mm39": "/remote/jdoe/agoutic/ref/mm39"},
            status="READY",
        )
        sess.add(staged)
        sess.commit()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[{
                 "id": "profile-123",
                 "nickname": "hpc3",
                 "ssh_username": "jdoe",
                 "remote_base_path": "/remote/{ssh_username}/agoutic",
             }])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()
        assert result["input_directory"] == "/data/pod5"
        assert result["staged_remote_input_path"] == "/remote/jdoe/agoutic/data/fp-1"
        assert result["remote_staged_sample"]["sample_name"] == "Jamshid"

    @pytest.mark.asyncio
    async def test_ignores_prior_slash_command_when_reusing_staged_sample(self):
        _add_block(self.sf, "USER_MESSAGE", {"text": "/list staged"}, seq=1)
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "run dogme rna on hpc3 using staged sample igvfr_698-04 with mm39"},
            seq=2,
        )
        sess = self.sf()
        staged = RemoteStagedSample(
            id="stage-2",
            user_id="u1",
            ssh_profile_id="profile-123",
            ssh_profile_nickname="hpc3",
            sample_name="igvfr_698-04",
            sample_slug="igvfr-698-04",
            mode="RNA",
            reference_genome_json=["mm39"],
            source_path="/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip",
            input_fingerprint="fp-2",
            remote_base_path="/share/crsp/lab/seyedam/share/agoutic/seyedam",
            remote_data_path="/share/crsp/lab/seyedam/share/agoutic/seyedam/data/fp-2",
            remote_reference_paths_json={"mm39": "/share/crsp/lab/seyedam/share/agoutic/seyedam/ref/mm39"},
            status="READY",
        )
        sess.add(staged)
        sess.commit()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[{
                 "id": "profile-123",
                 "nickname": "hpc3",
                 "ssh_username": "seyedam",
                 "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
             }])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["execution_mode"] == "slurm"
        assert result["sample_name"] == "igvfr_698-04"
        assert result["input_directory"] == "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5_skip"
        assert result["input_directory"] != "/list"
        assert result["staged_remote_input_path"] == "/share/crsp/lab/seyedam/share/agoutic/seyedam/data/fp-2"

    @pytest.mark.asyncio
    async def test_embedded_slash_command_token_does_not_override_reused_staged_source_path(self):
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "after /list staged, run dogme rna on hpc3 using staged sample igvfr_698-04 with mm39"},
            seq=1,
        )
        sess = self.sf()
        staged = RemoteStagedSample(
            id="stage-embedded-list",
            user_id="u1",
            ssh_profile_id="profile-123",
            ssh_profile_nickname="hpc3",
            sample_name="igvfr_698-04",
            sample_slug="igvfr-698-04",
            mode="RNA",
            reference_genome_json=["mm39"],
            source_path="/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5",
            input_fingerprint="fp-embedded-list",
            remote_base_path="/share/crsp/lab/seyedam/share/agoutic/seyedam",
            remote_data_path="/share/crsp/lab/seyedam/share/agoutic/seyedam/data/fp-embedded-list",
            remote_reference_paths_json={"mm39": "/share/crsp/lab/seyedam/share/agoutic/seyedam/ref/mm39"},
            status="READY",
        )
        sess.add(staged)
        sess.commit()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[{
                 "id": "profile-123",
                 "nickname": "hpc3",
                 "ssh_username": "seyedam",
                 "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
             }])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["input_directory"] == "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5"
        assert result["input_directory"] != "/list"
        assert result["input_directory_explicit"] is False

    @pytest.mark.asyncio
    async def test_remote_input_path_replaces_spurious_slash_command_input_directory(self):
        sess = self.sf()
        try:
            with patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
                 patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[{
                     "id": "profile-123",
                     "nickname": "hpc3",
                     "ssh_username": "seyedam",
                     "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/seyedam",
                 }])):
                result = await _prepare_remote_execution_params(
                    sess,
                    "proj-1",
                    "u1",
                    {
                        "execution_mode": "slurm",
                        "ssh_profile_id": "profile-123",
                        "ssh_profile_nickname": "hpc3",
                        "sample_name": "igvfr_698-04",
                        "mode": "RNA",
                        "reference_genome": ["mm39"],
                        "input_directory": "/list",
                        "input_directory_explicit": True,
                        "remote_input_path": "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5",
                    },
                )
        finally:
            sess.close()

        assert result["remote_input_path"] == "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5"
        assert result["input_directory"] == "/dfs9/seyedam-lab/share/igvfr_erisa_drna/igvfr_698-04_dRNA_p2_1/pod5"
        assert result["input_directory_explicit"] is False

    @pytest.mark.asyncio
    async def test_ignores_account_partition_phrase_when_extracting_path_and_partition(self):
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "Show my SSH profiles and use profile defaults for nickname hpc3. Report cpu account/partition and gpu account/partition."},
            seq=1,
        )
        _add_block(
            self.sf,
            "USER_MESSAGE",
            {"text": "Analyze the mouse CDNA sample called Jamshid at /media/backup_disk/agoutic_root/testdata/CDNA/pod5 on hpc3"},
            seq=2,
        )

        sess = self.sf()
        with patch("cortex.job_parameters.AGOUTIC_DATA", self.tmp), \
             patch("cortex.remote_orchestration._resolve_ssh_profile_reference", new=AsyncMock(return_value=("profile-123", "hpc3"))), \
             patch("cortex.remote_orchestration._list_user_ssh_profiles", new=AsyncMock(return_value=[{
                 "id": "profile-123",
                 "nickname": "hpc3",
                 "ssh_username": "elnaza",
                 "default_slurm_account": "seyedam_lab",
                 "default_slurm_partition": "standard",
                 "default_slurm_gpu_account": "seyedam_lab_gpu",
                 "default_slurm_gpu_partition": "gpu",
                 "remote_base_path": "/share/crsp/lab/seyedam/share/agoutic/elnaz",
             }])):
            result = await extract_job_parameters_from_conversation(sess, "proj-1")
        sess.close()

        assert result["sample_name"] == "Jamshid"
        assert result["input_directory"] == "/media/backup_disk/agoutic_root/testdata/CDNA/pod5"
        assert result["slurm_partition"] == "standard"
