# AGOUTIC UI

**Version:** 1.0  
**Status:** Early Development

## Overview

The UI provides a web interface for monitoring AGOUTIC jobs, managing projects, and viewing analysis results.

## Features (Planned)

- 📊 Job monitoring dashboard
- 📈 Real-time progress tracking
- 📝 Project history and archival
- 📤 Result downloads
- 🔍 Search and filtering
- 📱 Responsive design

## Getting Started

### Installation

```bash
conda activate agoutic_core
pip install streamlit  # or flask, depending on framework
```

### Run the UI

```bash
cd ui
streamlit run app.py
# or
python app.py
```

The UI will be available at `http://localhost:8501` (Streamlit) or `http://localhost:5000` (Flask).

## Architecture

```
┌─────────────────────┐
│   Web Browser       │
│  (Streamlit/Flask)  │
└──────────┬──────────┘
           │
      REST API
           │
    ┌──────┴──────┐
    │              │
Server 1      Server 3
(Agent)      (Executor)
```

## Components

### Dashboard
- Display running jobs
- Show completed jobs
- View project timelines

### Job Monitoring
- Real-time status updates
- Progress bars
- Log viewing
- Error alerts

### Project Management
- Create new projects
- View project history
- Archive projects

## Integration Points

### Server 1 Integration
- Pull project history
- Display agent responses
- Show workflow plans

### Server 3 Integration
- Fetch job status
- Display progress
- Show results and reports

## Development Notes

See [app.py](app.py) for current implementation status.

## Next Steps

- Core dashboard
- Real-time updates
- Polish and optimization
