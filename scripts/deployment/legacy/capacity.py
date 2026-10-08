"""Legacy deployment policy; no CLI imports or target discovery on import."""

import re

from deployment.common.errors import DemoError
from deployment.common.resources import parse_quantity
from deployment.legacy.context import api

GIB = 1024**3


def quantity(value):
    try:
        return parse_quantity(value, extended=True)
    except ValueError as error:
        message = (
            f"Unsupported resource quantity: {value!r}"
            if str(error) == "syntax"
            else "Unsupported resource suffix."
        )
        raise DemoError(message) from None


def resources_of(spec):
    """Conservative concurrent envelope, including init containers and Pod overhead.

    Counting all init containers concurrently also safely covers native sidecars.
    It overestimates ordinary sequential init containers by their small resource budgets.
    """
    result = {
        "cpu_request": 0.0,
        "memory_request": 0.0,
        "memory_limit": 0.0,
        "cpu_limit": 0.0,
        "unbounded_memory": 0,
    }
    for c in spec.get("containers", []) + spec.get("initContainers", []):
        r = c.get("resources", {})
        for field in ("cpu", "memory"):
            request = quantity(r.get("requests", {}).get(field, 0))
            limit = quantity(r.get("limits", {}).get(field, 0))
            result[field + "_request"] += request
            result[field + "_limit"] += max(request, limit)
        result["unbounded_memory"] += int(not r.get("limits", {}).get("memory"))
    for field in ("cpu", "memory"):
        overhead = quantity(spec.get("overhead", {}).get(field, 0))
        result[field + "_request"] += overhead
        result[field + "_limit"] += overhead
    return result


def sum_budgets(specs):
    values = [resources_of(s) for s in specs]
    return {key: sum(v[key] for v in values) for key in resources_of({})}


def docker_usage(value):
    number = value.split("/")[0].strip()
    match = re.fullmatch(r"([0-9.]+)(B|KiB|MiB|GiB|TiB)", number)
    api().require(
        match, "Current Docker usage could not be measured; it cannot be assumed to be zero."
    )
    return (
        float(match[1])
        * {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": GIB, "TiB": 1024**4}[match[2]]
    )


def docker_cpu_limit(limits, total):
    caps = [float(total)]
    if limits["nano"] > 0:
        caps.append(limits["nano"] / 1e9)
    if limits["quota"] > 0 and limits["period"] > 0:
        caps.append(limits["quota"] / limits["period"])
    if limits["cpuset"]:
        count = 0
        for part in limits["cpuset"].split(","):
            bounds = [int(v) for v in part.split("-")]
            count += bounds[-1] - bounds[0] + 1
        caps.append(count)
    return min(caps)


def capacity_errors(
    *,
    desired,
    other,
    allocatable,
    host_available,
    docker_total,
    docker_used,
    node_used,
    node_cap,
    own_used,
    disk_free,
    disk_need,
    node_cpu_cap=None,
):
    """Requests govern scheduling; limits/observed usage govern memory risk separately."""
    errors = []
    # Missing inputs invalidate only the calculations that depend on them, never become zero.
    inputs = {
        "node allocatable": allocatable,
        "other workload resources": other,
        "host available memory": host_available,
        "Docker memory quota": docker_total,
        "Docker memory usage": docker_used,
        "node memory usage": node_used,
        "node memory quota": node_cap,
        "host free disk space": disk_free,
    }
    errors.extend(
        f"{name} was not measured; dependent capacity checks are incomplete."
        for name, value in inputs.items()
        if value is None
    )
    effective_cpu = (
        min(allocatable["cpu"], node_cpu_cap if node_cpu_cap is not None else allocatable["cpu"])
        if allocatable is not None
        else None
    )
    effective_memory = (
        min(allocatable["memory"], node_cap)
        if allocatable is not None and node_cap is not None
        else None
    )
    need = desired["memory_limit"]
    if other is not None and (
        (
            effective_cpu is not None
            and effective_cpu - other["cpu_request"] < desired["cpu_request"]
        )
        or (
            effective_memory is not None
            and effective_memory - other["memory_request"] < desired["memory_request"]
        )
    ):
        errors.append(
            "Insufficient target cluster capacity: effective allocatable/Docker node quotas "
            "minus other workload requests cannot accommodate this application."
        )
    if (
        effective_memory is not None
        and other is not None
        and effective_memory - other["memory_limit"] < need + 512 * 1024**2
    ):
        errors.append(
            "Insufficient target cluster memory: other workload requests/limits plus application "
            "limits and a 512 MiB margin exceed capacity."
        )
    # own_used=None is explicit uncertainty: for repeat deployments require measurement rather
    # than double-counting existing project processes or pretending they use zero bytes.
    if own_used is None:
        errors.append(
            "Existing application memory usage was not measured; incremental redeployment "
            "requirements cannot be calculated. Restore metrics or Pod access."
        )
    else:
        incremental = max(0, need - own_used)
        if host_available is not None and host_available < incremental + GIB:
            errors.append(
                "Host memory pressure: estimated available/reclaimable memory cannot cover "
                "incremental application memory plus a 1 GiB margin."
            )
        if (
            docker_total is not None
            and docker_used is not None
            and docker_total - docker_used < incremental + GIB
        ):
            errors.append(
                "Insufficient Docker quota: total quota minus current usage cannot cover "
                "incremental application memory plus 1 GiB."
            )
        if (
            effective_memory is not None
            and node_used is not None
            and effective_memory - node_used < incremental + 512 * 1024**2
        ):
            errors.append(
                "Insufficient target node memory headroom: current usage plus incremental "
                "application memory and 512 MiB exceeds node capacity."
            )
    if disk_free is not None and disk_free < disk_need:
        errors.append(
            f"Insufficient host disk space: incremental budget requires {disk_need / GIB:.1f} GiB; "
            f"{disk_free / GIB:.1f} GiB remains."
        )
    return errors
