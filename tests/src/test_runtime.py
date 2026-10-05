"""Verify the generated image module only configures admission when opted in."""

import importlib
import sys

import kopf
import pytest


def test_runtime_admission_config(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime loads handlers and configures the pinned Kopf HTTPS server."""
    handler = tmp_path / "operator.py"
    handler.write_text("loaded = True\n")
    monkeypatch.setenv("BASED_OPERATORS_HANDLER", str(handler))
    sys.modules.pop("based_operators.runtime", None)
    try:
        runtime = importlib.import_module("based_operators.runtime")
        settings = kopf.OperatorSettings()
        runtime.configure_webhooks(settings)
        assert settings.admission.server is None
        monkeypatch.setenv("BASED_OPERATORS_WEBHOOKS", "true")
        runtime.configure_webhooks(settings)
        assert isinstance(settings.admission.server, kopf.WebhookServer)
        assert settings.admission.server.port == 9443
        assert settings.admission.server.certfile == "/etc/operator-webhook/tls.crt"
        with pytest.raises(RuntimeError, match="Conflicting"):
            runtime.configure_webhooks(settings)
    finally:
        sys.modules.pop("based_operators.runtime", None)
