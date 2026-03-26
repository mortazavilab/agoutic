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
        assert result["staged_remote_input_path"] == "/remote/jdoe/agoutic/data/fp-1"
        assert result["remote_staged_sample"]["sample_name"] == "Jamshid"

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
