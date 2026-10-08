"""Legacy deployment stages; the runtime supplies target/state/command operations."""

import json
import secrets
import subprocess
import sys
import time


def up(ctx, args, plan):
    # Check ownership again under the operation lock, before replacing any attempt report.
    """Record a new attempt; archive old evidence and fail closed on interruption."""
    ctx.require_local_docker()
    ctx.check_ownership(ctx.state(optional=True))
    warnings = list(plan["report"]["blockers"])
    for warning in warnings:
        print(f"WARNING: {warning} Deployment will still be attempted.", flush=True)
    report = {
        "profile": ctx.PROFILE,
        "cluster_uid": ctx.TARGET["cluster_uid"],
        "attempt_id": secrets.token_hex(12),
        "started_at": int(time.time()),
        "status": "in_progress",
        "stage": "report_initialization",
        "component": None,
        "preflight": plan["report"],
        "diagnostic_warnings": warnings,
        "application_ready": False,
        "review_completed": False,
        "ui_verified": False,
    }

    def stage(name, component=None):
        report.update(stage=name, component=component)
        ctx.save("startup.json", report)
        print(f"Deployment stage: {name}" + (f" ({component})" if component else ""), flush=True)

    try:
        from deployment.legacy.state import archive_reports

        archive_reports()
        if (ctx.STATE / "undeployment.json").exists():
            from deployment.legacy.state import read

            previous_cleanup = read("undeployment.json")
            ctx.save(
                "undeployment.json", previous_cleanup | {"status": "superseded", "quiesced": False}
            )
        ctx.save("startup.json", report)
        ctx.save(
            "deployment.json",
            {
                "attempt_id": report["attempt_id"],
                "status": "in_progress",
                "application_ready": False,
            },
        )
        ctx.save(
            "verification.json",
            {
                "status": "not_run",
                "reason": "deployment_started",
                "deployment_attempt_id": report["attempt_id"],
                "review_completed": False,
                "persistence_verified": False,
                "api_acceptance_passed": False,
                "ui_verified": False,
            },
        )
        timings = ctx.deploy_application(args, plan, stage)
        report.update(timings)
        report.update(status="ready", stage="complete", component=None, application_ready=True)
        ctx.save("startup.json", report)
    except (Exception, KeyboardInterrupt) as exc:
        # Record and re-raise; no failed deployment step is ever skipped or continued.
        if report["stage"] == "complete":
            report["stage"] = "report_save"
        report.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            application_ready=False,
            error_type=type(exc).__name__,
        )
        message = (
            str(exc)
            if isinstance(exc, ctx.DemoError)
            else f"{type(exc).__name__}; details withheld"
        )
        report["error"] = message
        try:
            ctx.save("startup.json", report)
            ctx.save(
                "deployment.json",
                {
                    "attempt_id": report["attempt_id"],
                    "status": report["status"],
                    "stage": report["stage"],
                    "application_ready": False,
                },
            )
        except OSError:
            print(
                "ERROR: Could not save the failed attempt report; existing report may be stale.",
                file=sys.stderr,
            )
        if isinstance(exc, KeyboardInterrupt):
            raise
        component = f" ({report['component']})" if report["component"] else ""
        raise ctx.DemoError(
            f"Deployment failed during {report['stage']}{component}: {message}"
        ) from None
    print(json.dumps(report))
    print("Ready is not inference acceptance. Next: verify, then port-forward.")


def deploy_application(ctx, args, plan, stage):
    """Apply one checked plan under the target lock, preserving owned data and signing state.

    Build/load images, initialize DynamoDB, apply workloads and wait for readiness
    before publishing completed deployment evidence. Errors propagate to up()."""
    import boto3  # noqa: F401
    import yaml  # noqa: F401

    stage("ownership_check")
    ctx.require_local_docker()
    arch = plan["architecture"]
    owner = ctx.state(optional=True)
    ns = ctx.check_ownership(owner)
    from deployment.legacy.ollama import HOSTNAME, PORT, resolve_host_ip

    stage("ollama_host_resolution")
    ollama_host_ip = resolve_host_ip()
    print(f"Ollama egress: {HOSTNAME} -> {ollama_host_ip}/32 TCP {PORT}", flush=True)
    if owner is None:
        owner = ctx.TARGET | {"owner": secrets.token_hex(24), "state_version": 2}
        ctx.save("owner.json", owner)
    port = plan.get("port") or args.port or owner.get("port", 8080)
    stage("namespace_setup")
    if ns is None:
        ctx.k(
            "create",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": ctx.NAMESPACE,
                        "labels": {ctx.LABEL: ctx.NAMESPACE},
                        "annotations": {ctx.OWNER_KEY: owner["owner"]},
                    },
                }
            ),
        )
        ns = ctx.obj("namespace", ctx.NAMESPACE)
    owner["namespace_uid"] = ns["metadata"]["uid"]
    ctx.save("owner.json", owner)
    ctx.guard_cluster()
    from deployment.legacy.state import IDENTITY, image_proof, read

    planned = {key: owner[key] for key in IDENTITY}
    planned.update(
        attempt_id=read("startup.json")["attempt_id"],
        status="planned",
        application_ready=False,
        port=port,
        architecture=arch,
        storage_class=plan["storage_class"],
        build_fingerprints=plan["fingerprints"],
        source_root=str(ctx.ROOT),
        model_backend="ollama",
        ollama_network={"hostname": HOSTNAME, "ipv4": ollama_host_ip, "port": PORT},
        images={},
        image_ids={},
        runtime_image_ids={},
    )
    ctx.save("plan.json", planned)
    stage("image_manifest_check")
    ctx.check_images(arch)
    tag = time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(5)
    images = {name: f"{name}:minikube-{tag}" for name in ("review-backend", "review-frontend")}
    started = time.monotonic()
    for name, context in (("review-backend", "backend"), ("review-frontend", "frontend")):
        stage("image_build", name)
        reusable = plan["reusable_images"].get(name)
        if reusable:
            print(f"Reusing verified native {name} layers with unique tag {tag}.", flush=True)
            ctx.run(["docker", "tag", reusable, images[name]])
        else:
            print(f"Building native {name}, unique tag {tag}…", flush=True)
            code = subprocess.call(
                [
                    "docker",
                    "build",
                    "--platform",
                    f"linux/{arch}",
                    "-t",
                    images[name],
                    str(ctx.ROOT / context),
                ],
                env=ctx.clean_env(),
            )
            ctx.require(
                code == 0, f"{name} build failed; check wheel/architecture/disk diagnostics."
            )
        actual = ctx.run(
            ["docker", "image", "inspect", images[name], "--format", "{{.Architecture}}"]
        ).stdout.strip()
        ctx.require(actual == arch, "Built image architecture mismatch; refusing emulation.")
        stage("image_load", name)
        # Persist the build identity before loading; compare loaded content to that exact build.
        expected_id = ctx.run(
            ["docker", "image", "inspect", images[name], "--format", "{{.Id}}"]
        ).stdout.strip()
        planned["images"][name] = images[name]
        planned["image_ids"][name] = expected_id
        ctx.save("plan.json", planned)
        ctx.mk("image", "load", "--daemon=true", images[name], timeout=900)
        local_id, runtime_id = image_proof(images[name], arch, expected_id)
        planned["images"][name] = images[name]
        planned["image_ids"][name] = local_id
        planned["runtime_image_ids"][name] = runtime_id
        ctx.save("plan.json", planned)
    stage("resource_render")
    ctx.require(
        resolve_host_ip() == ollama_host_ip,
        "Ollama host address changed during deployment; rerun up to generate a fresh rule.",
    )
    resources = ctx.render(
        port,
        images,
        args.cold_timeout,
        plan["storage_class"],
        ollama_host_ip,
    )
    # Preflight ALL resource conflicts before applying any application changes.
    stage("resource_ownership_check")
    for resource in resources:
        previous = ctx.obj(resource["kind"], resource["metadata"]["name"], optional=True)
        if previous:
            ctx.owned(previous, owner["owner"])
    stage("secret_setup")
    ctx.ensure_secret(owner["owner"])
    with ctx.idle_application_update(owner["owner"]):
        initial = [
            r
            for r in resources
            if r["kind"] != "Deployment" or r["metadata"]["name"] == "review-dynamodb"
        ]
        stage("dependency_apply")
        ctx.apply(initial, owner["owner"])
        stage("dependency_readiness")
        ctx.wait_rollout("review-dynamodb", 180)
        stage("dependency_initialization")
        ctx.init_users()  # A hard sequencing gate before backend is created/updated.
        model_start = time.monotonic()
        stage("application_apply")
        ctx.apply(
            [
                r
                for r in resources
                if r["kind"] == "Deployment" and r["metadata"]["name"] != "review-dynamodb"
            ],
            owner["owner"],
        )
        stage("backend_readiness")
        ctx.wait_rollout("review-backend", args.cold_timeout)
        stage("frontend_readiness")
        ctx.wait_rollout("review-frontend", 180)
    stage("state_save")
    owner.update(
        port=port,
        images=images,
        architecture=arch,
        storage_class=plan["storage_class"],
        cni=plan["cni"],
        build_fingerprints=plan["fingerprints"],
        image_ids=planned["image_ids"],
        runtime_image_ids=planned["runtime_image_ids"],
    )
    ctx.save("owner.json", owner)
    ctx.save("deployment.json", planned | {"status": "ready", "application_ready": True})

    return {
        "build_and_deploy_seconds": round(time.monotonic() - started, 1),
        "backend_ready_seconds_including_download_if_needed": round(
            time.monotonic() - model_start, 1
        ),
    }
