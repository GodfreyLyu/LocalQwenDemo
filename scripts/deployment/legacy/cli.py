"""Legacy argument parsing and dispatch; runtime context is supplied by the caller."""

import argparse
import fcntl
import json
import os


def main(ctx):
    from deployment.legacy.target import connected_target

    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "You manage the cluster; this script deploys the application. doctor "
            "exits nonzero for failed or incomplete diagnostics. up warns and att"
            "empts deployment despite resource/version diagnostics; target, owner"
            "ship, architecture and storage requirements remain mandatory. recove"
            "r-cleanup defaults to a read-only preview; deletion requires explici"
            "t target identities and --execute --purge-data --confirm-data-loss l"
            "ocal-review-demo. See docs/guides/minikube-legacy.md."
        ),
    )
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "up",
            "port-forward",
            "verify",
            "status",
            "logs",
            "stop",
            "import-state",
            "undeploy",
            "inspect-target",
            "recover-cleanup",
        ],
    )
    parser.add_argument(
        "--profile",
        help=(
            "existing running profile; required for inspect-target/recover-cleanu"
            "p or when multiple clusters are running"
        ),
    )
    parser.add_argument(
        "--minikube-home",
        default=str(ctx.MINIKUBE_HOME),
        help="existing minikube state directory; defaults to MINIKUBE_HOME or ~/.minikube",
    )
    parser.add_argument(
        "--storage-class", help="existing minikube-hostpath StorageClass; default: standard"
    )
    parser.add_argument("--port", type=int, help="localhost HTTP port; set during up; default 8080")
    parser.add_argument("--cold-timeout", type=int, default=3600)
    parser.add_argument("--warm-timeout", type=int, default=600)
    parser.add_argument(
        "--skip-restart",
        action="store_true",
        help="API diagnostics only; does not pass persistence acceptance",
    )
    parser.add_argument(
        "--state-root",
        help="private shared state root; overrides LOCAL_QWEN_STATE_HOME / XDG_STATE_HOME",
    )
    parser.add_argument(
        "--from-state", help="explicit legacy checkout, state root, or target directory to import"
    )
    parser.add_argument(
        "--purge-data",
        action="store_true",
        help="delete persistent data (confirmation required); recovery also deletes namespace",
    )
    parser.add_argument(
        "--execute", action="store_true", help="recover-cleanup: execute the read-only plan"
    )
    parser.add_argument(
        "--restore-frontend",
        action="store_true",
        help="recover-cleanup: restore only this journal's paused frontend UID",
    )
    parser.add_argument(
        "--expect-cluster-uid", help="recover-cleanup: independently confirmed cluster UID"
    )
    parser.add_argument(
        "--expect-namespace-uid", help="recover-cleanup: independently confirmed namespace UID"
    )
    parser.add_argument(
        "--expect-owner", help="recover-cleanup: original resource ownership marker"
    )
    parser.add_argument(
        "--confirm-data-loss", help="required with --purge-data: type local-review-demo"
    )
    parser.add_argument(
        "--delete-timeout",
        type=int,
        default=120,
        help="cleanup: per-resource wait bound in seconds",
    )
    args = parser.parse_args()
    ctx.require(
        not args.purge_data
        or (
            args.command in {"undeploy", "recover-cleanup"}
            and args.confirm_data_loss == ctx.NAMESPACE
        ),
        "Data deletion requires --purge-data --confirm-data-loss local-review-demo.",
    )
    ctx.require(
        not args.execute or (args.command == "recover-cleanup" and args.purge_data),
        (
            "Recovery execution requires --execute --purge-data --confirm-data-lo"
            "ss local-review-demo."
        ),
    )
    ctx.require(
        not args.restore_frontend
        or (args.command == "recover-cleanup" and (not args.execute) and (not args.purge_data)),
        "--restore-frontend is a separate recovery action; do not combine it with deletion.",
    )
    if args.command in {"inspect-target", "recover-cleanup"}:
        ctx.require(bool(args.profile), "Recovery inspection requires an explicit --profile.")
        ctx.require(not args.from_state, "Recovery never imports or migrates state.")
    if args.command == "recover-cleanup":
        ctx.require(
            bool(args.expect_cluster_uid and args.expect_namespace_uid and args.expect_owner),
            (
                "Recovery requires --expect-cluster-uid, --expect-namespace-uid and -"
                "-expect-owner. Read identities with inspect-target first."
            ),
        )
    ctx.require(1 <= args.delete_timeout <= 600, "Deletion timeout must be 1–600 seconds.")
    if args.command == "stop":
        print(
            "stop performs no operations. You manage the cluster using minikube c"
            "ommands; consider other projects on the same cluster. Use undeploy f"
            "or owned application cleanup before stopping the cluster yourself."
        )
        return
    ctx.require(args.port is None or 1024 <= args.port <= 65535, "Choose a port 1024–65535.")
    ctx.require(
        args.cold_timeout >= 60 and args.warm_timeout >= 60, "Readiness waits must be >= 60s."
    )
    safe = ctx.clean_env()
    for key in list(os.environ):
        if key.startswith(("AWS_", "HF_", "MINIKUBE_")):
            del os.environ[key]
    os.environ.update(safe)
    from deployment.legacy.store import (
        maybe_import,
        operation_lock,
        private_directory,
        recover_absent_namespace,
        state_root,
    )

    ctx.STATE_ROOT = state_root(args.state_root)
    with connected_target(args) as kubeconfig:
        with operation_lock() as lock:
            if args.command in {"inspect-target", "recover-cleanup"}:
                from deployment.legacy.recovery import inspect_target, recover_cleanup

                if args.command == "inspect-target":
                    inspect_target()
                else:
                    recover_cleanup(args)
                return
            maybe_import(args)
            if args.command == "import-state":
                return
            if args.command == "doctor":
                ctx.doctor(args)
                return
            if args.command == "undeploy":
                from deployment.legacy.undeploy import undeploy

                undeploy(args)
                return
            if args.command == "up":
                ctx.guard_target()
                recover_absent_namespace()
                plan = ctx.deployment_plan(args)
                private_directory(ctx.STATE)
                ctx.check_ownership(ctx.state(optional=True))
                ctx.save("kubeconfig", kubeconfig)
                ctx.up(args, plan)
                return
            owner = ctx.guard_cluster()
            if args.command == "port-forward":
                from deployment.legacy.state import verify_state

                port = verify_state(owner)["port"]
                ctx.require(
                    args.port is None or args.port == port,
                    "Port differs from ALLOWED_ORIGIN; run up --port PORT first.",
                )
                fcntl.flock(lock, fcntl.LOCK_UN)
                with ctx.forward("review-frontend", 8080, port, wait=True):
                    print(
                        f"Browser: http://localhost:{port} "
                        "(loopback only; Ctrl-C stops forwarding)",
                        flush=True,
                    )
            elif args.command == "status":
                print(
                    ctx.k(
                        "get",
                        "deployments,pods,pvc,services",
                        "-l",
                        f"{ctx.LABEL}={ctx.NAMESPACE}",
                        "-o",
                        "wide",
                    ).stdout
                )
                from deployment.legacy.target import cni_status

                print(json.dumps(cni_status(), ensure_ascii=False))
            elif args.command == "logs":
                ctx.logs()
            elif args.command == "verify":
                from deployment.legacy.verify import verify

                verify(owner, args)
