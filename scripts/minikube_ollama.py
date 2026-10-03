"""Discover the selected node's host endpoint and build narrowly scoped egress.

Only deployment calls discovery. Offline rendering and ownership enumeration do
not require Docker, DNS, or a running Ollama server. No host settings are changed.
"""

import ipaddress

HOSTNAME = "host.minikube.internal"
PORT = 11434
PRIVATE_NETWORKS = tuple(
    ipaddress.IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def validate_host_ip(value):
    from minikube_demo import DemoError

    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError):
        raise DemoError("Ollama host resolution returned an invalid IPv4 address.") from None
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise DemoError(
            "Ollama host must resolve to an RFC1918 private IPv4 address; "
            "no public, loopback, link-local, or IPv6 fallback is allowed."
        )
    return str(address)


def resolve_host_ip():
    """Resolve inside the verified Docker node, not using the Mac's resolver."""
    import minikube_demo as d

    node = d.TARGET.get("node_name")
    d.require(node and d.PROFILE, "No verified minikube node selected for Ollama resolution.")
    result = d.run(
        ["docker", "exec", node, "getent", "ahostsv4", HOSTNAME],
        check=False,
        timeout=15,
    )
    d.require(
        result.returncode == 0 and result.stdout.strip(),
        "Cannot resolve host.minikube.internal in the selected node; "
        "check the minikube host mapping. No cached IP or broad egress fallback is used.",
    )
    addresses = {
        validate_host_ip(line.split()[0]) for line in result.stdout.splitlines() if line.strip()
    }
    d.require(
        len(addresses) == 1,
        "Ollama host resolution is ambiguous; expected one private IPv4 address.",
    )
    return addresses.pop()


def egress_rule(host_ip):
    return {
        "to": [{"ipBlock": {"cidr": validate_host_ip(host_ip) + "/32"}}],
        "ports": [{"protocol": "TCP", "port": PORT}],
    }
