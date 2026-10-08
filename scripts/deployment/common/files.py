"""Private atomic records and file-lock mechanics; callers choose identity and policy."""

import contextlib
import fcntl
import json
import os


def atomic_json(path, value, *, create_parent=False):
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w") as stream:
            os.chmod(temp, 0o600)
            json.dump(value, stream, indent=2)
            stream.write("\n")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


@contextlib.contextmanager
def exclusive_lock(stream):
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield stream
    finally:
        fcntl.flock(stream, fcntl.LOCK_UN)
