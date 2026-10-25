"""Build the Java SDK and run its external example through the shared Maven build."""

import argparse
import os
import shutil
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "clients/java"
CONSUMER = SDK / "examples/consumer"


def maven(pom, *goals, capture_output=False):
    """MAVEN selects an executable; Maven itself also honors MAVEN_ARGS/MAVEN_OPTS."""
    executable = shutil.which(os.environ.get("MAVEN", "mvn"))
    if executable is None:
        raise RuntimeError("Maven 3.9+ is required; put mvn on PATH or set MAVEN to its path")
    return subprocess.run(
        [executable, "-B", "-ntp", "-f", str(pom), *goals],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def compile_example():
    maven(
        SDK / "pom.xml",
        "package",
        "dependency:build-classpath",
        "-DincludeScope=test",
        "-Dmdep.outputFile=target/example-classpath.txt",
    )
    dependencies = (SDK / "target/example-classpath.txt").read_text().strip()
    # Execute against the packaged JAR, not an accidental second copy of the bindings.
    return os.pathsep.join(
        (str(SDK / "target/test-classes"), str(SDK / "target/llm-client-0.1.0.jar"), dependencies)
    )


def compile_consumer(directory=CONSUMER):
    """Install locally, then build a standalone application with only the SDK dependency."""
    maven(SDK / "pom.xml", "install", "-DskipTests")
    maven(directory / "pom.xml", "clean", "package")


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
