"""Build and manage Helm releases on an existing Minikube."""

import argparse
import re
import subprocess
import sys
import time

from deployment.common.errors import DemoError
from deployment.common.target import connect, require
from deployment.helm.session import Session


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=[
            "init",
            "doctor",
            "up",
            "status",
            "logs",
            "port-forward",
            "verify",
            "undeploy",
            "rollback",
        ],
    )
    p.add_argument("--profile", required=True, help="existing single-node Docker Minikube profile")
    p.add_argument("--namespace", default="local-review-demo")
    p.add_argument("--release", default="local-review")
    p.add_argument("--minikube-home")
    p.add_argument("--state-root")
    p.add_argument("-f", "--values", action="append", default=[])
    p.add_argument("--port", type=int)
    p.add_argument("--storage-class")
    p.add_argument("--timeout", "--cold-timeout", dest="timeout", type=int, default=3900)
    p.add_argument("--warm-timeout", type=int, default=600)
    p.add_argument("--delete-timeout", type=int, default=120)
    p.add_argument("--revision", type=int)
    p.add_argument("--skip-restart", action="store_true")
    p.add_argument("--purge-data", action="store_true")
    p.add_argument("--delete-namespace", action="store_true")
    p.add_argument("--confirm-data-loss")
    return p


def main():
    args = parser().parse_args()
    require(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,51}[a-z0-9])?", args.release), "Invalid release name."
    )
    require(
        args.namespace not in {"default", "kube-system", "kube-public", "kube-node-lease"},
        "Use a dedicated application namespace.",
    )
    require(args.port is None or 1024 <= args.port <= 65535, "Invalid local port.")
    require(
        60 <= args.timeout <= 7200
        and 60 <= args.warm_timeout <= 7200
        and 1 <= args.delete_timeout <= 600,
        "Invalid timeout.",
    )
    require(
        not args.purge_data
        or (args.command == "undeploy" and args.confirm_data_loss == args.namespace),
        "Purge requires --confirm-data-loss NAMESPACE.",
    )
    require(
        not args.delete_namespace or args.purge_data, "Namespace deletion requires --purge-data."
    )
    require(
        args.command != "rollback" or (args.revision and args.revision > 0),
        "Rollback requires an explicit positive --revision.",
    )
    with connect(args.profile, args.namespace, args.minikube_home) as target:
        session = Session(target, args)
        if args.command == "port-forward":
            _, port = session.origin()
            with session.forward(port, wait=True):
                print(f"Browser: http://localhost:{port} (Ctrl-C stops forwarding)", flush=True)
            return
        with session.locked():
            session.archive_stale()
            if args.command == "status":
                session.status()
                return
            if args.command == "logs":
                session.validate_release(session.release_info(), allow_pending=True)
                print(
                    target.kubectl(
                        "logs", "deployment/review-backend", "-c", "review-backend", "--tail=100"
                    ).stdout
                )
                return
            if args.command == "doctor":
                values = session.options()
                report = session.doctor(values, session.render(values))
                require(not report["warnings"], "Diagnostics incomplete; inspect warnings above.")
                return
            record = {"command": args.command, "status": "in_progress", "started_at": time.time()}
            session.save("operation.json", record)
            if args.command in {"up", "rollback", "undeploy"}:
                session.save("verification.json", {"status": "stale", "reason": args.command})
            try:
                if args.command == "undeploy":
                    from deployment.helm.lifecycle import undeploy

                    undeploy(session)
                elif args.command == "verify":
                    from deployment.helm.verify import verify

                    verify(session)
                else:
                    getattr(session, args.command)()
                record["status"] = "complete"
            except BaseException as exc:
                record.update(
                    status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                    error_type=type(exc).__name__,
                )
                raise
            finally:
                session.save("operation.json", record)


def entry():
    try:
        main()
    except (ValueError, RuntimeError, DemoError, OSError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
