"""Compile generated Java bindings and run the direct Thrift example."""

import argparse
import os
import shutil
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


def compile_example():
    """Compile checked-in bindings and examples; no SDK is installed or published."""
    executable = shutil.which(os.environ.get("MAVEN", "mvn"))
    if executable is None:
        raise RuntimeError("Maven 3.9+ is required; put mvn on PATH or set MAVEN to its path")
    subprocess.run(
        [
            executable,
            "-B",
            "-ntp",
            "-f",
            str(CONTRACTS / "pom.xml"),
            "test-compile",
            "dependency:build-classpath",
            "-DincludeScope=test",
            "-Dmdep.outputFile=target/java-classpath.txt",
        ],
        cwd=ROOT,
        check=True,
    )
    dependencies = (CONTRACTS / "target/java-classpath.txt").read_text().strip()
    return os.pathsep.join(
        (str(CONTRACTS / "target/test-classes"), str(CONTRACTS / "target/classes"), dependencies)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9091)
    parser.add_argument("--request-id", default=None)
    args = parser.parse_args()
    classpath = compile_example()
    if not args.compile_only:
        subprocess.run(
            [
                "java",
                "-cp",
                classpath,
                "Generate",
                args.host,
                str(args.port),
                args.request_id or str(uuid.uuid4()),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
