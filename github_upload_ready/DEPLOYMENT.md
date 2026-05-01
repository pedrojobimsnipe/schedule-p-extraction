# Deployment quick reference

Target audience: operators deploying the Schedule P Streamlit app beyond a developer laptop.

## Recommended platform

**Streamlit Community Cloud** — native fit for `streamlit run`, GitHub integration, secrets UI. Use **`runtime.txt`** (Python 3.11) and root **`requirements.txt`**.

**Alternative:** **Render** (or similar) with the included **`Dockerfile`** — `render.yaml` defines a Docker web service.

## Prerequisites

- **Python:** 3.10–3.12 (locked to **3.11** in `runtime.txt` / Dockerfile for reproducibility).
- **Repo layout:** `streamlit_app.py` at repository root; `app/` package committed; `assets/` present.

## Single command (local / VM / container shell)

```bash
pip install -r requirements.txt
python -m streamlit run streamlit_app.py --server.port=8501 --server.address=0.0.0.0 --browser.gatherUsageStats=false
```

Docker:

```bash
docker build -t schedule-p .
docker run -p 8501:8501 schedule-p
```

## Required / typical environment variables

| Variable | Required | Notes |
|----------|----------|--------|
| `SCHEDULE_P_MAX_UPLOAD_MB` | No | Default **500**. Must be ≤ Streamlit **`server.maxUploadSize`** (`.streamlit/config.toml`). |
| `SCHEDULE_P_EXTRACTION_TIMEOUT_SEC` | No | Default **900** seconds; **`0`** = no extraction time limit (use for very large PDFs). |
| `SCHEDULE_P_DEBUG` | No | **`1`** only when diagnosing — verbose logs and tracebacks in ops console. |

Optional branding: `SCHEDULE_P_APP_TITLE`, `SCHEDULE_P_APP_SUBTITLE`.

CLI-only (batch mode): `SCHEDULE_P_INPUT_DIR`, `SCHEDULE_P_OUTPUT_DIR`.

## Production checklist

1. Set **`SCHEDULE_P_DEBUG`** unset or **`0`** unless troubleshooting.
2. Align **`SCHEDULE_P_MAX_UPLOAD_MB`** with **`[server] maxUploadSize`** in `.streamlit/config.toml` (and Cloud/Render env if overriding).
3. For **multi‑hundred‑page** PDFs, set **`SCHEDULE_P_EXTRACTION_TIMEOUT_SEC=0`** or raise the limit; otherwise partial workbook + warning is possible.
4. Ensure the host has **enough RAM** for pdfplumber + peak upload size (annual statements).
5. Watch logs at startup for **`schedule_p.startup`** (resolved limits) and **`schedule_p.config_mismatch`** if upload ceiling misconfigured.

## Verify after deploy

1. Open the app URL — sidebar should show **Release v0.2.x** and limits.
2. Upload a small PDF — confirm progress moves **page-by-page**, then download Excel.
3. Optional: `SCHEDULE_P_DEBUG=1` temporarily and confirm `[schedule_p]` log lines.

## Known limits

- **Large PDFs:** CPU/RAM bound; progress is real (per page), not simulated.
- **Timeouts:** Wall-clock limit is configurable; partial results may download with a **warning** in the results table.
- **Screenshots:** No OCR — ZIP for manual review only (by design).
