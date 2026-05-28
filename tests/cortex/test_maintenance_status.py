import io
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from cortex.maintenance_status import build_snapshot, main
from cortex.models import Conversation, ConversationMessage, Project, User
from launchpad.config import JobStatus
from launchpad.models import DogmeJob


NOW = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)


def _session_factory(db_session):
    Session = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=db_session.get_bind(),
    )
    return Session


def _seed_user_and_project(db_session):
    user = User(
        id="user-1",
        email="user@example.com",
        display_name="Example User",
        role="user",
        is_active=True,
    )
    project = Project(
        id="project-1",
        name="Example Project",
        owner_id=user.id,
    )
    db_session.add_all([user, project])
    db_session.commit()
    return user, project


def _add_conversation_message(db_session, *, user: User, project: Project, created_at: datetime):
    conversation = Conversation(
        id=f"conv-{created_at.timestamp()}",
        user_id=user.id,
        project_id=project.id,
        title="Maintenance test",
        created_at=created_at,
        updated_at=created_at,
    )
    message = ConversationMessage(
        id=f"msg-{created_at.timestamp()}",
        conversation_id=conversation.id,
        role="user",
        content="hello",
        seq=1,
        created_at=created_at,
    )
    db_session.add_all([conversation, message])
    db_session.commit()
    return conversation, message


def _add_job(
    db_session,
    *,
    user: User,
    project: Project,
    status: str,
    submitted_at: datetime,
    started_at: datetime | None = None,
):
    job = DogmeJob(
        run_uuid=f"run-{status.lower()}-{int(submitted_at.timestamp())}",
        project_id=project.id,
        user_id=user.id,
        workflow_key="dogme",
        workflow_display_name="dogme",
        sample_name="sample-a",
        input_directory="/tmp/input",
        status=status,
        submitted_at=submitted_at,
        started_at=started_at,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_empty_state_recommends_safe_to_restart(db_session):
    snapshot = build_snapshot(db_session, now=NOW)

    assert snapshot["users"] == []
    assert snapshot["jobs"] == []
    assert snapshot["chats"] == []
    assert snapshot["recommendation"]["status"] == "SAFE TO RESTART"


def test_all_clear_state_with_recent_user_activity_still_recommends_safe(db_session):
    user, project = _seed_user_and_project(db_session)
    _add_job(
        db_session,
        user=user,
        project=project,
        status=JobStatus.COMPLETED.value,
        submitted_at=NOW - timedelta(minutes=10),
        started_at=NOW - timedelta(minutes=20),
    )

    snapshot = build_snapshot(db_session, now=NOW)

    assert [entry["email"] for entry in snapshot["users"]] == [user.email]
    assert snapshot["jobs"] == []
    assert snapshot["chats"] == []
    assert snapshot["recommendation"]["status"] == "SAFE TO RESTART"


def test_busy_state_with_running_job_recommends_wait(db_session):
    user, project = _seed_user_and_project(db_session)
    _add_job(
        db_session,
        user=user,
        project=project,
        status=JobStatus.RUNNING.value,
        submitted_at=NOW - timedelta(minutes=30),
        started_at=NOW - timedelta(minutes=25),
    )

    snapshot = build_snapshot(db_session, now=NOW)

    assert len(snapshot["jobs"]) == 1
    assert snapshot["recommendation"]["status"] == "WAIT"
    assert snapshot["stale_jobs"] == []


def test_old_running_job_is_reported_as_stale_and_excluded_from_wait(db_session):
    user, project = _seed_user_and_project(db_session)
    old_job = _add_job(
        db_session,
        user=user,
        project=project,
        status=JobStatus.RUNNING.value,
        submitted_at=NOW - timedelta(days=9),
        started_at=NOW - timedelta(days=8),
    )

    snapshot = build_snapshot(db_session, now=NOW)

    assert snapshot["jobs"] == []
    assert [entry["run_uuid"] for entry in snapshot["stale_jobs"]] == [old_job.run_uuid]
    assert snapshot["recommendation"]["status"] == "SAFE TO RESTART"


def test_only_stale_jobs_recommend_safe_to_restart(db_session):
    user, project = _seed_user_and_project(db_session)
    _add_job(
        db_session,
        user=user,
        project=project,
        status=JobStatus.PENDING.value,
        submitted_at=NOW - timedelta(days=10),
        started_at=NOW - timedelta(days=9),
    )

    snapshot = build_snapshot(db_session, now=NOW)

    assert snapshot["jobs"] == []
    assert len(snapshot["stale_jobs"]) == 1
    assert snapshot["recommendation"]["status"] == "SAFE TO RESTART"


def test_active_job_max_age_parameter_marks_two_hour_job_as_stale(db_session):
    user, project = _seed_user_and_project(db_session)
    job = _add_job(
        db_session,
        user=user,
        project=project,
        status=JobStatus.RUNNING.value,
        submitted_at=NOW - timedelta(hours=3),
        started_at=NOW - timedelta(hours=2),
    )

    active_snapshot = build_snapshot(db_session, now=NOW, active_job_max_age_hours=168)
    stale_snapshot = build_snapshot(db_session, now=NOW, active_job_max_age_hours=1)

    assert [entry["run_uuid"] for entry in active_snapshot["jobs"]] == [job.run_uuid]
    assert stale_snapshot["jobs"] == []
    assert [entry["run_uuid"] for entry in stale_snapshot["stale_jobs"]] == [job.run_uuid]


def test_chat_active_state_recommends_wait(db_session):
    user, project = _seed_user_and_project(db_session)
    _add_conversation_message(
        db_session,
        user=user,
        project=project,
        created_at=NOW - timedelta(minutes=2),
    )

    snapshot = build_snapshot(db_session, now=NOW)

    assert len(snapshot["chats"]) == 1
    assert snapshot["jobs"] == []
    assert snapshot["recommendation"]["status"] == "WAIT"


def test_json_output_shape_is_parseable(db_session):
    output = io.StringIO()

    exit_code = main(["--json"], session_factory=_session_factory(db_session), stdout=output)

    payload = json.loads(output.getvalue())
    assert exit_code == 0
    assert set(payload) == {
        "users",
        "jobs",
        "stale_jobs",
        "chats",
        "transfers",
        "stale_transfers",
        "recommendation",
        "generated_at",
    }


def test_last_active_window_parameter_is_respected(db_session):
    user, project = _seed_user_and_project(db_session)
    _add_job(
        db_session,
        user=user,
        project=project,
        status=JobStatus.COMPLETED.value,
        submitted_at=NOW - timedelta(minutes=30),
        started_at=NOW - timedelta(minutes=35),
    )

    default_snapshot = build_snapshot(db_session, now=NOW)
    wide_snapshot = build_snapshot(db_session, now=NOW, last_active_window_minutes=60)

    assert default_snapshot["users"] == []
    assert [entry["email"] for entry in wide_snapshot["users"]] == [user.email]


def test_quiet_mode_outputs_single_recommendation_line(db_session):
    output = io.StringIO()

    exit_code = main(["--quiet"], session_factory=_session_factory(db_session), stdout=output)

    assert exit_code == 0
    assert output.getvalue().strip() == "SAFE TO RESTART"


def test_broken_pipe_guard_returns_zero_without_breaking_normal_output(db_session):
    class BrokenPipeWriter:
        def write(self, _text):
            raise BrokenPipeError()

    broken_exit_code = main([], session_factory=_session_factory(db_session), stdout=BrokenPipeWriter())
    normal_output = io.StringIO()
    normal_exit_code = main([], session_factory=_session_factory(db_session), stdout=normal_output)

    assert broken_exit_code == 0
    assert normal_exit_code == 0
    assert "=== Summary recommendation ===" in normal_output.getvalue()
