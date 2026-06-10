# Context Hub — developer convenience targets.
.PHONY: help api-install api desktop-install desktop-dev desktop-build tauri test landing

help:
	@echo "Context Hub targets:"
	@echo "  make api-install     # create venv + install the FastAPI service"
	@echo "  make api             # run the central API on :8787"
	@echo "  make desktop-install # npm install the desktop app"
	@echo "  make desktop-dev     # run the desktop UI in a browser (Vite)"
	@echo "  make tauri           # run the native desktop app (Tauri)"
	@echo "  make desktop-build   # typecheck + production web build"
	@echo "  make test            # run API + desktop test suites"
	@echo "  make landing         # serve the static landing page on :8788"

api-install:
	cd apps/api && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

api:
	cd apps/api && . .venv/bin/activate && uvicorn contexthub.main:app --reload --port 8787

desktop-install:
	cd apps/desktop && npm install

desktop-dev:
	cd apps/desktop && npm run dev

tauri:
	cd apps/desktop && npm run tauri dev

desktop-build:
	cd apps/desktop && npm run build

landing:
	cd apps/web && python3 -m http.server 8788

test:
	cd apps/api && . .venv/bin/activate && pytest -q
	cd apps/desktop && npx vitest run
