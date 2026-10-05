"""Model-aware registration delegates to Kopf's own public decorators."""

import functools
import inspect
import logging
import types
from typing import Any, Callable, Union, get_args, get_origin

import kopf
from pydantic import BaseModel

from based_operators.metadata import resolve_metadata
from based_operators.validation import InvalidDesiredInput, respond_invalid, validate_resource

log = logging.getLogger(__name__)

_RESOURCE_FAMILIES = (
    "create", "resume", "update", "delete", "field", "timer",
    "daemon", "event", "index", "validate", "mutate",
)


def _optional_resource(annotation: Any, model: type[BaseModel]) -> bool:
    return annotation is not inspect.Parameter.empty and (
        get_origin(annotation) in (Union, types.UnionType)
        and set(get_args(annotation)) == {model, type(None)}
    )


def _prepare(
    fn: Callable[..., Any],
    kwargs: dict[str, Any],
    model: type[BaseModel],
    category: str,
    delay: float,
) -> dict[str, Any] | None:
    body = kwargs.get("body")
    if category in {"validate", "mutate"}:
        body = kwargs.get("new") or body
        if body is None:  # DELETE admission has no new desired object.
            return _filter(fn, kwargs)
    if body is None:
        return _filter(fn, kwargs)
    try:
        resource = validate_resource(model, body)
    except InvalidDesiredInput as error:
        if category == "delete":
            resource = None
        else:
            log.warning(
                "Invalid desired spec for %s/%s: %s",
                body.get("metadata", {}).get("namespace", ""),
                body.get("metadata", {}).get("name", ""),
                error,
            )
            respond_invalid(category, error, delay)
            return None
    payload = dict(kwargs)
    signature = inspect.signature(fn)
    if "resource" in signature.parameters or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    ):
        payload["resource"] = resource
    return _filter(fn, payload)


def _filter(fn: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(fn)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return kwargs
    return {name: value for name, value in kwargs.items() if name in signature.parameters}


def _registration(category: str) -> Callable[..., Any]:
    upstream = getattr(kopf.on, category) if hasattr(kopf.on, category) else getattr(kopf, category)

    @functools.wraps(upstream)
    def register(
        *args: Any,
        model: type[BaseModel] | None = None,
        retry_delay: float = 30.0,
        strict_delete: bool = False,
        **options: Any,
    ) -> Any:
        if model is None:
            if strict_delete or retry_delay != 30.0:
                raise ValueError("model-specific options require model=")
            return upstream(*args, **options)
        if retry_delay <= 0:
            raise ValueError("retry_delay must be positive")
        meta = resolve_metadata(model)
        if args:
            raise ValueError("model= cannot be combined with positional resource selectors")
        for key, inferred in (
            ("group", meta.group), ("version", meta.version),
            ("kind", meta.names.kind), ("plural", meta.names.plural),
        ):
            if key in options and options[key] != inferred:
                raise ValueError(f"{key} conflicts with model identity: {inferred}")
        options.update(group=meta.group, version=meta.version, kind=meta.names.kind)
        decorator = upstream(**options)

        def attach(fn: Callable[..., Any]) -> Callable[..., Any]:
            signature = inspect.signature(fn)
            if category == "delete" and not strict_delete and "resource" in signature.parameters:
                if not _optional_resource(signature.parameters["resource"].annotation, model):
                    raise TypeError(
                        "delete fallback requires resource annotated Model | None; "
                        "use strict_delete=True to require a validated snapshot"
                    )
            if category == "delete" and strict_delete:
                effective_category = "update"
            else:
                effective_category = category

            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def async_wrapper(**kwargs: Any) -> Any:
                    prepared = _prepare(fn, kwargs, model, effective_category, retry_delay)
                    if prepared is not None:
                        return await fn(**prepared)
                    return None

                decorator(async_wrapper)
            else:
                @functools.wraps(fn)
                def sync_wrapper(**kwargs: Any) -> Any:
                    prepared = _prepare(fn, kwargs, model, effective_category, retry_delay)
                    if prepared is not None:
                        return fn(**prepared)
                    return None

                decorator(sync_wrapper)
            return fn

        return attach

    return register


class ModelDecorators:
    """Kopf decorator namespace, extended with explicit ``model=`` registration."""

    create = staticmethod(_registration("create"))
    resume = staticmethod(_registration("resume"))
    update = staticmethod(_registration("update"))
    delete = staticmethod(_registration("delete"))
    field = staticmethod(_registration("field"))
    timer = staticmethod(_registration("timer"))
    daemon = staticmethod(_registration("daemon"))
    event = staticmethod(_registration("event"))
    index = staticmethod(_registration("index"))
    validate = staticmethod(_registration("validate"))
    mutate = staticmethod(_registration("mutate"))
    startup = staticmethod(kopf.on.startup)
    cleanup = staticmethod(kopf.on.cleanup)
    login = staticmethod(kopf.on.login)
    probe = staticmethod(kopf.on.probe)
    subhandler = staticmethod(kopf.on.subhandler)
    register = staticmethod(kopf.on.register)


on = ModelDecorators()
