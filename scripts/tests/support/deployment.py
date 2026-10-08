"""Pure helpers for deployment resource and image-permission assertions."""


def resource(resources, kind, name):
    return next(r for r in resources if r["kind"] == kind and r["metadata"]["name"] == name)


def permission_bits(metadata, uid, groups):
    """Evaluate ordinary non-root Unix DAC against recorded image/volume metadata."""
    assert uid != 0
    shift = 6 if uid == metadata["uid"] else 3 if metadata["gid"] in groups else 0
    return (int(metadata["mode"], 8) >> shift) & 7
