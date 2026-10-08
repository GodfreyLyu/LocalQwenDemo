"""Product acceptance invariants used by Helm verification."""

import httpx
import pytest
from deployment.common import acceptance
from deployment.common.errors import DemoError


@pytest.mark.unit
@pytest.mark.parametrize("status", ["failed", "running", "queued"])
def test_acceptance_rejects_non_completed_review(status):
    with pytest.raises(DemoError, match="did not complete"):
        acceptance.completed_review({"status": status})


@pytest.mark.unit
def test_acceptance_rejects_wrong_model_and_irrelevant_text():
    value = {
        "status": "completed",
        "model_id": acceptance.MODEL,
        "model_revision": acceptance.REVISION,
        "source_code": acceptance.SOURCE,
        "language": "python",
        "review_result": "## Summary\nAverage values.\n## Findings\nUse good names.\n"
        "## Suggestions\nAdd comments.",
    }
    with pytest.raises(DemoError, match="empty-input"):
        acceptance.completed_review(value)
    value["model_revision"] = "0" * 40
    with pytest.raises(DemoError, match="identity"):
        acceptance.completed_review(value)


@pytest.mark.unit
def test_acceptance_rejects_html_api_fallback():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/html"}, text="<html>SPA</html>"
            )
        ),
        base_url="http://localhost",
    ) as client:
        with pytest.raises(DemoError, match="non-JSON"):
            acceptance.request(client, "GET", "/health/ready", 200)


@pytest.mark.unit
@pytest.mark.contract
def test_acceptance_uses_ollama_digest_and_rejects_hf_revision():
    from deployment.common import acceptance as v

    identity = {"model_id": "qwen3:1.7b", "model_revision": "sha256:" + "a" * 64}
    review = identity | {
        "status": "completed",
        "source_code": v.SOURCE,
        "language": "python",
        "review_result": "## Summary\nThe average function computes a mean of values.\n\n"
        "## Findings\nEmpty values causes division by zero.\n\n"
        "## Suggestions\nGuard average against empty values before division.",
    }
    v.completed_review(review, identity)
    with pytest.raises(DemoError, match="identity"):
        v.completed_review(
            review | {"model_revision": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"}, identity
        )
