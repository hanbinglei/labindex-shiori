# LabIndex Shiori

> Lab document indexing and research lineage visualization system — Full-text search with Meilisearch + researcher genealogy graph.

**LabIndex Shiori** is a self-hosted, local-first document management system for academic research labs. It indexes papers, theses, and experimental data from shared network drives, provides full-text search via Meilisearch, and automatically discovers **research lineage** — visualizing how research topics and techniques are passed down between successive students and researchers.

## Features

- **Full-text search** — Instant, typo-tolerant search across all indexed papers and documents (Meilisearch backend)
- **Research lineage graph** — Automatically discovers citation relationships, topic succession, and keyword overlaps between researchers across graduation years
- **Multi-language** — Japanese, Chinese, and English locale support
- **Rule-based** — Zero AI/ML dependencies. All metadata extraction uses path-based parsing, TF-IDF similarity, and pattern matching
- **Incremental scanning** — Fast daily updates by detecting only new/changed files
- **NAS-friendly** — Works over UNC network paths, designed for shared lab storage

## Quick Start

### Prerequisites

- Windows 10+
- Python 3.11+
- uv (recommended) or pip

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/hanbinglei/labindex-shiori.git
cd labindex-shiori

# 2. Create virtual environment and install dependencies
uv venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
uv pip install -r requirements.txt

# 3. Create config.yaml from template
cp config.example.yaml config.yaml
# Edit config.yaml:
#   - Set your NAS path(s) in SCAN_ROOTS
#   - Generate a Meilisearch master key: python -c "import secrets; print(secrets.token_hex(20))"
#   - Set it in meilisearch.api_key

# 4. Download Meilisearch
#    Download meilisearch.exe from https://github.com/meilisearch/meilisearch/releases
#    Place it in the project root directory

# 5. Initialize the database
python -m src.main init

# 6. Scan your documents
python -m src.main scan --root "\\YOUR_NAS\Papers" --full

# 7. Parse metadata
python -m src.main parse --force

# 8. Calculate research relations
python -m src.main relation

# 9. Index to Meilisearch
python -m src.main index --full

# 10. Start the web UI
python -m src.main serve
# Open http://127.0.0.1:5000 in your browser
```

> **Tip:** On Windows, use `启动.bat` to start all services, or `扫描.bat` for the interactive scanning pipeline.

## Project Structure

```
LabIndex Shiori/
├── src/
│   ├── main.py              # CLI entry point
│   ├── config.py            # Configuration loader
│   ├── scanner/             # File scanning (NAS traversal, hashing)
│   ├── parser/              # Metadata extraction (path, PDF, DOCX, PPTX)
│   ├── relation/            # Research relation discovery (M10 engine)
│   ├── indexer/             # Meilisearch indexing + auto-classification
│   ├── overlay/             # Manual correction layer
│   ├── web/                 # Flask web application
│   │   ├── app.py           # API endpoints
│   │   ├── templates/       # Jinja2 templates
│   │   └── static/          # CSS, JS
│   ├── database/            # SQLite schema & operations
│   └── i18n/                # Internationalization
├── locales/
│   ├── ja.json              # Japanese locale
│   ├── zh.json              # Chinese locale
│   └── en.json              # English locale
├── data/                    # Runtime data (gitignored)
├── config.example.yaml      # Configuration template
├── 启动.bat                 # Windows launcher
├── 扫描.bat                 # Windows scan pipeline
├── Clear.bat                # Database reset
└── Parse.bat                # Re-parse & re-index tool
```

## Architecture

| Layer | Technology | Purpose |
|-------|-----------|---------|
| File Scanner | Python + os.walk | Traverse NAS folders, compute file hashes |
| Parser | python-docx, python-pptx, PyMuPDF | Extract metadata (researcher, title, year, references) |
| Relation Engine | TF-IDF, regex | Discover citation links, topic succession, keyword overlap |
| Search Index | Meilisearch | Full-text search with typo tolerance |
| Web UI | Flask + Vanilla JS | Browser-based search + lineage visualization |

## Configuration

Copy `config.example.yaml` to `config.yaml` and customize:

| Setting | Description |
|---------|-------------|
| `SCAN_ROOTS` | UNC paths to scan (e.g., `\\NAS\Research`) |
| `meilisearch.api_key` | 40-char hex Meilisearch master key |
| `i18n.default_language` | Display language: `ja`, `zh`, or `en` |
| `scanner.supported_extensions` | File types to index (`.pdf`, `.docx`, `.pptx`) |

## License

MIT
