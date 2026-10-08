"""HTTP/product acceptance shared by independently owned deployment workflows."""

import json
import re

from deployment.common.errors import DemoError, require

MODEL = "qwen3:1.7b"


REVISION = "sha256:8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7"


SOURCE = "def average(values):\n    return sum(values) / len(values)\n"


def completed_review(value, identity=None):
    identity = identity or {"model_id": MODEL, "model_revision": REVISION}
    require(value.get("status") == "completed", "Review did not complete successfully.")
    require(
        value.get("model_id") == identity["model_id"]
        and value.get("model_revision") == identity["model_revision"],
        "Model identity/revision mismatch.",
    )
    require(
        value.get("source_code") == SOURCE and value.get("language") == "python",
        "Persisted sample differs from the submitted sample.",
    )
    from tooling_paths import prepare_backend

    prepare_backend()
    from app.errors import AppError
    from app.inference.review_output import validate_review_output

    try:
        validate_review_output(value.get("review_result") or "", SOURCE)
    except AppError:
        raise DemoError("Completed result failed the unchanged product quality gate.") from None
    # Additional sample-specific relevance, without modifying product prompts or validators.
    body = value["review_result"].casefold()
    require(
        re.search(r"\b(average|values)\b", body)
        and re.search(r"\b(empty|zero|division|divide|zerodivisionerror)\b", body),
        "Review lacks the sample's averaging/empty-input issue; manual diagnosis required.",
    )


def request(client, method, path, expected, **kwargs):
    response = client.request(method, path, **kwargs)
    require(
        response.status_code == expected,
        f"Acceptance HTTP status mismatch: expected {expected}, received {response.status_code}.",
    )
    if expected != 204:
        require(
            response.headers.get("content-type", "").startswith("application/json"),
            "API/health route returned non-JSON (possible SPA fallback).",
        )
        return response.json()
    return None


def login(client, credentials):
    session = request(client, "POST", "/api/v1/auth/login", 200, json=credentials)
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    request(client, "GET", "/api/v1/auth/me", 200)
    cookie = next((c for c in client.cookies.jar if c.name == "review_session"), None)
    require(
        cookie
        and not cookie.secure
        and cookie.has_nonstandard_attr("HttpOnly")
        and cookie.get_nonstandard_attr("SameSite") == "strict",
        "Local cookie flags mismatch.",
    )


def history_contains(client, review_id, identity=None):
    rows = request(client, "GET", "/api/v1/reviews?limit=50", 200)
    require(
        any(row["review_id"] == review_id for row in rows["items"]), "History lost accepted review."
    )
    detail = request(client, "GET", f"/api/v1/reviews/{review_id}", 200)
    completed_review(detail, identity)
    return detail


def cache_inventory(executor):
    result = executor(
        "import json, httpx; from app.config import Settings\n"
        "s=Settings()\n"
        "with httpx.Client(base_url=s.ollama_base_url, trust_env=False, timeout=10) as c:\n"
        " r=c.get('/api/tags'); r.raise_for_status(); models=r.json()['models']\n"
        " matches=[m for m in models if m['name']==s.ollama_model]\n"
        " assert len(matches)==1\n"
        " m=matches[0]; digest='sha256:'+m['digest'].removeprefix('sha256:')\n"
        " assert not s.ollama_model_digest or digest==s.ollama_model_digest\n"
        " print(json.dumps({s.ollama_model:[digest,m.get('size')]}))"
    )
    data = json.loads(result.stdout)
    require(bool(data), "Selected Ollama model inventory missing or incomplete.")
    return data
