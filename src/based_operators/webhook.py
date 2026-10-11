"""The admission webhook server of an operator.

Import this module to have a kopf operator serve admission webhooks::

    kopf run operator.py -m based_operators.webhook

``kopf run -m`` imports the module on startup, and importing it registers the one
operator-level startup handler below. That handler turns the webhook server on, so
an operator whose resources declare ``on.validate`` or ``on.mutate`` handlers has
a server to be served by; without one, kopf refuses to start as soon as admission
handlers are registered.

Everything that depends on *where* the operator runs is read from the
environment, which the Helm chart that :mod:`based_operators.scaffold` writes
sets on the operator container:

| Variable | Meaning | Default |
| --- | --- | --- |
| ``WEBHOOK_CERT_DIR`` | Directory with the TLS key pair and CA. | :data:`DEFAULT_CERT_DIR` |
| ``WEBHOOK_PORT`` | Port to listen on; the chart's Service matches. | :data:`DEFAULT_PORT` |
| ``WEBHOOK_HOST`` | Hostname the API server calls (the Service). | :data:`DEFAULT_HOST` |
| ``WEBHOOK_MANAGED`` | Name of the webhook configurations kopf syncs. | :data:`DEFAULT_MANAGED` |

The defaults only apply outside a cluster, where the chart's environment is
absent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import kopf

__all__ = [
    "DEFAULT_CERT_DIR",
    "DEFAULT_HOST",
    "DEFAULT_MANAGED",
    "DEFAULT_PORT",
    "CERT_DIR_VAR",
    "HOST_VAR",
    "MANAGED_VAR",
    "PORT_VAR",
    "configure_webhook",
]

CERT_DIR_VAR = "WEBHOOK_CERT_DIR"
"""The environment variable naming the directory the TLS material is mounted in."""

PORT_VAR = "WEBHOOK_PORT"
"""The environment variable naming the port the webhook server listens on."""

HOST_VAR = "WEBHOOK_HOST"
"""The environment variable naming the hostname the API server calls."""

MANAGED_VAR = "WEBHOOK_MANAGED"
"""The environment variable naming the webhook configurations kopf manages."""

DEFAULT_CERT_DIR = "/etc/webhook/certs"
"""Where the chart mounts the TLS certificate by default."""

DEFAULT_PORT = 8443
"""The port the webhook server listens on when the environment says nothing."""

DEFAULT_HOST = "localhost"
"""The hostname of the webhook server when the environment says nothing."""

DEFAULT_MANAGED = "based-operators-webhooks"
"""The name of the managed webhook configurations when the environment says nothing."""

_CERT_FILENAME = "tls.crt"
"""The server certificate, which is its own CA when the issuer is self-signed."""

_KEY_FILENAME = "tls.key"
"""The private key of the server certificate."""

_CA_FILENAME = "ca.crt"
"""The CA certificate, when the chart's issuer provides one."""


@kopf.on.startup()
def configure_webhook(settings: kopf.OperatorSettings, **_: Any) -> None:
    """Serve admission webhooks over the mounted certificate.

    kopf calls this once, after the startup handlers ran and before any webhook
    is needed, and reconnects the server whenever the certificate files change,
    so a renewed certificate is picked up without a restart.

    Args:
        settings: The operator settings kopf hands to every startup handler.
        **_: Any other argument a future kopf release may add.

    Raises:
        RuntimeError: If the TLS material of the chart is not mounted, which means
            the webhook deployment of the chart is incomplete.
    """
    cert_dir = _cert_dir()
    settings.admission.server = kopf.WebhookServer(
        addr="0.0.0.0",
        port=_port(),
        host=_host(),
        certfile=cert_dir / _CERT_FILENAME,
        pkeyfile=cert_dir / _KEY_FILENAME,
        cadata=_ca_bundle(cert_dir),
    )
    settings.admission.managed = _managed()


def _ca_bundle(cert_dir: Path) -> bytes:
    """Return the CA bundle the API server verifies the webhook with.

    Args:
        cert_dir: The directory the TLS material is mounted in.

    Returns:
        The contents of ``ca.crt``, falling back to the server certificate
        itself: with a self-signed issuer, as the chart's cert-manager
        ``Certificate`` uses by default, the certificate is its own CA.

    Raises:
        RuntimeError: If neither file is present.
    """
    for name in (_CA_FILENAME, _CERT_FILENAME):
        candidate = cert_dir / name
        if candidate.is_file():
            return candidate.read_bytes()
    raise RuntimeError(f"no TLS material in {cert_dir}; is the webhook secret mounted?")


def _cert_dir() -> Path:
    """Return the directory the TLS material is mounted in.

    Returns:
        The directory named by :data:`CERT_DIR_VAR`, or :data:`DEFAULT_CERT_DIR`.
    """
    return Path(os.environ.get(CERT_DIR_VAR, DEFAULT_CERT_DIR))


def _port() -> int:
    """Return the port the webhook server listens on.

    Returns:
        The port named by :data:`PORT_VAR`, or :data:`DEFAULT_PORT`.

    Raises:
        ValueError: If the variable does not hold a number.
    """
    return int(os.environ.get(PORT_VAR, DEFAULT_PORT))


def _host() -> str:
    """Return the hostname the API server calls.

    kopf advertises the endpoint verbatim in the webhook configuration, so the
    name has to resolve from the API server.

    Returns:
        The hostname named by :data:`HOST_VAR`, or :data:`DEFAULT_HOST`.
    """
    return os.environ.get(HOST_VAR, DEFAULT_HOST)


def _managed() -> str:
    """Return the name of the webhook configurations kopf manages.

    Returns:
        The name named by :data:`MANAGED_VAR`, or :data:`DEFAULT_MANAGED`.
    """
    return os.environ.get(MANAGED_VAR, DEFAULT_MANAGED)
