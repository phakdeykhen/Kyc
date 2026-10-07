"""API key network allow-lists (Phase 17).

A key with an allow-list works only from those networks. The client address is the
socket peer, or the X-Forwarded-For hop that a trusted proxy reports when uvicorn runs
with --proxy-headers (see docs/architecture-phase17.md).
"""

import ipaddress

MAX_NETWORKS = 20


def _network(value: str):
    network = ipaddress.ip_network(str(value).strip(), strict=False)
    if network.version == 6 and network.prefixlen >= 96 and network.network_address.ipv4_mapped is not None:
        return ipaddress.ip_network((network.network_address.ipv4_mapped, network.prefixlen - 96))
    return network


def parse_networks(values: list[str] | None) -> list[str]:
    """Canonical CIDR strings; a bare address becomes a /32 or /128."""
    networks = []
    for value in values or []:
        try:
            network = _network(value)
        except ValueError:
            raise ValueError("Every allow-list entry must be an IP address or CIDR network.") from None
        if network.prefixlen == 0:
            raise ValueError("An allow-list cannot contain 0.0.0.0/0 or ::/0; leave it empty instead.")
        networks.append(str(network))
    if len(networks) > MAX_NETWORKS:
        raise ValueError(f"At most {MAX_NETWORKS} networks are allowed.")
    return sorted(set(networks))


def address_allowed(address: str | None, networks: list[str] | tuple[str, ...]) -> bool:
    if not networks:
        return True
    try:
        client = ipaddress.ip_address(address or "")
    except ValueError:
        return False
    if client.version == 6 and client.ipv4_mapped is not None:
        client = client.ipv4_mapped
    try:
        allowed = [_network(item) for item in networks]
    except ValueError:
        return False  # Corrupt stored policy never opens access.
    return any(client in network for network in allowed)


def networks_within(child: list[str], parent: list[str] | tuple[str, ...]) -> bool:
    """True when every child network lies inside one of the parent's (no parent list: anything)."""
    if not parent:
        return True
    if not child:
        return False
    parents = [_network(item) for item in parent]
    for item in child:
        network = _network(item)
        if not any(network.version == other.version and network.subnet_of(other) for other in parents):
            return False
    return True
