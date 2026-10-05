"""Kopf module loaded by generated downstream images."""

import os
import runpy

import kopf


@kopf.on.startup(id="based-operators-webhooks")
def configure_webhooks(settings: kopf.OperatorSettings, **kwargs: object) -> None:
    """Enable HTTPS admission only when the generated chart requests it."""
    if os.environ.get("BASED_OPERATORS_WEBHOOKS") != "true":
        return
    if settings.admission.server is not None or settings.admission.managed is not None:
        raise RuntimeError("Conflicting downstream Kopf admission settings")
    settings.admission.server = kopf.WebhookServer(
        addr="0.0.0.0",
        port=9443,
        certfile="/etc/operator-webhook/tls.crt",
        pkeyfile="/etc/operator-webhook/tls.key",
        cafile="/etc/operator-webhook/ca.crt",
    )


runpy.run_path(os.environ["BASED_OPERATORS_HANDLER"])
