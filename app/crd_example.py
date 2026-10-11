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
# # Custom resources and CRD generation
#
# This notebook shows the smallest useful workflow: define a `spec` model,
# subclass `CustomResource`, and render a CustomResourceDefinition manifest.

# %%
from pydantic import BaseModel

from based_operators.crd import CustomResource, ResourceConfig, Scope, crd_yaml


class WidgetSpec(BaseModel):
    """The resource specification."""

    replicas: int = 1
    message: str


class Widget(CustomResource[WidgetSpec, None]):
    """A status-less, namespaced widget resource."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Widget",
        plural="widgets",
        scope=Scope.NAMESPACED,
        short_names=("wd",),
    )


# %%
print(crd_yaml(Widget))
