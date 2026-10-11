"""The kopf handler interface for typed custom resources.

This module adapts the resource-level decorators of :mod:`kopf.on` to the typed
resource classes produced by :mod:`based_operators.crd`. Every resource class
exposes one shared namespace as ``<Resource>.on`` whose members mirror kopf's
decorators::

    import based_operators as bk

    from .models import Greeting

    @Greeting.on.create
    @Greeting.on.resume
    async def reconcile(resource: Greeting, patch: bk.Patch, logger, **kwargs: Any) -> None:
        desired = resource.spec

A handler registered this way is attached to kopf exactly like its ``kopf.on``
counterpart, but kopf invokes the *adapted* function: the raw ``body`` mapping
is validated into the concrete resource model and passed as ``resource``, while
every other keyword argument kopf provides (``patch``, ``logger``, ``param``,
``retry``, ``old``, ``new``, ``diff``, ``meta``, ``spec``, ``status``, ...) is
forwarded unchanged. kopf's own :class:`kopf.Resource` descriptor, which it also
passes as ``resource``, is forwarded as ``api_resource`` instead so that no
information is lost.

Only the resource-level decorators are wrapped here. The operator-level handlers
--- ``kopf.on.startup``, ``kopf.on.cleanup``, ``kopf.on.login`` and
``kopf.on.probe`` --- do not operate on custom resources and are used directly
from kopf.
"""

from __future__ import annotations

import functools
import inspect
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, overload

import kopf

if TYPE_CHECKING:
    from based_operators.crd import CustomResource

__all__ = [
    "Diff",
    "HandlerError",
    "Patch",
    "RawEvent",
    "ResourceHandlers",
    "handler_namespace",
]

Patch = kopf.Patch
"""The mutable merge-patch kopf applies when a handler finishes.

Use ``patch.spec[...]``, ``patch.status[...]`` or ``patch.metadata[...]`` to
accumulate changes; setting a key to ``None`` deletes it. ``patch.fns`` is a list
of transformation functions which are applied to the raw body instead.
"""

Diff = kopf.Diff
"""The list of field changes detected between two states of a resource."""

RawEvent = kopf.RawEvent
"""A raw JSON-decoded watch event, as delivered by the Kubernetes API."""


class HandlerError(Exception):
    """Raised when an adapted handler cannot be invoked.

    This signals a framework contract violation rather than a problem with the
    Kubernetes object itself: for example, an adapted handler being invoked
    without the ``body`` argument kopf always provides. Errors in the object
    body surface as :class:`pydantic.ValidationError` instead.
    """


class ResourceHandlers:
    """The kopf handler namespace of a single resource class.

    Instances are created and shared by :func:`handler_namespace` and are reached
    through the ``on`` attribute of a :class:`~based_operators.crd.CustomResource`
    subclass::

        @Greeting.on.create
        async def reconcile(resource: Greeting, patch: Patch, logger, **kwargs: Any) -> None:
            ...

    Every decorator mirrors the same-named :mod:`kopf.on` decorator. It may be
    used bare (``@<Resource>.on.create``) or called with kopf's keyword options
    (``@<Resource>.on.create(field="spec.replicas", param=10)``). The resource
    selector is always derived from the class's
    :class:`~based_operators.crd.ResourceConfig`, so it is never passed by hand.
    """

    def __init__(self, resource_type: type[CustomResource[Any, Any]]) -> None:
        """Bind the namespace to a resource class.

        Args:
            resource_type: The :class:`~based_operators.crd.CustomResource`
                subclass whose handlers are registered here.
        """
        self._resource_type = resource_type
        self._selector: dict[str, str] | None = None

    # -- state-changing handlers ------------------------------------------------

    @overload
    def create[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def create[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def create[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a handler for the creation of this resource.

        The handler is invoked whenever a matching object is created while the
        operator is running. It receives ``resource`` (the validated resource
        model) in addition to every keyword argument kopf provides, such as
        ``patch``, ``logger``, ``param``, ``retry``, ``started``, ``runtime``,
        ``memo``, ``body``, ``meta``, ``spec``, ``status``, ``name``,
        ``namespace``, ``uid``, ``labels``, ``annotations``, ``reason`` and
        ``api_resource``.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.create``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.create`, e.g. ``id``, ``param``, ``field``,
                ``value``, ``labels``, ``annotations``, ``when``, ``errors``,
                ``timeout``, ``retries``, ``backoff`` or ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("create", fn, options)

    @overload
    def update[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def update[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def update[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a handler for updates of this resource.

        The handler is invoked when kopf detects an essential change of the
        object. In addition to ``resource`` it receives ``old``, ``new`` and
        ``diff``, describing the detected changes; ``old`` and ``new`` are raw
        bodies for whole-object handlers and field values for handlers
        restricted with ``field=``. Parse them with ``<Resource>.model_validate``
        when their typed form is needed.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.update``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.update`, e.g. ``id``, ``param``, ``field``,
                ``value``, ``old``, ``new``, ``labels``, ``annotations``,
                ``when``, ``errors``, ``timeout``, ``retries``, ``backoff`` or
                ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("update", fn, options)

    @overload
    def delete[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def delete[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def delete[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a handler for the deletion of this resource.

        kopf invokes the handler when the object is marked for deletion, before
        it disappears from the cluster. Because kopf's automatic finalizer is
        only added when a deletion handler requires it, pass ``optional=True`` to
        handle deletions that need not block the object's removal.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.delete``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.delete`, e.g. ``id``, ``param``, ``optional``,
                ``field``, ``value``, ``labels``, ``annotations``, ``when``,
                ``errors``, ``timeout``, ``retries``, ``backoff`` or
                ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("delete", fn, options)

    @overload
    def resume[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def resume[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def resume[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a handler for objects that already existed at operator start.

        A resuming handler covers objects that were created while the operator
        was not running. It is commonly stacked under ``create`` so that one
        function reconciles both newly created and pre-existing objects::

            @Greeting.on.create
            @Greeting.on.resume
            async def reconcile(resource: Greeting, **kwargs: Any) -> None:
                ...

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.resume``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.resume`, e.g. ``id``, ``param``, ``deleted``,
                ``field``, ``value``, ``labels``, ``annotations``, ``when``,
                ``errors``, ``timeout``, ``retries``, ``backoff`` or
                ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("resume", fn, options)

    # -- event-watching handler -------------------------------------------------

    @overload
    def event[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def event[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def event[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a handler for every raw watch event of this resource.

        Unlike the cause-based handlers, an event handler sees the unfiltered
        Kubernetes events. kopf passes ``type`` (``ADDED``, ``MODIFIED`` or
        ``DELETED``) and ``event`` (the raw event) in addition to ``resource``
        and the usual resource-level arguments.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.event``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.event`, e.g. ``id``, ``param``, ``labels``,
                ``annotations``, ``when``, ``field``, ``value`` or ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("event", fn, options)

    # -- field handler ----------------------------------------------------------

    @overload
    def field[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def field[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def field[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a handler for changes of a single field of this resource.

        The ``field`` option is mandatory and names the watched field with a
        dotted path into the body, e.g. ``field="spec.replicas"``. The handler is
        invoked on updates with ``old`` and ``new`` holding the field's previous
        and current values, in addition to the usual ``resource``.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.field``. Because ``field`` is required,
                the called form ``@<Resource>.on.field(field=...)`` is the norm.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.field`, which requires ``field`` and accepts
                ``id``, ``param``, ``value``, ``old``, ``new``, ``labels``,
                ``annotations``, ``when``, ``errors``, ``timeout``, ``retries``,
                ``backoff`` or ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("field", fn, options)

    # -- indexing handler -------------------------------------------------------

    @overload
    def index[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def index[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def index[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register an in-memory indexing handler for this resource.

        The handler's return value becomes the index value for the object. The
        index is exposed to every other handler under the handler's name (or its
        ``id``), letting handlers look up related objects without extra API
        calls.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.index``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.index`, e.g. ``id``, ``param``, ``field``,
                ``value``, ``labels``, ``annotations``, ``when``, ``errors``,
                ``timeout``, ``retries``, ``backoff`` or ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("index", fn, options)

    # -- daemon and timer handlers ----------------------------------------------

    @overload
    def daemon[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def daemon[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def daemon[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a daemon that runs for as long as the resource exists.

        The handler is started once per resource and kept running; kopf passes
        ``stopped``, a flag that handlers should poll or wait on to react to the
        resource's deletion. Use this for behaviour that must run continuously
        rather than once per event.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.daemon``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.daemon`, e.g. ``id``, ``param``,
                ``initial_delay``, ``cancellation_backoff``,
                ``cancellation_timeout``, ``cancellation_polling``, ``errors``,
                ``timeout``, ``retries``, ``backoff``, ``when`` or ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("daemon", fn, options)

    @overload
    def timer[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def timer[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def timer[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a timer that is invoked repeatedly for each resource.

        The ``interval`` option (in seconds) is required; ``initial_delay``,
        ``sharp`` and ``idle`` control when the first tick happens and how missed
        ticks are handled. Timers are useful for periodic reconciliation.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.timer``. Because ``interval`` is
                required, the called form ``@<Resource>.on.timer(interval=...)``
                is the norm.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.timer`, which requires ``interval`` and accepts
                ``id``, ``param``, ``initial_delay``, ``sharp``, ``idle``,
                ``errors``, ``timeout``, ``retries``, ``backoff``, ``when`` or
                ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("timer", fn, options)

    # -- admission webhook handlers ---------------------------------------------

    @overload
    def validate[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def validate[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def validate[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a validating admission webhook for this resource.

        The handler inspects the candidate object and rejects it by raising
        :class:`kopf.AdmissionError`. kopf additionally provides ``dryrun``,
        ``subresource``, ``userinfo``, ``warnings``, ``headers`` and
        ``sslpeer``. Admission handlers must not cause side effects unless
        ``side_effects=True`` is passed.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.validate``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.validate`, e.g. ``id``, ``param``,
                ``operations``, ``subresource``, ``persistent``,
                ``side_effects``, ``ignore_failures``, ``labels``,
                ``annotations``, ``when``, ``field``, ``value`` or ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("validate", fn, options)

    @overload
    def mutate[F: Callable[..., Any]](self, fn: F, /, **options: Any) -> F: ...
    @overload
    def mutate[F: Callable[..., Any]](
        self, fn: None = None, /, **options: Any
    ) -> Callable[[F], F]: ...
    def mutate[F: Callable[..., Any]](
        self, fn: F | None = None, /, **options: Any
    ) -> F | Callable[[F], F]:
        """Register a mutating admission webhook for this resource.

        The handler populates ``patch`` with the mutations to apply to the
        candidate object. As with every admission handler, it must be free of
        side effects unless ``side_effects=True`` is passed, and it must respect
        ``dryrun``.

        Args:
            fn: The handler function, supplied only when the decorator is used
                bare as ``@<Resource>.on.mutate``.
            **options: Keyword options forwarded verbatim to
                :func:`kopf.on.mutate`, e.g. ``id``, ``param``, ``operations``,
                ``subresource``, ``persistent``, ``side_effects``,
                ``ignore_failures``, ``labels``, ``annotations``, ``when``,
                ``field``, ``value`` or ``registry``.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        return self._register("mutate", fn, options)

    # -- internals --------------------------------------------------------------

    def _register(
        self,
        reason: str,
        fn: Callable[..., Any] | None,
        options: dict[str, Any],
    ) -> Any:
        """Attach a handler to kopf, adapting it to the resource model.

        Args:
            reason: The name of the ``kopf.on`` decorator to delegate to.
            fn: The handler function, or ``None`` when the decorator is called
                with options.
            options: Keyword options forwarded verbatim to the kopf decorator.

        Returns:
            The handler function unchanged when ``fn`` is given, otherwise a
            decorator applying the same behaviour.

        Raises:
            TypeError: If ``fn`` is supplied but is not callable.
        """
        registrar = getattr(kopf.on, reason)
        decorator = registrar(**self._resolve_selector(), **options)

        if fn is not None:
            if not callable(fn):
                raise TypeError(
                    f"{self._resource_type.__name__}.on.{reason} expects a callable "
                    f"handler or keyword options, got {fn!r}"
                )
            decorator(_adapt_handler(self._resource_type, fn))
            return fn

        def decorate(handler: Callable[..., Any]) -> Callable[..., Any]:
            decorator(_adapt_handler(self._resource_type, handler))
            return handler

        return decorate

    def _resolve_selector(self) -> dict[str, str]:
        """Return the kopf resource selector derived from the resource config.

        Returns:
            A mapping of the ``group``, ``version`` and ``plural`` keywords that
            kopf uses to match the resource, resolved once and cached.

        Raises:
            ResourceDefinitionError: If the resource class does not define a
                valid :class:`~based_operators.crd.ResourceConfig`.
        """
        if self._selector is None:
            config = self._resource_type._require_resource_config()
            self._selector = {
                "group": config.group,
                "version": config.version,
                "plural": config.plural,
            }
        return self._selector


_NAMESPACES: weakref.WeakKeyDictionary[type, ResourceHandlers] = weakref.WeakKeyDictionary()
"""The handler namespace shared by each resource class."""


def handler_namespace(resource_type: type[CustomResource[Any, Any]]) -> ResourceHandlers:
    """Return the shared handler namespace of a resource class.

    The namespace is created on first use and cached per class, so that two
    references to ``<Resource>.on`` always register into the same place.

    Args:
        resource_type: The :class:`~based_operators.crd.CustomResource` subclass
            whose namespace is requested.

    Returns:
        The :class:`ResourceHandlers` namespace bound to ``resource_type``.
    """
    namespace = _NAMESPACES.get(resource_type)
    if namespace is None:
        namespace = ResourceHandlers(resource_type)
        _NAMESPACES[resource_type] = namespace
    return namespace


def _adapt_handler(
    resource_type: type[CustomResource[Any, Any]],
    fn: Callable[..., Any],
) -> Callable[..., Any]:
    """Wrap a handler so that it receives the parsed resource model.

    The synchronous or asynchronous nature of ``fn`` is preserved, because kopf
    dispatches the two differently: synchronous handlers run in an executor
    thread while asynchronous ones are awaited on the event loop. The wrapper
    delegates its metadata (including ``__wrapped__``) to ``fn``, which is also
    how kopf detects the coroutine-ness through the wrapper.

    Args:
        resource_type: The resource class to validate the raw body into.
        fn: The user-supplied handler function.

    Returns:
        An adapted handler with the same name, documentation and sync/async
        nature as ``fn``.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_adapter(**kwargs: Any) -> Any:
            return await fn(**_inject_resource(resource_type, kwargs))

        return async_adapter

    @functools.wraps(fn)
    def sync_adapter(**kwargs: Any) -> Any:
        return fn(**_inject_resource(resource_type, kwargs))

    return sync_adapter


def _inject_resource(
    resource_type: type[CustomResource[Any, Any]],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Build the keyword arguments passed to an adapted handler.

    Args:
        resource_type: The resource class to validate the raw body into.
        kwargs: The keyword arguments supplied by kopf.

    Returns:
        A copy of ``kwargs`` with the validated resource under ``resource`` and,
        when kopf supplied its own :class:`kopf.Resource` descriptor, that
        descriptor moved to ``api_resource``.

    Raises:
        HandlerError: If kopf did not supply a ``body`` to validate.
    """
    if "body" not in kwargs:
        raise HandlerError(
            f"{resource_type.__name__} handler was invoked without a 'body' argument; "
            "resource handlers must be registered through '<Resource>.on.<reason>'"
        )

    forwarded = dict(kwargs)
    kopf_resource = forwarded.pop("resource", None)
    forwarded["resource"] = resource_type.model_validate(kwargs["body"])
    if kopf_resource is not None:
        forwarded["api_resource"] = kopf_resource
    return forwarded
