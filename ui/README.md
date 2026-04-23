# AGOUTIC UI

**Release:** 3.6.6  
**Status:** Active Development

## Overview

The AGOUTIC UI is a **Streamlit** web application for interacting with the AGOUTIC agent, managing projects, monitoring jobs, and viewing analysis results. It communicates **exclusively with Cortex** — all backend architecture (Atlas, 3, 4) is abstracted behind Cortex's API.

## Features

- 🔐 **Google OAuth authentication** with multi-user support
- 💬 **Chat interface** for conversing with the AGOUTIC agent
- ✅ **Approval gates** for reviewing and editing job parameters before submission
- ⚙️ **Live job monitoring** with Nextflow-style task progress visualization
- 📊 **Results analysis** page for browsing, parsing, and downloading job outputs
- 📁 **Project management** with history, switching, and conversation recall
- 🔑 **Admin panel** for user approval and role management
- 📈 **Interactive Plotly charts** — the agent can generate histogram, scatter, line, area, bar, box, violin, strip, heatmap, pie, venn, and upset charts from any DataFrame in the conversation; rendered inline as `AGENT_PLOT` blocks
- 🧮 **Pending dataframe actions** — saved in-memory dataframe transforms can be reviewed and applied or dismissed directly from chat via `PENDING_ACTION` blocks
- 📋 **Built-in dataframe + grouped DE help** — sidebar and deterministic help responses include dataframe inspection, transform, plotting, and reconcile-abundance differential-expression examples

## Getting Started

### Prerequisites
- Python 3.10+ with the `agoutic_core` conda environment
- Cortex running at `http://localhost:8000`

### Run the UI

```bash
conda activate agoutic_core
cd ui
streamlit run appUI.py
```

The UI will be available at `http://localhost:8501`.

## Architecture

The UI follows a strict **single-gateway** pattern: every request goes through Cortex.

```
┌─────────────────────┐
│   Web Browser       │
│    (Streamlit)      │
└──────────┬──────────┘
           │
      REST API (authenticated)
           │
    ┌──────┴──────┐
    │   Cortex  │  ← Only server the UI talks to
    │   (Agent)   │
    └──────┬──────┘
           │ MCP / REST proxies
     ┌─────┼─────┐
     │     │     │
   Srv 2  Srv 3  Srv 4
 (ENCODE) (Exec) (Analysis)
```

## Pages

### Main App ([appUI.py](appUI.py))
- Chat with the AGOUTIC agent
- Approval gates for job submission parameters
- Live Nextflow job progress with task-level detail
- Project switching, conversation history
- Model selection (default / fast / smart)
- `AGENT_PLOT` blocks: inline Plotly charts triggered by the agent using `[[PLOT:...]]` tags; supports histogram, scatter, line, area, bar, box, violin, strip, heatmap, pie, venn, and upset chart types
- `PENDING_ACTION` blocks: block-specific Apply / Dismiss controls for saved dataframe transforms
- Deterministic help shortcuts for grouped differential expression from reconcile abundance outputs or saved dataframes

### Results ([pages/results.py](pages/results.py))
- Browse completed job files (CSV, BED, text)
- Parse and preview structured data as tables
- Download result files
- All data fetched via Cortex analysis proxy endpoints

### Admin ([pages/admin.py](pages/admin.py))
- Approve or reject pending user registrations
- Promote users to admin role
- Revoke user access

## Development Notes

See [appUI.py](appUI.py) and [auth.py](auth.py) for implementation details.
