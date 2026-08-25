#!/usr/bin/env python3
"""Mark orphaned AGOUTIC job rows and transfer rows beyond their thresholds."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cortex.maintenance_status import mark_stale_jobs_main


if __name__ == "__main__":
    raise SystemExit(mark_stale_jobs_main())