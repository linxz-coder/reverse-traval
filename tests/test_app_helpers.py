from app import app as flask_app
from app import nearest_supported_city, nearby_cities_for, normalize_city, parse_bool


def test_nearby_city_helpers_resolve_manual_and_location():
    assert normalize_city("廣州") == "广州"
    assert nearest_supported_city(22.54, 114.05) == "深圳"
    assert nearby_cities_for("深圳", limit=4) == ["汕尾", "惠州", "广州", "东莞"]
    assert nearby_cities_for("珠海", limit=2) == ["中山", "江门"]


def test_parse_bool_accepts_form_values():
    assert parse_bool("true") is True
    assert parse_bool("on") is True
    assert parse_bool("false") is False
    assert parse_bool(None, default=False) is False


def test_api_errors_return_json():
    client = flask_app.test_client()

    response = client.get("/api/not-found")

    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["error"]
