import httpx

from integrations.food_platform import FoodPlatformClient


def test_food_platform_client_fetches_order_timeline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/orders/order_123/timeline"
        assert request.headers["authorization"] == "Bearer test_key"
        return httpx.Response(
            200,
            json={
                "placed_at": "2026-05-14T08:00:00Z",
                "delivered_at": "2026-05-14T09:10:00Z",
                "post_delivery_rating": 4.7,
            },
        )

    client = FoodPlatformClient(
        api_key="test_key",
        base_url="https://food.example.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    timeline = client.get_order_timeline("order_123")

    assert timeline["post_delivery_rating"] == 4.7


def test_food_platform_client_fetches_delivery_photo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/orders/order_123/delivery-photo"
        return httpx.Response(
            200,
            json={
                "photo_url": "https://cdn.example.test/pod.jpg",
                "captured_at": "2026-05-14T09:10:00Z",
            },
        )

    client = FoodPlatformClient(
        api_key="test_key",
        base_url="https://food.example.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    photo = client.get_delivery_photo("order_123")

    assert photo["photo_url"] == "https://cdn.example.test/pod.jpg"
