"""Real SQLite admission fencing used by Helm lifecycle operations."""

import io
import json
import sqlite3

import pytest
from deployment.common import queue_guard


@pytest.mark.integration
@pytest.mark.parametrize("status", ["idle", "queued", "running", "inference_draining"])
def test_real_sqlite_guard_blocks_racing_submission_and_rejects_busy(tmp_path, monkeypatch, status):
    import urllib.request

    database = tmp_path / "reviews.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE reviews(status TEXT)")
        if status in {"queued", "running"}:
            db.execute("INSERT INTO reviews VALUES (?)", (status,))
    calls = []

    class Response(io.BytesIO):
        def __init__(self, code, state):
            super().__init__(json.dumps({"status": state}).encode())
            self.status = code

    def ready(*args, **kwargs):
        calls.append(1)
        return (
            Response(200, "ready")
            if len(calls) == 1
            else Response(
                503,
                "inference_draining" if status == "inference_draining" else "storage_unavailable",
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", ready)
    raced = []

    def wait(*args):
        with sqlite3.connect(database, timeout=0.01) as writer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("INSERT INTO reviews VALUES ('queued')")
        raced.append(True)
        return [], [], []

    monkeypatch.setattr(queue_guard.select, "select", wait)
    program = queue_guard.IDLE_GUARD.replace("/data/reviews.sqlite3", str(database))
    namespace = {}
    try:
        if status == "idle":
            exec(compile(program, "idle-guard", "exec"), namespace)
            assert raced == [True]
        else:
            with pytest.raises(SystemExit):
                exec(compile(program, "idle-guard", "exec"), namespace)
            assert not raced
    finally:
        if "db" in namespace:
            namespace["db"].close()
