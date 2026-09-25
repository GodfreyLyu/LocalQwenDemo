"""Tiny real safetensors fixtures test cache validation without loading model weights."""

import errno
import json
import logging
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.main import SafeFormatter
from app.model import download_model_snapshot, snapshot_has_model_weights
from app.model_cache import ModelCacheIncompleteError, validate_model_snapshot
from app.startup import startup_stage

SHARDS = ("model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors")


def weight(path, name):
    header = json.dumps({name: {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 4)


@pytest.fixture
def snapshot(tmp_path):
    (tmp_path / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"one": SHARDS[0], "two": SHARDS[1]}})
    )
    weight(tmp_path / SHARDS[0], "one")
    weight(tmp_path / SHARDS[1], "two")
    return tmp_path


def test_complete_single_and_indexed_snapshots(snapshot, tmp_path):
    assert snapshot_has_model_weights(str(snapshot))
    single = tmp_path / "single"
    single.mkdir()
    weight(single / "model.safetensors", "one")
    assert snapshot_has_model_weights(str(single))


@pytest.mark.parametrize("mode", ["missing", "broken_link", "truncated", "wrong_tensor"])
def test_second_shard_alone_is_not_a_complete_snapshot(snapshot, mode):
    first = snapshot / SHARDS[0]
    first.unlink()
    if mode == "broken_link":
        first.symlink_to(snapshot / "absent_blob")
    elif mode == "truncated":
        first.write_bytes(b"incomplete")
    elif mode == "wrong_tensor":
        weight(first, "different")
    assert not snapshot_has_model_weights(str(snapshot))
    with pytest.raises(ModelCacheIncompleteError):
        validate_model_snapshot(str(snapshot))


def test_unreadable_shard_retains_only_errno(snapshot, monkeypatch):
    original = Path.open

    def unreadable(path, *args, **kwargs):
        if path.name == SHARDS[0]:
            raise PermissionError(errno.EACCES, "PRIVATE_PATH_SENTINEL")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unreadable)
    assert not snapshot_has_model_weights(str(snapshot))
    with pytest.raises(ModelCacheIncompleteError) as error:
        validate_model_snapshot(str(snapshot))
    assert error.value.cache_reason == "unreadable_shard"
    assert error.value.errno == errno.EACCES
    assert str(error.value) == "model_cache_incomplete"


@pytest.mark.parametrize(
    "index",
    [
        None,
        {},
        {"weight_map": {}},
        {"weight_map": []},
        {"weight_map": {"one": ["invalid"]}},
        {"weight_map": {"one": "../escape.safetensors"}},
        {"weight_map": {"one": "bad\0.safetensors"}},
    ],
)
def test_invalid_index_never_falls_back_to_single_file(snapshot, index):
    weight(snapshot / "model.safetensors", "one")
    (snapshot / "model.safetensors.index.json").write_text(json.dumps(index))
    assert not snapshot_has_model_weights(str(snapshot))


def test_broken_index(snapshot):
    index = snapshot / "model.safetensors.index.json"
    index.unlink()
    index.symlink_to(snapshot / "absent_index")
    assert not snapshot_has_model_weights(str(snapshot))


def download(snapshot, implementation):
    return download_model_snapshot(
        SimpleNamespace(hf_home=snapshot, model_id="fixed/model", model_revision="fixed-revision"),
        implementation,
        FileNotFoundError,
    )


def test_download_return_does_not_prove_complete(snapshot, caplog):
    (snapshot / SHARDS[0]).unlink()
    calls = []

    def downloader(**kwargs):
        calls.append(kwargs)
        return str(snapshot)

    with pytest.raises(ModelCacheIncompleteError):
        download(snapshot, downloader)
    assert len(calls) == 2
    assert calls[0]["local_files_only"] is True
    assert "local_files_only" not in calls[1]
    assert all("force_download" not in call for call in calls)
    assert any(
        r.getMessage() == "startup_stage_failed"
        and r.stage == "cache_validation"
        and r.error_code == "model_cache_incomplete"
        for r in caplog.records
    )


def test_complete_cache_reused_without_download(snapshot):
    calls = []

    def downloader(**kwargs):
        calls.append(kwargs)
        return str(snapshot)

    assert download(snapshot, downloader) == str(snapshot)
    assert len(calls) == 1 and calls[0]["local_files_only"] is True


def test_one_download_repairs_missing_first_shard(snapshot):
    (snapshot / SHARDS[0]).unlink()
    before = (snapshot / SHARDS[1]).stat()
    calls = []

    def downloader(**kwargs):
        calls.append(kwargs)
        if not kwargs.get("local_files_only"):
            weight(snapshot / SHARDS[0], "one")
        return str(snapshot)

    assert download(snapshot, downloader) == str(snapshot)
    after = (snapshot / SHARDS[1]).stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert len(calls) == 2


def test_download_failure_is_not_retried_or_hidden(snapshot, caplog):
    calls = []

    def downloader(**kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            raise FileNotFoundError
        error = OSError(errno.ENOSPC, "https://example.invalid/TOKEN_COOKIE_PROMPT_SENTINEL")
        error.response = SimpleNamespace(status_code=503)
        raise error

    with pytest.raises(OSError):
        download(snapshot, downloader)
    assert len(calls) == 2
    records = [r for r in caplog.records if r.getMessage() == "startup_stage_failed"]
    payload = json.loads(SafeFormatter().format(records[-1]))
    assert payload["stage"] == "model_download"
    assert payload["errno"] == errno.ENOSPC
    assert payload["http_status"] == 503
    assert payload["exception_type"] == "OSError"
    assert "SENTINEL" not in json.dumps(payload)


@pytest.mark.parametrize(
    "stage",
    ["tokenizer_load", "weights_load", "cpu_placement", "startup_generation", "post_model_storage"],
)
def test_stage_failure_diagnostics_never_format_exception(stage, caplog):
    with pytest.raises(ValueError), startup_stage(stage):
        raise ValueError("PRIVATE_SOURCE_PROMPT_MODEL_TOKEN_SENTINEL")
    payload = json.loads(SafeFormatter().format(caplog.records[-1]))
    assert payload["stage"] == stage
    assert payload["exception_type"] == "ValueError"
    assert "SENTINEL" not in json.dumps(payload)


def test_formatter_rejects_unbounded_diagnostic_fields():
    record = logging.makeLogRecord(
        {
            "msg": "startup_stage_failed",
            "stage": "TOKEN_SENTINEL",
            "exception_type": "https://TOKEN_SENTINEL",
            "cache_reason": "TOKEN_SENTINEL",
            "http_status": "TOKEN_SENTINEL",
            "errno": True,
        }
    )
    payload = json.loads(SafeFormatter().format(record))
    assert "SENTINEL" not in json.dumps(payload)
    assert not {"stage", "exception_type", "cache_reason", "http_status", "errno"} & payload.keys()


@pytest.mark.parametrize(
    "fail_stage", [None, "tokenizer_load", "weights_load", "cpu_placement", "startup_generation"]
)
def test_real_load_pipeline_stops_at_failed_stage(snapshot, monkeypatch, caplog, fail_stage):
    import sys
    from unittest.mock import Mock

    from app.model import TransformersModel

    calls = []

    def step(stage, value=None):
        def run(*args, **kwargs):
            calls.append(stage)
            if fail_stage == stage:
                raise RuntimeError("TOKEN_PROMPT_SENTINEL")
            return value

        return run

    weights = SimpleNamespace(eval=Mock())
    weights.to = step("cpu_placement", weights)
    torch = SimpleNamespace(set_num_threads=Mock(), bfloat16=object())
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda **kwargs: str(snapshot)),
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub.errors",
        SimpleNamespace(LocalEntryNotFoundError=FileNotFoundError),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=step("tokenizer_load", object())),
            AutoModelForCausalLM=SimpleNamespace(from_pretrained=step("weights_load", weights)),
        ),
    )
    model = TransformersModel(
        SimpleNamespace(
            hf_home=snapshot,
            model_id="fixed/model",
            model_revision="fixed-revision",
            model_cpu_threads=2,
            model_dtype="bfloat16",
        )
    )
    monkeypatch.setattr(model, "validate_startup_generation", step("startup_generation"))
    order = ["tokenizer_load", "weights_load", "cpu_placement", "startup_generation"]
    if fail_stage:
        with pytest.raises(RuntimeError):
            model.load()
        assert calls == order[: order.index(fail_stage) + 1]
        failure = next(r for r in caplog.records if r.getMessage() == "startup_stage_failed")
        payload = json.loads(SafeFormatter().format(failure))
        assert payload["stage"] == fail_stage
        assert "SENTINEL" not in json.dumps(payload)
    else:
        model.load()
        assert calls == order
        torch.set_num_threads.assert_called_once_with(2)


def test_coordinator_reports_post_model_storage_failure_without_ready(caplog):
    import asyncio
    from unittest.mock import Mock

    from app.coordinator import Coordinator

    store = SimpleNamespace(
        initialize=Mock(),
        recover=Mock(),
        ping=Mock(side_effect=OSError(errno.EIO, "SECRET_SENTINEL")),
    )
    coordinator = Coordinator(
        store,
        SimpleNamespace(ping=Mock()),
        SimpleNamespace(load=Mock()),
        SimpleNamespace(max_retries=1, queue_capacity=1),
    )
    asyncio.run(coordinator.run())
    coordinator.executor.shutdown()
    assert not coordinator.ready and not coordinator.live
    assert coordinator.state == "startup_or_storage_failure"
    assert not any(r.getMessage() == "model_ready" for r in caplog.records)
    payload = json.loads(SafeFormatter().format(caplog.records[-1]))
    assert payload["stage"] == "post_model_storage"
    assert payload["errno"] == errno.EIO
    assert "SENTINEL" not in json.dumps(payload)
