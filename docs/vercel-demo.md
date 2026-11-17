# Vercel demo deployment

This configuration hosts a **resettable sample-data demo**. Each Python worker
gets a fresh temporary SQLite database and sample room photos. Visitors sharing
a worker share its changes. Changes can disappear or differ between requests
when Vercel scales, replaces workers or redeploys. This includes uploaded photos,
registrations, bookings and payments. Do not enter real resident or banking data.

Your local `instance/` database, private settings, secret and uploads are excluded
from deployment by `.vercelignore`. Demo mode also ignores `DATABASE_URL` and local
database settings. It never copies or seeds your existing installation.

## Deploy without Git

The existing `app.py` is the Flask entrypoint. `.python-version` selects Python
3.14. `vercel.json` runs the static-asset build and enables production/demo mode.
Leave the framework on Flask/automatic detection; do not select a static frontend
framework or override the output directory. GitHub is not required.

From the project directory, use Node.js/npm to run:

```sh
npx vercel@latest login
npx vercel@latest link
```

Create/link a Vercel project when prompted. Add these two secrets interactively:

```sh
npx vercel@latest env add SECRET_KEY production
npx vercel@latest env add RPMS_DEMO_PASSWORD production
```

Generate the session secret with:

```sh
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
```

Use a demo password of at least 10 characters. Both sample accounts use the
chosen `RPMS_DEMO_PASSWORD`; it is never committed or rendered in the pages.
Keep `SECRET_KEY` stable across workers and deploys. Do not add `DATABASE_URL`.
`RPMS_ENV=production` and `RPMS_DEMO=1` are already set by `vercel.json`.

Deploy:

```sh
npx vercel@latest --prod
```

For preview deployments, add both secrets to the Preview environment as well.
The build command copies `rpms/static` into `public/static`, preserving the app's
existing `/static/...` URLs while serving those assets through Vercel's CDN.
No database migrations or seed commands need to be run manually for this demo.

## Try the deployed demo

- Staff: `/login`, email `admin@rpms.local`, your demo password.
- Customer: `/customer/login`, phone `012345678`, the same demo password.
- Visitors: `/customer/` to browse the sample rooms.

Check the dashboard, room photos, customer rentals, invoice and sample payment
history. The demo notice should appear on both staff and customer pages.
Requests are capped at 4 MiB total in demo mode; keep photo uploads below this
including form overhead. Uploading several photos may require smaller files.

If startup fails, check Vercel Runtime Logs for a missing secret, short demo
password, dependency installation error or failed sample seeding. If styling is
missing, check the build log for `Prepared public/static for Vercel.`

## Local use

Normal `.venv/bin/python app.py` still uses your local database unless you set
`RPMS_DEMO=1`. To preview the disposable demo locally, export `RPMS_DEMO=1`,
`SECRET_KEY` and `RPMS_DEMO_PASSWORD` in a separate terminal, then start the app.
Leave `RPMS_ENV` unset for HTTP localhost; production mode requires HTTPS cookies.

## Persistent hosting later

Permanent records need a hosted database and durable object storage for room
photos, utility evidence and payment proofs. Simply setting a PostgreSQL URL does
not make local uploads persistent. That requires a separate storage integration.

References: [Flask on Vercel](https://vercel.com/docs/frameworks/backend/flask),
[Vercel function filesystem](https://vercel.com/docs/functions/runtimes#file-system-support).
