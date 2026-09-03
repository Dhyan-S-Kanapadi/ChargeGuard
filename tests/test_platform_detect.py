import httpx

from integrations.platform_detect import suggest_storefront_platform


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_platform_detect_suggests_shopify_from_html_and_header() -> None:
    html_client = _client(
        lambda request: httpx.Response(
            200,
            text='<meta name="generator" content="Shopify">',
            request=request,
        )
    )
    header_client = _client(
        lambda request: httpx.Response(200, headers={"X-ShopId": "42"}, request=request)
    )

    assert suggest_storefront_platform("https://shop.example", client=html_client) == "shopify"
    assert suggest_storefront_platform("https://shop.example", client=header_client) == "shopify"

    domain_client = _client(
        lambda request: httpx.Response(200, request=request)
    )
    assert suggest_storefront_platform("https://demo.myshopify.com", client=domain_client) == "shopify"


def test_platform_detect_suggests_woocommerce_from_html_and_endpoint() -> None:
    html_client = _client(
        lambda request: httpx.Response(
            200,
            text="/wp-content/plugins/woocommerce/assets/app.js",
            request=request,
        )
    )

    def endpoint_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401 if request.url.path.endswith("/wp-json/wc/v3/") else 200,
            request=request,
        )

    assert suggest_storefront_platform("https://shop.example", client=html_client) == "woocommerce"
    assert suggest_storefront_platform("https://shop.example", client=_client(endpoint_handler)) == "woocommerce"


def test_platform_detect_suggests_custom_when_no_signal_matches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        status = 404 if request.url.path.endswith("/wp-json/wc/v3/") else 200
        return httpx.Response(status, text="custom storefront", request=request)

    assert suggest_storefront_platform("https://shop.example", client=_client(handler)) == "custom"


def test_platform_detect_returns_unknown_when_store_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    assert suggest_storefront_platform("https://shop.example", client=_client(handler)) == "unknown"


def test_platform_detect_rejects_private_network_targets() -> None:
    assert suggest_storefront_platform("http://127.0.0.1") == "unknown"
