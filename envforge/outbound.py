"""Pin SaaS agent connections to validated public addresses, preserving TLS identity."""

import asyncio
import ipaddress
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import httpx

_RESOLVERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-dns")
_RESOLVER_SLOTS = threading.BoundedSemaphore(4)


class InvalidAgentResponse(ValueError):
    """The endpoint failed the bounded JSON response contract."""


def _resolve(host: str, timeout: float) -> set[str]:
    if not _RESOLVER_SLOTS.acquire(blocking=False):
        raise httpx.ConnectError("Agent DNS resolver is busy; retry later")
    try:
        future = _RESOLVERS.submit(
            socket.getaddrinfo,
            host,
            443,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except RuntimeError:
        _RESOLVER_SLOTS.release()
        raise httpx.ConnectError("Agent DNS resolver is unavailable") from None
    future.add_done_callback(lambda _: _RESOLVER_SLOTS.release())
    try:
        answers = future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        raise httpx.ConnectTimeout("Agent DNS resolution timed out") from None
    except socket.gaierror:
        raise httpx.ConnectError("Agent hostname could not be resolved") from None
    return {str(answer[4][0]) for answer in answers}


_TRANSLATION_RANGES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("2001::/32"),
)


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return public_address(str(address.ipv4_mapped))
        if any(address in network for network in _TRANSLATION_RANGES):
            return False
    return address.is_global and not address.is_multicast and not address.is_reserved


def agent_target(
    url: str, timeout: float = 5
) -> tuple[httpx.URL, dict[str, str], dict]:
    """Resolve once and connect to the literal IP, avoiding a second DNS lookup.

    Host and the documented HTTPCore SNI extension preserve virtual hosting and
    certificate hostname checks: https://www.encode.io/httpcore/extensions/#sni_hostname
    Redirects and environment proxies remain disabled by the caller.
    """
    original = httpx.URL(url)
    if original.scheme != "https" or original.port not in {None, 443}:
        raise ValueError("SaaS agents require HTTPS on port 443")
    try:
        literal = ipaddress.ip_address(original.host)
    except ValueError:
        addresses = _resolve(original.host, timeout)
    else:
        addresses = {str(literal)}
    if not addresses or not all(public_address(address) for address in addresses):
        raise ValueError("Agent network destination is not a public address")
    # Prefer IPv4 where both families exist; Docker hosts may lack IPv6 routing.
    target = min(addresses, key=lambda address: (":" in address, address))
    return (
        original.copy_with(host=target),
        {"Host": original.netloc.decode("ascii")},
        {"sni_hostname": original.host},
    )


def post_agent_json(
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: float,
    *,
    public_only: bool,
) -> dict:
    deadline = time.monotonic() + timeout
    target: str | httpx.URL = url
    extensions = {}
    headers = {**headers, "Accept-Encoding": "identity"}
    if public_only:
        target, network_headers, extensions = agent_target(url, min(timeout, 5))
        headers.update(network_headers)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Agent request exceeded its time budget")

    async def request() -> dict:
        # Cancellation covers connect, TLS, slow headers and body reads, rather
        # than relying only on an inactivity timeout between received bytes.
        async with (
            asyncio.timeout(remaining),
            httpx.AsyncClient(
                timeout=remaining, follow_redirects=False, trust_env=False
            ) as client,
            client.stream(
                "POST", target, json=payload, headers=headers, extensions=extensions
            ) as response,
        ):
            response.raise_for_status()
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise InvalidAgentResponse("Agent responses must use identity encoding")
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > 65536:
                    raise InvalidAgentResponse("Agent response exceeds 64 KiB")
            try:
                result = json.loads(body)
            except ValueError:
                raise InvalidAgentResponse("Agent response is not valid JSON") from None
            if not isinstance(result, dict):
                raise InvalidAgentResponse("Agent response must be a JSON object")
            return result

    return asyncio.run(request())
