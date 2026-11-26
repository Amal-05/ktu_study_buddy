# Deploy on Render Free

A Render Free Docker service serves the app over HTTPS. A separate Neon PostgreSQL database stores accounts, study data, sessions, upload originals, and encrypted AI credentials. Use a dedicated Neon project.

1. Create a Free project in the [Neon console](https://console.neon.tech/). Where available, choose a region close to Render's Singapore region.
2. Open **Connect** in Neon and copy its PostgreSQL connection string. The pooled connection string with SSL is supported.
3. Open the [Render Blueprint installer](https://render.com/deploy?repo=https://github.com/Amal-05/ktu_study_buddy). Link the GitHub repository if prompted and review the `free` web service.
4. Paste the Neon connection string into `BUDDY_DATABASE_URL` in the Render deployment form. Render generates `BUDDY_PROVIDER_SECRET` and supplies the public HTTPS URL automatically.
5. Deploy, then check `/api/health` on the assigned URL. Create a test account, upload a file, and verify sign-in and downloading after a redeploy.

Keep the database connection string and provider encryption secret in Render's secret settings. Back up the database and preserve the encryption secret across redeploys so saved provider keys can still be read. Students supply their own AI keys in Settings.

The free configuration caps signups at 12 workspaces and targets 40 MiB per workspace. It uses keyword retrieval to fit Render's memory limit. Render may sleep when idle, so the next request can take about a minute. Queued notes resume when the process is running and a fresh lease is available.

Render's own free PostgreSQL instances expire after 30 days. This deployment uses Neon for ongoing storage within its free allowance. [Render free-tier limits](https://render.com/docs/free) · [Neon free PostgreSQL](https://neon.com/blog/neon-free-plan-1-gb-per-project)
