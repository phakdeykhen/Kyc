# KYC VM deployment

Website: https://kyc.opssolution.tech · VM: `104.248.154.28` (shared with POS and other apps)

GitHub Actions runs `test → build_smoke_deploy` on pushes to `main`. Pull requests run
the tests, image builds and smoke test only. A manual run is also available. This follows
the POS pipeline: the smoke-tested images themselves are piped over SSH to a
command-restricted key, so the VM needs no GitHub token or registry credentials.

Nginx on the host terminates HTTPS and proxies to `127.0.0.1:3400` (3300 is POS). The
`web` container serves the React app and proxies `/v1/` and `/health/` to the `api`
container. PostgreSQL 17 runs in the stack with TLS on its private network, because
production mode refuses a database link without it.

The stack runs with `ENVIRONMENT=production`. The API refuses to start without the
restricted `kyc_app` role, TLS, four separate keyrings, the host allow-list and
required document consent. There is no development key, no `/capture` page and
no public API docs.

## Files on the VM (`/opt/kyc`, mode 700)

| File | Purpose |
| --- | --- |
| `kyc.env`, `postgres.env` | Generated once on the VM by `bootstrap.sh` (mode 600). **Back them up separately:** without the keyrings the encrypted captures, identity fields and templates are unreadable. |
| `compose.yaml`, `receive-release.sh`, `nginx.conf` | Copied from this directory by an administrator |
| `postgres-init.sh` | Copy of `infra/postgres-init.sh`; creates the restricted role on first start |
| `pg-tls/` | Self-signed certificate for the private database link |
| `models/` | Pinned YuNet/SFace face models and `tessdata-best` Khmer/English OCR models, mounted read-only |
| `releases/<sha>/images.env`, `current`, `previous` | Exact image tags per release |

## One-time provisioning (done 23 Oct 2026)

1. Generate a dedicated deployment key: `ssh-keygen -t ed25519 -f ~/.ssh/kyc_deploy`.
2. Copy `compose.yaml`, `receive-release.sh`, `bootstrap.sh`, `nginx.conf`,
   `infra/postgres-init.sh`, the public key as `kyc-ci-key.pub` and `var/models/` to
   `/opt/kyc`. Check the `.onnx` SHA-256 values against `scripts/download_face_models.py`.
3. Run `bash /opt/kyc/bootstrap.sh`. It never overwrites existing secrets.

Repository Actions secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
| --- | --- |
| `KYC_DEPLOY_SSH_KEY` | The private key `~/.ssh/kyc_deploy` |
| `KYC_DEPLOY_KNOWN_HOSTS` | The VM's host key line from a trusted `known_hosts` (`ssh-keygen -F 104.248.154.28`) |

## Releases

Each push to `main` builds `kyc-api:<sha>` and `kyc-web:<sha>`, smoke-tests them against
a throwaway TLS database in production mode, and sends them to the VM. `receive-release.sh`
takes a lock, then:

1. starts PostgreSQL;
2. runs `scripts/bootstrap_local.py` (Alembic upgrade and the declared role grants);
3. starts the stack and waits for health checks;
4. checks `/health/ready`, the React app, and that an unauthenticated session request gets 401.

If a step fails, it restores the previous images. Migrations are forward-only and stay
applied, so keep schema changes backward-compatible with the previous release.

## Operations

```bash
ssh -i ~/.ssh/kpk root@104.248.154.28
cd /opt/kyc
dc() { docker compose --env-file current/images.env -f compose.yaml "$@"; }
dc ps
dc logs --tail 100 api
```

Provision an organization, an API key and a reviewer (tokens are shown once):

```bash
dc run --rm admin python scripts/manage_tenants.py org create "Organization name"
dc run --rm admin python scripts/manage_tenants.py key create ORG_UUID "Console" \
  --scope sessions:write --scope sessions:read --scope results:identity
dc run --rm admin python scripts/create_reviewer.py create "Reviewer name" --role REVIEWER --organization ORG_UUID
```

Retention: run `dc run --rm admin python scripts/purge_captures.py` hourly (cron), and
back up the `kyc_postgres_data` and `kyc_capture_data` volumes together with `kyc.env`.

Manual rollback to the preceding healthy release:

```bash
cd /opt/kyc
target=$(readlink previous)
docker compose --env-file "$target/images.env" -f compose.yaml up -d --wait
ln -sfn "$(readlink current)" previous
ln -sfn "$target" current
```

## Limits on this VM

Two vCPUs and ~4 GB RAM, shared with other apps. One API worker with inline OCR: a
Khmer card takes roughly 20–45 s to read, and concurrent uploads queue. The platform is
**NOT_READY** for production use with real customers (see
`docs/production-readiness-2026-10-07.md`). Face match and liveness are uncalibrated, so
every face session goes to manual review.
