from app.config import get_settings
from app.events import bus


def test_audio_ws_without_api_key_reports_offline(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "model_api_key", "")
    with client.websocket_connect("/ws/audio") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "status" and msg["state"] == "offline"
    assert "MODEL_API_KEY" in msg["reason"]


def test_events_ws_streams_published_events(client):
    with client.websocket_connect("/ws/events") as ws:
        # The subscription is registered once the handler starts awaiting; publish after a
        # round trip so the queue exists.
        client.get("/healthz")
        bus.publish("basket_updated", {"id": "x", "total_paise": 100})
        event = ws.receive_json()
    assert event["type"] == "basket_updated"
    assert event["data"] == {"id": "x", "total_paise": 100}
