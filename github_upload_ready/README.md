# Schedule P — Unpaid extraction

Client-ready toolkit to extract **Schedule P Part 1** unpaid **Column 23** and **Column 24** from insurer PDFs into structured Excel workbooks (validation summary + per-part worksheets).

**Modes**

| Mode | Audience | Entry |
|------|-----------|--------|
| **Web app** | Business users | `python -m streamlit run streamlit_app.py` |
| **CLI** | Automation / folders | `python -m app.main` |

Extraction behavior is unchanged at the core (`extract_pdf_parts` → `write_workbook`). The web UI adds validation, progress, downloads, enterprise styling (**PwC-inspired**), and a **PDF-first / screenshot-fallback** workflow.

---

## Supported inputs (web app)

| Input | Role | Automation |
|-------|------|--------------|
| **PDF** (annual statement) | **Recommended** — full document | **Yes** — Schedule P unpaid cols 23–24 → Excel |
| **PNG / JPG / JPEG** (screenshots of Schedule P section) | **Fallback** when PDF is oversized, encrypted, corrupt, unreadable, or extraction fails | **No** — images are **validated** and packaged into a **ZIP for manual review** only (`AUTOMATED_SCREENSHOT_EXTRACTION_ENABLED` is false until OCR/vision is added). |

**Per-file size cap** applies to **both** PDFs and images (default **500 MB**; see configuration). Do not assume Streamlit Cloud can parse multi‑hundred‑MB PDFs without sufficient memory — prefer self-hosted resources for extreme files.

---

## Features (web)

- **PDF-first** radio workflow plus integrated **screenshot fallback** with coaching copy and capture tips.
- Drag-and-drop **multi-file PDF** and **multi-image** uploads (type + size validation).
- PDF path: **per-file progress**, results table, Excel downloads (+ ZIP).
- Screenshot path: intake summary, **ZIP bundle** (+ optional single-file download) for manual follow-up.
- Failure messages on PDFs **point users** to screenshot fallback when relevant.
- **Technical panel** (sidebar) for structured JSON payloads (QA/support).
- **Session reset** clears both upload queues and results.
- Server logs with optional **`SCHEDULE_P_DEBUG`** verbosity.

---

## Repository layout

```text
.
├── app/
│   ├── main.py                      # CLI
│   ├── config.py                    # Paths + web settings
│   ├── core/                        # Parsing constants + shared UI copy
│   ├── services/
│   │   ├── pdf_extraction.py        # Core PDF logic (preserve)
│   │   ├── excel_export.py
│   │   ├── upload_processing.py     # PDF upload validation + pipeline
│   │   └── screenshot_intake.py    # Image validation + manual-review packaging (no OCR)
│   └── ui/
│       ├── theme.py                 # CSS loader (repo-root assets/)
│       └── streamlit_dashboard.py   # Production dashboard layout
├── assets/streamlit_theme.css       # Enterprise theme overrides
├── streamlit_app.py                 # Streamlit entrypoint
├── .streamlit/config.toml           # Baseline Streamlit theme
├── Dockerfile
├── runtime.txt                     # Python version for Streamlit Community Cloud
├── render.yaml                     # Optional Render.com Docker service
├── DEPLOYMENT.md                   # Operator deploy checklist
├── input/ , output/                # CLI defaults
├── tests/
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Quick start (local)

**Python:** 3.10–3.12 recommended (see `runtime.txt` for the pinned Cloud default).

```powershell
cd "<repo-root>"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

Open the URL shown (usually `http://localhost:8501`). On startup the server logs **`schedule_p.startup`** with resolved upload MB, extraction timeout, and version — useful for production sanity checks.

**Optional:** verbose server logs (dev/troubleshooting only)

```powershell
set SCHEDULE_P_DEBUG=1
python -m streamlit run streamlit_app.py
```

---

## CLI (batch folders)

From repo root:

```bash
python -m app.main --input-dir ./input --output-dir ./output
```

---

## Configuration

| Variable | Applies to | Purpose |
|----------|------------|---------|
| `SCHEDULE_P_INPUT_DIR` | CLI | Default PDF folder |
| `SCHEDULE_P_OUTPUT_DIR` | CLI | Default Excel folder |
| `SCHEDULE_P_MAX_UPLOAD_MB` | Web | Per-file upload limit in MB (default **500**; must align with Streamlit — see below) |
| `SCHEDULE_P_EXTRACTION_TIMEOUT_SEC` | Web | Max seconds for PDF extraction pass (default **900**; **`0`** = no limit — use for huge PDFs) |
| `SCHEDULE_P_APP_TITLE` | Web | Hero title |
| `SCHEDULE_P_APP_SUBTITLE` | Web | Hero subtitle |
| `SCHEDULE_P_DEBUG` | Server logs | **`1`** / `true` for DEBUG logging and richer tracebacks (avoid in production unless diagnosing) |

Copy `.env.example` for reference; **Streamlit does not auto-load `.env`** — export variables in your shell or set them in your host’s secrets UI (Cloud, Render, Docker `-e`, etc.).

At startup, **`schedule_p.startup`** logs effective limits; **`schedule_p.config_mismatch`** warns if `server.maxUploadSize` &lt; `SCHEDULE_P_MAX_UPLOAD_MB` (uploads can fail in the browser before Python runs).

### Large PDF uploads (annual statements)

- **Single policy:** the UI, backend validation, and Streamlit’s upload ceiling must agree.
- **App default:** `500` MB per PDF (`app.config.DEFAULT_MAX_UPLOAD_MB` and `SCHEDULE_P_MAX_UPLOAD_MB`).
- **Streamlit server:** `.streamlit/config.toml` sets `[server] maxUploadSize = 500` (megabytes). If you raise `SCHEDULE_P_MAX_UPLOAD_MB`, **always** raise `maxUploadSize` to at least the same value (or Streamlit will reject the HTTP upload before Python validation runs).
- **CLI override:** `python -m streamlit run streamlit_app.py --server.maxUploadSize 800`
- **Extraction timeout:** default **900** seconds for the parsing pass; set **`SCHEDULE_P_EXTRACTION_TIMEOUT_SEC=0`** for no wall-clock cap on multi‑hundred‑page filings. If the limit is hit, the UI may still yield a **partial** workbook with a clear **warning** in results.
- **Progress UX:** extraction reports **real page-level progress** (not simulated); see sidebar limits + status panel during long runs.
- **Processing:** large files use more RAM and CPU; Streamlit Cloud and small containers may **OOM or time out** on very large PDFs even when uploads succeed. For routinely huge statements, prefer **self-hosted Docker/VM** with ample memory over free-tier Cloud.
- **Screenshots:** same upload ceiling as PDFs; bundling is lightweight, but **many large images** still increase memory during ZIP creation.

### Recommended workflow

1. Upload the **full PDF** and run extraction.
2. If that succeeds, use the Excel outputs as today.
3. If the PDF fails or is impractical, switch to **Screenshot fallback**, capture the **unpaid Schedule P** table clearly (headers + columns), upload PNG/JPEG in order, and **package** — then download the ZIP for **manual transcription or downstream tooling**.

---

## Deploy

**Full operator checklist:** see **[DEPLOYMENT.md](DEPLOYMENT.md)** (Streamlit Cloud, Render/Docker, env vars, post-deploy tests).

### Streamlit Community Cloud (recommended)

**Prerequisites:** GitHub repo with `requirements.txt`, `streamlit_app.py`, `runtime.txt` (Python **3.11**), and `.streamlit/config.toml` at the repository root.

1. Sign in at [streamlit.io/cloud](https://streamlit.io/cloud).
2. **New app** → select the repo.
3. **Main file:** `streamlit_app.py`
4. **Branch:** `main` (or default).
5. **Secrets / Environment variables** (as needed): `SCHEDULE_P_MAX_UPLOAD_MB`, `SCHEDULE_P_EXTRACTION_TIMEOUT_SEC` (use **`0`** for very large PDFs), avoid `SCHEDULE_P_DEBUG` in production.

Cloud clones the whole repo — `app/`, `assets/`, and theme CSS resolve like local. **`runtime.txt`** selects the Python version on Community Cloud.

Keep **`SCHEDULE_P_MAX_UPLOAD_MB` ≤ `server.maxUploadSize`** in `.streamlit/config.toml`. Hosted tiers may still **OOM** on huge PDFs regardless of upload success.

### Render / Docker (alternative)

Use the included **`Dockerfile`** (Python 3.11-slim, port **8501**). Optional **`render.yaml`** defines a Docker web service on [Render](https://render.com).

```bash
docker build -t schedule-p-ui .
docker run -p 8501:8501 \
  -e SCHEDULE_P_EXTRACTION_TIMEOUT_SEC=0 \
  schedule-p-ui
```

### If deployment fails

| Symptom | Check |
|---------|--------|
| **ModuleNotFoundError: app** | Main file must be `streamlit_app.py` at repo root; `app/` committed. |
| **Missing CSS** | `assets/streamlit_theme.css` pushed. |
| **Dependency errors** | `pip install -r requirements.txt`; Python **3.10–3.12**. |
| **Upload rejected** | `server.maxUploadSize` vs `SCHEDULE_P_MAX_UPLOAD_MB`; server logs **`schedule_p.config_mismatch`**. |
| **Memory / timeout** | Larger host or **`SCHEDULE_P_EXTRACTION_TIMEOUT_SEC=0`**; partial workbook + warning if wall-clock limit hit. |

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```

---

## Branding notice

Theme colors and typography echo common consulting-slide patterns (orange accent, neutral greys, serif titles). Replace `assets/streamlit_theme.css` if your organization supplies approved tokens.

---

## Governance

Validate Excel outputs against source filings before regulatory or external reporting. Usage is subject to your organization’s policies.
