# Hosted Study Buddy

This deployment uses one Linux VM, Docker Compose, Caddy TLS, and a persistent local volume. It supports public signup with private workspaces. Registrations are capped at 50 by default. Pick a VM with at least 2 GiB available to the application, extra memory for the host, and persistent storage. A free VM can serve a small cohort, subject to the provider's capacity and usage limits.

## Prepare the host

1. Provision an instance explicitly labelled Always Free eligible in your Oracle Cloud home region. Use Ubuntu or another Linux distribution that supports Docker Engine and its Compose plugin.
2. Reserve a stable public IP if your free account includes one and point a public DNS hostname at it. Open TCP 80 and 443 in the cloud firewall and host firewall. Keep SSH restricted to the addresses used for administration.
3. Clone the GitHub repository to the VM and configure the deployment:

```sh
git clone https://github.com/Amal-05/ktu_study_buddy.git
cd ktu_study_buddy
umask 077
cp deploy/host.env.example .env.host
```

Set `BUDDY_HOST` in `.env.host` to the public DNS hostname. Any public origin used for signup needs a valid HTTPS certificate. Caddy obtains and renews that certificate automatically when DNS and inbound ports are available.

```sh
docker compose --env-file .env.host config --quiet
docker compose --env-file .env.host up -d --build
docker compose --env-file .env.host ps
curl --fail "https://YOUR-HOST/api/health"
```

Open the public URL in an unauthenticated browser, create a test account, and confirm that a second account has an independent library before sharing the URL. The deployment volume starts empty. Each student configures their own Gemini or other supported API key inside their account. Live requests use that account's provider quota. Web research requires a separate operator-provided Tavily key and is disabled by default. When enabled, hosted citation verification is restricted to the official KTU hostnames.

## Operate and update

- Keep `study_data`, `caddy_data`, and `caddy_config` across deployments. Avoid commands that remove the data volumes.
- Before updates, run `sh deploy/backup.sh /absolute/path/study-buddy-YYYY-MM-DD.tar.gz`. The script briefly stops the app for a consistent snapshot. Store backups encrypted and off the VM. They contain account records, study data, and the files needed to decrypt provider credentials.
- To update, run `git pull --ff-only` and `docker compose --env-file .env.host up -d --build`, then verify health and a sign-in. Alembic applies study database migrations on startup.
- Restore a backup only into an empty deployment data volume with matching application code. Bring up the services on a fresh host, stop the app, then unpack with `docker compose --env-file .env.host run --rm --no-deps -T --entrypoint tar app -C /data -xzf - < /path/to/backup.tar.gz`. Start the app and check login and file access before switching DNS.
- Watch disk usage and service health. Pause registration with `BUDDY_OPEN_SIGNUP=0` when capacity is tight. Increase account or workspace limits only after checking available disk and memory.
- Provider secrets, browser sessions, and user files are private runtime data. Use HTTPS and keep the app container's port on the internal Compose network.
- The app uses one worker and SQLite per account. The free-host package is designed for a single instance. Schedule backups and plan a maintenance window for updates.

Reference material includes [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [Docker Engine installation](https://docs.docker.com/engine/install/), and [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).
