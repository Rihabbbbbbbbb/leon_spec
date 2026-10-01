# Deploying LEON on Databricks Apps — step by step

Target workspace: `https://adb-5678659344564033.13.azuredatabricks.net`

LEON runs as **one Databricks App** (`leon-spec`) serving both UIs and the
shared API from a single port:

| URL in the app | What it is |
|---|---|
| `/` | Conformity Matrix Analyzer UI (+ spec validation tabs) |
| `/qa` | Spec Q&A Assistant UI |
| `/api/*` | the 19 shared LEON API routes |
| `/health` | health + Azure configuration status |

Authentication = your workspace login (SSO). No API keys for end users.
Grant access with `CAN_USE` permission (step 7).

> **Auto-stop note (important for operations):** Databricks Apps serverless
> compute stops after inactivity, which is why users can occasionally see
> "App Not Available". See the dedicated section near the bottom of this file
> for the automatic keepalive and recovery steps.

---

## ✅ DEPLOYED AND VERIFIED (2026-09-23)

Live at **https://leon-spec-5678659344564033.13.azure.databricksapps.com**

Verified on the running app: both UIs, `/api/files`, `/api/ask` (real grounded
answer via Azure Search + gpt-4o), `/api/upload-conformity`,
`/api/conformity-files`, and `/api/conformity` (real ODS matrix — 211 rows,
OK 175 / NOK 28 / NA 7, columns auto-detected).

### The commands that actually work

`bundle deploy` alone only *creates* the app resource — it does not build or run
the source. Both steps are required:

```
powershell -ExecutionPolicy Bypass -File _deploy_to_databricks.ps1   # validate + deploy files
cd C:\Users\TA29225\leon-databricks-deploy
databricks bundle run leon-spec --profile leon                       # build + start the app
```

### Why the deploy script exists (read before "fixing" it)

`.pytest_cache` in the project root is an orphaned directory owned by an account
that no longer exists. Its ACL denies all access and it cannot be removed: this
corporate PC does not allow elevation (`takeown`, `icacls`, `Remove-Item`, `rd`
all fail, and `IsInRole(Administrator)` reports `False`).

The Databricks bundler **opens every directory** in the bundle root before
applying `sync.exclude`, so it aborts with:

```
Error: open .pytest_cache: Access is denied.
```

No exclude pattern can work around this. The script therefore builds a clean
deploy folder **outside** the project
(`C:\Users\TA29225\leon-databricks-deploy`) holding only the runtime files, and
deploys from there.

This is **not** the code duplication that was rejected earlier: the folder is a
build artifact, deleted and rebuilt from the live `app/` package on every run,
so the deployed code cannot go stale.

### Three real bugs fixed before it worked

1. **`valueFrom` → `value_from`.** CLI 1.17 silently ignores camelCase, so the
   app would have shipped with no API keys at all (it starts fine, then falls
   back to keyword search).
2. **Wrong OpenAI endpoint.** The code calls
   `OpenAI(base_url=AZURE_OPENAI_ENDPOINT)`, so this must be the full
   AI-Services v1 base URL — `https://Ragchatbotemwh.services.ai.azure.com/openai/v1/`
   — which is also the resource that built the 3072-dim index.
3. **Redirecting `DATA_DIR` to `/tmp` broke document lookup.** Reference docs are
   discovered under `DATA_DIR/refs`, and the Conformity endpoints use
   CWD-relative `Path("data/uploads")`. Fixed by seeding `/tmp/leon/data/refs`
   from the bundled docs and `os.chdir()`-ing to the ephemeral root.

### Azure egress — confirmed by test, not assumed

Both services accept connections from Databricks' dynamic egress IPs. Verified
empirically: Azure OpenAI returned a **3072-dim** embedding (matching the index
width) and AI Search listed `leon-specs-index`. No private-endpoint restriction.

> If that ever changes, LEON degrades **silently** to keyword search and
> `/health` still reports `ok`. Watch for `/api/ask` returning 0 sources.

### If users see "Databricks App Not Available"

The app's serverless compute **auto-stops after a period of inactivity** (this
is platform behaviour, not a crash — the app logs stay clean). A stopped app
serves **503 / "App Not Available"**. When it restarts it also drops its
active deployment, so compute alone isn't enough.

**Automatic keepalive (installed 2026-09-24):** a Windows scheduled task named
`LEON Keepalive` runs `C:\Users\TA29225\_keepalive.ps1` every 30 minutes,
06:00–20:00 Mon–Fri. It checks the app and runs `databricks apps start`
(which re-launches the last active deployment) whenever the app isn't RUNNING.
Caveat: it runs on this PC, so the PC must be on for it to fire.

**Manual recovery** (any time, any machine with the CLI):
```
databricks apps start leon-spec --profile leon
databricks bundle run leon-spec --profile leon     # only if still 502 afterwards
```

### Why there is no cloud-side auto-restart (permissions)

Three "proper" fixes were attempted and blocked by this workspace:
1. **Scheduled job with a job cluster** → `PERMISSION_DENIED: not authorized to
   create clusters`.
2. **`compute_min_instances: 1`** (keeps one warm instance, no scale-to-zero) →
   `Manual instance count configuration is not enabled in this workspace`.
3. **Serverless job** → not attempted (needs the serverless-jobs entitlement).

To make LEON always-on without the PC dependency, ask your Databricks admin to
grant **one** of: "create clusters" permission (then a scheduled self-healing
job works), the **serverless jobs** entitlement, or enable **manual instance
count** for Apps. The keepalive script `jobs/ensure_app_running.py` is already
written and ready to deploy the moment cluster permission is granted.

---

## 0. Prerequisites (one-time, on your machine)

- Python 3.11+ available locally (to run the smoke test).
- Databricks CLI v0.218+ (bundles + apps support). Install:

```
pip install databricks-sdk
pip install databricks-cli
```
or download the official CLI binary (recommended):
https://docs.databricks.com/aws/en/dev-tools/cli/

> The rest of this guide assumes the standalone `databricks` CLI binary.

## 1. Authenticate the CLI

```
databricks auth login --host https://adb-5678659344564033.13.azuredatabricks.net --profile leon
```

This opens a browser for workspace SSO. Verify:

```
databricks auth env --profile leon
databricks workspace get status --profile leon
```

## 2. Create the `leon` secret scope + keys

The app pulls its two Azure API keys from a Databricks secret scope named
`leon` (referenced by name in `databricks.yml` — no secret values in any file).

```
databricks secrets create-scope leon --profile leon
databricks secrets put-secret leon azure-openai-api-key --profile leon
databricks secrets put-secret leon azure-search-api-key --profile leon
```

Each `put-secret` opens an editor (or use `--text-value "..."`):
- `azure-openai-api-key` → key of the **oai-leon-prod-fr** Azure OpenAI resource
- `azure-search-api-key`  → admin/query key of the **leon-spec-search-915f** AI Search resource

> Use the SAME Azure OpenAI resource that built the `leon-specs-index`
> embeddings — a different resource silently degrades Q&A quality.

## 3. Verify the endpoints in `databricks.yml`

Open `databricks.yml` and confirm these match your Azure resources
(values were pre-filled from `DEPLOYMENT_FINAL_STATUS.md`):

- `AZURE_OPENAI_ENDPOINT` → `https://oai-leon-prod-fr.openai.azure.com/`
- `AZURE_SEARCH_ENDPOINT` → `https://leon-spec-search-915f.search.windows.net`
- `AZURE_SEARCH_INDEX_NAME` → `leon-specs-index`

## 4. Validate + deploy the bundle

From the project root (`c:\Users\TA29225\Spec AI Project`):

```
databricks bundle validate --profile leon
databricks bundle deploy --profile leon
```

The bundle syncs only what the app needs (~46 files, ~4 MB — `app/`,
`data/refs/`, `requirements.txt`, `databricks_main.py`, `app.yaml`).
Your tests, `azure_function/`, scratch files, `.venv` and secrets never leave
your machine (see the `sync.exclude` list in `databricks.yml`).

## 5. Start the app and open it

```
databricks apps get leon-spec --profile leon
```

If state is `IDLE`, start it (or click Start in the workspace UI → Apps):

```
databricks apps start leon-spec --profile leon
```

Then open the app URL (also visible in the UI, or `databricks bundle open leon-spec --profile leon`).

**First check:** open `<app-url>/health` — it must show:

```json
"azure_openai_configured": true,
"azure_search_configured": true
```

If either is `false`, the secret scope wiring is wrong (go back to step 2).
If both are `true` but answers look keyword-only, see "Egress" below.

## 6. Give your users access

In the workspace UI: **Apps → leon-spec → ⋯ → Permissions → Add**,
add your users/groups with **Can use** (or via CLI):

```
databricks apps update-permissions leon-spec --json '{"access_control_list":[{"group_name":"<your-group>","permission_level":"CAN_USE"}]}' --profile leon
```

Users then open the app URL with their normal corporate SSO — that is the
whole authentication story.

## 7. Updating the app later

Any code change → re-run:

```
databricks bundle deploy --profile leon
```

---

## Egress — read this if Q&A answers look degraded

Databricks Apps run on serverless compute that reaches the internet through
**dynamic egress IPs**. Your Azure OpenAI and Azure AI Search resources must
therefore allow **public network access** (Azure portal → resource →
Networking). If your IT enforces private-endpoint-only access on those
resources, calls from the app will fail — and because LEON is built for
graceful degradation, it will **silently fall back** to keyword retrieval and
deterministic validation instead of showing an error. Check `/health` and the
app logs (workspace UI → Apps → leon-spec → Logs) if quality seems lower than
on your local machine. The fix is either allowing public access on the two
Azure resources, or (bigger change) migrating to Databricks Model Serving +
Vector Search — ask me if you want that assessed.

## Operational notes

- **Ephemeral storage (your choice):** uploaded files live in `/tmp/leon` and
  disappear when the app restarts. Nothing persists between restarts.
- **beStandard disabled:** `BESTANDARD_AUTO_RESOLVE=false` is set because
  bestandard.fcagroup.com is a Stellantis intranet service unreachable from
  Databricks. Validation works without it; standard references are not
  auto-resolved.
- **120-second request timeout** is enforced by Databricks Apps. Very large
  conformity-batch uploads or full-spec LLM validations may need splitting.
- **Compute size:** the app starts on the default (small) compute. If
  heavy batch analysis feels slow, add `compute_size: MEDIUM` under
  `config:` in `databricks.yml` and redeploy.
- **Local smoke test before deploying:** `python databricks_main.py` then
  open http://localhost:8080/ and http://localhost:8080/qa.
