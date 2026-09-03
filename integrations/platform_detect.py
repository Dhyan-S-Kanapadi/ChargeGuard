import ipaddress
import socket
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


StorefrontPlatform = Literal["shopify", "woocommerce", "custom", "unknown"]
_MAX_REDIRECTS = 5


class StoreURLValidationError(ValueError):
    pass


class _GeneratorParser(HTMLParser):
    shopify = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        values = {key.casefold(): (value or "").casefold() for key, value in attrs}
        if values.get("name") == "generator" and values.get("content", "").startswith("shopify"):
            self.shopify = True


def normalize_store_url(url: str, *, resolve_host: bool = True) -> str:
    candidate = url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise StoreURLValidationError("Store URL must use HTTP or HTTPS and include a host.")
    if parsed.username or parsed.password:
        raise StoreURLValidationError("Store URL must not contain credentials.")
    if parsed.hostname.casefold() == "localhost":
        raise StoreURLValidationError("Store URL must resolve to a public host.")
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise StoreURLValidationError("Store URL must resolve to a public host.")
    if resolve_host and not _host_is_public(parsed.hostname):
        raise StoreURLValidationError("Store URL must resolve to a public host.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _host_is_public(host: str) -> bool:
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }
    except OSError:
        return False
    if not addresses:
        return False
    return all(ipaddress.ip_address(address).is_global for address in addresses)


def _get(
    client: httpx.Client,
    url: str,
    *,
    resolve_host: bool,
) -> httpx.Response:
    current = normalize_store_url(url, resolve_host=resolve_host)
    for _ in range(_MAX_REDIRECTS + 1):
        response = client.get(current, follow_redirects=False)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                return response
            current = normalize_store_url(urljoin(current, location), resolve_host=resolve_host)
            continue
        return response
    raise httpx.TooManyRedirects("Store URL exceeded the redirect limit.")


def _resolves_through_shopify(host: str) -> bool:
    try:
        canonical, aliases, _ = socket.gethostbyname_ex(host)
    except OSError:
        return False
    return any(
        name.casefold() == "myshopify.com" or name.casefold().endswith(".myshopify.com")
        for name in [canonical, *aliases]
    )


def _is_shopify(response: httpx.Response, *, check_dns_alias: bool) -> bool:
    hostname = (response.url.host or "").casefold()
    parser = _GeneratorParser()
    parser.feed(response.text)
    return (
        hostname == "myshopify.com"
        or hostname.endswith(".myshopify.com")
        or bool(response.headers.get("X-ShopId"))
        or parser.shopify
        or (check_dns_alias and _resolves_through_shopify(hostname))
    )


def suggest_storefront_platform(
    store_url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> StorefrontPlatform:
    """Return a non-authoritative storefront suggestion without blocking onboarding."""
    resolve_host = client is None
    try:
        if client is None:
            with httpx.Client(timeout=timeout) as owned_client:
                return _suggest(store_url, owned_client, resolve_host=resolve_host)
        return _suggest(store_url, client, resolve_host=resolve_host)
    except (httpx.HTTPError, OSError, StoreURLValidationError):
        return "unknown"


def _suggest(
    store_url: str,
    client: httpx.Client,
    *,
    resolve_host: bool,
) -> StorefrontPlatform:
    response = _get(client, store_url, resolve_host=resolve_host)
    if _is_shopify(response, check_dns_alias=resolve_host):
        return "shopify"
    if "/wp-content/plugins/woocommerce/" in response.text.casefold():
        return "woocommerce"

    base_url = normalize_store_url(str(response.url), resolve_host=resolve_host).rstrip("/")
    woo_response = _get(
        client,
        f"{base_url}/wp-json/wc/v3/",
        resolve_host=resolve_host,
    )
    return "woocommerce" if woo_response.status_code != 404 else "custom"
