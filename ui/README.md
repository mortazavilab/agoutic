# AGOUTIC UI

**Version:** 2.0  
**Status:** Active Development

## Overview

The AGOUTIC UI is a **Streamlit** web application for interacting with the AGOUTIC agent, managing projects, monitoring jobs, and viewing analysis results. It communicates **exclusively with Server 1** — all backend architecture (Server 2, 3, 4) is abstracted behind Server 1's API.

## Features

- 🔐 **Google OAuth authentication** with multi-user support
- 💬 **Chat interface** for conversing with the AGOUTIC agent
- ✅ **Approval gates** for reviewing and editing job parameters before submission
- ⚙️ **Live job monitoring** with Nextflow-style task progress visualization
- 📊 **Results analysis** page for browsing, parsing, and downloading job outputs
- 📁 **Project management** with history, switching, and conversation recall
- 🔑 **Admin panel** for user approval and role management

## Getting Started

### Prerequisites
- Python 3.10+ with the `agoutic_core` conda environment
- Server 1 running at `http://localhost:8000`

### Run the UI

```bash
conda activate agoutic_core
cd ui
streamlit run app.py
```

The UI will be available at `http://localhost:8501`.

## Architecture

The UI follows a strict **single-gateway** pattern: every request goes through Server 1.

```
┌─────────────────────┐
│   Web Browser       │
│    (Streamlit)      │
└──────────┬──────────┘
           │
      REST API (authenticated)
           │
    ┌──────┴──────┐
    │   Server 1  │  ← Only server the UI talks to
    │   (Agent)   │
    └──────┬──────┘
           │ MCP / REST proxies
     ┌─────┼─────┐
     │     │     │
   Srv 2  Srv 3  Srv 4
 (ENCODE) (Exec) (Analysis)
```

## Pages

### Main App ([app.py](app.py))
- Chat with the AGOUTIC agent
- Approval gates for job submission parameters
- Live Nextflow job progress with task-level detail
- Project switching, conversation history
- Model selection (default / fast / smart)

### Results ([pages/results.py](pages/results.py))
- Browse completed job files (CSV, BED, text)
- Parse and preview structured data as tables
- Download result files
- All data fetched via Server 1 analysis proxy endpoints

### Admin ([pages/admin.py](pages/admin.py))
- Approve or reject pending user registrations
- Promote users to admin role
- Revoke user access

## Development Notes

See [app.py](app.py) and [auth.py](auth.py) for implementation details.
