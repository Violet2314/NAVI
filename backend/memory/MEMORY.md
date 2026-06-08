## Ongoing Projects

### NeuroAgent Paper PPT (2026-05-08)
- **Source**: arXiv:2605.06584v1 — "NeuroAgent: LLM Agents for Multimodal Neuroimaging Analysis and Research"
- **Task**: 15-page Chinese PPT, dark theme, consulting style
- **Project path**: `projects/neuroagent-ppt_ppt169_20260508`
- **Status**: Strategist Phase (Step 4) — Eight Confirmations presented, awaiting user confirmation
- **Design decisions so far**: PPT 16:9, dark medical-tech colors (bg #0D2137, primary #1565C0, accent #00BFA5), phosphor-duotone icons, Arial + Microsoft YaHei fonts, body 18px baseline, placeholder images for figures
- **Next steps after confirmation**: Write design_spec.md + spec_lock.md → Image acquisition (skip, all placeholders) → Executor Phase (SVG generation) → Post-processing → Export to PPTX

### NAVI Intro PPT — Test Run (2026-05-07)
- **Project path**: `projects\navi-intro_ppt169_20260507`
- **Purpose**: Quick test of ppt-master skill installation; not a production deliverable
- **Status**: Executor Phase — design_spec.md + spec_lock.md written; SVG generation in progress (slides 01-04 done, slides 05-06 remaining)
- **Design**: 6-page dark theme (bg #0F1629, primary #00D4FF cyan, accent #7B61FF purple, secondary accent #FF6B9D pink), Microsoft YaHei + Arial fonts, 20px body baseline
- **Slide 04 (Architecture)**: 2-column layout — tech stack list (Backend: Python+FastAPI, Frontend: Tauri+Live2D, DB: SQLite+ChromaDB, Comm: WebSocket, AI: Multi-LLM) + system flow diagram (Desktop Collector → Memory System → LLM Agent, with Skill Plugins / Cron Scheduler / Live2D Output)

## Infrastructure: ppt-master Skill (2026-05-07)
- **Source**: https://github.com/hugohe3/ppt-master (MIT, v2.6.0)
- **Install path**: `skills/ppt-master/` inside Navi backend
- **Dependencies**: managed via `uv`; installed 45 packages (python-pptx, pymupdf, reportlab, beautifulsoup4, curl-cffi, etc.)
- **Key feature**: Generates natively editable PPTX with real DrawingML shapes/text boxes/charts, not images. Supports SVG→PPTX pipeline, AI image generation, animations, template replication.
- **Skill format compatibility**: Claude Code skills are compatible with Navi — both use SKILL.md + frontmatter + scripts structure
- **Pipeline**: Source Document → Create Project → Template Option → Strategist → [Image_Generator] → Executor → Post-processing → Export
- **Executor workflow detail**: Must batch-read all template SVGs before first page; re-read spec_lock.md per page; output template mapping declaration per page; design params confirmed before first SVG; page_rhythm tags (anchor/dense/breathing) control layout density

## Technical Notes

- **Python env on Navi backend**: venv uses Python 3.11 at `D:\learn\Navi\backend\.venv\Scripts\python.exe`; pdfplumber is installed in system Python 3.9, not venv. pymupdf (fitz) is available in venv and works for PDF text extraction.
- **Windows exec output issue**: `exec` tool output is consistently swallowed/empty on Windows. Reliable workaround: write Python scripts to .py files, then execute them. File read via `read_file` works for extracting results.
- **PDF extraction workflow**: Use pymupdf (fitz) to extract text page-by-page, write to UTF-8 file, then read the file. Avoid direct print output due to GBK encoding issues on Windows.

## Scope Rules (IMPORTANT — SSOT separation)
- THIS memory is for NARRATIVE context: ongoing projects, technical decisions,
  cross-session todos, recurring topics.
- DO NOT write user personal facts here (preferences, habits, biographical,
  skills, relationships, status). Those are auto-extracted into FactMemory
  by a separate pipeline. Writing them here causes duplication and drift.
- If a piece of info is "what the user IS / LIKES / DOES generally" → skip it.
- If a piece of info is "what we are WORKING ON / DECIDED / PLANNED" → keep it.