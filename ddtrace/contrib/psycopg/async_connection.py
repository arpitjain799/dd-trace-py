from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import dbapi_async
from ddtrace.contrib.psycopg.async_cursor import Psycopg3FetchTracedAsyncCursor
from ddtrace.contrib.psycopg.async_cursor import Psycopg3TracedAsyncCursor
from ddtrace.contrib.psycopg.connection import patch_conn
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.internal.constants import COMPONENT


class Psycopg3TracedAsyncConnection(dbapi_async.TracedAsyncConnection):
    def __init__(self, conn, pin=None, cursor_cls=None):
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = (
                Psycopg3FetchTracedAsyncCursor if config.psycopg.trace_fetch_methods else Psycopg3TracedAsyncCursor
            )

        super(Psycopg3TracedAsyncConnection, self).__init__(conn, pin, config.psycopg, cursor_cls=cursor_cls)

    async def execute(self, *args, **kwargs):
        """Execute a query and return a cursor to read its results."""
        span_name = "{}.{}".format(self._self_datadog_name, "execute")

        async def patched_execute(*args, **kwargs):
            try:
                cur = await self.cursor()
                if kwargs.get("binary", None):
                    cur.format = 1  # set to 1 for binary or 0 if not
                return cur.execute(*args, **kwargs)
            except Exception as ex:
                raise ex.with_traceback(None)

        return await self._trace_method(patched_execute, span_name, {}, *args, **kwargs)


async def patched_connect_async(connect_func, _, args, kwargs):
    traced_conn_cls = Psycopg3TracedAsyncConnection

    _config = globals()["config"]._config
    module_name = (
        connect_func.__module__
        if len(connect_func.__module__.split(".")) == 1
        else connect_func.__module__.split(".")[0]
    )
    pin = Pin.get_from(_config[module_name].base_module)

    if not pin or not pin.enabled() or not pin._config.trace_connect:
        conn = await connect_func(*args, **kwargs)
    else:
        with pin.tracer.trace(
            "{}.{}".format(connect_func.__module__, connect_func.__name__),
            service=ext_service(pin, pin._config),
            span_type=SpanTypes.SQL,
        ) as span:
            span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
            span.set_tag_str(COMPONENT, pin._config.integration_name)
            if span.get_tag(db.SYSTEM) is None:
                span.set_tag_str(db.SYSTEM, pin._config.dbms_name)

            span.set_tag(SPAN_MEASURED_KEY)
            conn = await connect_func(*args, **kwargs)

    return patch_conn(conn, pin=pin, traced_conn_cls=traced_conn_cls)
