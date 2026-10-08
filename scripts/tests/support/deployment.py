"""Offline regression tests; these are never evidence of real-model acceptance."""

import deployment.legacy.runtime as demo  # noqa: E402


def resource(resources, kind, name):
    return next(r for r in resources if r["kind"] == kind and r["metadata"]["name"] == name)


def permission_bits(metadata, uid, groups):
    """Evaluate ordinary non-root Unix DAC against recorded image/volume metadata."""
    assert uid != 0
    shift = 6 if uid == metadata["uid"] else 3 if metadata["gid"] in groups else 0
    return (int(metadata["mode"], 8) >> shift) & 7


def roomy_budget():
    return dict(
        desired={
            "cpu_request": 2.2,
            "memory_request": 4.5 * demo.GIB,
            "memory_limit": 7 * demo.GIB,
        },
        other={"cpu_request": 0.5, "memory_request": demo.GIB, "memory_limit": demo.GIB},
        allocatable={"cpu": 6, "memory": 10 * demo.GIB},
        host_available=10 * demo.GIB,
        docker_total=16 * demo.GIB,
        docker_used=2 * demo.GIB,
        node_used=demo.GIB,
        node_cap=10 * demo.GIB,
        own_used=0,
        disk_free=40 * demo.GIB,
        disk_need=28 * demo.GIB,
    )


def profile(name, host="Running"):
    return {"Name": name, "Host": host, "APIServer": "Running", "Kubelet": "Running"}
