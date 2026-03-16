"""add_remote_execution_tables

Revision ID: b95a2c38062c
Revises: dfe8be4881a7
Create Date: 2026-03-16 12:32:47.022094

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b95a2c38062c'
down_revision: Union[str, Sequence[str], None] = 'dfe8be4881a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- New columns on dogme_jobs ---
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("execution_mode", sa.String(), server_default="local", nullable=False))
        batch_op.add_column(sa.Column("ssh_profile_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("slurm_job_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("slurm_state", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("slurm_account", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("slurm_partition", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("slurm_cpus", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("slurm_memory_gb", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("slurm_walltime", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("slurm_gpus", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("slurm_gpu_type", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("remote_work_dir", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("remote_output_dir", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("result_destination", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("transfer_state", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("run_stage", sa.String(), nullable=True))
        batch_op.create_index("ix_dogme_jobs_slurm_job_id", ["slurm_job_id"])

    # --- ssh_profiles ---
    op.create_table(
        "ssh_profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("nickname", sa.String(), nullable=True),
        sa.Column("ssh_host", sa.String(), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_username", sa.String(), nullable=False),
        sa.Column("auth_method", sa.String(), nullable=False),
        sa.Column("key_file_path", sa.String(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ssh_host", "ssh_username", name="uq_ssh_profile_user_host"),
    )
    op.create_index("ix_ssh_profiles_user_id", "ssh_profiles", ["user_id"])

    # --- slurm_defaults ---
    op.create_table(
        "slurm_defaults",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("ssh_profile_id", sa.String(), nullable=True),
        sa.Column("account", sa.String(), nullable=False),
        sa.Column("partition", sa.String(), nullable=False),
        sa.Column("cpus", sa.Integer(), nullable=False),
        sa.Column("memory_gb", sa.Integer(), nullable=False),
        sa.Column("walltime", sa.String(), nullable=False),
        sa.Column("gpus", sa.Integer(), nullable=False),
        sa.Column("gpu_type", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_slurm_defaults_user_project"),
    )
    op.create_index("ix_slurm_defaults_user_id", "slurm_defaults", ["user_id"])
    op.create_index("ix_slurm_defaults_project_id", "slurm_defaults", ["project_id"])

    # --- remote_path_configs ---
    op.create_table(
        "remote_path_configs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("ssh_profile_id", sa.String(), nullable=True),
        sa.Column("remote_input_path", sa.String(), nullable=True),
        sa.Column("remote_work_path", sa.String(), nullable=True),
        sa.Column("remote_output_path", sa.String(), nullable=True),
        sa.Column("remote_log_path", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "project_id", "ssh_profile_id", name="uq_remote_paths_user_project_profile"),
    )
    op.create_index("ix_remote_path_configs_user_id", "remote_path_configs", ["user_id"])
    op.create_index("ix_remote_path_configs_project_id", "remote_path_configs", ["project_id"])

    # --- run_audit_logs ---
    op.create_table(
        "run_audit_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_uuid", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("ssh_profile_id", sa.String(), nullable=True),
        sa.Column("slurm_account", sa.String(), nullable=True),
        sa.Column("slurm_job_id", sa.String(), nullable=True),
        sa.Column("resources_json", sa.Text(), nullable=True),
        sa.Column("result_destination", sa.String(), nullable=True),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_audit_logs_run_uuid", "run_audit_logs", ["run_uuid"])
    op.create_index("ix_run_audit_logs_user_id", "run_audit_logs", ["user_id"])

    # --- user_execution_preferences ---
    op.create_table(
        "user_execution_preferences",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("preferred_execution_mode", sa.String(), nullable=True),
        sa.Column("preferred_ssh_profile_id", sa.String(), nullable=True),
        sa.Column("preferred_result_destination", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_user_execution_preferences_user_id", "user_execution_preferences", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_execution_preferences")
    op.drop_table("run_audit_logs")
    op.drop_table("remote_path_configs")
    op.drop_table("slurm_defaults")
    op.drop_table("ssh_profiles")

    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_index("ix_dogme_jobs_slurm_job_id")
        batch_op.drop_column("run_stage")
        batch_op.drop_column("transfer_state")
        batch_op.drop_column("result_destination")
        batch_op.drop_column("remote_output_dir")
        batch_op.drop_column("remote_work_dir")
        batch_op.drop_column("slurm_gpu_type")
        batch_op.drop_column("slurm_gpus")
        batch_op.drop_column("slurm_walltime")
        batch_op.drop_column("slurm_memory_gb")
        batch_op.drop_column("slurm_cpus")
        batch_op.drop_column("slurm_partition")
        batch_op.drop_column("slurm_account")
        batch_op.drop_column("slurm_state")
        batch_op.drop_column("slurm_job_id")
        batch_op.drop_column("ssh_profile_id")
        batch_op.drop_column("execution_mode")
