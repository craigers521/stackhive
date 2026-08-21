"""Custom Jinja2 filters for network configuration templating."""


def ip_to_cidr(ip, mask, prefix_length=None):
    """Convert an address/mask pair (or address + prefix length) to CIDR.

    Examples:
        ip_to_cidr('10.0.0.1', '255.255.255.0') -> '10.0.0.1/24'
        ip_to_cidr('10.0.0.1', 24)              -> '10.0.0.1/24'
    """
    if prefix_length is not None:
        return f"{ip}/{prefix_length}"
    if isinstance(mask, int):
        return f"{ip}/{mask}"
    if isinstance(mask, str) and mask.isdigit():
        return f"{ip}/{int(mask)}"
    octets = str(mask).split(".")
    if len(octets) != 4:
        raise ValueError(f"invalid netmask: {mask}")
    bits = 0
    for octet in octets:
        value = int(octet)
        if value < 0 or value > 255:
            raise ValueError(f"invalid netmask octet: {octet}")
        bits = (bits << 8) | value
    prefix = 0
    while bits & 0x80000000:
        prefix += 1
        bits <<= 1
    if bits != 0:
        raise ValueError(f"non-contiguous netmask: {mask}")
    return f"{ip}/{prefix}"


def generate_range(prefix, start, end):
    """Generate a list of numbered names, e.g. generate_range('Gi1/0/', 1, 4)."""
    start = int(start)
    end = int(end)
    if start > end:
        raise ValueError(f"range start {start} > end {end}")
    return [f"{prefix}{n}" for n in range(start, end + 1)]


def generate_vlan_range(start, end):
    """Generate a VLAN list string, e.g. '10,20,30' or '10-20' style list."""
    start = int(start)
    end = int(end)
    if start > end:
        raise ValueError(f"range start {start} > end {end}")
    return ",".join(str(n) for n in range(start, end + 1))


def first_present(*values):
    """Return the first non-empty value from the candidates."""
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


class FilterModule(object):
    """Filter registration."""

    filters = {
        "ip_to_cidr": ip_to_cidr,
        "generate_range": generate_range,
        "generate_vlan_range": generate_vlan_range,
        "first_present": first_present,
    }
