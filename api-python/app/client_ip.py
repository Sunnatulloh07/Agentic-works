"""The client address a per-IP throttle may key on: proxy-aware, spoof-resistant.

Two opposite failures, both closed here:

* **Behind a reverse proxy** every request arrives from the proxy's address, so a
  throttle keyed on the raw peer puts every client in one bucket and a single
  attacker locks everybody out of login.
* **Trusting X-Forwarded-For blindly** lets the client choose its own bucket per
  request, because the client writes that header.

The rule: the header is read ONLY when the direct peer is a declared trusted proxy
(``TRUSTED_PROXIES``: comma-separated IPs/CIDRs, empty by default), and then the
key is the **right-most** hop that is not itself a trusted proxy -- the one our own
proxy appended, which the client cannot forge. With the default empty list the
header is ignored and the peer is the key, exactly as before.

uvicorn (>= 0.30) applies the same right-most rule for peers in its own
``FORWARDED_ALLOW_IPS`` (default ``127.0.0.1``) before this code runs; the two
compose, because the header is unchanged and this walk starts again from it.

Standard library only, so it is tested without FastAPI.
"""
from __future__ import annotations

import ipaddress
import os
from functools import lru_cache

MAX_KEY_CHARS = 64
# A header longer than this is not a real proxy chain; only its right end matters.
MAX_FORWARDED_HOPS = 32
MAX_FORWARDED_CHARS = 4096


class ProxyConfigError(ValueError):
    """TRUSTED_PROXIES names something that is not an IP address or network."""


@lru_cache(maxsize=16)
def parse_trusted_proxies(raw: str) -> tuple:
    """Parse the declared proxy list. A typo raises instead of being skipped:
    a silently dropped proxy is a proxy whose clients all share one bucket."""
    networks = []
    for part in (raw or '').split(','):
        entry = part.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            raise ProxyConfigError(
                f'TRUSTED_PROXIES has an invalid entry: {entry[:MAX_KEY_CHARS]!r}') from None
    return tuple(networks)


def _address(text):
    """One hop as an IP address, or ``None``. Accepts ``[v6]:port`` and ``v4:port``."""
    value = text.strip()
    if value.startswith('['):
        end = value.find(']')
        value = value[1:end] if end > 0 else value
    elif value.count(':') == 1:
        value = value.split(':', 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address


def _key(text, address):
    return str(address) if address is not None else (text.strip()[:MAX_KEY_CHARS] or 'unknown')


def _trusted(address, networks):
    return address is not None and any(address in network for network in networks)


def client_address(peer, forwarded_for=(), networks=None) -> str:
    """The throttle key for one request. See the module docstring for the rule."""
    if networks is None:
        networks = parse_trusted_proxies(os.environ.get('TRUSTED_PROXIES', ''))
    peer_text = str(peer or '')
    peer_address = _address(peer_text)
    if not _trusted(peer_address, networks):
        return _key(peer_text, peer_address)
    joined = ','.join(str(value) for value in forwarded_for or ())[-MAX_FORWARDED_CHARS:]
    hops = [hop.strip() for hop in joined.split(',') if hop.strip()][-MAX_FORWARDED_HOPS:]
    for hop in reversed(hops):
        address = _address(hop)
        if not _trusted(address, networks):
            return _key(hop, address)
    if hops:
        # Every hop is one of our proxies: the left-most is the original client.
        return _key(hops[0], _address(hops[0]))
    return _key(peer_text, peer_address)


def request_client(request) -> str:
    """``client_address`` for a Starlette/FastAPI request."""
    peer = request.client.host if request.client else ''
    return client_address(peer, request.headers.getlist('x-forwarded-for'))
