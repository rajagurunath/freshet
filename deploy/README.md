# Deploy the Freshet hub on EC2

The central hub is the FastAPI service in `apps/api`. Developers' desktop apps push
their curated sessions to it; it stores them (disk **or** S3), builds the knowledge
graph, and answers company-wide search / RAG / AICP handoff. This runs it on a single
EC2 host behind HTTPS.

## What you get
- The hub container (`ghcr.io/rajagurunath/freshet-api`, built by CI) on port 8787.
- **Caddy** in front for automatic Let's Encrypt TLS + reverse proxy.
- Storage on a Docker volume by default, or **S3** for raw session blobs.

## 1. Launch the instance
- **AMI:** Ubuntu 22.04+. **Type:** t3.small is fine to start (bump for large corpora / local embeddings).
- **Security group inbound:** `443` and `80` from the world (or your office CIDR), `22` from your IP only.
- **DNS:** point an A record (e.g. `hub.example.com`) at the instance's public IP.
- **(S3 option):** attach an IAM role with `s3:PutObject/GetObject` on your bucket, or use keys in `.env`.

## 2. Install Docker
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

## 3. Configure + run
```bash
git clone https://github.com/rajagurunath/freshet.git
cd freshet/deploy
cp .env.example .env
# Edit .env: set FRESHET_DOMAIN, real API_KEYS, the two token secrets,
# and (optionally) S3_BUCKET + AWS creds and ANTHROPIC_API_KEY.
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f api   # watch it come up
```
Caddy fetches a TLS cert automatically. Verify:
```bash
curl https://hub.example.com/healthz            # -> {"status":"ok"}
```
> Don't have GHCR access yet? Set `# build: ../apps/api` (uncomment) in the compose
> file to build the image on the host instead of pulling.

## 4. Point the desktop app at the hub
In the Freshet desktop app → **Settings**: set **API base URL** to `https://hub.example.com`
and **API key** to one of the keys from `API_KEYS`. Turn on auto-sync (or push per session).
Add the hub origin to `CORS_ORIGINS` in `.env` if you serve the app from a browser.

## Storage: disk vs S3
- **Disk (default):** everything lands on the `freshet_data` Docker volume
  (`/data/{lancedb,blobs,graph.db,…}`). Snapshot the EBS volume to back up.
- **S3:** set `S3_BUCKET` (+ region and, if not using an instance role, the AWS keys).
  Raw session blobs go to S3; the LanceDB vector index + SQLite graph stay on the
  local volume (they're derived and rebuildable from the blobs).

## Uploading / storing sessions
The hub receives sessions via `POST /v1/sessions` (the desktop app's push, bearer-auth).
It writes the raw JSON to the blob store (disk or S3), chunks + embeds it into LanceDB,
and extracts the knowledge graph. No session data is on the hub until a developer
explicitly pushes it (redacted client-side first).

## Operations
```bash
docker compose -f docker-compose.prod.yml pull && \
docker compose -f docker-compose.prod.yml up -d      # upgrade to a new image
docker compose -f docker-compose.prod.yml down       # stop (data volume persists)
```

## Security checklist
- Replace `API_KEYS` — the hub **refuses to start** in `ENVIRONMENT=production` with the
  default token secrets, so set `ASSET_TOKEN_SECRET` / `SHARE_TOKEN_SECRET` to random values.
- Keep `22` locked to your IP; only expose `443`.
- Scope `CORS_ORIGINS` to the app origins you actually use.
- Prefer an IAM instance role over long-lived AWS keys for S3.
- Back up the data volume (or rely on S3 for blobs + accept index rebuild).
