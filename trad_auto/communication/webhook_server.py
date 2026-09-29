"""Flask webhook server for receiving inbound Twilio WhatsApp messages.

Exposes a single ``/webhook/whatsapp`` POST endpoint that:
1. Extracts Twilio POST parameters and X-Twilio-Signature header.
2. Delegates to ``WhatsAppAdapter.handle_webhook()``.
3. Returns an empty TwiML ``<Response/>`` (reply sent via REST API).

The server runs in a daemon thread so it doesn't block the main engine loop.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from flask import Flask, Response, jsonify, request

from trad_auto.communication.dashboard_html import DASHBOARD_HTML
from trad_auto.communication.whatsapp_adapter import WhatsAppAdapter

logger = logging.getLogger(__name__)

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


class WebhookServer:
    """Lightweight Flask server for Web Dashboard and Twilio WhatsApp webhook ingestion.

    The server is designed to run as a background daemon thread inside
    the ``TradingEngine`` lifecycle. It provides:
    1. Interactive Institutional Web Dashboard on ``/`` and ``/dashboard``.
    2. Telemetry polling endpoint on ``/api/status``.
    3. Terminal command execution endpoint on ``/api/command``.
    4. Inbound Twilio WhatsApp webhook on ``/webhook/whatsapp``.
    5. Health check on ``/health``.
    """

    def __init__(
        self,
        adapter: WhatsAppAdapter | None = None,
        host: str = "0.0.0.0",
        port: int = 5000,
        status_provider: Callable[[], dict[str, Any]] | None = None,
        command_handler: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self._adapter = adapter
        self._host = host
        self._port = port
        self._status_provider = status_provider
        self._command_handler = command_handler
        self._thread: threading.Thread | None = None

        # Build Flask app
        self._app = Flask("trad_auto_webhook")
        self._app.logger.setLevel(logging.WARNING)  # Suppress Flask noise

        # Suppress Werkzeug request logs
        werkzeug_logger = logging.getLogger("werkzeug")
        werkzeug_logger.setLevel(logging.WARNING)

        self._register_routes()

    def _register_routes(self) -> None:
        """Registers dashboard, API, and webhook endpoints on the Flask app."""

        @self._app.route("/", methods=["GET"])
        @self._app.route("/dashboard", methods=["GET"])
        def dashboard_view() -> Response:
            """Renders the live institutional Trad-Auto Web Dashboard."""
            return Response(DASHBOARD_HTML, status=200, mimetype="text/html")

        @self._app.route("/api/status", methods=["GET"])
        def api_status() -> Response:
            """Returns JSON telemetry snapshot for dashboard auto-polling."""
            if self._status_provider is not None:
                try:
                    data = self._status_provider()
                except Exception as err:
                    logger.exception("Failed to fetch dashboard telemetry: %s", err)
                    data = {"error": str(err), "session_state": "ERROR"}
            else:
                data = {
                    "session_state": "DISCONNECTED",
                    "message": "No status provider registered",
                }
            return jsonify(data)

        @self._app.route("/api/command", methods=["POST"])
        def api_command() -> tuple[Response, int] | Response:
            """Executes commands dispatched from dashboard command terminal."""
            req_data = request.get_json(silent=True) or {}
            cmd_text = req_data.get("command", "")
            if not cmd_text:
                return jsonify({"success": False, "error": "No command provided"}), 400

            if self._command_handler is not None:
                try:
                    result = self._command_handler(cmd_text)
                except Exception as err:
                    logger.exception("Dashboard command execution error: %s", err)
                    result = {"success": False, "error": str(err)}
            else:
                result = {"success": False, "error": "No command handler registered"}
            return jsonify(result)

        @self._app.route("/webhook/whatsapp", methods=["POST"])
        def whatsapp_webhook() -> Response:
            """Handles inbound Twilio WhatsApp webhook POST."""
            if self._adapter is None:
                logger.warning("WhatsApp webhook hit but WhatsAppAdapter is not configured")
                return Response(_EMPTY_TWIML, status=200, mimetype="text/xml")

            reply_text = ""
            try:
                # Extract Twilio POST parameters
                params: dict[str, str] = {}
                for key in request.form:
                    val = request.form.get(key)
                    if val is not None:
                        params[key] = val

                # Extract signature header
                signature = request.headers.get("X-Twilio-Signature", "")

                # Build candidate URLs that Twilio may have signed (TLS-terminating tunnels)
                forwarded_proto = request.headers.get("X-Forwarded-Proto")
                forwarded_host = request.headers.get("X-Forwarded-Host")
                if forwarded_proto and forwarded_host:
                    base_url = f"{forwarded_proto}://{forwarded_host}{request.path}"
                else:
                    base_url = request.url

                candidate_urls = [base_url]
                if base_url.startswith("http://"):
                    candidate_urls.append(base_url.replace("http://", "https://", 1))
                elif base_url.startswith("https://"):
                    candidate_urls.append(base_url.replace("https://", "http://", 1))
                if request.url not in candidate_urls:
                    candidate_urls.append(request.url)

                # Try candidate URLs until signature matches
                executed = False
                for candidate in candidate_urls:
                    try:
                        reply_text = self._adapter.handle_webhook(
                            url=candidate,
                            params=params,
                            signature=signature,
                        )
                        executed = True
                        break
                    except Exception as err:
                        if "Signature" in type(err).__name__:
                            continue
                        raise

                if not executed and signature:
                    logger.warning(
                        "Twilio signature verification failed across candidate URLs: %s",
                        candidate_urls,
                    )

            except Exception:
                logger.exception("Error processing WhatsApp webhook")

            if reply_text:
                from xml.sax.saxutils import escape

                xml_body = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    f"<Response><Message>{escape(reply_text)}</Message></Response>"
                )
            else:
                xml_body = _EMPTY_TWIML

            return Response(xml_body, status=200, mimetype="text/xml")

        @self._app.route("/health", methods=["GET"])
        def health_check() -> Response:
            """Simple health check endpoint."""
            return Response('{"status": "ok"}', status=200, mimetype="application/json")

    @property
    def app(self) -> Flask:
        """Returns the underlying Flask app (for testing)."""
        return self._app

    def start(self) -> None:
        """Starts the webhook server in a background daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Webhook server is already running")
            return

        self._thread = threading.Thread(
            target=self._run_server,
            name="webhook-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Trad-Auto Webhook & Dashboard server started on http://%s:%d (Dashboard: http://%s:%d/dashboard)",
            self._host,
            self._port,
            self._host,
            self._port,
        )

    def _run_server(self) -> None:
        """Runs Flask dev server (called in background thread)."""
        # use_reloader=False is critical for threaded usage
        self._app.run(
            host=self._host,
            port=self._port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

    def stop(self) -> None:
        """Signals the webhook server to stop.

        Since Flask's built-in dev server doesn't have a clean shutdown
        mechanism, and we run it as a daemon thread, it will be killed
        when the main process exits. This method is provided for API
        symmetry and future upgrade to a proper WSGI server.
        """
        logger.info("Webhook server stop requested (daemon thread will exit with main process)")
        self._thread = None

    def get_info(self) -> dict[str, Any]:
        """Returns server info for diagnostics."""
        return {
            "host": self._host,
            "port": self._port,
            "running": self._thread is not None and self._thread.is_alive(),
            "endpoint": f"http://{self._host}:{self._port}/webhook/whatsapp",
            "dashboard_url": f"http://{self._host}:{self._port}/dashboard",
        }
