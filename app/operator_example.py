# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.5
#   kernelspec:
#     display_name: based-pyproject (3.13.5)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # A complete operator entry point
#
# This is the file the scaffolder asks for: one module that declares a typed
# custom resource and the handlers that reconcile it. `based-operators scaffold`
# loads it exactly as `kopf run` does, derives the CRD from the resource and
# bakes `kopf run app/operator_example.py` into the image it generates:
#
# ```shell
# uv run app/scaffold.py scaffold app/operator_example.py -o deploy
# ```

# %%
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

import based_operators as bk


# %% [markdown]
# ## The typed custom resource
#
# The `spec` and `status` models are ordinary Pydantic models. Their JSON Schema
# is what the CRD is generated from, so constraints such as `ge`/`le` reach the
# cluster and are enforced by the API server, not only by the operator.


# %%
class Phase(StrEnum):
    """The lifecycle phase reported back on the resource."""

    PENDING = "Pending"
    READY = "Ready"


class WidgetSpec(BaseModel):
    """What the user asks for."""

    message: str = Field(min_length=1)
    replicas: int = Field(default=1, ge=0, le=10)


class WidgetStatus(BaseModel):
    """What the operator reports about the object."""

    phase: Phase = Phase.PENDING
    ready: int = 0


class Widget(bk.CustomResource[WidgetSpec, WidgetStatus]):
    """A namespaced widget resource with a status subresource."""

    resource_config = bk.ResourceConfig(
        group="example.com",
        version="v1",
        kind="Widget",
        plural="widgets",
        scope=bk.Scope.NAMESPACED,
        short_names=("wd",),
    )


# %% [markdown]
# ## The handlers
#
# Handlers hang off `<Resource>.on` and receive the validated resource instead of
# a raw dictionary. Stacking `create` and `resume` on one function makes it
# reconcile both newly created and pre-existing objects.


# %%
@Widget.on.create
@Widget.on.resume
async def reconcile(resource: Widget, patch: bk.Patch, logger: Any, **kwargs: Any) -> None:
    """Bring a widget in line with its spec."""
    replicas = resource.spec.replicas
    logger.info("reconciling %s with %s replica(s)", resource.metadata.name, replicas)
    patch.status["ready"] = replicas
    patch.status["phase"] = Phase.READY if replicas else Phase.PENDING


# %% [markdown]
# A single field can be watched instead of the whole object. kopf passes the
# previous and the current value of that field as `old` and `new`.


# %%
@Widget.on.update(field="spec.replicas")
def replicas_changed(resource: Widget, old: Any, new: Any, logger: Any, **kwargs: Any) -> None:
    """Record a scale-up or a scale-down in the object's log."""
    logger.info("%s scaled from %s to %s", resource.metadata.name, old, new)


# %%
if __name__ == "__main__":
    print("Handlers attached to", Widget.__name__)
