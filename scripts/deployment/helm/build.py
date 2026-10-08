import json
import secrets
import time

from tooling_paths import ROOT

from deployment.common.target import require, run


def build_images(session, arch):
    overrides, identities = {}, {}
    tag = "minikube-" + str(time.time_ns()) + "-" + secrets.token_hex(3)
    for component in ("backend", "frontend"):
        image = f"review-{component}:{tag}"
        print(f"Building {image} for linux/{arch} (Docker layer cache enabled)", flush=True)
        run(
            "docker",
            "build",
            "--platform",
            f"linux/{arch}",
            "-t",
            image,
            ROOT / component,
            timeout=3600,
            env=session.target.env,
        )
        local = json.loads(run("docker", "image", "inspect", image, env=session.target.env).stdout)[
            0
        ]
        require(
            local["Architecture"] == arch and local["Os"] == "linux",
            "Image architecture mismatch.",
        )
        run(
            "minikube",
            "--skip-audit",
            "-p",
            session.target.profile,
            "image",
            "load",
            "--daemon=true",
            image,
            timeout=900,
            env=session.target.env,
        )
        loaded = json.loads(
            run(
                "docker",
                "exec",
                session.target.node,
                "crictl",
                "inspecti",
                image,
                env=session.target.env,
            ).stdout
        )
        spec = loaded["info"]["imageSpec"]
        require(
            spec.get("architecture") == arch
            and spec.get("os") == "linux"
            and spec.get("config") == local.get("Config")
            and spec.get("rootfs", {}).get("diff_ids") == local.get("RootFS", {}).get("Layers"),
            "Loaded image does not match local build.",
        )
        overrides[component] = {
            "image": {
                "repository": f"review-{component}",
                "tag": tag,
                "digest": "",
                "pullPolicy": "Never",
            }
        }
        identities[component] = {
            "image": image,
            "local_id": local["Id"],
            "runtime_id": loaded["status"]["id"],
        }
        session.save("build.json", identities)
    return overrides
