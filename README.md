# Product Intelligence

Projects and integration management — the V1 control plane.

Planning documents live in [`docs/`](docs/); permanent rules for contributors
(human or Claude Code) are in [`CLAUDE.md`](CLAUDE.md).

**Current state: Milestone 1.** Authentication, workspaces, and projects work
end to end. Google Analytics 4 and Search Console integrations arrive in
Milestones 3–5; production deployment in Milestone 7.

## Stack

Next.js · React · TypeScript · Tailwind CSS · shadcn/ui — `apps/web`
Django · Django REST Framework · PostgreSQL — `apps/api`

## Local development

Requires Python 3.11+, Node 20+, and PostgreSQL 16+ (Docker or local).

```bash
# 1. Configuration
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # paste as DJANGO_SECRET_KEY

# 2. Database — either Docker…
docker compose -f compose.dev.yaml up -d
#    …or an existing local PostgreSQL, with DATABASE_URL pointed at it.

# 3. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r apps/api/requirements-dev.lock.txt
cd apps/api && python manage.py migrate && python manage.py runserver 127.0.0.1:8000

# 4. Frontend (second terminal)
cd apps/web && npm install && npm run dev
```

Open <http://localhost:3000> and create an account. A workspace is created for
you automatically; then create a project.

### Google OAuth (Milestone 3)

Connecting a provider needs a **development** Google Cloud OAuth client. Set
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `CREDENTIAL_ENCRYPTION_KEYS` in
`.env` — see `.env.example` for how to generate the encryption key. The
authorized redirect URI on the Google client must match
`GOOGLE_OAUTH_REDIRECT_URI` exactly; locally that is
`http://localhost:3000/api/integrations/oauth/google/callback`.

Without those values the app runs normally; only **Connect** fails.

Django admin is at <http://127.0.0.1:8000/admin/> — create a superuser with
`python manage.py createsuperuser`. V1 has no invitation UI, so additional
workspace members are added there.

### Why development is same-origin

The browser talks only to `http://localhost:3000`. Next.js proxies `/api/*` to
the Django dev server, so cookies, CSRF, and session behaviour are identical to
production behind the reverse proxy. Nothing in the app works only in
development.

API paths carry no trailing slash (`/api/auth/login`). See §10 of
[`docs/V1_TECHNICAL_DESIGN.md`](docs/V1_TECHNICAL_DESIGN.md) for why.

## Python dependencies

Four files, two of which are generated:

| File | Purpose |
| --- | --- |
| `requirements.txt` | Direct runtime dependencies, human-readable ranges. Edit this. |
| `requirements-dev.txt` | Adds the test dependencies. Edit this. |
| `requirements.lock.txt` | Every runtime dependency pinned exactly. **Install this.** |
| `requirements-dev.lock.txt` | Same, including test dependencies. **Install this.** |

Install from a lock file so everyone — and the production image — gets the same
versions. After changing a direct dependency, regenerate both locks and commit
them with the change:

```bash
./scripts/lock-python-deps.sh
```

The script installs the direct requirements into a throwaway virtualenv and
freezes the resolved set. It needs nothing beyond `pip` and `venv`. The locks
target Linux / CPython 3.11+, which is what development and production both use.

## Checks

```bash
cd apps/api && python -m pytest tests/     # backend tests
cd apps/web && npm test                    # frontend component tests
cd apps/web && npx tsc --noEmit            # type checking
cd apps/web && npm run lint                # linting
cd apps/web && npm run build               # production build
```

## Security

Never commit `.env`. `.env.example` holds names and placeholders only.
Tenant isolation, CSRF, and credential handling rules are in `CLAUDE.md` and are
covered by tests — read them before changing anything in those paths.
