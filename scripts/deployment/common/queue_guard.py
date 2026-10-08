"""Hold a remote queue fence through an explicitly supplied transport."""

import contextlib
import os
import select
import subprocess
import time

from deployment.common.errors import DemoError, require

IDLE_GUARD = r"""
import json, select, sqlite3, sys, urllib.request, urllib.error

def ready():
    try:
        response = urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response).get("status")

try:
    if ready() != (200, "ready"):
        raise RuntimeError("not_ready")
    db = sqlite3.connect("file:/data/reviews.sqlite3?mode=rw", uri=True, timeout=5)
    db.execute("BEGIN IMMEDIATE")
    query = "SELECT count(*) FROM reviews WHERE status IN ('queued','running')"
    count = db.execute(query).fetchone()[0]
    if count or ready() != (503, "storage_unavailable"):
        raise RuntimeError("not_idle")
    print("idle_guard_ready", flush=True)
    select.select([sys.stdin], [], [], 180)
    db.rollback()
    db.close()
except Exception:
    print("idle_guard_failed", flush=True)
    sys.exit(1)
"""


@contextlib.contextmanager
def idle_guard(pod, *, command, env):
    proc = subprocess.Popen(
        command("exec", "-i", pod, "-c", "review-backend", "--", "python", "-c", IDLE_GUARD),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 45
        data = b""
        while time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.2)[0]:
                data += os.read(proc.stdout.fileno(), 128)
            require(
                proc.poll() is None and b"idle_guard_failed" not in data,
                "Queue/health guard failed; active, draining or unmeasurable inference cannot "
                "be interrupted.",
            )
            if b"idle_guard_ready\n" in data:
                yield proc
                return
            require(len(data) < 256, "Unexpected queue guard output; details withheld.")
        raise DemoError("Queue guard timed out; no workloads deleted.")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        proc.stdout.close()
