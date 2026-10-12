# Decision Drift Lab v17 

## What visitors can do

Browse the research report at `/report`, archived results at `/results`, experiment inputs in the lab, and download original JSON. Visitors may also execute experiments using **their own** Typesafe AI Jev and OpenAI API keys, or select Demo mode without paid API calls. No keys are provided by the site operator. Generated runs remain in each visitor’s browser unless they export them; they are **not published automatically**.

**Credential warning:** The server receives each submitted key and relays it to the corresponding provider. Its operator could inspect those keys. For maximum control, fork and run locally rather than supplying a key to an untrusted hosted service. Browser keys use session storage and are not intentionally included in exported JSON.

This app can be deployed on on Docker


```env
PUBLIC_READ_ONLY=false
REQUIRE_USER_KEYS=true
RATE_LIMIT_PER_HOUR=90
GITHUB_REPO_URL=https://github.com/iouak/dda
```

`GITHUB_REPO_URL` is **configuration**, not a real repository link until replaced. When configured, the site displays a **Fork on GitHub** link. Users can fork using GitHub’s UI, clone, and run locally with `python server.py` or `docker compose up --build`. Repo must be public for arbitrary visitors to fork it.

## Security and limitations

Public BYOK mode only permits built-in Jev and OpenAI model calls. It **disables the generic/custom endpoint proxy** to reduce server-side request forgery exposure. Server-side keys are *not used* in BYOK mode. Requests have an hourly per-IP limit (in memory) and a browser origin/host check. This is lightweight protection, **not** full public-service security: Coolify proxy addresses can affect per-IP throttling, and a public API can still be scripted. Use WAF and global rate limiting at your reverse proxy, monitor traffic, and deploy only if you accept the operational risk. This lightweight HTTP server is a research prototype, not a hardened multi-user service.

Set `PUBLIC_READ_ONLY=true` to block all live model calls again.

## Pages

- `/` interactive lab
- `/results` previous results
- `/report` research report
- `/api/results` indexed archive
- `/api/experiments` input catalogue
- `/api/site` repository-link configuration

## Running locally

```sh
python server.py
# Open http://localhost:8765
```

Or use `docker compose up --build` (binds only to localhost by default). Populate `GITHUB_REPO_URL` in `compose.yaml` if desired.

## Visitor API-key privacy notice

We do not intentionally store, retain, or log visitor API keys on the server and do not include credentials in archived experiment results. Live requests do pass through the Python backend, so the operator could technically access submitted keys. Visitors may inspect the public source code. We recommend forking the repository and running the application locally for maximum control of personal API credentials. Browser session storage is cleared on tab/session closure, subject to browser behavior; the **Clear session keys** button provides an explicit removal control.

