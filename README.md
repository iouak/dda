# Decision Drift Lab 

This is the deployable repository root. There is **no nested application**.

## Structure

- `server.py`, `index.html`: interactive lab and Python API
- `results.html`: historical run browser
- `research-report.html`: research report
- `experiments/`: six canonical input JSON files
- `archived-results/`: eight historical result exports, their summaries, and catalogue
- `docs/`: background guides and original documentation
- `Dockerfile`, `compose.yaml`: deployment configurations
- `README-DEPLOY.md`: Coolify instructions

## Start

Docker: `docker compose up --build -d`
Python: `python server.py`

Visit `http://localhost:8765`, `/results`, and `/report`.

For Docker deployment, select Dockerfile build, base directory `/`, port `8765`. Configure `GITHUB_REPO_URL` for Fork on GitHub. 
The public BYOK setting is `PUBLIC_READ_ONLY=false` and `REQUIRE_USER_KEYS=true`; in this mode, users' API credentials transit the hosted server. 
See `README-DEPLOY.md` for security cautions.
