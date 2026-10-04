# jellyfin-media-manager

A Dockerized Python service that watches your Jellyfin media library and automatically organizes files into Jellyfin-compatible naming conventions.

## What it does

- Watches `/mnt/storage/Movies` and `/mnt/storage/TV Shows` for new files
- Looks up canonical metadata via TMDB (with optional TVDB fallback)
- Renames and moves files to match Jellyfin's expected structure:
  - **Movies**: `Movies/Movie Title (Year)/Movie Title (Year).mkv`
  - **TV Shows**: `TV Shows/Show Name/Season 01/Show Name - S01E01 - Episode Title.mkv`
- Handles torrent-style folders (picks the largest video file, discards extras)
- Deletes empty leftover folders after moving files
- Runs a full library scan on startup to catch already-misorganized files
- Supports dry-run mode to preview changes before committing

## Setup

1. Copy `.env.example` to `.env` and fill in your TMDB API key:
   ```
   cp .env.example .env
   ```
   Get a free TMDB API key at https://www.themoviedb.org/settings/api

2. Adjust the library paths in `docker-compose.yml` if your storage is mounted elsewhere.

3. Build and run:
   ```
   docker compose up -d
   ```

## Configuration

All settings are via environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `TMDB_API_KEY` | *(required)* | TMDB v3 API key |
| `TVDB_API_KEY` | *(empty)* | TVDB v4 API key (optional fallback for episode titles) |
| `TV_LIBRARY_PATH` | `/mnt/storage/TV Shows` | Path to TV library |
| `MOVIES_LIBRARY_PATH` | `/mnt/storage/Movies` | Path to movies library |
| `DRY_RUN` | `false` | Log intended moves without executing them |
| `LOG_LEVEL` | `INFO` | Logging verbosity (DEBUG, INFO, WARNING) |
| `SCAN_ON_START` | `false` | Full library scan on container start |
| `MIN_FILE_SIZE_MB` | `100` | Minimum file size to process (skips samples/trailers) |

## inotify limit (large libraries)

On the Docker host, you may need to increase the inotify watch limit:

```bash
echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

## Web Interface

Open `http://localhost:4000` after starting the container.

| Page | Description |
|---|---|
| **Dashboard** | Stats overview, recent activity, manual scan buttons |
| **Library** | Lazy file tree browser with organized/unorganized indicators |
| **Activity** | Full paginated list of all organize operations with filters |
| **Logs** | Real-time log stream via WebSocket; level filter + search |
| **Settings** | Edit runtime config (dry run, log level, file size threshold, etc.) |

Settings changed in the UI are persisted to `config.json` and survive container restarts.

## Architecture

```
src/
├── main.py          Entry point, wires together scanner + watcher + web UI
├── config.py        Pydantic-settings config; config.json overlay support
├── parser.py        guessit-based filename parsing
├── processor.py     Core pipeline: parse → lookup → move → clean
├── scanner.py       Full library scan with ThreadPoolExecutor
├── watcher.py       watchdog filesystem watcher with debounce queue
├── cleaner.py       Removes empty directories after moves
├── metadata/
│   ├── tmdb.py      TMDB v3 REST client (primary)
│   └── tvdb.py      TVDB v4 REST client (optional fallback)
├── organizer/
│   ├── movies.py    Movie destination path computation
│   └── tv.py        TV show destination path computation
└── web/
    ├── app.py       FastAPI app factory
    ├── api.py       REST endpoints (stats, scan, library, settings)
    ├── ws.py        WebSocket log streaming
    ├── state.py     Thread-safe shared in-memory state (ring buffers, scan status)
    ├── logging_handler.py  Bridges Python logging → web UI
    └── static/
        └── index.html  Alpine.js + Tailwind SPA
```
