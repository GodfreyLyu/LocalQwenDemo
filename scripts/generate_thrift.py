"""Generate/check Python and Java bindings using Apache Thrift 0.25.0."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not shutil.which("thrift"):
        parser.error("Install Apache Thrift 0.25.0, or use the thrift:0.25.0 image.")
    version = subprocess.check_output(["thrift", "--version"], text=True).strip()
    if version != "Thrift version 0.25.0":
        parser.error(f"Expected Apache Thrift 0.25.0; found {version}.")
    with tempfile.TemporaryDirectory(prefix="llm-thrift-") as directory:
        for language, relative, destination in (
            ("py", "llm_contract/generated", "contracts/python/llm_contract/generated"),
            (
                "java:generated_annotations=suppress",
                "demo/llm/generated",
                "contracts/java/demo/llm/generated",
            ),
        ):
            output = Path(directory) / language.split(":")[0]
            output.mkdir()
            subprocess.run(
                [
                    "thrift",
                    "--gen",
                    language,
                    "-out",
                    str(output),
                    str(ROOT / "contracts/llm.thrift"),
                ],
                check=True,
            )
            generated = output / relative
            target = ROOT / destination
            files = {
                p.name: p.read_bytes()
                for p in generated.iterdir()
                if p.is_file() and not p.name.endswith("-remote")
            }
            if args.check:
                actual = {p.name: p.read_bytes() for p in target.glob("*") if p.is_file()}
                if actual != files:
                    parser.exit(1, "Bindings are stale; run python scripts/generate_thrift.py.\n")
            else:
                target.mkdir(parents=True, exist_ok=True)
                for name, content in files.items():
                    (target / name).write_bytes(content)
    print("Python and Java bindings match llm.thrift.")


if __name__ == "__main__":
    main()
