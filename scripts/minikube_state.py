"""Separate ownership, intended deployment, completed deployment and acceptance evidence.

These helpers share the target selected by minikube_demo; they are not CLI entry
points. Validate identities and attempt records before acceptance can mutate the
application. Archived reports exclude credentials and review bodies.
"""

import json
import os
import re
import secrets

BUILD_CONTEXTS = {"review-backend": "backend", "review-frontend": "frontend"}
COMPONENTS = tuple(BUILD_CONTEXTS)
IDENTITY = ("root", "profile", "minikube_home", "cluster_uid", "namespace_uid", "owner")
CONFIG = (
    "port",
    "architecture",
    "storage_class",
    "images",
    "image_ids",
    "runtime_image_ids",
    "build_fingerprints",
)


def api():
    """Resolve the active CLI state lazily; sibling imports must not construct another target."""
    import minikube_demo

    return minikube_demo


def invalid(field):
    d = api()
    raise d.DemoError(
        f"Deployment state is incomplete or invalid: {field}. "
        f"Run scripts/minikube_demo.sh up --profile {d.PROFILE} to finish deployment, "
        "then retry verify. Do not delete owner.json or adopt running images as build evidence."
    )


def read(name):
    try:
        value = json.loads((api().STATE / name).read_text())
    except (OSError, ValueError):
        invalid(name)
    if not isinstance(value, dict):
        invalid(name)
    return value


def ownership(value):
    """Validate ownership fields without mistaking a partial deployment for completed acceptance."""
    if not isinstance(value, dict):
        invalid("owner.json object")
    for key in ("root", "profile", "minikube_home", "cluster_uid", "owner"):
        if not isinstance(value.get(key), str) or not value[key]:
            invalid(f"owner.json.{key}")
    if "namespace_uid" in value and not isinstance(value["namespace_uid"], str):
        invalid("owner.json.namespace_uid")
    for key in ("images", "image_ids", "runtime_image_ids", "build_fingerprints"):
        if key in value and (
            not isinstance(value[key], dict)
            or any(not isinstance(v, str) for v in value[key].values())
        ):
            invalid(f"owner.json.{key}")


def complete(value, name):
    """Reject incomplete identity/build evidence; never authorize verification with defaults."""
    if not isinstance(value, dict):
        invalid(name)
    for key in IDENTITY:
        if not isinstance(value.get(key), str) or not value[key]:
            invalid(f"{name}.{key}")
    if type(value.get("port")) is not int or not 1024 <= value["port"] <= 65535:
        invalid(f"{name}.port")
    if value.get("architecture") not in ("arm64", "amd64"):
        invalid(f"{name}.architecture")
    if not isinstance(value.get("storage_class"), str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]*", value["storage_class"]
    ):
        invalid(f"{name}.storage_class")
    for key in CONFIG[3:]:
        mapping = value.get(key)
        if not isinstance(mapping, dict) or set(mapping) != set(COMPONENTS):
            invalid(f"{name}.{key}: both backend and frontend records are required")
        for component in COMPONENTS:
            item = mapping[component]
            pattern = (
                rf"{component}:minikube-[0-9]{{14}}-[0-9a-f]{{10}}"
                if key == "images"
                else r"[0-9a-f]{64}"
                if key == "build_fingerprints"
                else r"sha256:[0-9a-f]{64}"
            )
            if not isinstance(item, str) or not re.fullmatch(pattern, item):
                invalid(f"{name}.{key}.{component}")


def archive_reports():
    """Keep previous attempts; never archive credentials or user review bodies."""
    d = api()
    folder = d.STATE / "attempts" / secrets.token_hex(12)
    for name in (
        "startup.json",
        "plan.json",
        "deployment.json",
        "verification.json",
        "undeployment.json",
    ):
        path = d.STATE / name
        if path.is_file():
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination = folder / name
            destination.write_bytes(path.read_bytes())
            os.chmod(destination, 0o600)


def verify_state(owner):
    """Fail before accounts, inference, forwarding or restarts; never supply defaults."""
    d = api()
    complete(owner, "owner.json")
    record = read("deployment.json")
    complete(record, "deployment.json")
    for key in IDENTITY + CONFIG:
        if record.get(key) != owner.get(key):
            invalid(f"deployment.json.{key}: does not match ownership/build records")
    for key in ("profile", "minikube_home", "cluster_uid"):
        if record[key] != d.TARGET.get(key):
            invalid(f"deployment.json.{key}: target identity mismatch")
    attempt = record.get("attempt_id")
    if not isinstance(attempt, str) or not re.fullmatch(r"[0-9a-f]{24}", attempt):
        invalid("deployment.json.attempt_id")
    if record.get("status") != "ready" or record.get("application_ready") is not True:
        invalid("deployment.json: no completed deployment")
    startup = read("startup.json")
    if (
        startup.get("attempt_id") != attempt
        or startup.get("status") != "ready"
        or startup.get("application_ready") is not True
    ):
        invalid("startup.json: latest deployment is failed, interrupted or incomplete")
    plan = read("plan.json")
    if plan.get("status") != "planned" or plan.get("application_ready") is not False:
        invalid("plan.json: expected an uncommitted deployment plan")
    if plan.get("attempt_id") != attempt or any(
        plan.get(k) != record[k] for k in IDENTITY + CONFIG
    ):
        invalid("plan.json: build evidence does not match the completed attempt")
    return record


def image_proof(image, arch, expected_id=None):
    """Bind a verified local build to the loaded CRI config digest, never just its tag."""
    d = api()
    local = json.loads(d.run(["docker", "image", "inspect", image]).stdout)[0]
    d.require(local["Architecture"] == arch, "Build image architecture mismatch; run up again.")
    if expected_id is not None:
        d.require(local["Id"] == expected_id, "Local build image ID changed; run up again.")
    loaded = json.loads(
        d.run(["docker", "exec", d.TARGET["node_name"], "crictl", "inspecti", image]).stdout
    )
    spec = loaded["info"]["imageSpec"]
    d.require(
        spec.get("architecture") == arch
        and spec.get("os") == local.get("Os") == "linux"
        and spec.get("config") == local.get("Config")
        and spec.get("rootfs", {}).get("type") == local.get("RootFS", {}).get("Type")
        and spec.get("rootfs", {}).get("diff_ids") == local.get("RootFS", {}).get("Layers"),
        "Loaded image content/config does not match the local build; run up again.",
    )
    runtime_id = loaded["status"]["id"]
    d.require(re.fullmatch(r"sha256:[0-9a-f]{64}", runtime_id), "Unrecognized runtime image ID.")
    return local["Id"], runtime_id


def verify_images(record):
    """Recheck source fingerprints and Docker/CRI content proof before acceptance side effects."""
    from minikube_target import source_fingerprint

    d = api()
    for component in COMPONENTS:
        d.require(
            source_fingerprint(BUILD_CONTEXTS[component])
            == record["build_fingerprints"][component],
            f"{component} build inputs changed; run up before verify.",
        )
        _, runtime_id = image_proof(
            record["images"][component], record["architecture"], record["image_ids"][component]
        )
        d.require(
            runtime_id == record["runtime_image_ids"][component],
            f"{component} loaded image ID changed; run up before verify.",
        )


def planned_options(owner):
    """Reuse only confirmed port/storage choices from an interrupted attempt for this owner."""
    d = api()
    if not owner or not (d.STATE / "plan.json").exists():
        return owner
    plan = read("plan.json")
    if any(plan.get(key) != owner.get(key) for key in IDENTITY):
        invalid("plan.json: ownership/target identity mismatch")
    port, storage = plan.get("port"), plan.get("storage_class")
    if type(port) is not int or not 1024 <= port <= 65535:
        invalid("plan.json.port")
    if not isinstance(storage, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", storage):
        invalid("plan.json.storage_class")
    return owner | {"port": port, "storage_class": storage}
