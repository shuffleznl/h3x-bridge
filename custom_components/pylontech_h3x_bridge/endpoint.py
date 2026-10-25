"""Normalize Modbus endpoints without doing DNS on the event loop."""

from ipaddress import ip_address


def normalize_host(value: str) -> str:
    """Accept bare IP addresses and DNS names, rejecting URLs and host:port."""
    host = value.strip().strip("[]")
    if (
        not host
        or any(char.isspace() for char in host)
        or any(char in host for char in "/\\@?#")
    ):
        raise ValueError("Enter a hostname or IP address without a URL or port")
    try:
        return str(ip_address(host))
    except ValueError:
        if ":" in host:
            raise ValueError("Enter the port in the separate port field") from None
        name = host.rstrip(".").encode("idna").decode("ascii").lower()
        labels = name.split(".")
        if len(name) > 253 or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not label.replace("-", "").isalnum()
            for label in labels
        ):
            raise ValueError("Invalid DNS hostname")
        return name
