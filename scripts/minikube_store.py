"""Private checkout-independent state, target locks, and atomic legacy import."""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from minikube_state import IDENTITY, api, ownership


def state_root(override=None):
    base = override or os.environ.get("LOCAL_QWEN_STATE_HOME")
    if not base:
        base = str(
            Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
            / "local-qwen-demo"
        )
    path = Path(base).expanduser()
    api().require(path.is_absolute(), "State root must be an absolute path.")
    api().require(not path.is_symlink(), "State root must not be a symbolic link.")
    return path.resolve()


def private_directory(path):
    d = api()
    path = Path(path)
    d.require(not path.is_symlink(), "Private state directory must not be a symbolic link.")
    previous_mask = os.umask(0o077)
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    finally:
        os.umask(previous_mask)
    d.require(path.stat().st_uid == os.getuid(), "Private state directory belongs to another user.")
    path.chmod(0o700)


@contextlib.contextmanager
def file_lock(path):
    d = api()
    private_directory(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        d.require(os.fstat(stream.fileno()).st_uid == os.getuid(), "State lock owner mismatch.")
        os.fchmod(stream.fileno(), 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise d.DemoError(
                "Another application deployment operation is active on this target."
            ) from None
        yield stream


@contextlib.contextmanager
def operation_lock():
    d = api()
    private_directory(d.STATE_ROOT)
    digest = hashlib.sha256(str(d.STATE.relative_to(d.STATE_ROOT)).encode()).hexdigest()
    with file_lock(d.STATE_ROOT / "locks" / (digest + ".lock")) as lock:
        yield lock


def legacy_source(source):
    """Accept only a supplied checkout/state root/exact target; never search home directories."""
    from minikube_target import target_path

    d = api()
    path = Path(source).expanduser().absolute()
    if (path / "owner.json").is_file():
        return path
    root = path / ".local/minikube-demo" if (path / ".local/minikube-demo").is_dir() else path
    return target_path(root, Path(d.TARGET["minikube_home"]), d.PROFILE, d.TARGET["cluster_uid"])


def read_json(path):
    d = api()
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        raise d.DemoError("Legacy state is unreadable or invalid; no state was adopted.") from None
    d.require(isinstance(value, dict), "Legacy state must contain JSON objects.")
    return value


def import_state(source):
    """Caller holds the shared lock; keep the source and publish only a complete private copy."""
    d = api()
    source = legacy_source(source)
    d.require(source != d.STATE, "Import source is already the shared target directory.")
    d.require(
        (source / "owner.json").is_file(),
        "No legacy owner.json for the selected target at --from-state.",
    )
    d.require(not source.is_symlink(), "Legacy state directory must not be a symbolic link.")
    # Coordinate with the old checkout's CLI as well as other new checkouts.
    with file_lock(source / "operation.lock"):
        original = read_json(source / "owner.json")
        ownership(original)
        d.require(
            re.fullmatch(r"[0-9a-f]{48}", original["owner"]),
            "Legacy state lacks a recognized random ownership marker; no takeover.",
        )
        for key in ("profile", "minikube_home", "cluster_uid"):
            d.require(original.get(key) == d.TARGET[key], "Legacy state target identity mismatch.")
        d.require(
            original.get("namespace_uid"),
            "Legacy state lacks a trusted namespace UID; cannot import.",
        )
        for name in ("plan.json", "deployment.json", "startup.json", "verification.json"):
            if (source / name).exists():
                record = read_json(source / name)
                d.require(
                    all(record[k] == original[k] for k in IDENTITY if k in record),
                    "Legacy records have inconsistent identities.",
                )
        d.guard_target()
        d.check_ownership(original)
        if d.STATE.exists():
            marker = d.STATE / "migration.json"
            d.require(
                marker.is_file(),
                "Shared target already exists; refusing to overwrite or merge state.",
            )
            imported = read_json(marker)
            current = d.state()
            d.require(
                imported.get("source") == str(source.resolve())
                and all(current.get(k) == original.get(k) for k in IDENTITY if k != "root"),
                "Shared target ownership/import conflict; refusing to merge.",
            )
            print("State already imported; shared records retained without overwriting history.")
            return
        private_directory(d.STATE.parent)
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=d.STATE.parent))
        try:
            for item in source.rglob("*"):
                d.require(
                    not item.is_symlink(), "Legacy state contains a symbolic link; import refused."
                )
                relative = item.relative_to(source)
                if item.name == "operation.lock" or item.suffix == ".tmp":
                    continue
                destination = staging / relative
                if item.is_dir():
                    private_directory(destination)
                else:
                    d.require(item.is_file(), "Legacy state contains an unsupported file type.")
                    private_directory(destination.parent)
                    with destination.open("xb") as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        stream.write(item.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
            (staging / "migration.json").write_text(
                json.dumps(
                    {
                        "status": "imported",
                        "source": str(source.resolve()),
                        "source_owner_sha256": hashlib.sha256(
                            (source / "owner.json").read_bytes()
                        ).hexdigest(),
                        "deployment_performed": False,
                        "acceptance_performed": False,
                    },
                    indent=2,
                )
                + "\n"
            )
            (staging / "migration.json").chmod(0o600)
            # An interruption before rename leaves the source intact and no adopted target.
            staging.rename(d.STATE)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        print(
            "State imported; source preserved. Historical builds/reviews do not validate new code."
        )


def maybe_import(args):
    d = api()
    source = getattr(args, "from_state", None)
    if source:
        import_state(source)
    elif not d.STATE.exists():
        candidate = legacy_source(d.ROOT)
        if (candidate / "owner.json").is_file():
            if d.obj("namespace", d.NAMESPACE, optional=True):
                import_state(candidate)
            else:
                print(
                    "Legacy records preserved in the checkout: the namespace is absent; "
                    "no old deployment success is reused."
                )
    if args.command == "import-state":
        d.require(
            (d.STATE / "owner.json").is_file(),
            "No trusted state found. Use import-state --profile NAME --from-state "
            "/path/to/old/checkout.",
        )


def recover_absent_namespace():
    """Absence is not success. Archive evidence before starting a new namespace incarnation."""
    d = api()
    retire_completed_cleanup()
    owner = d.state(optional=True)
    if (
        not owner
        or not owner.get("namespace_uid")
        or d.obj("namespace", d.NAMESPACE, optional=True)
    ):
        return
    import secrets

    from minikube_state import archive_reports

    archive_reports()
    folder = d.STATE / "attempts" / secrets.token_hex(12)
    private_directory(folder)
    shutil.copyfile(d.STATE / "owner.json", folder / "owner.json")
    (folder / "owner.json").chmod(0o600)
    for name in (
        "plan.json",
        "deployment.json",
        "verification.json",
        "startup.json",
        "undeployment.json",
    ):
        path = d.STATE / name
        if path.exists():
            path.unlink()
    d.save("owner.json", d.TARGET | {"owner": secrets.token_hex(24), "state_version": 3})
    d.save(
        "recovery.json",
        {
            "status": "namespace_absent",
            "previous_namespace_uid": owner["namespace_uid"],
            "application_ready": False,
        },
    )
    print(
        "WARNING: Previous namespace is absent. Historical evidence archived; old data is not "
        "claimed recovered."
    )


def retire_completed_cleanup():
    """Only a later explicit up retires lost-state evidence, after rechecking actual absence."""
    import secrets

    from minikube_recovery import IDENTITY, JOURNAL, check_records, record

    d = api()
    previous = record(JOURNAL)
    if previous.get("status") != "cleaned":
        return
    d.require(
        previous.get("namespace_deleted") is True
        and previous.get("namespace_delete_requested") is True,
        "Recovery completion evidence is incomplete; inspect the recovery journal.",
    )
    d.require(
        all(previous.get(k) == d.TARGET[k] for k in ("profile", "minikube_home", "cluster_uid")),
        "Recovery target identity changed; old history must not authorize this deployment.",
    )
    # A replaced namespace must still pass normal ownership; never retire evidence to adopt it.
    d.require(
        d.obj("namespace", d.NAMESPACE, optional=True) is None,
        "A namespace exists after recovery cleanup; refusing adoption or history replacement.",
    )
    check_records({k: previous[k] for k in IDENTITY})
    folder = d.STATE / "attempts" / secrets.token_hex(12)
    private_directory(folder)
    names = (
        "owner.json",
        "plan.json",
        "startup.json",
        "deployment.json",
        "verification.json",
        "undeployment.json",
        JOURNAL,
    )
    for name in names:
        source = d.STATE / name
        if source.exists():
            shutil.copyfile(source, folder / name)
            (folder / name).chmod(0o600)
    # All originals have complete copies before active records are retired. The journal is last;
    # an interruption can repeat this step and cannot make partial records into a valid owner.
    for name in names:
        (d.STATE / name).unlink(missing_ok=True)
    print(
        "Completed recovery evidence archived after confirming namespace absence. "
        "This up will create a new deployment; old acceptance is historical only."
    )
