"""Compile/run the small Java example with pinned TCP-only dependencies."""

import argparse
import hashlib
import os
import subprocess
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAVEN = "https://repo.maven.apache.org/maven2/"
# SHA-256 pins, initially verified against Maven Central release checksums.
ARTIFACTS = {
    "org/apache/thrift/libthrift/0.25.0/libthrift-0.25.0.jar": (
        "0d79412164e2f48dcaab541e23a324dac44ea4574c8aa4ea1261c72fc69e73c0"
    ),
    "org/slf4j/slf4j-api/1.7.36/slf4j-api-1.7.36.jar": (
        "d3ef575e3e4979678dc01bf1dcce51021493b4d11fb7f1be8ad982877c16a1c0"
    ),
    "org/slf4j/slf4j-nop/1.7.36/slf4j-nop-1.7.36.jar": (
        "c214958b07816cb4412b30c7bdbd4308ffdc6ba2a83767b8f3a9229cbd9274d6"
    ),
}


def compile_example():
    directory = ROOT / ".local/java-thrift"
    directory.mkdir(parents=True, exist_ok=True)
    for artifact, expected in ARTIFACTS.items():
        target = directory / Path(artifact).name
        if not target.exists():
            with urllib.request.urlopen(MAVEN + artifact, timeout=30) as response:
                target.write_bytes(response.read())
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Java dependency checksum mismatch")
    classpath = os.pathsep.join(str(directory / Path(p).name) for p in ARTIFACTS)
    sources = sorted((ROOT / "contracts/java").rglob("*.java"))
    sources.append(ROOT / "contracts/examples/java/Generate.java")
    subprocess.run(
        ["javac", "-cp", classpath, "-d", str(directory), *map(str, sources)], check=True
    )
    return os.pathsep.join((str(directory), classpath))


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
