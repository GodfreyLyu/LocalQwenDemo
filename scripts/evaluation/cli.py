"""Plan or evaluate the fixed synthetic suite against an explicitly selected Ollama service."""

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from evaluation import config, runner
from evaluation.config import (
    CASE_IDS,
    load_fixtures,
    settings_for,
)
from evaluation.report import build_report, write_report


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid evaluation arguments. Use --help.\n")


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument(
        "--run-real-model",
        action="store_true",
        help="explicitly evaluate the configured local Ollama model",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and save a plan only")
    parser.add_argument("--case", choices=("all", *CASE_IDS), default="all")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    parser.add_argument("--ollama-model-digest", default=None)
    from app.model_config import ModelSettings

    for name, field in ModelSettings.model_fields.items():
        if name in {
            "ollama_base_url",
            "ollama_model_digest",
            "model_max_output_tokens",
            "inference_timeout_seconds",
            "model_inference_concurrency",
        }:
            continue
        parser.add_argument(
            "--" + name.replace("_", "-"), type=type(field.default), default=field.default
        )
    parser.add_argument("--max-output-tokens", type=int, default=384)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument(
        "--review-in-terminal",
        action="store_true",
        help="explicit private TTY view and manual judgments for synthetic output",
    )
    args = parser.parse_args(argv)
    if args.run_real_model and args.dry_run or args.review_in_terminal and not args.run_real_model:
        parser.error("incompatible modes")
    directory = None
    try:
        suite = load_fixtures()
        settings = settings_for(
            ollama_base_url=args.ollama_base_url,
            ollama_model_digest=args.ollama_model_digest,
            model_max_output_tokens=args.max_output_tokens,
            inference_timeout_seconds=args.timeout_seconds,
            **{
                name: getattr(args, name)
                for name in ModelSettings.model_fields
                if hasattr(args, name) and name not in {"ollama_base_url", "ollama_model_digest"}
            },
        )
        selected = list(CASE_IDS) if args.case == "all" else [args.case]
        report = build_report(
            settings, suite, selected, args.run_real_model, args.review_in_terminal
        )
        config.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        directory = Path(
            tempfile.mkdtemp(
                prefix=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + "run-",
                dir=config.OUTPUT_ROOT,
            )
        )
        write_report(directory, report)
        if not args.run_real_model:
            print(
                f"Plan only: {len(selected)} selected case(s); {settings.ollama_model} "
                f"at {settings.ollama_model_digest}; Ollama, serial; "
                f"{settings.model_max_output_tokens} output tokens / "
                f"{settings.inference_timeout_seconds:g} seconds; Ollama tokenizer."
            )
        if args.run_real_model:
            if any(v is None for v in report["environment"]["dependencies"].values()):
                report.update(status="failed", error_code="missing_model_dependencies")
            elif args.review_in_terminal and not sys.stdin.isatty():
                report.update(status="failed", error_code="private_terminal_required")
            else:
                cases = [c for c in suite["cases"] if c["id"] in selected]
                runner.supervise(report, settings, cases, directory, args.review_in_terminal)
        report["finished_at"] = datetime.now().astimezone().isoformat()
        write_report(directory, report)
        print(
            f"Evaluation {report['status']}; report: "
            f".local/model-evaluations/{directory.name}/report.json"
        )
        if report["status"] == "failed":
            print("See docs/testing/model-evaluation.md for Ollama prerequisites and failures.")
        return {"passed": 0, "not_run": 0, "needs_manual_review": 3, "failed": 1}[report["status"]]
    except Exception:
        # Never stringify arbitrary exceptions, Settings validation inputs or CLI paths.
        if directory is not None:
            report.update(status="failed", error_code="evaluation_failed")
            try:
                write_report(directory, report)
            except OSError:
                pass
        print("Evaluation failed safely. See docs/testing/model-evaluation.md.", file=sys.stderr)
        return 1


def entry():
    raise SystemExit(main())
