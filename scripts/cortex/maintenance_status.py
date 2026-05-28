#!/usr/bin/env python3
"""
Read-only operator report for current AGOUTIC maintenance readiness.

Run this before any planned restart, deployment, or migration to see whether
recent chat activity, running jobs, and active transfers suggest it is safe to
restart the server stack. The report is read-only and safe to run at any time.

The `--active-job-max-age` flag exists for database hygiene. AGOUTIC can
accumulate orphaned `RUNNING` or `PENDING` job rows after crashes or unreported
completions, and those stale rows should not block a maintenance decision
forever. The default threshold is 168 hours (one week), which is a reasonable
default because legitimate Nextflow runs should not remain active that long.

Recommended operator sequence:
    ./agoutic_servers.sh --status                       # confirm servers are running
    python scripts/cortex/maintenance_status.py        # check if safe to restart
    ./agoutic_servers.sh --restart                     # only if SAFE TO RESTART

Example output:
    Generated at: 2026-05-28T12:00:00+00:00

    === Recently active users (approximated from chat and job activity — AGOUTIC does not track presence) ===
    - Jane Example <jane@example.com> | 2026-05-28T11:57:00+00:00 | chat | 3 minutes ago

    === Currently running jobs ===
    - a1b2c3d4 | dogme | jane@example.com | Example Project | RUNNING | started 2026-05-28T11:10:00+00:00 | runtime 50m

    === Active chat sessions ===
    - jane@example.com | Example Project | last message 2026-05-28T11:57:00+00:00 | 2 message(s) in window

    === Summary recommendation ===
    WAIT — 1 running job(s) and 1 active chat session(s). Longest-running active job: 50m.

JSON schema (`--json`):
    {
      "generated_at": "ISO-8601 timestamp",
      "users": [
        {
          "user_id": "...",
          "name": "...",
          "email": "...",
          "last_activity_at": "ISO-8601 timestamp",
          "source": "chat|job",
          "relative": "human-readable elapsed time"
        }
      ],
      "jobs": [
        {
          "run_uuid": "...",
          "run_uuid_short": "...",
          "workflow_type": "...",
          "owner_email": "...",
          "project_name": "...",
          "state": "PENDING|RUNNING",
          "started_at": "ISO-8601 timestamp or null",
          "runtime_duration": "human-readable duration",
          "runtime_seconds": 0
        }
      ],
      "stale_jobs": [
        {
          "run_uuid": "...",
          "run_uuid_short": "...",
          "workflow_type": "...",
          "owner_email": "...",
          "project_name": "...",
          "state": "PENDING|RUNNING",
          "started_at": "ISO-8601 timestamp or null",
          "runtime_duration": "human-readable duration",
          "runtime_seconds": 0
        }
      ],
      "chats": [
        {
          "conversation_id": "...",
          "owner_email": "...",
          "project_name": "...",
          "last_message_at": "ISO-8601 timestamp",
          "message_count": 0
        }
      ],
      "transfers": [
        {
          "source": "dogme_job|staging_task",
          "identifier": "...",
          "state": "...",
          "owner_email": "...",
          "project_name": "...",
          "workflow_type": "...",
          "started_at": "ISO-8601 timestamp or null",
          "duration": "human-readable duration",
          "duration_seconds": 0
        }
      ],
      "stale_transfers": [
        {
          "source": "dogme_job|staging_task",
          "identifier": "...",
          "state": "...",
          "owner_email": "...",
          "project_name": "...",
          "workflow_type": "...",
          "started_at": "ISO-8601 timestamp or null",
          "duration": "human-readable duration",
          "duration_seconds": 0
        }
      ],
      "recommendation": {
        "status": "SAFE TO RESTART|WAIT",
        "message": "human-readable summary",
        "longest_running_job": null
      }
    }
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cortex.maintenance_status import main


if __name__ == "__main__":
    raise SystemExit(main())