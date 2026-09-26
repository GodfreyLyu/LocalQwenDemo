"""State-machine and build-identity tests; never evidence of real inference acceptance."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import minikube_demo as d  # noqa: E402
import minikube_state as s  # noqa: E402
import minikube_verify as v  # noqa: E402


@pytest.fixture
def complete_state(monkeypatch, tmp_path):
    """Build mutually consistent synthetic attempt records in tmp_path.

    Tests corrupt one contract at a time; these records prove validation behavior,
    not an actual image build, running cluster, or completed inference."""
    target = dict(
        root="/project",
        profile="minikube",
        minikube_home="/home/.minikube",
        cluster_uid="cluster",
        node_name="minikube",
    )
    owner = target | dict(
        owner="owner",
        namespace_uid="namespace",
        port=8080,
        architecture="arm64",
        storage_class="standard",
    )
    for key in s.CONFIG[3:]:
        owner[key] = {
            name: (
                f"{name}:minikube-20260921120000-0123456789"
                if key == "images"
                else "c" * 64
                if key == "build_fingerprints"
                else "sha256:" + "a" * 64
            )
            for name in s.COMPONENTS
        }
    monkeypatch.setattr(d, "STATE", tmp_path)
    monkeypatch.setattr(d, "TARGET", target)
    monkeypatch.setattr(d, "PROFILE", "minikube")
    record = owner | dict(attempt_id="b" * 24, status="ready", application_ready=True)
    d.save("owner.json", owner)
    d.save("deployment.json", record)
    d.save("plan.json", record | dict(status="planned", application_ready=False))
    d.save(
        "startup.json",
        {
            k: record[k]
            for k in ("attempt_id", "profile", "cluster_uid", "status", "application_ready")
        },
    )
    return owner


def test_partial_owner_is_valid_ownership_not_completed_deployment(complete_state):
    owner = {k: complete_state[k] for k in s.IDENTITY}
    d.save("owner.json", owner)
    assert d.state() == owner
    with pytest.raises(d.DemoError, match=r"owner.json.port.*up --profile minikube"):
        s.verify_state(owner)


@pytest.mark.parametrize(
    "field,value",
    [
        ("port", None),
        ("port", True),
        ("port", "8080"),
        ("port", 80),
        ("architecture", None),
        ("architecture", []),
        ("architecture", "aarch64"),
        ("storage_class", None),
        ("storage_class", 42),
        ("images", {}),
        ("images", []),
        ("image_ids", {}),
        ("runtime_image_ids", {}),
        ("build_fingerprints", {}),
    ],
)
def test_invalid_state_stops_before_any_acceptance_side_effect(
    complete_state, monkeypatch, field, value
):
    complete_state[field] = value
    runtime = Mock()
    monkeypatch.setattr(v, "runtime_checks", runtime)
    monkeypatch.setattr(s, "verify_images", Mock())
    monkeypatch.setattr(v, "restart_idle", Mock())
    monkeypatch.setattr(v.httpx, "Client", Mock())
    d.save("verification.json", {"status": "passed", "api_acceptance_passed": True})
    with pytest.raises(d.DemoError, match=f"owner.json.{field}"):
        v.verify(complete_state, SimpleNamespace())
    runtime.assert_not_called()
    s.verify_images.assert_not_called()
    v.restart_idle.assert_not_called()
    v.httpx.Client.assert_not_called()
    assert not (d.STATE / "acceptance-account.json").exists()
    report = s.read("verification.json")
    assert report["status"] == "failed" and not report["api_acceptance_passed"]
    assert not report["review_completed"]
    assert any(
        json.loads(p.read_text()).get("status") == "passed"
        for p in (d.STATE / "attempts").glob("*/verification.json")
    )


@pytest.mark.parametrize("key", s.CONFIG[3:])
def test_backend_only_image_record_cannot_pass(complete_state, key):
    del complete_state[key]["review-frontend"]
    with pytest.raises(d.DemoError, match="both backend and frontend"):
        s.verify_state(complete_state)


@pytest.mark.parametrize("key", ["images", "image_ids", "runtime_image_ids", "build_fingerprints"])
def test_malformed_image_record_cannot_pass(complete_state, key):
    complete_state[key]["review-frontend"] = "PRIVATE_SENTINEL"
    with pytest.raises(d.DemoError) as exc:
        s.verify_state(complete_state)
    assert "PRIVATE_SENTINEL" not in str(exc.value)


@pytest.mark.parametrize("status", ["failed", "in_progress", "interrupted"])
def test_old_ready_record_never_overrides_latest_failed_attempt(complete_state, status):
    report = s.read("startup.json") | {"status": status, "application_ready": False}
    d.save("startup.json", report)
    with pytest.raises(d.DemoError, match="latest deployment"):
        s.verify_state(complete_state)


@pytest.mark.parametrize("name", ["deployment.json", "startup.json", "plan.json"])
def test_missing_or_malformed_completion_record_is_actionable(complete_state, name):
    (d.STATE / name).write_text('["PRIVATE_SENTINEL"]')
    with pytest.raises(d.DemoError, match=name) as exc:
        s.verify_state(complete_state)
    assert "PRIVATE_SENTINEL" not in str(exc.value)


@pytest.mark.parametrize("field", s.IDENTITY)
def test_completion_identity_conflict_cannot_be_adopted(complete_state, field):
    record = s.read("deployment.json")
    record[field] = "foreign"
    d.save("deployment.json", record)
    with pytest.raises(d.DemoError, match="does not match"):
        s.verify_state(complete_state)


def test_current_complete_attempt_is_accepted(complete_state):
    assert s.verify_state(complete_state)["status"] == "ready"


def test_retry_reuses_confirmed_plan_options_without_claiming_success(complete_state):
    partial = {k: complete_state[k] for k in s.IDENTITY}
    plan = s.read("plan.json") | {"port": 8089, "storage_class": "local-cache"}
    d.save("plan.json", plan)
    choices = s.planned_options(partial)
    assert choices["port"] == 8089 and choices["storage_class"] == "local-cache"
    assert "images" not in choices and "status" not in choices
    plan["cluster_uid"] = "foreign"
    d.save("plan.json", plan)
    with pytest.raises(d.DemoError, match="identity mismatch"):
        s.planned_options(partial)


def test_loaded_tag_is_not_proof_of_build_identity(monkeypatch):
    local = dict(
        Id="sha256:" + "a" * 64,
        Architecture="arm64",
        Os="linux",
        Config={"Cmd": ["run"]},
        RootFS={"Type": "layers", "Layers": ["sha256:one"]},
    )
    remote = {
        "status": {"id": "sha256:" + "b" * 64},
        "info": {
            "imageSpec": {
                "architecture": "arm64",
                "os": "linux",
                "config": local["Config"],
                "rootfs": {"type": "layers", "diff_ids": ["sha256:other"]},
            }
        },
    }
    monkeypatch.setattr(d, "TARGET", {"node_name": "minikube"})
    monkeypatch.setattr(
        d,
        "run",
        lambda args: SimpleNamespace(stdout=json.dumps([local] if args[1] == "image" else remote)),
    )
    with pytest.raises(d.DemoError, match="content/config"):
        s.image_proof("review-backend:unique", "arm64", local["Id"])
    remote["info"]["imageSpec"]["rootfs"]["diff_ids"] = ["sha256:one"]
    assert s.image_proof("review-backend:unique", "arm64", local["Id"]) == (
        local["Id"],
        remote["status"]["id"],
    )
    with pytest.raises(d.DemoError, match="Local build image ID changed"):
        s.image_proof("review-backend:unique", "arm64", "sha256:" + "c" * 64)


def test_secret_ownership_requests_only_metadata(monkeypatch):
    from minikube_undeploy import METADATA_PATH

    execute = Mock(return_value=SimpleNamespace(stdout="review-secrets\tuid\t1\t\t\t\t\t"))
    monkeypatch.setattr(d, "k", execute)
    assert d.obj("secret", "review-secrets")["metadata"]["name"] == "review-secrets"
    assert execute.call_args.args[-1] == "jsonpath=" + METADATA_PATH
    assert "{.metadata}" not in METADATA_PATH


def test_failed_repeat_up_restores_replicas_without_hiding_failure(monkeypatch):
    objects = {
        name: {
            "kind": "Deployment",
            "metadata": {"name": name, "uid": name, "annotations": {d.OWNER_KEY: "ours"}},
            "spec": {"replicas": 1},
        }
        for name in ("review-backend", "review-frontend")
    }
    monkeypatch.setattr(d, "obj", lambda kind, name, **kwargs: objects[name])
    idle = Mock()
    monkeypatch.setattr(d, "require_idle", idle)
    actions = []

    def execute(*args, **kwargs):
        actions.append(args)
        if args[0] == "scale":
            objects[args[1].split("/")[1]]["spec"]["replicas"] = int(args[2].split("=")[1])

    monkeypatch.setattr(d, "k", execute)
    with (
        pytest.raises(d.DemoError, match="actual apply failure"),
        d.idle_application_update("ours"),
    ):
        assert all(o["spec"]["replicas"] == 0 for o in objects.values())
        raise d.DemoError("actual apply failure")
    assert all(o["spec"]["replicas"] == 1 for o in objects.values())
    assert idle.call_count == 2
    assert not any(a[0] in ("delete", "start", "stop") for a in actions)


def test_invalid_retry_does_not_erase_ownership_or_adopt_resources(complete_state, monkeypatch):
    original = (d.STATE / "owner.json").read_bytes()
    monkeypatch.setattr(d, "require_local_docker", lambda: None)
    monkeypatch.setattr(d, "check_ownership", Mock(side_effect=d.DemoError("Ownership conflict")))
    with pytest.raises(d.DemoError, match="Ownership conflict"):
        d.up(SimpleNamespace(), {"report": {"blockers": []}})
    assert (d.STATE / "owner.json").read_bytes() == original
    assert s.read("deployment.json")["status"] == "ready"


def test_interrupted_attempt_then_repeat_up_requires_new_completion_record(
    complete_state, monkeypatch
):
    monkeypatch.setattr(d, "require_local_docker", lambda: None)
    monkeypatch.setattr(d, "check_ownership", lambda owner: {})
    attempts = []

    def deployment(args, plan, stage):
        attempt = s.read("startup.json")["attempt_id"]
        attempts.append(attempt)
        planned = complete_state | {
            "attempt_id": attempt,
            "status": "planned",
            "application_ready": False,
        }
        d.save("plan.json", planned)
        stage("backend_readiness")
        if len(attempts) == 1:
            raise KeyboardInterrupt
        d.save("deployment.json", planned | {"status": "ready", "application_ready": True})
        return {}

    monkeypatch.setattr(d, "deploy_application", deployment)
    plan = {"report": {"blockers": []}}
    with pytest.raises(KeyboardInterrupt):
        d.up(SimpleNamespace(), plan)
    assert s.read("startup.json")["status"] == "interrupted"
    assert s.read("deployment.json")["status"] == "interrupted"
    assert s.read("verification.json")["status"] == "not_run"
    with pytest.raises(d.DemoError):
        s.verify_state(complete_state)
    d.up(SimpleNamespace(), plan)
    assert len(set(attempts)) == 2
    assert s.verify_state(complete_state)["attempt_id"] == attempts[-1]
    assert s.read("verification.json")["status"] == "not_run"
    assert not s.read("verification.json")["api_acceptance_passed"]
    archived = [json.loads(p.read_text()) for p in (d.STATE / "attempts").glob("*/startup.json")]
    assert any(r.get("status") == "interrupted" for r in archived)
    assert d.state()["owner"] == complete_state["owner"]


@pytest.fixture
def real_build_inputs(monkeypatch, tmp_path):
    """Use real build files and the production hash function; isolate Docker checks only."""
    from minikube_target import source_fingerprint

    files = {
        "backend/Dockerfile": "FROM python:3.12\n",
        "backend/pyproject.toml": '[project]\nname = "fixture"\n',
        "backend/requirements.lock": "fastapi==0.115.0\n",
        "backend/requirements-model.lock": "transformers==4.57.6\n",
        "backend/app/inference/model.py": "MODEL = 'fixed'\n",
        "frontend/Dockerfile": "FROM node:24\n",
        "frontend/package.json": '{"name":"fixture"}\n',
        "frontend/src/App.tsx": 'export const title = "Review";\n',
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setattr(d, "ROOT", tmp_path)
    # Match up's directory-based calls independently of verify's component mapping.
    record = {
        "architecture": "arm64",
        "build_fingerprints": {
            f"review-{context}": source_fingerprint(context) for context in ("backend", "frontend")
        },
        "images": {name: f"{name}:unique" for name in s.COMPONENTS},
        "image_ids": {name: "sha256:" + "a" * 64 for name in s.COMPONENTS},
        "runtime_image_ids": {name: "sha256:" + "b" * 64 for name in s.COMPONENTS},
    }
    proof = Mock(return_value=("sha256:" + "a" * 64, "sha256:" + "b" * 64))
    monkeypatch.setattr(s, "image_proof", proof)
    return record, files, proof


def test_verify_unchanged_real_build_inputs_for_both_components(real_build_inputs):
    record, _, proof = real_build_inputs
    before = json.dumps(record, sort_keys=True)
    s.verify_images(record)
    assert proof.call_count == 2
    for component in ("review-backend", "review-frontend"):
        proof.assert_any_call(record["images"][component], "arm64", record["image_ids"][component])
    assert json.dumps(record, sort_keys=True) == before


@pytest.mark.parametrize(
    "context,path",
    [
        ("backend", "app/inference/model.py"),
        ("frontend", "src/App.tsx"),
    ],
)
def test_verify_rejects_changed_real_build_input(real_build_inputs, context, path):
    record, _, _ = real_build_inputs
    before = json.dumps(record, sort_keys=True)
    with (d.ROOT / context / path).open("a") as stream:
        stream.write("\n# Changed build input\n")
    with pytest.raises(d.DemoError, match=f"review-{context} build inputs changed"):
        s.verify_images(record)
    assert json.dumps(record, sort_keys=True) == before


def test_valid_context_hash_algorithm_is_unchanged(real_build_inputs):
    import hashlib

    from minikube_target import source_fingerprint

    _, files, _ = real_build_inputs
    for context in ("backend", "frontend"):
        expected = hashlib.sha256()
        for name in sorted(name for name in files if name.startswith(context + "/")):
            expected.update(name.encode())
            expected.update(files[name].encode())
        assert source_fingerprint(context) == expected.hexdigest()


@pytest.mark.parametrize(
    "context", ["review-backend", "review-frontend", "unknown", "", "../backend", None]
)
def test_invalid_build_context_is_explicit_error(monkeypatch, tmp_path, context):
    from minikube_target import source_fingerprint

    monkeypatch.setattr(d, "ROOT", tmp_path)
    with pytest.raises(d.DemoError, match="Invalid build context; expected backend or frontend"):
        source_fingerprint(context)


@pytest.mark.parametrize("context", ["backend", "frontend"])
@pytest.mark.parametrize("kind", ["absent", "file", "empty"])
def test_missing_or_empty_build_context_never_hashes_empty_inputs(
    monkeypatch, tmp_path, context, kind
):
    from minikube_target import source_fingerprint

    monkeypatch.setattr(d, "ROOT", tmp_path)
    if kind == "file":
        (tmp_path / context).write_text("Not a directory")
    elif kind == "empty":
        (tmp_path / context).mkdir()
    with pytest.raises(d.DemoError, match="Build (context|fingerprint input)"):
        source_fingerprint(context)


@pytest.mark.parametrize("reject_logout", [None, 1, 2])
@pytest.mark.parametrize("fail_rebuild", [False, True])
def test_verify_logout_requests_preserve_json_and_session_headers(
    complete_state, monkeypatch, reject_logout, fail_rebuild
):
    """Exercise both real call sites through HTTPX serialization, with no network or cluster."""
    import contextlib

    import httpx

    origin = "http://localhost:8080"
    logout_requests = []
    rejected_requests = []
    sessions = {}
    active_cookie = active_csrf = None
    review = {
        "review_id": "offline-review",
        "status": "completed",
        "model_id": v.MODEL,
        "model_revision": v.REVISION,
        "source_code": v.SOURCE,
        "language": "python",
        "review_result": (
            "## Summary\nThe `average` function computes the mean of `values`.\n\n"
            "## Findings\nEmpty `values` causes division by zero in `average`.\n\n"
            "## Suggestions\nGuard against an empty list before dividing in `average`."
        ),
    }

    def respond(request):
        nonlocal active_cookie, active_csrf
        path = request.url.path
        if request.method in {"POST", "PUT", "PATCH"}:
            assert request.headers["Content-Type"] == "application/json"
            body = json.loads(request.content)
        if path == "/":
            return httpx.Response(
                200,
                text="<html>Offline fixture</html>",
                headers={
                    name: "test"
                    for name in (
                        "content-security-policy",
                        "x-frame-options",
                        "x-content-type-options",
                        "referrer-policy",
                    )
                },
            )
        if path in {"/health/live", "/health/ready"}:
            return httpx.Response(200, json={"status": "ready"})
        if path in {"/api/v1/auth/register", "/api/v1/auth/login"}:
            active_cookie = f"offline-session-{len(sessions)}"
            active_csrf = f"offline-csrf-{len(sessions)}"
            sessions[f"review_session={active_cookie}"] = body["login_id"].startswith("isolation-")
            return httpx.Response(
                201 if path.endswith("register") else 200,
                json={"csrf_token": active_csrf},
                headers={
                    "Set-Cookie": (
                        f"review_session={active_cookie}; Path=/; HttpOnly; SameSite=strict"
                    )
                },
            )
        if path == "/api/v1/auth/logout":
            assert body == {}
            assert request.headers["Origin"] == origin
            assert request.headers["Cookie"] == f"review_session={active_cookie}"
            assert request.headers["X-CSRF-Token"] == active_csrf
            logout_requests.append(request)
            if len(logout_requests) == reject_logout:
                return httpx.Response(415, json={"error_code": "unsupported_media_type"})
            return httpx.Response(
                204,
                headers={
                    "Set-Cookie": "review_session=; Path=/; Max-Age=0; HttpOnly; SameSite=strict"
                },
            )
        if path == "/api/v1/auth/me":
            return httpx.Response(200, json={"login_id": "offline-user"})
        if path == "/api/v1/reviews" and request.method == "POST":
            if request.headers["Origin"] != origin or request.headers["X-CSRF-Token"] == "invalid":
                rejected_requests.append(request)
                return httpx.Response(403, json={"error_code": "csrf_failed"})
            return httpx.Response(202, json={"review_id": review["review_id"]})
        cookie = request.headers.get("Cookie")
        if cookie not in sessions:
            return httpx.Response(401, json={"error_code": "authentication_required"})
        if path == "/api/v1/reviews":
            return httpx.Response(200, json={"items": [] if sessions[cookie] else [review]})
        if path == "/api/v1/reviews/offline-review":
            return (
                httpx.Response(404, json={})
                if sessions[cookie]
                else httpx.Response(200, json=review)
            )
        raise AssertionError(f"Unexpected offline request: {request.method} {path}")

    client_type = httpx.Client
    monkeypatch.setattr(
        v.httpx,
        "Client",
        lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(s, "verify_images", Mock())
    monkeypatch.setattr(v, "runtime_checks", Mock(return_value={}))
    monkeypatch.setattr(v, "cache_inventory", Mock(return_value={"offline-cache": [1, 2, 3]}))
    monkeypatch.setattr(v, "memory_sample", Mock(return_value={}))
    monkeypatch.setattr(v, "require_idle", Mock())
    forwards = []

    @contextlib.contextmanager
    def offline_forward(*args):
        forwards.append(args)
        if fail_rebuild and len(forwards) == 2:
            raise d.DemoError("Loopback port 8080: permission_denied; errno=1 (EPERM).")
        yield

    monkeypatch.setattr(v, "forward", offline_forward)
    restart = Mock(return_value=0)
    monkeypatch.setattr(v, "restart_idle", restart)
    monkeypatch.setattr(v.os, "umask", Mock())
    monkeypatch.setattr(d, "run", Mock(side_effect=AssertionError("No external commands allowed")))
    args = SimpleNamespace(skip_restart=False, warm_timeout=60)
    if reject_logout:
        with pytest.raises(d.DemoError, match="expected 204, received 415"):
            v.verify(complete_state, args)
        assert len(logout_requests) == reject_logout
        assert s.read("verification.json")["status"] == "failed"
        assert not s.read("verification.json")["api_acceptance_passed"]
        restart.assert_not_called()
    elif fail_rebuild:
        with pytest.raises(d.DemoError, match="permission_denied"):
            v.verify(complete_state, args)
        report = s.read("verification.json")
        assert report["status"] == "failed"
        assert report["stage"] == "persistence_acceptance"
        assert report["review_completed"] is True
        assert report["persistence_verified"] is False
        assert report["api_acceptance_passed"] is False
        assert report["ui_verified"] is False
        assert report["review_id"] == "offline-review"
        assert forwards == [("review-frontend", 8080, 8080)] * 2
        restart.assert_called_once_with(args)
    else:
        v.verify(complete_state, args)
        assert len(logout_requests) == 2
        assert logout_requests[0].headers["Cookie"] != logout_requests[1].headers["Cookie"]
        assert (
            logout_requests[0].headers["X-CSRF-Token"] != logout_requests[1].headers["X-CSRF-Token"]
        )
        assert len(rejected_requests) == 2  # Intentional bad-CSRF and bad-Origin requests remain.
        restart.assert_called_once_with(args)
    d.run.assert_not_called()


def test_new_checkout_uses_same_owner_but_does_not_rewrite_build_evidence(
    complete_state, monkeypatch
):
    monkeypatch.setattr(d, "TARGET", d.TARGET | {"root": "/new/checkout"})
    before = (d.STATE / "deployment.json").read_bytes()
    assert d.state()["owner"] == complete_state["owner"]
    assert s.verify_state(d.state())["build_fingerprints"] == complete_state["build_fingerprints"]
    assert (d.STATE / "deployment.json").read_bytes() == before


def test_new_up_invalidates_previous_idle_cleanup_proof(complete_state, monkeypatch):
    d.save("undeployment.json", complete_state | {"status": "undeployed", "quiesced": True})
    monkeypatch.setattr(d, "require_local_docker", lambda: None)
    monkeypatch.setattr(d, "check_ownership", lambda owner: None)
    monkeypatch.setattr(d, "deploy_application", lambda *a: {})
    d.up(SimpleNamespace(), {"report": {"blockers": []}})
    assert s.read("undeployment.json")["quiesced"] is False
    assert s.read("undeployment.json")["status"] == "superseded"
    assert any(
        json.loads(p.read_text())["quiesced"]
        for p in (d.STATE / "attempts").glob("*/undeployment.json")
    )
