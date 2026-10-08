"""Real API and persistence acceptance tied to a live Helm revision, not old script state."""

import json
import secrets
import time
from uuid import uuid4

import httpx

from deployment.common.acceptance import (
    SOURCE,
    cache_inventory,
    completed_review,
    history_contains,
    login,
    request,
)
from deployment.common.target import require
from deployment.helm.lifecycle import quiesced


def restart(session):
    # The queue fence stops the backend before restarting its local database.
    with quiesced(session, restore=True):
        session.target.kubectl("rollout", "restart", "deployment/review-dynamodb")
        session.target.kubectl(
            "rollout",
            "status",
            "deployment/review-dynamodb",
            f"--timeout={session.args.warm_timeout}s",
            timeout=session.args.warm_timeout + 10,
        )
    session.wait_ready()


def verify(session):
    report = {
        "status": "in_progress",
        "review_completed": False,
        "persistence_verified": False,
        "api_acceptance_passed": False,
        "ui_verified": False,
    }
    session.save("verification.json", report)
    try:
        session.wait_ready()
        report["evidence"] = session.evidence()
        config = session.config()
        origin, port = session.origin()

        def backend_python(code):
            return session.target.kubectl(
                "exec",
                "deployment/review-backend",
                "-c",
                "review-backend",
                "--",
                "python",
                "-c",
                code,
            )

        cache_before = cache_inventory(backend_python)
        expected = {
            "model_id": config["OLLAMA_MODEL"],
            "model_revision": config["OLLAMA_MODEL_DIGEST"],
        }
        require(expected["model_revision"], "Acceptance requires a pinned model identity.")
        credentials = {
            "login_id": "acceptance-" + uuid4().hex,
            "password": secrets.token_urlsafe(32),
        }
        session.save("acceptance-account.json", credentials)
        with httpx.Client(
            base_url=origin, headers={"Origin": origin}, trust_env=False, timeout=15
        ) as client:
            with session.forward(port):
                home = client.get("/")
                require(
                    home.status_code == 200 and "<html" in home.text.lower(), "Homepage failed."
                )
                for header in (
                    "content-security-policy",
                    "x-frame-options",
                    "x-content-type-options",
                    "referrer-policy",
                ):
                    require(header in home.headers, "Frontend security header missing.")
                request(client, "GET", "/health/live", 200)
                request(client, "GET", "/health/ready", 200)
                request(client, "GET", "/api/v1/reviews", 401)
                result = request(client, "POST", "/api/v1/auth/register", 201, json=credentials)
                client.headers["X-CSRF-Token"] = result["csrf_token"]
                request(client, "POST", "/api/v1/auth/logout", 204, json={})
                login(client, credentials)
                payload = {
                    "source_code": SOURCE,
                    "language": "python",
                    "client_request_id": str(uuid4()),
                }
                request(
                    client,
                    "POST",
                    "/api/v1/reviews",
                    403,
                    json=payload,
                    headers={"X-CSRF-Token": "invalid"},
                )
                request(
                    client,
                    "POST",
                    "/api/v1/reviews",
                    403,
                    json=payload,
                    headers={"Origin": "http://untrusted.invalid"},
                )
                job = request(client, "POST", "/api/v1/reviews", 202, json=payload)
                review_id = job["review_id"]
                deadline = time.monotonic() + 660
                while True:
                    detail = request(client, "GET", f"/api/v1/reviews/{review_id}", 200)
                    require(
                        detail["status"] != "failed",
                        "Real review failed; no retries or success claimed.",
                    )
                    if detail["status"] == "completed":
                        completed_review(detail, expected)
                        break
                    require(
                        time.monotonic() < deadline,
                        "Real review timed out; no workloads restarted.",
                    )
                    time.sleep(2)
                report["review_completed"] = True
                history_contains(client, review_id, expected)
                with httpx.Client(
                    base_url=origin, headers={"Origin": origin}, trust_env=False, timeout=15
                ) as other:
                    request(
                        other,
                        "POST",
                        "/api/v1/auth/register",
                        201,
                        json={
                            "login_id": "isolation-" + uuid4().hex,
                            "password": secrets.token_urlsafe(32),
                        },
                    )
                    request(other, "GET", f"/api/v1/reviews/{review_id}", 404)
                    require(
                        not request(other, "GET", "/api/v1/reviews", 200)["items"],
                        "Cross-user history leak.",
                    )
            if not session.args.skip_restart:
                restart(session)
                require(
                    cache_inventory(backend_python) == cache_before,
                    "Model cache changed across restart.",
                )
                with session.forward(port):
                    # Existing session cookie proves the signing key survived the restart.
                    request(client, "GET", "/api/v1/auth/me", 200)
                    persisted = history_contains(client, review_id, expected)
                    require(
                        persisted["review_result"] == detail["review_result"],
                        "Persisted result changed across restart.",
                    )
                    login(client, credentials)
                    history_contains(client, review_id, expected)
                report["persistence_verified"] = True
        require(
            session.evidence() == report["evidence"],
            "Release/configuration/images changed during acceptance.",
        )
        report["api_acceptance_passed"] = (
            report["review_completed"] and report["persistence_verified"]
        )
        report["status"] = "passed" if report["api_acceptance_passed"] else "partial"
        print(json.dumps(report, indent=2))
        print("Browser UI is not verified; use port-forward for manual browser acceptance.")
    except BaseException as exc:
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            api_acceptance_passed=False,
            error_type=type(exc).__name__,
        )
        raise
    finally:
        session.save("verification.json", report)
    require(report["api_acceptance_passed"], "Partial verification: persistence check was skipped.")
