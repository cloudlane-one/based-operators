"""Model-aware registration delegates to Kopf's own public decorators."""

import functools
import inspect
import logging
import types
from collections.abc import Callable
from typing import Any, Union, get_args, get_origin, get_type_hints

import kopf
from pydantic import BaseModel

from based_operators.daemon import ResourceContext
from based_operators.metadata import resolve_metadata
from based_operators.validation import InvalidDesiredInputError, respond_invalid, validate_resource

log = logging.getLogger(__name__)


def _optional_resource(annotation: Any, model: type[BaseModel]) -> bool:
    return annotation is not inspect.Parameter.empty and (
        get_origin(annotation) in (Union, types.UnionType)
        and set(get_args(annotation)) == {model, type(None)}
    )


def _prepare(
    kwargs: dict[str, Any],
    model: type[BaseModel],
    category: str,
    delay: float,
    parameter_names: frozenset[str],
    accepts_kwargs: bool,
) -> dict[str, Any] | None:
    body = kwargs.get("body")
    if category in {"validate", "mutate"}:
        new = kwargs.get("new")
        if new is not None:
            body = new
        if body is None:  # DELETE admission has no new desired object.
            return _filter(parameter_names, accepts_kwargs, kwargs)
    if body is None:
        return _filter(parameter_names, accepts_kwargs, kwargs)
    try:
        resource = validate_resource(model, body)
    except InvalidDesiredInputError as error:
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
    if "resource" in parameter_names or accepts_kwargs:
        payload["resource"] = resource
    return _filter(parameter_names, accepts_kwargs, payload)


def _filter(
    parameter_names: frozenset[str], accepts_kwargs: bool, kwargs: dict[str, Any],
) -> dict[str, Any]:
    if accepts_kwargs:
        return kwargs
    return {name: value for name, value in kwargs.items() if name in parameter_names}


def _model_options(
    model: type[BaseModel], args: tuple[Any, ...], options: dict[str, Any],
) -> dict[str, Any]:
    meta = resolve_metadata(model)
    if args:
        raise ValueError("model= cannot be combined with positional resource selectors")
    for key, inferred in (
        ("group", meta.group), ("version", meta.version),
        ("kind", meta.names.kind), ("plural", meta.names.plural),
    ):
        if key in options and options[key] != inferred:
            raise ValueError(f"{key} conflicts with model identity: {inferred}")
    return {**options, "group": meta.group, "version": meta.version, "kind": meta.names.kind}


def _register_function(
    fn: Callable[..., Any],
    decorator: Callable[..., Any],
    model: type[BaseModel],
    category: str,
    retry_delay: float,
    strict_delete: bool,
) -> Callable[..., Any]:
    signature = inspect.signature(fn)
    parameter_names = frozenset(signature.parameters)
    accepts_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )
    _verify_delete(fn, model, category, strict_delete, parameter_names)
    effective_category = "update" if category == "delete" and strict_delete else category
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(**kwargs: Any) -> Any:
            if category == "daemon":
                context = ResourceContext(model, kwargs["body"], kwargs["stopped"])
                resource = await context.wait_async(retry_delay)
                if resource is None:
                    return None
                return await fn(**_filter(parameter_names, accepts_kwargs, {
                    **kwargs, "resource": resource, "resource_context": context,
                }))
            prepared = _prepare(
                kwargs, model, effective_category, retry_delay, parameter_names, accepts_kwargs,
            )
            if prepared is not None:
                return await fn(**prepared)
            return None

        decorator(async_wrapper)
    else:
        @functools.wraps(fn)
        def sync_wrapper(**kwargs: Any) -> Any:
            if category == "daemon":
                context = ResourceContext(model, kwargs["body"], kwargs["stopped"])
                resource = context.wait(retry_delay)
                if resource is None:
                    return None
                return fn(**_filter(parameter_names, accepts_kwargs, {
                    **kwargs, "resource": resource, "resource_context": context,
                }))
            prepared = _prepare(
                kwargs, model, effective_category, retry_delay, parameter_names, accepts_kwargs,
            )
            if prepared is not None:
                return fn(**prepared)
            return None

        decorator(sync_wrapper)
    return fn


def _verify_delete(
    fn: Callable[..., Any],
    model: type[BaseModel],
    category: str,
    strict_delete: bool,
    parameter_names: frozenset[str],
) -> None:
    if category != "delete" or strict_delete or "resource" not in parameter_names:
        return
    try:
        annotation = get_type_hints(fn).get("resource")
    except (NameError, TypeError) as error:
        raise TypeError("delete fallback needs a resolvable resource annotation") from error
    if not _optional_resource(annotation, model):
        raise TypeError(
            "delete fallback requires resource annotated Model | None; "
            "use strict_delete=True to require a validated snapshot"
        )


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
        decorator = upstream(**_model_options(model, args, options))

        def attach(fn: Callable[..., Any]) -> Callable[..., Any]:
            return _register_function(fn, decorator, model, category, retry_delay, strict_delete)

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
