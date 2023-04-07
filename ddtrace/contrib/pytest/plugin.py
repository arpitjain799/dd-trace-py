from doctest import DocTest
import json
import os
import re
from typing import Dict

import pytest

import ddtrace
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.pytest.constants import FRAMEWORK
from ddtrace.contrib.pytest.constants import HELP_MSG
from ddtrace.contrib.pytest.constants import KIND
from ddtrace.contrib.pytest.constants import XFAIL_REASON
from ddtrace.ext import SpanTypes
from ddtrace.ext import test
from ddtrace.internal import compat
from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.logger import get_logger


PATCH_ALL_HELP_MSG = "Call ddtrace.patch_all before running tests."
log = get_logger(__name__)


def encode_test_parameter(parameter):
    param_repr = repr(parameter)
    # if the representation includes an id() we'll remove it
    # because it isn't constant across executions
    return re.sub(r" at 0[xX][0-9a-fA-F]+", "", param_repr)


def is_enabled(config):
    """Check if the ddtrace plugin is enabled."""
    return config.getoption("ddtrace") or config.getini("ddtrace")


def _extract_span(item):
    """Extract span from `pytest.Item` instance."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `pytest.Item` instance."""
    setattr(item, "_datadog_span", span)


def _extract_repository_name(repository_url):
    # type: (str) -> str
    """Extract repository name from repository url."""
    try:
        return compat.parse.urlparse(repository_url).path.rstrip(".git").rpartition("/")[-1]
    except ValueError:
        # In case of parsing error, default to repository url
        log.warning("Repository name cannot be parsed from repository_url: %s", repository_url)
        return repository_url


def pytest_addoption(parser):
    """Add ddtrace options."""
    group = parser.getgroup("ddtrace")

    group._addoption(
        "--ddtrace",
        action="store_true",
        dest="ddtrace",
        default=False,
        help=HELP_MSG,
    )

    group._addoption(
        "--ddtrace-patch-all",
        action="store_true",
        dest="ddtrace-patch-all",
        default=False,
        help=PATCH_ALL_HELP_MSG,
    )

    parser.addini("ddtrace", HELP_MSG, type="bool")
    parser.addini("ddtrace-patch-all", PATCH_ALL_HELP_MSG, type="bool")


def pytest_configure(config):
    config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")
    if is_enabled(config):
        _CIVisibility.enable(config=ddtrace.config.pytest)


def pytest_sessionfinish(session, exitstatus):
    if is_enabled(session.config):
        _CIVisibility.disable()


@pytest.fixture(scope="function")
def ddspan(request):
    if _CIVisibility.enabled:
        return _extract_span(request.node)


@pytest.fixture(scope="session")
def ddtracer():
    if _CIVisibility.enabled:
        return _CIVisibility._instance.tracer
    return ddtrace.tracer


@pytest.fixture(scope="session", autouse=True)
def patch_all(request):
    if request.config.getoption("ddtrace-patch-all") or request.config.getini("ddtrace-patch-all"):
        ddtrace.patch_all()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if not _CIVisibility.enabled:
        yield
        return

    with _CIVisibility._instance.tracer.trace(
        ddtrace.config.pytest.operation_name,
        service=_CIVisibility._instance._service,
        resource=item.nodeid,
        span_type=SpanTypes.TEST,
    ) as span:
        span.set_tag_str(COMPONENT, "pytest")
        span.set_tag(SPAN_KIND, KIND)
        span.set_tag(test.FRAMEWORK, FRAMEWORK)
        span.set_tag(test.NAME, item.name)
        if hasattr(item, "module"):
            span.set_tag(test.SUITE, item.module.__name__)
        elif hasattr(item, "dtest") and isinstance(item.dtest, DocTest):
            span.set_tag(test.SUITE, item.dtest.globs["__name__"])
        span.set_tag(test.TYPE, SpanTypes.TEST)
        span.set_tag(test.FRAMEWORK_VERSION, pytest.__version__)

        if item.location and item.location[0]:
            _CIVisibility.set_codeowners_of(item.location[0], span=span)

        # We preemptively set FAIL as a status, because if pytest_runtest_makereport is not called
        # (where the actual test status is set), it means there was a pytest error
        span.set_tag(test.STATUS, test.Status.FAIL.value)

        # Parameterized test cases will have a `callspec` attribute attached to the pytest Item object.
        # Pytest docs: https://docs.pytest.org/en/6.2.x/reference.html#pytest.Function
        if getattr(item, "callspec", None):
            parameters = {"arguments": {}, "metadata": {}}  # type: Dict[str, Dict[str, str]]
            for param_name, param_val in item.callspec.params.items():
                try:
                    parameters["arguments"][param_name] = encode_test_parameter(param_val)
                except Exception:
                    parameters["arguments"][param_name] = "Could not encode"
                    log.warning("Failed to encode %r", param_name, exc_info=True)
            span.set_tag(test.PARAMETERS, json.dumps(parameters))

        markers = [marker.kwargs for marker in item.iter_markers(name="dd_tags")]
        for tags in markers:
            span.set_tags(tags)
        _store_span(item, span)

        if os.environ.get("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED") == "1":
            from ddtrace.internal.ci_visibility.coverage import cover

            with cover(span, root=str(item.config.rootdir)):
                yield
        else:
            yield


def _extract_reason(call):
    if call.excinfo is not None:
        return call.excinfo.value


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store outcome for tracing."""
    outcome = yield

    if not _CIVisibility.enabled:
        return

    span = _extract_span(item)
    if span is None:
        return

    is_setup_or_teardown = call.when == "setup" or call.when == "teardown"
    has_exception = call.excinfo is not None

    if is_setup_or_teardown and not has_exception:
        return

    result = outcome.get_result()
    xfail = hasattr(result, "wasxfail") or "xfail" in result.keywords
    has_skip_keyword = any(x in result.keywords for x in ["skip", "skipif", "skipped"])

    # If run with --runxfail flag, tests behave as if they were not marked with xfail,
    # that's why no XFAIL_REASON or test.RESULT tags will be added.
    if result.skipped:
        if xfail and not has_skip_keyword:
            # XFail tests that fail are recorded skipped by pytest, should be passed instead
            span.set_tag(test.STATUS, test.Status.PASS.value)
            if not item.config.option.runxfail:
                span.set_tag(test.RESULT, test.Status.XFAIL.value)
                span.set_tag(XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
        else:
            span.set_tag(test.STATUS, test.Status.SKIP.value)
        reason = _extract_reason(call)
        if reason is not None:
            span.set_tag(test.SKIP_REASON, reason)
    elif result.passed:
        span.set_tag(test.STATUS, test.Status.PASS.value)
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=False) are recorded passed by pytest
            span.set_tag(XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
            span.set_tag(test.RESULT, test.Status.XPASS.value)
    else:
        span.set_tag(test.STATUS, test.Status.FAIL.value)
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=True) are recorded failed by pytest, longrepr contains reason
            span.set_tag(XFAIL_REASON, getattr(result, "longrepr", "XFail"))
            span.set_tag(test.RESULT, test.Status.XPASS.value)
        if call.excinfo:
            span.set_exc_info(call.excinfo.type, call.excinfo.value, call.excinfo.tb)
