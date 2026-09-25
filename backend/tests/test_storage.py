import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.errors import AppError
from app.storage import Store


def test_persistent_recovery_and_bounded_retry(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    first = store.create_review("alice", str(uuid4()), "auto", "source", 8)
    second = store.create_review("bob", str(uuid4()), "plain", "source", 8)
    assert store.claim()["review_id"] == first["review_id"]
    restarted = Store(tmp_path)
    restarted.initialize()
    restarted.recover(max_retries=1, capacity=8)
    assert restarted.get_review("alice", first["review_id"])["retry_count"] == 1
    assert restarted.get_review("bob", second["review_id"])["status"] == "queued"
    restarted.claim()
    restarted.recover(max_retries=1, capacity=8)
    assert restarted.get_review("alice", first["review_id"])["error_code"] == "interrupted"
    assert restarted.claim()["review_id"] == second["review_id"]


def test_concurrent_idempotency_is_atomic(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    request_id, barrier = str(uuid4()), threading.Barrier(8)

    def insert(_):
        barrier.wait()
        return store.create_review("alice", request_id, "auto", "x", 8)["review_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(insert, range(8)))
    assert len(set(results)) == 1 and len(store.history("alice", 20)["items"]) == 1


def test_capacity_and_reduced_recovery_capacity(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    store.create_review("alice", "1", "auto", "x", 2)
    store.create_review("bob", "1", "auto", "x", 2)
    with pytest.raises(AppError, match="queue is full"):
        store.create_review("charlie", "1", "auto", "x", 2)
    store.recover(1, 1)
    assert (
        store.get_review("bob", store.history("bob", 1)["items"][0]["review_id"])["status"]
        == "failed"
    )


def test_queue_depth_is_a_content_free_aggregate(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    first = store.create_review("alice", "1", "auto", "PRIVATE_SOURCE_ONE", 8)
    second = store.create_review("bob", "1", "auto", "PRIVATE_SOURCE_TWO", 8)

    assert first["_created"] is True and first["_queue_depth"] == 1
    assert second["_created"] is True and second["_queue_depth"] == 2
    assert store.queue_depth() == 2
    store.claim()
    assert store.queue_depth() == 1


def test_history_cursor_is_stable_and_source_is_not_in_list(tmp_path):
    store = Store(tmp_path)
    store.initialize()
    for i in range(3):
        row = store.create_review("alice", str(i), "auto", "source", 8)
        store.claim()
        store.finish(row["review_id"], result="ok", model_id="model", revision="revision")
    first = store.history("alice", 2)
    second = store.history("alice", 2, first["next_cursor"])
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert second["next_cursor"] is None and "source_code" not in first["items"][0]
