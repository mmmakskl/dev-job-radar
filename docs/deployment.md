# Deployment

The supported production workflow builds a `linux/amd64` image in GitHub Actions and deploys it to a VPS over SSH. The current deployment script expects a `master` checkout at `/home/deploy/apps/dev-job-radar` owned by the `deploy` user. For local usage and configuration, start with the [README](../README.md).

## Prepare the server

1. Install Docker Engine and the Compose plugin. Ensure the `deploy` user can access Docker, has SSH access, and can clone the repository with a read-only GitHub deploy key.
2. Clone the repository to `/home/deploy/apps/dev-job-radar`, check out `master`, copy `.env.example` to `.env`, and configure it on the server. Keep `.env` private.
3. Prepare `data/` for container UID/GID `10001`, and install the Google service-account JSON as `secrets/google-credentials.json`, readable by that UID. The account needs editor access to the spreadsheet.
4. Configure the public hostname in [`deploy/caddy/Caddyfile`](../deploy/caddy/Caddyfile), `ADMIN_ALLOWED_HOSTS`, and `CANDIDATE_MINIAPP_URL`. Use HTTPS and secure cookies for public access. Configure DNS and allow ports 80 and 443.
5. Configure `ADMIN_PASSWORD` and a unique `ADMIN_SESSION_SECRET`; generate the latter with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Set the Telegram, Mistral, Google Sheets, and Candidate Bot variables described in [`.env.example`](../.env.example).

After building or loading the image, authorize the Telegram session once:

```bash
cd /home/deploy/apps/dev-job-radar
docker compose config --quiet
docker compose run --rm bot python scripts/auth.py
```

The session is stored in `data/`. Stop any other process using that session before authorizing or processing history. Enable at least one Telegram source through the admin settings before live monitoring; `TARGET_CHANNELS` is only a first-run bootstrap.

## Deploy

The [production workflow](../.github/workflows/deploy.yml) runs on pushes to `master` and through manual dispatch on `master`. It runs Python and frontend checks, browser e2e tests, and image builds before deployment. Configure these secrets in the GitHub `production` environment:

- `VPS_HOST` and `VPS_USER` (`deploy`)
- `VPS_SSH_KEY`: a dedicated private key authorized on the VPS
- `VPS_KNOWN_HOSTS`: the VPS host key, verified against its fingerprint through a trusted channel

The workflow loads the image over SSH, tags the previous image, and invokes [`scripts/deploy.sh`](../scripts/deploy.sh) with the checked commit SHA. The script validates configuration, starts the services, and checks readiness. On a failed deployment after service changes it attempts to restore the previous image. Server `.env`, credentials, session, and data are not replaced by the workflow.

For a manual deployment from the configured server checkout, with the required files and session already in place:

```bash
cd /home/deploy/apps/dev-job-radar
bash scripts/deploy.sh
docker compose --profile candidate ps
```

The script builds the image unless `SKIP_IMAGE_BUILD=1` is set; only use that setting when the intended image is already loaded. The production script starts the Candidate Bot profile, so its required configuration must be present.

## Verify and recover

```bash
docker compose --profile candidate ps
docker compose logs --tail=100 bot admin
docker compose --profile candidate logs --tail=100 candidate-bot
```

Check service health and the GitHub Actions run. Automatic personal delivery additionally requires `CANDIDATE_PROFILE_DELIVERY_ENABLED=true`, an active matching profile, and an automatic delivery mode. Enabling it on an existing database can deliver accumulated matches; review the backlog first.

To restore the previous image manually:

```bash
cd /home/deploy/apps/dev-job-radar
docker tag dev-job-radar-bot:previous dev-job-radar-bot:latest
docker compose --profile candidate up -d --no-build --remove-orphans
docker compose --profile candidate ps
```

Verify service health after restart. Revert source changes with a new `git revert` commit rather than resetting the server checkout. Back up `data/` and protect `.env`, the Google credentials, and Telegram sessions; do not remove persistent data during cleanup.
