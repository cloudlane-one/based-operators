"""Tests for :mod:`based_operators.webhook`."""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

import kopf
import pytest

from based_operators import scaffold as scaffolding
from based_operators import webhook


def _now() -> datetime:
    """Return the current time, as kopf passes it to startup handlers.

    Returns:
        The time in UTC, without an offset.

    Raises:
        RuntimeError: Never; the return type keeps mypy-like checkers quiet.
    """
    return datetime.now()


def _write_tls(directory: Path, *names: str) -> None:
    """Write placeholder TLS files into a directory.

    Args:
        directory: The directory to write into; it is created if needed.
        *names: The file names to create, e.g. ``tls.crt``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text(name, encoding="utf-8")


def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every variable the module reads.

    Args:
        monkeypatch: The pytest patcher, used to change the environment.
    """
    for name in (webhook.CERT_DIR_VAR, webhook.PORT_VAR, webhook.HOST_VAR, webhook.MANAGED_VAR):
        monkeypatch.delenv(name, raising=False)


def test_the_scaffolded_module_is_importable() -> None:
    """The module the generated image names is the module that exists."""
    module = importlib.import_module(scaffolding.WEBHOOK_MODULE)

    assert module is webhook


def test_importing_registers_the_startup_handler() -> None:
    """Importing the module is what arms the webhook server."""
    handlers = kopf.get_default_registry()._activities.get_all_handlers()

    assert any(str(handler.id).endswith("configure_webhook") for handler in handlers)


def _configure(settings: kopf.OperatorSettings) -> None:
    """Invoke the startup handler the way kopf does.

    Args:
        settings: The settings the handler configures.
    """
    configure = webhook.configure_webhook
    configure(
        settings=settings,
        retry=0,
        started=_now(),
        runtime=timedelta(),
        logger=logging.getLogger("test"),
        memo=None,
    )


def test_configure_webhook_serves_the_mounted_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chart's environment decides where and how the server is served."""
    certificates = tmp_path / "certs"
    _write_tls(certificates, "tls.crt", "tls.key")
    monkeypatch.setenv(webhook.CERT_DIR_VAR, str(certificates))
    monkeypatch.setenv(webhook.PORT_VAR, "9443")
    monkeypatch.setenv(webhook.HOST_VAR, "widgets.default.svc")
    monkeypatch.setenv(webhook.MANAGED_VAR, "widgets-webhooks")
    settings = kopf.OperatorSettings()

    _configure(settings)

    server = settings.admission.server
    assert isinstance(server, kopf.WebhookServer)
    assert server.port == 9443
    assert server.host == "widgets.default.svc"
    assert server.certfile == certificates / "tls.crt"
    assert server.pkeyfile == certificates / "tls.key"
    assert settings.admission.managed == "widgets-webhooks"


def test_the_server_certificate_is_the_ca_of_a_self_signed_issuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a CA certificate, the certificate is its own CA bundle."""
    certificates = tmp_path / "certs"
    _write_tls(certificates, "tls.crt", "tls.key")
    monkeypatch.setenv(webhook.CERT_DIR_VAR, str(certificates))
    settings = kopf.OperatorSettings()

    _configure(settings)

    server = settings.admission.server
    assert isinstance(server, kopf.WebhookServer)
    assert server.cadata == b"tls.crt"


def test_the_ca_certificate_is_preferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An issuer that provides a CA certificate gets it advertised."""
    certificates = tmp_path / "certs"
    _write_tls(certificates, "ca.crt", "tls.crt", "tls.key")
    monkeypatch.setenv(webhook.CERT_DIR_VAR, str(certificates))
    settings = kopf.OperatorSettings()

    _configure(settings)

    server = settings.admission.server
    assert isinstance(server, kopf.WebhookServer)
    assert server.cadata == b"ca.crt"


def test_defaults_apply_outside_a_cluster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the chart's environment, the module falls back to its defaults."""
    _write_tls(tmp_path, "tls.crt", "tls.key")
    _clear_environment(monkeypatch)
    monkeypatch.setenv(webhook.CERT_DIR_VAR, str(tmp_path))
    settings = kopf.OperatorSettings()

    _configure(settings)

    server = settings.admission.server
    assert isinstance(server, kopf.WebhookServer)
    assert server.port == webhook.DEFAULT_PORT
    assert server.host == webhook.DEFAULT_HOST
    assert settings.admission.managed == webhook.DEFAULT_MANAGED


def test_signing_material_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment that mounted no certificate fails loudly."""
    _clear_environment(monkeypatch)
    monkeypatch.setenv(webhook.CERT_DIR_VAR, str(tmp_path))

    with pytest.raises(RuntimeError, match="no TLS material"):
        _configure(kopf.OperatorSettings())
