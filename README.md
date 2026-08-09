# MyWBooks API

**MyWBooks** is a lightweight API service for managing and automating the download and conversion of web novels into eBooks.

The main idea is simple:  
When a new chapter is released on your favorite web novel site, MyWBooks automatically downloads it, converts it to EPUB, and sends it to your eReader — instantly.

---

## ✨ Features

- 📖 **Fetch and convert web novels**  
  Currently supports [RoyalRoad](https://www.royalroad.com/), with plans to add other platforms later.
  

- 🧹 **Maintenance tools**  
  Clean up old tasks and expired files automatically via background jobs or API calls.

---


## ✨ Soon Features

- ⚙️ **Automated updates**  
  Schedule downloads or let the system automatically fetch new chapters as they’re published.

- 📤 **Send directly to your reading device**  
  Automatically deliver new eBooks via:
  - Email (e.g. Kindle send-to-email)
  - Dropbox sync
  - (Planned) Custom delivery options or other cloud services


## 🏗️ Architecture Overview

The project consists of:
- **Python backend** (`FastAPI`) — Handles the API, downloads, conversions, and scheduling.
- **Frontend (optional)** — A SvelteKit web app for managing your library (not required to run the API).
- **Database** — Tracks books, tasks, and device configurations.
- **Workers / background tasks** — Handle scheduled updates and cleanups.

---

## 🚀 Getting Started

### Requirements
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- SQLite or PostgreSQL
- (Optional) Nginx for routing if deploying multiple apps on one server

### Setup

```bash
git clone https://github.com/torhoaakon/mywbooks.git
cd mywbooks/api

# Install dependencies
uv sync

# Run the API
uv run fastapi dev src/mywbooks/api/app.py
```

Then visit:  
👉 http://localhost:8000/docs to explore the API.

---

## ⚡ Example Workflow

1. Add a new book by providing the RoyalRoad URL.  
2. The API fetches all available chapters and converts them into an EPUB file.  
3. When new chapters appear, the system automatically:
   - Downloads them
   - Updates the EPUB
   - Sends it to your configured reading device.

---

## 🔧 Deployment

You can deploy on your own server (e.g., Oracle Cloud Ubuntu instance):

- **Backend:** Runs as four Docker Compose services (`api`, `worker`, `maintenance`, `redis`) instead of systemd units — see below.
- **Frontend:** [/frontend](https://github.com/torhoaakon/MyWBooks-page) Build with SvelteKit and serve static files through Nginx, same as local development. The frontend is *not* containerized.
- **Routing:** Nginx (on the host) proxies `/api/...` to the `api` container on `127.0.0.1:8000`.

### Backend via Docker Compose

```bash
cd api
cp .env.example .env   # fill in AUTHX_SECRET_KEY, SUPABASE_*, SMTP_* etc.
docker compose up -d --build
```

This builds one image (`api/Dockerfile`) and runs it as three services (`api`,
`worker`, `maintenance`) plus a `redis` service, matching the four processes
in the `Procfile`. The SQLite DB and the EPUB/cache directories live on a
shared named volume (`data`) so they survive `docker compose down`/`up`.
`DATABASE_URL`, `CACHE_DIR`, `EPUB_DIR` and `REDIS_URL` are wired by
`docker-compose.yml` itself — only secrets (auth, SMTP) need to go in `.env`.

The `api` service publishes port 8000 on the host, same as running uvicorn
directly — Nginx proxies to it exactly as before.

Example Nginx snippet (note: no trailing slash on `proxy_pass` — the app's
routes are mounted under `/api`, so the prefix must be preserved, not
stripped):

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
}
```

---

## 🧭 Future Plans

- Add support for other platforms (e.g. ScribbleHub, Wattpad).
- Add better delivery methods (e.g. Calibre Companion sync, Nextcloud).
- Web-based task scheduling and monitoring.
- Multi-user support (optional).

---

## 🤝 Notes

This project is meant for **personal use** — a small, private tool for book enthusiasts who want a smooth reading experience without manual downloads.
