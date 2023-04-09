import pytest

from ddtrace import Tracer


@pytest.fixture
def tracer():
    yield Tracer()


def test_dynamic_config(tracer):
    with tracer.trace("before_config"):
        pass
    tracer.flush()

    with tracer.trace("after_config") as conf_span:
        pass

    # assert conf_span.get_tag("version") == "ade31f"
    # assert conf_span.get_tag("env") == "rc-env"
    assert conf_span._get_ctx_item("config")
    assert conf_span._get_ctx_item("config").service_mapping.get("foobar")
    assert conf_span._get_ctx_item("config").service_mapping.get("foobar")
