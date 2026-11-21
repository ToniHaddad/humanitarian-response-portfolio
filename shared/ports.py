"""The one place ports are defined. Project NN runs on 8500 + NN."""

from __future__ import annotations

HOMEPAGE_PORT: int = 8500
API_PORT: int = 8600


def project_port(number: int | str) -> int:
    """Port for project NN: 01 -> 8501 ... 20 -> 8520."""
    n = int(number)
    if not 1 <= n <= 20:
        raise ValueError(f"Project number must be 1-20, got {number}")
    return HOMEPAGE_PORT + n


def project_url(number: int | str, host: str = "localhost") -> str:
    return f"http://{host}:{project_port(number)}"


def homepage_url(host: str = "localhost") -> str:
    return f"http://{host}:{HOMEPAGE_PORT}"


def api_url(host: str = "localhost") -> str:
    return f"http://{host}:{API_PORT}"
