"""Unit tests for WebhookServer Flask routes."""

from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from trad_auto.communication.webhook_server import WebhookServer
from trad_auto.communication.whatsapp_adapter import (
    SignatureVerificationError,
    UnauthorizedSenderError,
    WhatsAppAdapter,
)


@pytest.fixture()
def mock_adapter() -> MagicMock:
    """Creates a mock WhatsAppAdapter."""
    return MagicMock(spec=WhatsAppAdapter)


@pytest.fixture()
def server(mock_adapter: MagicMock) -> WebhookServer:
    """Creates a WebhookServer with mocked adapter."""
    return WebhookServer(adapter=mock_adapter, host="127.0.0.1", port=5099)


@pytest.fixture()
def client(server: WebhookServer) -> FlaskClient:
    """Creates a Flask test client."""
    server.app.config["TESTING"] = True
    return server.app.test_client()


class TestWebhookRoute:
    """Tests for the /webhook/whatsapp POST endpoint."""

    def test_webhook_calls_adapter(self, client: FlaskClient, mock_adapter: MagicMock) -> None:
        """Verifies that the webhook forwards POST params to the adapter."""
        mock_adapter.handle_webhook.return_value = "✅ OK"

        response = client.post(
            "/webhook/whatsapp",
            data={
                "From": "whatsapp:+917856917258",
                "Body": "status",
                "MessageSid": "SM123",
            },
        )

        assert response.status_code == 200
        assert b"<Response>" in response.data
        mock_adapter.handle_webhook.assert_called_once()

        call_kwargs = mock_adapter.handle_webhook.call_args
        params = call_kwargs.kwargs["params"]
        assert params["From"] == "whatsapp:+917856917258"
        assert params["Body"] == "status"
        assert params["MessageSid"] == "SM123"

    def test_webhook_returns_twiml_on_adapter_error(
        self, client: FlaskClient, mock_adapter: MagicMock
    ) -> None:
        """Verifies 200 + TwiML even when adapter raises an exception."""
        mock_adapter.handle_webhook.side_effect = UnauthorizedSenderError("bad sender")

        response = client.post(
            "/webhook/whatsapp",
            data={"From": "whatsapp:+19999999999", "Body": "hack"},
        )

        # Must still return 200 to prevent Twilio retries
        assert response.status_code == 200
        assert b"<Response>" in response.data

    def test_webhook_returns_twiml_on_signature_error(
        self, client: FlaskClient, mock_adapter: MagicMock
    ) -> None:
        """Verifies 200 + TwiML even when signature validation fails."""
        mock_adapter.handle_webhook.side_effect = SignatureVerificationError("bad sig")

        response = client.post(
            "/webhook/whatsapp",
            data={"From": "whatsapp:+917856917258", "Body": "status"},
        )

        assert response.status_code == 200
        assert b"<Response>" in response.data

    def test_webhook_passes_signature_header(
        self, client: FlaskClient, mock_adapter: MagicMock
    ) -> None:
        """Verifies the X-Twilio-Signature header is forwarded."""
        mock_adapter.handle_webhook.return_value = "ok"

        client.post(
            "/webhook/whatsapp",
            data={"From": "whatsapp:+917856917258", "Body": "status"},
            headers={"X-Twilio-Signature": "abc123sig"},
        )

        call_kwargs = mock_adapter.handle_webhook.call_args
        assert call_kwargs.kwargs["signature"] == "abc123sig"


class TestHealthEndpoint:
    """Tests for the /health GET endpoint."""

    def test_health_returns_ok(self, client: FlaskClient) -> None:
        """Verifies the health check returns 200 with ok status."""
        response = client.get("/health")
        assert response.status_code == 200
        assert b'"status"' in response.data


class TestServerLifecycle:
    """Tests for WebhookServer start/stop lifecycle."""

    def test_get_info(self, server: WebhookServer) -> None:
        """Verifies get_info returns expected structure."""
        info = server.get_info()
        assert info["host"] == "127.0.0.1"
        assert info["port"] == 5099
        assert info["running"] is False
        assert "/webhook/whatsapp" in info["endpoint"]

    def test_stop_resets_thread(self, server: WebhookServer) -> None:
        """Verifies stop() clears the thread reference."""
        server.stop()
        info = server.get_info()
        assert info["running"] is False


class TestDashboardEndpoints:
    """Tests for dashboard UI and telemetry API endpoints."""

    def test_dashboard_page(self, client: FlaskClient) -> None:
        """Verifies GET / and GET /dashboard render the dashboard template."""
        res_root = client.get("/")
        assert res_root.status_code == 200
        assert "text/html" in res_root.content_type
        assert b"TRAD-AUTO" in res_root.data

        res_dash = client.get("/dashboard")
        assert res_dash.status_code == 200
        assert "text/html" in res_dash.content_type
        assert b"Smart Money Scalper" in res_dash.data

    def test_api_status_with_provider(self) -> None:
        """Verifies GET /api/status invokes status provider."""
        mock_provider = MagicMock(
            return_value={"session_state": "TRADING", "usdt_balance": "5000.00"}
        )
        srv = WebhookServer(status_provider=mock_provider)
        srv.app.config["TESTING"] = True
        c = srv.app.test_client()

        res = c.get("/api/status")
        assert res.status_code == 200
        data = res.get_json()
        assert data["session_state"] == "TRADING"
        assert data["usdt_balance"] == "5000.00"
        mock_provider.assert_called_once()

    def test_api_status_without_provider(self) -> None:
        """Verifies GET /api/status handles missing status provider gracefully."""
        srv = WebhookServer()
        srv.app.config["TESTING"] = True
        c = srv.app.test_client()

        res = c.get("/api/status")
        assert res.status_code == 200
        data = res.get_json()
        assert data["session_state"] == "DISCONNECTED"

    def test_api_command_execution(self) -> None:
        """Verifies POST /api/command forwards command to handler."""
        mock_handler = MagicMock(return_value={"success": True, "message": "PAUSED"})
        srv = WebhookServer(command_handler=mock_handler)
        srv.app.config["TESTING"] = True
        c = srv.app.test_client()

        res = c.post("/api/command", json={"command": "pause"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["message"] == "PAUSED"
        mock_handler.assert_called_once_with("pause")

    def test_api_command_missing_body(self) -> None:
        """Verifies POST /api/command returns 400 when command string is empty."""
        srv = WebhookServer()
        srv.app.config["TESTING"] = True
        c = srv.app.test_client()

        res = c.post("/api/command", json={})
        assert res.status_code == 400
        data = res.get_json()
        assert data["success"] is False

    def test_webhook_without_adapter(self) -> None:
        """Verifies POST /webhook/whatsapp returns empty TwiML if adapter is None."""
        srv = WebhookServer(adapter=None)
        srv.app.config["TESTING"] = True
        c = srv.app.test_client()

        res = c.post("/webhook/whatsapp", data={"Body": "status"})
        assert res.status_code == 200
        assert b"<Response>" in res.data

