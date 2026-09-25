# LocalQwenDemo

**A local CPU-based LLM code-review service with automated deployment to an existing minikube cluster.**

React and FastAPI provide registration, login, a code editor, sanitized Markdown results
and private review history. The backend runs real CPU inference with the fixed
`Qwen/Qwen3-1.7B` model and revision. One worker processes a persistent SQLite queue;
submitted code is never executed and submissions do not share conversational memory.
This is a single-node local service, not a validated highly available production platform.
Project documentation, fixed outputs and UI text use English. User input and generated
content retain their original language.

## Deploy to local minikube

Run from the checkout root. Install Python 3.12, Docker, minikube and a kubectl version
compatible with your cluster. Docker must use the host's native architecture. Dependency
installation and the first image/model download require network access. Node 24 is needed
for frontend development/tests, but not for this Docker-based deployment path.

```bash
cd /path/to/LocalQwenDemo
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.lock
backend/.venv/bin/python -m pip install --no-deps -e backend
minikube profile list
```

**You manage cluster lifecycle.** If no suitable cluster is running, start one yourself.
For example, for a new native Docker-driver cluster:

```bash
minikube start --profile minikube --driver=docker --cpus=4 --memory=8192
```

This is a manual sizing example, not a guaranteed capacity assessment or an instruction
to resize an existing cluster. Account for other workloads, Docker capacity and host
memory/swap pressure. The backend retains its 2-CPU limit, 4-GiB memory request and 6-GiB
memory limit. The scripts never start, resize, stop or delete a cluster.

```bash
scripts/minikube_demo.sh doctor --profile minikube
scripts/minikube_demo.sh up --profile minikube
scripts/minikube_demo.sh port-forward --profile minikube
```

`doctor` returns nonzero for insufficient resources or unavailable measurements.
Run `up` separately when you decide to attempt deployment: resource diagnostics are
warnings for `up`, while target identity, ownership, native architecture, supported
storage and local dependency constraints remain mandatory. No `--force` is needed.
Actual build/load/apply/readiness failures still return nonzero.

Keep forwarding in its terminal and open **http://localhost:8080**. Register with a
3–100 character login and a 12–128 character password. Use `localhost`, not `127.0.0.1`,
as the browser origin. Close the owned forwarding process with Ctrl-C before running
acceptance on the same port:

```bash
scripts/minikube_demo.sh verify --profile minikube
```

`verify` uses normal authentication and submits real inference, checks account/history
isolation and performs controlled persistence checks. It is an operational write command,
not an offline test. Read [the minikube guide](docs/guides/minikube-demo.md) before running
it. Application Ready, a quality-valid `completed` review, persistence acceptance,
complete API `verify`, and browser acceptance are distinct results. A prior experiment
or successful startup does not establish current acceptance.

## Data, startup and ownership

The first cold start downloads roughly 4.08 GB of pinned weights. Subsequent deployments
reuse the model cache, accounts, signing Secret and history. Three separate PVCs hold
model weights, SQLite history/queue/sessions, and DynamoDB Local account data. Cache
completeness is checked against every indexed shard; a finished download progress bar
is insufficient evidence. See [startup troubleshooting](docs/operations/recovery-and-cleanup.md)
for missing shards, DynamoDB startup, Pending Pods, disk pressure and safe diagnostics.

Deployment state lives outside the checkout under
`${XDG_STATE_HOME:-$HOME/.local/state}/local-qwen-demo/`. Keep the same state root and
minikube home/profile when moving or downloading a checkout. Shared locks and cluster/
namespace UIDs plus ownership markers protect operations across directories. Current
source fingerprints and image identity are still verified; old images are not proof of
new code. An existing old checkout can be imported explicitly:

```bash
scripts/minikube_demo.sh import-state --profile minikube --from-state /path/to/old/checkout
```

Normal cleanup preserves the three PVCs, signing Secret and recovery information:

```bash
scripts/minikube_demo.sh undeploy --profile minikube
```

Complete data deletion requires explicit confirmation; state-loss cleanup is a separate
read-only-by-default recovery command. Read [normal undeploy](docs/guides/minikube-demo.md#undeploy-and-recovery)
and [lost-state recovery](docs/operations/minikube-lost-state-recovery.md). Neither adopts
unknown resources. Never remove state files to bypass ownership. Cluster shutdown is a
separate user action after any desired cleanup has actually succeeded.

## Development and offline checks

[Local development](docs/guides/local-development.md) retains a clearly labeled fake-model
harness, persistent DynamoDB Local via Compose, and optional real-model development.
These tools supplement the maintained minikube deployment path.

```bash
npm --prefix frontend ci
bash scripts/check.sh
```

The gate runs backend tests, frontend lint/tests/build, minikube/state/recovery regressions,
local DynamoDB transport/initialization tests, evaluator doubles and Kustomize checks.
It does not download weights, perform real inference, build container images or touch a
cluster. [Testing](docs/testing/README.md) describes prerequisites, isolated browser tests
and separately authorized acceptance. DynamoDB Local still uses boto3 with explicit
local endpoints and invalid credentials; missing or nonlocal endpoints fail closed.

Start with the [documentation index](docs/README.md), [architecture](docs/reference/architecture.md),
[model contract](docs/reference/model.md), [security](docs/reference/security.md),
[script reference](docs/reference/scripts.md), and [dated evidence](docs/reports/README.md).
Historical results retain their original dates and limitations; they are not current
validation results. There is no maintained cloud deployment or remote image publishing path.
