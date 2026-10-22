"""Release identities shared by generation, admission and post-merge tagging."""

import re

VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.[1-9][0-9]*"
SHA = r"[a-f0-9]{40}"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_candidate(manifest):
    candidate = manifest.get("candidate", {})
    number = candidate.get("runNumber")
    version = candidate.get("version", "")
    require(type(number) is int and number > 0, "Invalid candidate run number")
    require(re.fullmatch(r"[1-9][0-9]*", candidate.get("runId", "")), "Invalid candidate run ID")
    require(
        re.fullmatch(VERSION, version) and version.endswith("-rc." + str(number)),
        "Invalid candidate version",
    )
    require(
        candidate.get("branch") == "release-candidate/" + version,
        "Candidate branch does not match version",
    )
    require(re.fullmatch(SHA, candidate.get("baseSha", "")), "Invalid candidate base")
    require(manifest.get("chartVersion") == version, "Candidate version does not match Chart")
    require(re.fullmatch(SHA, manifest.get("sourceSha", "")), "Invalid source commit")
    operation = manifest.get("operation", "release")
    require(operation in {"release", "rollback"}, "Invalid release operation")
    if operation == "release":
        require("rollback" not in manifest, "Rollback metadata requires rollback operation")
        return
    rollback = manifest.get("rollback", {})
    require(manifest.get("schemaVersion") == 3, "Rollback requires format 3")
    require(rollback.get("of") == candidate["baseSha"], "Rollback must identify current base")
    require(re.fullmatch(SHA, rollback.get("targetSha", "")), "Invalid rollback target SHA")
    require(
        re.fullmatch("deploy/v" + VERSION, rollback.get("targetTag", "")),
        "Invalid rollback target tag",
    )
    reason = rollback.get("reason")
    require(
        isinstance(reason, str) and 0 < len(reason.strip()) <= 1000,
        "Rollback reason must contain 1 to 1000 characters",
    )
