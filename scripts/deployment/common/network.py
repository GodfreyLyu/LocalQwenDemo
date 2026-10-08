"""Explicit RFC1918 host addresses for node-to-host Ollama traffic."""

import ipaddress

PRIVATE_NETWORKS = tuple(
    ipaddress.IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def private_ipv4(value):
    address = ipaddress.IPv4Address(value)
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise ValueError("not_private")
    return str(address)
