import logging
import multiprocessing
import os
import time

from app.config import Settings  # noqa: E402
from app.inference.ollama import OllamaModel  # noqa: E402
from evaluation.config import DRAIN_SECONDS, LOAD_TIMEOUT_SECONDS
from evaluation.metrics import MetricCapture, peak_rss_mib
from evaluation.quality import aggregate, evaluate_case, manual_review
from evaluation.report import write_report

logger = logging.getLogger("review")


def worker(connection, configuration, cases, manual):
    """One disposable client worker runs serial requests; Ollama owns model memory."""
    # Redirect native file descriptors, not only Python streams; do not save suppressed output.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(logging.NullHandler())
    capture = MetricCapture()
    logger.handlers[:] = [capture]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    settings = Settings(_env_file=None, **configuration)
    started = time.monotonic()
    phase = "model_load_failed"
    try:
        model = OllamaModel(settings)
        model.load()
        connection.send(
            (
                "load",
                {
                    "status": "passed",
                    "model_id": settings.ollama_model,
                    "model_revision": model.digest,
                    "device": model.device,
                    "quantization": model.quantization,
                    "seconds": round(time.monotonic() - started, 3),
                    "peak_process_rss_mib": peak_rss_mib(),
                },
            )
        )
        phase = "worker_failed"
        for index, case in enumerate(cases):
            connection.send(("stage", index, "inference"))

            def reviewer(result, current, case_index=index):
                connection.send(("stage", case_index, "manual"))
                with (
                    open("/dev/tty", encoding="utf-8") as answers,
                    open("/dev/tty", "w", encoding="utf-8") as terminal,
                ):
                    return manual_review(result, current, terminal, answers)

            row = evaluate_case(model, settings, case, capture, reviewer if manual else None)
            connection.send(("case", index, row))
        connection.send(("done",))
    except Exception:
        connection.send(("failure", phase, round(time.monotonic() - started, 3), peak_rss_mib()))
    finally:
        connection.close()


def supervise(report, settings, cases, directory, manual, context=None):
    """Persist each sanitized result; a stalled/crashed worker never produces a passing run."""
    context = context or multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(child, settings.model_dump(), cases, manual))
    process.start()
    child.close()
    deadline = time.monotonic() + LOAD_TIMEOUT_SECONDS
    active = None
    complete = False
    try:
        while True:
            if parent.poll(0.1):
                try:
                    event = parent.recv()
                except EOFError:
                    break
                if event[0] == "load":
                    report["load"] = event[1]
                    for name in ("model_id", "model_revision", "device", "quantization"):
                        if name in event[1]:
                            report["parameters"][name] = event[1][name]
                elif event[0] == "stage":
                    active = event[1]
                    deadline = (
                        None
                        if event[2] == "manual"
                        else (time.monotonic() + settings.inference_timeout_seconds + DRAIN_SECONDS)
                    )
                elif event[0] == "case":
                    report["cases"][event[1]] = event[2]
                    active = None
                    deadline = time.monotonic() + DRAIN_SECONDS
                elif event[0] == "failure":
                    report["error_code"] = event[1]
                    report["load"] = {
                        "status": "failed",
                        "seconds": event[2],
                        "peak_process_rss_mib": event[3],
                    }
                    break
                elif event[0] == "done":
                    complete = True
                    break
                write_report(directory, report)
            if deadline is not None and time.monotonic() >= deadline:
                report["error_code"] = "worker_timeout"
                break
            if not process.is_alive() and not parent.poll():
                break
    except KeyboardInterrupt:
        report["error_code"] = "interrupted"
    finally:
        if not complete:
            report["error_code"] = report.get("error_code", "worker_failed")
            if active is not None:
                row = report["cases"][active]
                row["status"] = "failed"
                row["error_code"] = report["error_code"]
                row["checks"]["runtime_budget"] = "failed"
            elif report["load"]["status"] == "not_run":
                report["load"]["status"] = "failed"
        process.join(timeout=1 if not complete else 5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        parent.close()
    states = [report["load"]["status"], *(r["status"] for r in report["cases"])]
    if complete and ("not_run" in states or process.exitcode != 0):
        complete = False
        report["error_code"] = "incomplete_worker_result"
    report["status"] = "failed" if not complete else aggregate(states)
