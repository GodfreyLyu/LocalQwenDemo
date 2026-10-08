"""GitHub JSON request encoding; callers supply transport and authorization context."""

import json


def request(path, payload, *, execute):
    args = ["gh", "api", path]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    return json.loads(execute(args, json.dumps(payload) if payload is not None else None))
