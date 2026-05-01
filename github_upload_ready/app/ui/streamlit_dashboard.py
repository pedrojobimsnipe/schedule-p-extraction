"""
Enterprise dashboard layout for the Schedule P Streamlit app.

PDF-first workflow with validated screenshot fallback (manual-review packaging only).
"""

from __future__ import annotations

import html
import io
import logging
import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import Any, Mapping

import streamlit as st

import app
from app.config import WebSettings
from app.services.screenshot_intake import (
    AUTOMATED_SCREENSHOT_EXTRACTION_ENABLED,
    ScreenshotIntakeResult,
    process_screenshot_batch,
    screenshot_row_payload,
)
from app.services.upload_processing import (
    ExtractionResult,
    process_one_uploaded_pdf,
    summarize_part_status,
)
from app.ui.theme import streamlit_theme_markup

logger = logging.getLogger(__name__)

SK_RESULTS = "sp_extraction_results"
SK_LAST_SIG = "sp_last_run_file_signature"
SK_SCREENSHOT_RESULTS = "sp_screenshot_intake_results"
SK_SCREENSHOT_SIG = "sp_screenshot_last_signature"
SK_FATAL = "sp_fatal_batch_error"
SK_FATAL_TB = "sp_fatal_batch_traceback"
SK_TECH = "sp_show_technical_panel"


def _escape(text: str) -> str:
    return html.escape(text or "", quote=True)


def _build_pdf_progress_hook(
    progress: Any,
    status_slot: Any,
    *,
    file_index: int,
    total_files: int,
    display_name: str,
    file_size_bytes: int,
):
    """
    Maps extraction phases to Streamlit progress (measurable page fraction within each file).

    Each file owns an equal horizontal slice of the bar; within that slice, page_index/total_pages
    drives most of the movement (not a fake loop).
    """

    def hook(phase: str, info: Mapping[str, Any]) -> None:
        nf = max(total_files, 1)
        seg_lo = (file_index - 1) / nf
        seg_w = 1.0 / nf
        tp = max(int(info.get("total_pages") or 1), 1)
        pi = int(info.get("page_index") or 0)
        msg = str(info.get("message") or "")
        short = display_name if len(display_name) <= 56 else display_name[:53] + "…"
        large_hint = ""
        if file_size_bytes >= 4 * 1024 * 1024:
            large_hint = " Large file — extraction may take several minutes."
        if tp >= 120:
            large_hint += " Very long PDF — progress updates once per page."

        if phase == "opened":
            pct = min(seg_lo + seg_w * 0.03, 0.999)
            progress.progress(pct, text=f"Reading PDF — {tp} page(s): {short}")
            status_slot.markdown(
                f'<div class="pwc-file-status"><strong>Stage:</strong> file opened.{large_hint}<br/>'
                f"<strong>Detail:</strong> {_escape(display_name)} · <strong>{tp}</strong> pages</div>",
                unsafe_allow_html=True,
            )
        elif phase == "page":
            frac = (pi / tp) if tp else 0.0
            pct = min(seg_lo + seg_w * (0.06 + 0.88 * frac), 0.999)
            progress.progress(pct, text=f"Pages {pi}/{tp} · {short}")
            status_slot.markdown(
                f'<div class="pwc-file-status"><strong>Stage:</strong> extracting text & tables.<br/>'
                f"<strong>Progress:</strong> page <strong>{pi}</strong> of <strong>{tp}</strong>. "
                f"{_escape(msg)}</div>",
                unsafe_allow_html=True,
            )
        elif phase == "workbook":
            pct = min(seg_lo + seg_w * 0.96, 0.999)
            progress.progress(pct, text=f"Building Excel · {short}")
            status_slot.markdown(
                '<div class="pwc-file-status"><strong>Stage:</strong> writing workbook…</div>',
                unsafe_allow_html=True,
            )
        elif phase == "finished":
            timed = bool(info.get("timed_out"))
            pct = min(seg_lo + seg_w * (0.995 if not timed else 0.94), 1.0)
            progress.progress(pct, text=f"PDF pass complete · {short}")
            if timed:
                status_slot.markdown(
                    '<div class="pwc-file-status"><strong>Stage:</strong> stopped — time limit '
                    "(partial workbook may still download).</div>",
                    unsafe_allow_html=True,
                )

    return hook


def _format_mb_display(mb: float) -> str:
    return str(int(mb)) if float(mb).is_integer() else f"{mb:g}"


def _timeout_sidebar_label(web: WebSettings) -> str:
    t = web.extraction_timeout_sec
    if t is None:
        return "no limit"
    if float(t).is_integer():
        return f"{int(t)} s (~{int(t) // 60} min)"
    return f"{t:g} s"


def _maybe_warn_upload_cap_mismatch(web: WebSettings) -> None:
    try:
        from streamlit.config import get_option

        server_mb = float(get_option("server.maxUploadSize"))
        if server_mb < web.max_upload_mb:
            st.warning(
                f"**Upload ceiling mismatch:** Streamlit `server.maxUploadSize` is **{_format_mb_display(server_mb)} MB**, "
                f"but the app allows **{_format_mb_display(web.max_upload_mb)} MB** (`SCHEDULE_P_MAX_UPLOAD_MB`). "
                "Increase `maxUploadSize` under `[server]` in `.streamlit/config.toml` (or pass "
                "`--server.maxUploadSize`) so large files are not rejected before validation."
            )
    except Exception:
        logger.debug("Could not compare Streamlit server.maxUploadSize", exc_info=True)


def _init_session_state() -> None:
    st.session_state.setdefault(SK_TECH, False)


def _upload_signature(uploaded: list[Any]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((str(f.name), int(f.size)) for f in uploaded))


def _clear_run_state() -> None:
    for key in (
        SK_RESULTS,
        SK_LAST_SIG,
        SK_SCREENSHOT_RESULTS,
        SK_SCREENSHOT_SIG,
        SK_FATAL,
        SK_FATAL_TB,
    ):
        st.session_state.pop(key, None)


def _inject_theme() -> None:
    st.markdown(streamlit_theme_markup(), unsafe_allow_html=True)


def _section_heading(step: str, title: str, subtitle: str | None = None) -> None:
    sub = f'<p class="pwc-section-sub">{_escape(subtitle)}</p>' if subtitle else ""
    st.markdown(
        f'<div class="pwc-section-block"><span class="pwc-step-num">{_escape(step)}</span>'
        f'<span class="pwc-section-title-text">{_escape(title)}</span>{sub}</div>',
        unsafe_allow_html=True,
    )


def _hero(web: WebSettings) -> None:
    st.markdown(
        f"""
        <div class="pwc-product-header">
          <p class="pwc-eyebrow">Internal extraction workspace</p>
          <h1 class="pwc-product-title">{_escape(web.app_title)}</h1>
          <p class="pwc-product-subtitle">{_escape(web.app_subtitle)}</p>
          <div class="pwc-badge-row">
            <span class="pwc-badge pwc-badge-accent">PDF-first workflow</span>
            <span class="pwc-badge">Screenshot fallback · manual review</span>
            <span class="pwc-badge">Schedule P Part 1 · Unpaid · Columns 23–24</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _workflow_intro_html() -> str:
    return """
    <div class="pwc-upload-guide">
      <strong>Recommended workflow</strong>
      <ul>
        <li><strong>Start with the full annual statement PDF</strong> whenever possible — best coverage and consistency with automated extraction.</li>
        <li>Large statements may take longer to upload and process; stay within the per-file size limit shown in the sidebar.</li>
        <li>If the PDF is <strong>too large, encrypted, corrupt, unreadable, or extraction fails</strong>, switch to <strong>Screenshot fallback</strong>
            and upload PNG/JPEG captures of the <strong>unpaid Schedule P section</strong> only.</li>
      </ul>
    </div>
    """


def _upload_instructions_html(max_mb: float) -> str:
    lim = _format_mb_display(max_mb)
    return f"""
    <div class="pwc-upload-guide">
      <strong>PDF upload — guidance</strong>
      <ul>
        <li><strong>PDF upload is the recommended path</strong> for automated Excel outputs when the file is usable.</li>
        <li>Use text-selectable Schedule P PDFs where possible (avoid unreadable scans when alternatives exist).</li>
        <li><strong>Per-file limit:</strong> up to <strong>{lim} MB</strong> — consistent across UI, server, and validation.</li>
        <li><strong>Multiple PDFs:</strong> queued independently; heavy files need more time and server memory.</li>
        <li>If upload fails at the browser, check Streamlit <code>server.maxUploadSize</code>. After failures, try <strong>Screenshot fallback</strong>.</li>
      </ul>
    </div>
    """


def _screenshot_upload_instructions_html(max_mb: float) -> str:
    lim = _format_mb_display(max_mb)
    return f"""
    <div class="pwc-upload-guide">
      <strong>Screenshot fallback — guidance</strong>
      <ul>
        <li>Use when the <strong>PDF path is impractical</strong> (oversized host limits, malformed file, encryption, poor extraction) or when only one section is needed.</li>
        <li><strong>Accepted:</strong> PNG, JPG, JPEG — multiple images supported (e.g. multi-page tables).</li>
        <li><strong>Same per-file size cap as PDFs:</strong> up to <strong>{lim} MB</strong> each.</li>
        <li><strong>Automation:</strong> this release <strong>does not extract Schedule P from images</strong>. Images are validated and packaged for <strong>manual review</strong> (ZIP download).</li>
        <li>Capture the <strong>full unpaid table</strong> with headers and visible columns; avoid cropping rows or values.</li>
      </ul>
    </div>
    """


def _empty_pdf_upload_html() -> str:
    return """
    <div class="pwc-empty-upload">
      <strong>No PDFs queued.</strong><br />
      Drag annual statement PDFs below or use <strong>Browse files</strong>. Type: <strong>.pdf</strong>.
    </div>
    """


def _empty_screenshot_upload_html() -> str:
    return """
    <div class="pwc-empty-upload">
      <strong>No screenshots queued.</strong><br />
      Drag PNG or JPEG images below or use <strong>Browse files</strong>.
    </div>
    """


def _file_ready_chips(uploaded: list[Any], *, label: str) -> None:
    chips = "".join(
        '<span class="pwc-file-chip"><span class="pwc-file-chip-dot" aria-hidden="true"></span>'
        f"{_escape(f.name)}</span>"
        for f in uploaded
    )
    st.markdown(
        f"""
        <div class="pwc-upload-ready-banner" role="status">{_escape(label)}</div>
        <div class="pwc-file-chip-row">{chips}</div>
        """,
        unsafe_allow_html=True,
    )


def _screenshot_tips_expander() -> None:
    with st.expander("Screenshot capture tips (read before uploading)", expanded=False):
        st.markdown(
            """
1. **Try the PDF path first** whenever the document is usable.  
2. Capture **only** the relevant **Schedule P unpaid** section — not the whole annual statement unless required.  
3. Include **column headers** and **all needed numeric columns** in frame.  
4. Keep **reading order** if uploading multiple clips (top → bottom).  
5. Prefer **desktop snipping tools** over phone photos; avoid blur, glare, skew, or dark photos.  
6. Ensure **text is legible** at 100% zoom; retake if compressed or fuzzy.  
7. Do **not** crop partial rows or amounts at table edges.  
8. Split into **multiple screenshots** if the section spans pages — upload all in one batch.
            """
        )


def _render_kpi_row(total: int, ok: int, fail: int) -> None:
    mid_cls = "pwc-kpi-card is-success" if ok > 0 else "pwc-kpi-card"
    right_cls = "pwc-kpi-card is-danger" if fail > 0 else "pwc-kpi-card"
    st.markdown(
        f"""
        <div class="pwc-kpi-row">
          <div class="pwc-kpi-card">
            <div class="pwc-kpi-label">Batch size</div>
            <div class="pwc-kpi-value">{total}</div>
          </div>
          <div class="{mid_cls}">
            <div class="pwc-kpi-label">Successful</div>
            <div class="pwc-kpi-value">{ok}</div>
          </div>
          <div class="{right_cls}">
            <div class="pwc-kpi-label">Needs attention</div>
            <div class="pwc-kpi-value">{fail}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_screenshot_kpi_row(total: int, accepted: int, failed: int) -> None:
    mid_cls = "pwc-kpi-card is-success" if accepted > 0 else "pwc-kpi-card"
    right_cls = "pwc-kpi-card is-danger" if failed > 0 else "pwc-kpi-card"
    st.markdown(
        f"""
        <div class="pwc-kpi-row">
          <div class="pwc-kpi-card">
            <div class="pwc-kpi-label">Images in batch</div>
            <div class="pwc-kpi-value">{total}</div>
          </div>
          <div class="{mid_cls}">
            <div class="pwc-kpi-label">Accepted</div>
            <div class="pwc-kpi-value">{accepted}</div>
          </div>
          <div class="{right_cls}">
            <div class="pwc-kpi-label">Validation failed</div>
            <div class="pwc-kpi-value">{failed}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_results_table(results: list[ExtractionResult]) -> None:
    rows_html: list[str] = []
    for r in results:
        summ = summarize_part_status(r.part_status) if r.part_status else None
        pill = (
            '<span class="pwc-pill pwc-pill-success">● Success</span>'
            if r.success
            else '<span class="pwc-pill pwc-pill-danger">● Failed</span>'
        )
        pdata = str(summ["data"]) if summ else "—"
        pnone = str(summ["none"]) if summ else "—"
        pmiss = str(summ["missing"]) if summ else "—"
        ws = str(r.worksheets_created) if r.success else "—"
        detail_parts: list[str] = []
        if r.error_message and r.error_message.strip():
            detail_parts.append(r.error_message.strip())
        if r.warn_message and r.warn_message.strip():
            detail_parts.append(r.warn_message.strip())
        msg = _escape("\n".join(detail_parts)) if detail_parts else "—"
        rows_html.append(
            "<tr>"
            f'<td class="col-file">{_escape(r.source_filename)}</td>'
            f"<td>{pill}</td>"
            f"<td>{pdata}</td><td>{pnone}</td><td>{pmiss}</td><td>{ws}</td>"
            f'<td class="col-msg">{msg}</td>'
            "</tr>"
        )

    thead = (
        "<thead><tr>"
        "<th>Source PDF</th><th>Status</th>"
        "<th>Parts · data</th><th>Parts · none</th><th>Parts · missing</th>"
        "<th>Sheets</th><th>Detail</th>"
        "</tr></thead>"
    )
    body = "<tbody>" + "".join(rows_html) + "</tbody>"
    st.markdown(
        f'<div class="pwc-table-wrap"><table class="pwc-table">{thead}{body}</table></div>',
        unsafe_allow_html=True,
    )


def _render_screenshot_results_table(rows: list[ScreenshotIntakeResult]) -> None:
    body_parts: list[str] = []
    for r in rows:
        pill = (
            '<span class="pwc-pill pwc-pill-success">● Accepted</span>'
            if r.success
            else '<span class="pwc-pill pwc-pill-danger">● Rejected</span>'
        )
        kb = len(r.image_bytes) // 1024 if r.image_bytes else "—"
        sz_cell = f"{kb} KB" if isinstance(kb, int) else "—"
        note = (
            "Packaged for manual review (no automated extraction)."
            if r.success
            else _escape((r.error_message or "").strip() or "—")
        )
        body_parts.append(
            "<tr>"
            f'<td class="col-file">{_escape(r.source_filename)}</td>'
            f"<td>{pill}</td>"
            f"<td>{sz_cell}</td>"
            f'<td class="col-msg">{note}</td>'
            "</tr>"
        )
    thead = (
        "<thead><tr><th>Image file</th><th>Status</th><th>Size</th><th>Notes</th></tr></thead>"
    )
    body = "<tbody>" + "".join(body_parts) + "</tbody>"
    st.markdown(
        f'<div class="pwc-table-wrap"><table class="pwc-table">{thead}{body}</table></div>',
        unsafe_allow_html=True,
    )


def _result_payload(r: ExtractionResult) -> dict[str, object]:
    return {
        "input_kind": "pdf",
        "source_filename": r.source_filename,
        "success": r.success,
        "excel_filename": r.excel_filename,
        "error_message": r.error_message,
        "warn_message": r.warn_message,
        "worksheets_created": r.worksheets_created,
        "part_status": r.part_status,
        "excel_bytes_length": len(r.excel_bytes) if r.excel_bytes else 0,
    }


def _render_sidebar(web: WebSettings) -> None:
    with st.sidebar:
        st.markdown('<p class="pwc-side-section-label">Workspace</p>', unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="pwc-side-card">
              <p class="pwc-side-muted" style="margin:0 0 0.35rem 0;">
                <strong style="color:var(--pwc-ink);">Release</strong> v{_escape(app.__version__)}
              </p>
              <p class="pwc-side-muted" style="margin:0;">
                PDF-first automation; screenshot path validates images and prepares a ZIP for manual follow-up.
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.session_state[SK_TECH] = st.checkbox(
            "Technical panel",
            value=bool(st.session_state.get(SK_TECH)),
            help="Structured JSON summaries for QA / support (no binary bodies).",
        )

        st.markdown('<p class="pwc-side-section-label">Controls</p>', unsafe_allow_html=True)
        if st.button(
            "Reset workspace",
            use_container_width=True,
            help="Clears results, errors, signatures, and both upload queues.",
        ):
            _clear_run_state()
            st.session_state.pop("uploaded_pdf_widget", None)
            st.session_state.pop("uploaded_screenshot_widget", None)
            st.rerun()

        st.markdown('<p class="pwc-side-section-label">Limits</p>', unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="pwc-side-card">
              <p class="pwc-side-muted" style="margin:0;">
                Per file: <strong>{_format_mb_display(web.max_upload_mb)} MB</strong> for both PDFs and images.
                Multi-file batches supported.
              </p>
              <p class="pwc-side-muted" style="margin:0.5rem 0 0 0;">
                Extraction time budget (UI): <strong>{_escape(_timeout_sidebar_label(web))}</strong>
                · env <code>SCHEDULE_P_EXTRACTION_TIMEOUT_SEC</code> (<code>0</code> = no limit).
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if web.debug_logging:
            st.warning("Server debug logging is **on** (`SCHEDULE_P_DEBUG`).")


def run_dashboard(web: WebSettings) -> None:
    st.set_page_config(
        page_title=web.app_title,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_session_state()
    _inject_theme()

    _render_sidebar(web)
    _hero(web)
    _maybe_warn_upload_cap_mismatch(web)

    if st.session_state.get(SK_FATAL):
        st.error(st.session_state[SK_FATAL])
        if st.session_state.get(SK_FATAL_TB) and st.session_state.get(SK_TECH):
            with st.expander("Fatal error — traceback", expanded=False):
                st.code(st.session_state[SK_FATAL_TB])

    pdf_results: list[ExtractionResult] | None = st.session_state.get(SK_RESULTS)
    shot_results: list[ScreenshotIntakeResult] | None = st.session_state.get(SK_SCREENSHOT_RESULTS)

    # --- 1 Input mode & upload ---
    with st.container(border=True):
        _section_heading(
            "1",
            "Choose input & upload",
            "PDF-first for automation; screenshot fallback when the annual statement PDF is not viable.",
        )
        st.markdown(_workflow_intro_html(), unsafe_allow_html=True)

        input_mode = st.radio(
            "Document input",
            options=["pdf", "screenshot"],
            format_func=lambda x: (
                "Annual statement PDF (recommended)"
                if x == "pdf"
                else "Screenshots — Schedule P section (fallback)"
            ),
            horizontal=True,
            key="sp_input_mode_radio",
            help=(
                "Use PDF whenever practical. Switch to screenshots for oversized, encrypted, corrupt, "
                "or failed PDFs — images are packaged for manual review only."
            ),
        )

        uploaded_pdf: list[Any] | None = None
        uploaded_shots: list[Any] | None = None

        if input_mode == "pdf":
            st.markdown(_upload_instructions_html(web.max_upload_mb), unsafe_allow_html=True)
            uploaded_pdf = st.file_uploader(
                label="PDF files",
                type=["pdf"],
                accept_multiple_files=True,
                label_visibility="collapsed",
                help=(
                    f"Up to {_format_mb_display(web.max_upload_mb)} MB per PDF. "
                    "Large annual statements supported; allow time to process."
                ),
                key="uploaded_pdf_widget",
            )
            if uploaded_pdf:
                _file_ready_chips(uploaded_pdf, label=f"{len(uploaded_pdf)} PDF(s) queued — ready to extract")
            else:
                st.markdown(_empty_pdf_upload_html(), unsafe_allow_html=True)
        else:
            st.markdown(_screenshot_upload_instructions_html(web.max_upload_mb), unsafe_allow_html=True)
            _screenshot_tips_expander()
            uploaded_shots = st.file_uploader(
                label="Screenshot images",
                type=["png", "jpg", "jpeg"],
                accept_multiple_files=True,
                label_visibility="collapsed",
                help=f"PNG / JPG / JPEG · Up to {_format_mb_display(web.max_upload_mb)} MB each.",
                key="uploaded_screenshot_widget",
            )
            if uploaded_shots:
                _file_ready_chips(
                    uploaded_shots,
                    label=f"{len(uploaded_shots)} image(s) queued — ready to package",
                )
            else:
                st.markdown(_empty_screenshot_upload_html(), unsafe_allow_html=True)

        active_uploads = uploaded_pdf if input_mode == "pdf" else uploaded_shots

        last_pdf_sig = st.session_state.get(SK_LAST_SIG)
        last_shot_sig = st.session_state.get(SK_SCREENSHOT_SIG)
        if input_mode == "pdf" and pdf_results and uploaded_pdf and last_pdf_sig:
            sig = _upload_signature(uploaded_pdf)
            if sig != last_pdf_sig:
                st.markdown(
                    '<div class="pwc-stale-banner"><strong>PDF selection changed.</strong> '
                    "Run extraction again to refresh results for the files shown above.</div>",
                    unsafe_allow_html=True,
                )
        if input_mode == "screenshot" and shot_results and uploaded_shots and last_shot_sig:
            sig = _upload_signature(uploaded_shots)
            if sig != last_shot_sig:
                st.markdown(
                    '<div class="pwc-stale-banner"><strong>Image selection changed.</strong> '
                    "Package screenshots again to refresh the manual-review bundle.</div>",
                    unsafe_allow_html=True,
                )

    # --- 2 Run ---
    run_pdf = input_mode == "pdf"
    step2_title = "Run extraction" if run_pdf else "Package screenshots"
    step2_sub = (
        "Generate Excel workbooks from PDFs (automated Schedule P parsing)."
        if run_pdf
        else "Validate images and prepare a ZIP for manual review. No table OCR runs in this release."
    )
    with st.container(border=True):
        _section_heading("2", step2_title, step2_sub)

        if run_pdf:
            st.markdown(
                """
                <div class="pwc-cta-help">
                  <strong>What happens:</strong> each PDF is validated and parsed; successful files become downloadable workbooks.
                  Failures remain in the batch — see tips in results for switching to screenshot fallback when appropriate.
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
                <div class="pwc-cta-help">
                  <strong>What happens:</strong> each image is checked (type, size). Accepted files are bundled into a ZIP for your team to transcribe or route elsewhere.
                  <strong>Automated Schedule P extraction from images:</strong>
                  <strong>{'enabled' if AUTOMATED_SCREENSHOT_EXTRACTION_ENABLED else 'not enabled'}</strong> in this version.
                </div>
                """,
                unsafe_allow_html=True,
            )

        run_label = "Run extraction" if run_pdf else "Package screenshots for manual review"
        c_primary, c_secondary = st.columns([1.15, 1])
        with c_primary:
            run_clicked = st.button(
                run_label,
                type="primary",
                disabled=not active_uploads,
                use_container_width=True,
            )
        with c_secondary:
            has_any = pdf_results is not None or shot_results is not None
            if st.button("Clear results", disabled=not has_any, use_container_width=True):
                _clear_run_state()
                st.rerun()

        if not active_uploads:
            hint = (
                "Upload at least one PDF to enable extraction."
                if run_pdf
                else "Upload at least one PNG or JPEG to enable packaging."
            )
            st.markdown(f'<p class="pwc-disabled-hint">{_escape(hint)}</p>', unsafe_allow_html=True)

        if run_clicked and active_uploads:
            st.session_state.pop(SK_FATAL, None)
            st.session_state.pop(SK_FATAL_TB, None)

            if run_pdf:
                st.session_state.pop(SK_SCREENSHOT_RESULTS, None)
                st.session_state.pop(SK_SCREENSHOT_SIG, None)
                items = [(f.name, f) for f in uploaded_pdf or []]
                n = len(items)
                stem_counts: dict[str, int] = {}
                processed: list[ExtractionResult] = []
                progress = st.progress(0.0, text="Starting…")
                status_slot = st.empty()
                try:
                    with tempfile.TemporaryDirectory(prefix="schedule_p_ui_") as tmp:
                        temp_path = Path(tmp)
                        for i, (name, stream) in enumerate(items):
                            idx = i + 1
                            progress.progress(
                                (idx - 1) / max(n, 1),
                                text=f"Queued file {idx} of {n}: {_escape(name[:48])}{'…' if len(name) > 48 else ''}",
                            )
                            stream.seek(0, 2)
                            size_b = stream.tell()
                            stream.seek(0)
                            pdf_hook = _build_pdf_progress_hook(
                                progress,
                                status_slot,
                                file_index=idx,
                                total_files=n,
                                display_name=name,
                                file_size_bytes=size_b,
                            )
                            processed.append(
                                process_one_uploaded_pdf(
                                    temp_path,
                                    name,
                                    stream,
                                    stem_counts,
                                    max_upload_mb=web.max_upload_mb,
                                    progress_hook=pdf_hook,
                                    timeout_seconds=web.extraction_timeout_sec,
                                )
                            )
                            progress.progress(idx / max(n, 1), text=f"Finished file {idx} of {n}")
                        progress.progress(1.0, text=f"Batch complete — {n} file(s)")
                        st.session_state[SK_RESULTS] = processed
                        st.session_state[SK_LAST_SIG] = _upload_signature(uploaded_pdf or [])
                        logger.info(
                            "schedule_p.batch.complete files=%s ok=%s",
                            n,
                            sum(1 for r in processed if r.success),
                        )
                except Exception as exc:
                    logger.exception("schedule_p.batch.fatal")
                    st.session_state[SK_FATAL] = (
                        f"The batch stopped unexpectedly: {exc}. "
                        "Try fewer files, smaller PDFs, or use Screenshot fallback. Logs: SCHEDULE_P_DEBUG=1."
                    )
                    st.session_state[SK_FATAL_TB] = traceback.format_exc()
                finally:
                    progress.empty()
                    status_slot.empty()
            else:
                st.session_state.pop(SK_RESULTS, None)
                st.session_state.pop(SK_LAST_SIG, None)
                items = [(f.name, f) for f in uploaded_shots or []]
                n = len(items)
                progress = st.progress(0.0, text="Queued…")
                status_slot = st.empty()
                try:
                    progress.progress(0.35, text=f"Validating {n} image(s)…")
                    status_slot.markdown(
                        '<div class="pwc-file-status">Running PNG/JPEG validation and size checks…</div>',
                        unsafe_allow_html=True,
                    )
                    out = process_screenshot_batch(items, max_upload_mb=web.max_upload_mb)
                    progress.progress(1.0, text="Complete")
                    st.session_state[SK_SCREENSHOT_RESULTS] = out
                    st.session_state[SK_SCREENSHOT_SIG] = _upload_signature(uploaded_shots or [])
                    logger.info(
                        "schedule_p.screenshot.batch.complete files=%s ok=%s",
                        n,
                        sum(1 for r in out if r.success),
                    )
                except Exception as exc:
                    logger.exception("schedule_p.screenshot.batch.fatal")
                    st.session_state[SK_FATAL] = f"Screenshot packaging failed unexpectedly: {exc}"
                    st.session_state[SK_FATAL_TB] = traceback.format_exc()
                finally:
                    progress.empty()
                    status_slot.empty()

    pdf_results = st.session_state.get(SK_RESULTS)
    shot_results = st.session_state.get(SK_SCREENSHOT_RESULTS)

    # --- 3–4 PDF results ---
    if pdf_results:
        with st.container(border=True):
            _section_heading(
                "3",
                "PDF results & quality checks",
                "Automated extraction outcomes; failed rows include guidance on screenshot fallback.",
            )
            ok = sum(1 for r in pdf_results if r.success)
            fail = len(pdf_results) - ok
            if ok > 0:
                st.markdown(
                    '<div class="pwc-run-complete">Extraction finished — review the summary and download Excel outputs below.</div>',
                    unsafe_allow_html=True,
                )
            _render_kpi_row(len(pdf_results), ok, fail)
            _render_results_table(pdf_results)
            failures = [r for r in pdf_results if not r.success]
            if failures:
                with st.expander("Error detail by file", expanded=fail == len(pdf_results)):
                    st.caption(
                        "If PDF issues persist (size, encryption, corruption), switch to **Screenshots — fallback** "
                        "and capture the unpaid Schedule P section."
                    )
                    for r in failures:
                        st.markdown(f"**{_escape(r.source_filename)}**")
                        st.caption(_escape(r.error_message or "Unknown error."))

        with st.container(border=True):
            _section_heading("4", "Download outputs (PDF path)", "Excel files from automated extraction.")
            successes = [r for r in pdf_results if r.success and r.excel_bytes and r.excel_filename]
            if not successes:
                st.warning(
                    "No Excel outputs produced. Confirm Schedule P Part 1 unpaid content is present and selectable. "
                    "Consider screenshot fallback if the PDF cannot be parsed reliably."
                )
            else:
                st.markdown(
                    '<div class="pwc-download-toolbar"><div class="pwc-download-toolbar-title">Excel downloads</div></div>',
                    unsafe_allow_html=True,
                )
                grid = st.columns(min(3, len(successes)))
                for i, r in enumerate(successes):
                    with grid[i % len(grid)]:
                        st.download_button(
                            label=f"Download · {r.excel_filename or 'workbook.xlsx'}",
                            data=r.excel_bytes or b"",
                            file_name=r.excel_filename or "schedule_p.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_pdf_{i}_{r.excel_filename}",
                            use_container_width=True,
                        )
                if len(successes) > 1:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        for r in successes:
                            zf.writestr(r.excel_filename or "output.xlsx", r.excel_bytes or b"")
                    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
                    st.download_button(
                        label="Download all workbooks (ZIP)",
                        data=buf.getvalue(),
                        file_name="schedule_p_extractions.zip",
                        mime="application/zip",
                        key="dl_zip_pdf_all",
                        use_container_width=True,
                    )

    # --- 3–4 Screenshot results ---
    elif shot_results:
        with st.container(border=True):
            _section_heading(
                "3",
                "Screenshot intake summary",
                "Validated images for manual review — no automated table extraction yet.",
            )
            accepted = sum(1 for r in shot_results if r.success)
            failed_v = len(shot_results) - accepted
            if accepted > 0:
                st.markdown(
                    '<div class="pwc-run-complete">Images accepted — download the ZIP bundle for manual follow-up.</div>',
                    unsafe_allow_html=True,
                )
            _render_screenshot_kpi_row(len(shot_results), accepted, failed_v)
            _render_screenshot_results_table(shot_results)
            if failed_v:
                with st.expander("Rejected images — detail", expanded=failed_v == len(shot_results)):
                    for r in shot_results:
                        if not r.success:
                            st.markdown(f"**{_escape(r.source_filename)}**")
                            st.caption(_escape(r.error_message or "Validation failed."))

        with st.container(border=True):
            _section_heading(
                "4",
                "Download screenshot bundle (fallback path)",
                "ZIP contains originals as uploaded (successful validations only). "
                "This path does not generate Excel or run table OCR.",
            )
            ok_shots = [r for r in shot_results if r.success and r.image_bytes]
            if not ok_shots:
                st.warning(
                    "No images passed validation. Fix file type (PNG/JPEG), size, or capture quality and try again."
                )
            else:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for r in ok_shots:
                        zf.writestr(r.source_filename, r.image_bytes or b"")
                st.download_button(
                    label="Download screenshot bundle (ZIP)",
                    data=buf.getvalue(),
                    file_name="schedule_p_screenshot_manual_review.zip",
                    mime="application/zip",
                    key="dl_zip_screenshots",
                    use_container_width=True,
                )
                if len(ok_shots) == 1:
                    r0 = ok_shots[0]
                    st.download_button(
                        label=f"Download single image · {r0.source_filename}",
                        data=r0.image_bytes or b"",
                        file_name=r0.source_filename,
                        mime="image/png" if r0.source_filename.lower().endswith(".png") else "image/jpeg",
                        key="dl_single_shot",
                        use_container_width=True,
                    )

    # --- 5 Technical ---
    tech_payload: list[dict[str, object]] | None = None
    if st.session_state.get(SK_TECH):
        if pdf_results:
            tech_payload = [_result_payload(r) for r in pdf_results]
        elif shot_results:
            tech_payload = [screenshot_row_payload(r) for r in shot_results]

    if tech_payload is not None:
        with st.container(border=True):
            _section_heading("5", "Technical panel", "Structured summaries only.")
            with st.expander("Run payload (JSON)", expanded=False):
                st.json(tech_payload)

    st.markdown(
        """
        <div class="pwc-footer">
          Visual styling is PwC-inspired for internal-tool aesthetics only — not official branding.
          Validate Excel outputs against source filings; screenshot bundles require manual handling until OCR is enabled.
        </div>
        """,
        unsafe_allow_html=True,
    )
