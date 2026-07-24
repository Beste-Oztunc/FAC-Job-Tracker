# FAC Core Edition

The Core edition is the standalone Python version of FAC - Job Tracker.
It does not use the browser setup interface or FastAPI.

## First setup

Create a virtual environment and install the requirements.

### Mac

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements_core.txt
```

### Windows

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements_core.txt
```

## Run order

Always run the collector first:

```bash
python collect_all_ats.py
```

That creates:

```text
output/all_ats_job_dashboard.html
```

The agent is optional and should run after the collector:

```bash
python job_agent.py
```

That creates:

```text
output/job_intelligence.html
```

For local statistics without an AI request:

```bash
python job_agent.py --no-ai
```

Read `FAC_CORE_QUICK_START.txt` for the full workflow and
`FAC_SEARCH_TIPS.txt` for help preparing resume text, desired job titles,
location preferences, scoring terms, and exclusions.

Project version: **1.9.0**
