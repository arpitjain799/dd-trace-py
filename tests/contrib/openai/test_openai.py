import os
import sys
from typing import AsyncGenerator
from typing import Generator
from typing import List
from typing import Optional

import mock
import pytest
import vcr

import ddtrace
from ddtrace import Pin
from ddtrace import Span
from ddtrace import patch
from ddtrace.contrib.openai import _patch
from ddtrace.contrib.openai.patch import unpatch
from ddtrace.contrib.trace_utils import iswrapped
from ddtrace.filters import TraceFilter
from tests.utils import DummyTracer
from tests.utils import override_config
from tests.utils import override_global_config


# VCR is used to capture and store network requests made to OpenAI.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.

# To (re)-generate the cassettes: pass a real OpenAI API key with
# OPENAI_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_openai_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@pytest.fixture(scope="session")
def openai_vcr():
    yield get_openai_vcr()


@pytest.fixture
def openai_api_key():
    return "<not-a-real-key>"


@pytest.fixture
def openai_organization():
    return None


@pytest.fixture
def openai(openai_api_key, openai_organization):
    import openai

    openai.api_key = openai_api_key
    openai.organization = openai_organization
    yield openai
    # Bad hack:
    #   Since unpatching doesn't work (there's a bug with unwrapping the class methods
    #   see the OpenAI unpatch() method for the error), wipe out all the OpenAI modules
    #   so that state is reset for each test case.
    mods = list(k for k in sys.modules.keys() if k.startswith("openai"))
    for m in mods:
        del sys.modules[m]


class FilterOrg(TraceFilter):
    """Replace the organization tag on spans with fake data."""

    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        for span in trace:
            if span.get_tag("organization"):
                span.set_tag_str("organization", "not-a-real-org")
        return trace


@pytest.fixture(scope="session")
def mock_metrics():
    patcher = mock.patch("ddtrace.contrib.openai._patch.get_dogstatsd_client")
    DogStatsdMock = patcher.start()
    m = mock.MagicMock()
    DogStatsdMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def mock_logs(scope="session"):
    """
    Note that this fixture must be ordered BEFORE mock_tracer as it needs to patch the log writer
    before it is instantiated.
    """
    patcher = mock.patch("ddtrace.contrib.openai._patch.V2LogWriter")
    V2LogWriterMock = patcher.start()
    m = mock.MagicMock()
    V2LogWriterMock.return_value = m
    yield m
    patcher.stop()


@pytest.fixture
def ddtrace_config_openai():
    config = {}
    return config


@pytest.fixture
def patch_openai(ddtrace_config_openai):
    with override_config("openai", ddtrace_config_openai):
        patch(openai=True)
        yield
        unpatch()


@pytest.fixture
def snapshot_tracer(openai, patch_openai, mock_logs, mock_metrics):
    pin = Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    yield

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.fixture
def mock_tracer(openai, patch_openai, mock_logs, mock_metrics):
    pin = Pin.get_from(openai)
    mock_tracer = DummyTracer()
    pin.override(openai, tracer=mock_tracer)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    yield mock_tracer

    mock_logs.reset_mock()
    mock_metrics.reset_mock()


@pytest.mark.parametrize("ddtrace_config_openai", [dict(metrics_enabled=True), dict(metrics_enabled=False)])
def test_config(ddtrace_config_openai, mock_tracer, openai):
    # Ensure that the state is refreshed on each test run
    assert not hasattr(openai, "_test")
    openai._test = 1

    # Ensure overriding the config works
    assert ddtrace.config.openai.metrics_enabled is ddtrace_config_openai["metrics_enabled"]


def test_patching(openai):
    """Ensure that the correct objects are patched and not double patched."""

    # for some reason these can't be specified as the real python objects...
    # no clue why (eg. openai.Completion.create doesn't work)
    methods = [
        (openai.Completion, "create"),
        (openai.api_resources.completion.Completion, "create"),
        (openai.Completion, "acreate"),
        (openai.api_resources.completion.Completion, "acreate"),
        (openai.api_requestor, "_make_session"),
        (openai.util, "convert_to_openai_object"),
        (openai.Embedding, "create"),
        (openai.Embedding, "acreate"),
    ]
    if hasattr(openai, "ChatCompletion"):
        methods += [
            (openai.ChatCompletion, "create"),
            (openai.api_resources.chat_completion.ChatCompletion, "create"),
            (openai.ChatCompletion, "acreate"),
            (openai.api_resources.chat_completion.ChatCompletion, "acreate"),
        ]

    for m in methods:
        assert not iswrapped(getattr(m[0], m[1]))

    patch(openai=True)
    for m in methods:
        assert iswrapped(getattr(m[0], m[1]))

    # Ensure double patching does not occur
    patch(openai=True)
    for m in methods:
        assert not iswrapped(getattr(m[0], m[1]).__wrapped__)


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_completion(openai, openai_vcr, mock_metrics, snapshot_tracer):
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "model:ada",
        "endpoint:completions",
        "organization.id:",
        "organization.name:datadog-4",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution(
                "tokens.prompt",
                2,
                tags=expected_tags,
            ),
            mock.call.distribution(
                "tokens.completion",
                12,
                tags=expected_tags,
            ),
            mock.call.distribution(
                "tokens.total",
                14,
                tags=expected_tags,
            ),
            mock.call.distribution(
                "request.duration",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.remaining.requests",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.requests",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.remaining.tokens",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.tokens",
                mock.ANY,
                tags=expected_tags,
            ),
        ],
        any_order=True,
    )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
async def test_acompletion(openai, openai_vcr, mock_metrics, mock_logs, snapshot_tracer):
    with openai_vcr.use_cassette("completion_async.yaml"):
        await openai.Completion.acreate(
            model="curie", prompt="As Descartes said, I think, therefore", temperature=0.8, n=1, max_tokens=150
        )
    expected_tags = [
        "version:",
        "env:",
        "service:",
        "model:curie",
        "endpoint:completions",
        "organization.id:",
        "organization.name:datadog-4",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution(
                "tokens.prompt",
                10,
                tags=expected_tags,
            ),
            mock.call.distribution(
                "tokens.completion",
                150,
                tags=expected_tags,
            ),
            mock.call.distribution(
                "tokens.total",
                160,
                tags=expected_tags,
            ),
            mock.call.distribution(
                "request.duration",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.remaining.requests",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.requests",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.remaining.tokens",
                mock.ANY,
                tags=expected_tags,
            ),
            mock.call.gauge(
                "ratelimit.tokens",
                mock.ANY,
                tags=expected_tags,
            ),
        ],
        any_order=True,
    )
    mock_logs.assert_not_called()


@pytest.mark.xfail(reason="An API key is required when logs are enabled")
@pytest.mark.parametrize("ddtrace_config_openai", [dict(_api_key="", logs_enabled=True)])
def test_logs_no_api_key(openai, ddtrace_config_openai, mock_tracer):
    """When no DD_API_KEY is set, the patching fails"""
    pass


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [
        # Default service, env, version
        dict(
            _api_key="<not-real-but-it's-something>",
            logs_enabled=True,
            log_prompt_completion_sample_rate=1.0,
        ),
    ],
)
def test_logs_completions(openai_vcr, openai, ddtrace_config_openai, mock_logs, mock_tracer):
    """Ensure logs are emitted for completion endpoints when configured.

    Also ensure the logs have the correct tagging including the trace-logs correlation tagging.
    """
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)
    span = mock_tracer.pop_traces()[0][0]
    trace_id, span_id = span.trace_id, span.span_id

    assert mock_logs.enqueue.call_count == 1
    mock_logs.assert_has_calls(
        [
            mock.call.start(),
            mock.call.enqueue(
                {
                    "timestamp": mock.ANY,
                    "message": mock.ANY,
                    "hostname": mock.ANY,
                    "ddsource": "openai",
                    "service": "",
                    "status": "info",
                    "ddtags": "env:,version:,endpoint:completions,model:ada,organization.name:datadog-4",
                    "dd.trace_id": str(trace_id),
                    "dd.span_id": str(span_id),
                    "prompt": "Hello world",
                    "choices": mock.ANY,
                }
            ),
        ]
    )


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [dict(_api_key="<not-real-but-it's-something>", logs_enabled=True, log_prompt_completion_sample_rate=1.0)],
)
def test_global_tags(openai_vcr, ddtrace_config_openai, openai, mock_metrics, mock_logs, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data

    All data should also be tagged with the same OpenAI data.
    """
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        with openai_vcr.use_cassette("completion.yaml"):
            openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)

    span = mock_tracer.pop_traces()[0][0]
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("model") == "ada"
    assert span.get_tag("endpoint") == "completions"
    assert span.get_tag("organization.name") == "datadog-4"

    for _, args, kwargs in mock_metrics.mock_calls:
        expected_metrics = [
            "service:test-svc",
            "env:staging",
            "version:1234",
            "model:ada",
            "endpoint:completions",
            "organization.name:datadog-4",
        ]
        actual_tags = kwargs.get("tags")
        for m in expected_metrics:
            assert m in actual_tags

    for call, args, kwargs in mock_logs.mock_calls:
        if call != "enqueue":
            continue
        log = args[0]
        assert log["service"] == "test-svc"
        assert log["ddtags"] == "env:staging,version:1234,endpoint:completions,model:ada,organization.name:datadog-4"


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_chat_completion(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")

    with openai_vcr.use_cassette("chat_completion.yaml"):
        openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Who won the world series in 2020?"},
                {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
                {"role": "user", "content": "Where was it played?"},
            ],
            top_p=0.9,
            n=2,
        )


@pytest.mark.parametrize("ddtrace_config_openai", [dict(metrics_enabled=b) for b in [True, False]])
def test_enable_metrics(openai, openai_vcr, ddtrace_config_openai, mock_metrics, mock_tracer):
    """Ensure the metrics_enabled configuration works."""
    with openai_vcr.use_cassette("completion.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)
    if ddtrace_config_openai["metrics_enabled"]:
        assert mock_metrics.mock_calls
    else:
        assert not mock_metrics.mock_calls


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
async def test_achat_completion(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    with openai_vcr.use_cassette("chat_completion_async.yaml"):
        await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Who won the world series in 2020?"},
                {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
                {"role": "user", "content": "Where was it played?"},
            ],
            top_p=0.9,
            n=2,
        )


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(
    not hasattr(openai, "Embedding"),
    reason="embedding not supported for this version of openai",
)
def test_embedding(openai, openai_vcr):
    with openai_vcr.use_cassette("embedding.yaml"):
        openai.Embedding.create(input="hello world", model="text-embedding-ada-002")


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(
    not hasattr(openai, "Embedding"),
    reason="embedding not supported for this version of openai",
)
async def test_aembedding(openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("embedding_async.yaml"):
        await openai.Embedding.acreate(input="hello world", model="text-embedding-ada-002")


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.skipif(not hasattr(openai, "Moderation"), reason="moderation not supported for this version of openai")
def test_unsupported(openai, openai_vcr, snapshot_tracer):
    # no openai spans expected
    with openai_vcr.use_cassette("moderation.yaml"):
        openai.Moderation.create(
            input="Here is some perfectly innocuous text that follows all OpenAI content policies."
        )


@pytest.mark.snapshot(ignores=["meta.http.useragent", "meta.error.stack"])
@pytest.mark.skipif(not hasattr(openai, "Completion"), reason="completion not supported for this version of openai")
def test_misuse(openai):
    with pytest.raises(openai.error.InvalidRequestError):
        openai.Completion.create(input="wrong arg")


def test_completion_stream(openai, openai_vcr, mock_metrics, mock_tracer):
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        resp = openai.Completion.create(model="ada", prompt="Hello world", stream=True)
        assert isinstance(resp, Generator)
        chunks = [c for c in resp]

    completion = "".join([c["choices"][0]["text"] for c in chunks])
    assert completion == '! ... A page layouts page drawer? ... Interesting. The "Tools" is'

    traces = mock_tracer.pop_traces()
    assert len(traces) == 2
    t1, t2 = traces
    assert len(t1) == len(t2) == 1
    assert t2[0].parent_id == t1[0].span_id

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "model:ada",
        "endpoint:completions",
        "organization.id:",
        "organization.name:",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution(
                "tokens.prompt",
                2,
                tags=expected_tags,
            ),
            mock.call.distribution(
                "tokens.completion",
                len(chunks),
                tags=expected_tags,
            ),
            mock.call.distribution(
                "tokens.total",
                len(chunks) + 2,
                tags=expected_tags,
            ),
        ],
        any_order=True,
    )


@pytest.mark.asyncio
async def test_completion_async_stream(openai, openai_vcr, mock_metrics, mock_tracer):
    with openai_vcr.use_cassette("completion_async_streamed.yaml"):
        resp = await openai.Completion.acreate(model="ada", prompt="Hello world", stream=True)
        assert isinstance(resp, AsyncGenerator)
        chunks = [c async for c in resp]

    completion = "".join([c["choices"][0]["text"] for c in chunks])
    assert completion == "\" and just start creating stuff. Don't expect it to draw like this."

    traces = mock_tracer.pop_traces()
    assert len(traces) == 2
    t1, t2 = traces
    assert len(t1) == len(t2) == 1
    assert t2[0].parent_id == t1[0].span_id

    expected_tags = [
        "version:",
        "env:",
        "service:",
        "model:ada",
        "endpoint:completions",
        "organization.id:",
        "organization.name:",
        "error:0",
    ]
    mock_metrics.assert_has_calls(
        [
            mock.call.distribution(
                "tokens.prompt",
                2,
                tags=expected_tags,
            ),
            mock.call.distribution(
                "tokens.completion",
                len(chunks),
                tags=expected_tags,
            ),
            mock.call.distribution(
                "tokens.total",
                len(chunks) + 2,
                tags=expected_tags,
            ),
        ],
        any_order=True,
    )


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_chat_completion_stream(openai, openai_vcr, snapshot_tracer):
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")

    with openai_vcr.use_cassette("chat_completion_streamed.yaml"):
        openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Who won the world series in 2020?"},
            ],
            stream=True,
        )


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.subprocess(ddtrace_run=True)
def test_integration_sync():
    """OpenAI uses requests for its synchronous requests.

    Running in a subprocess with ddtrace-run should produce traces
    with both OpenAI and requests spans.
    """
    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import FilterOrg
    from tests.contrib.openai.test_openai import get_openai_vcr

    pin = ddtrace.Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    with get_openai_vcr().use_cassette("completion_2.yaml"):
        openai.Completion.create(model="ada", prompt="hello world")


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
@pytest.mark.subprocess(ddtrace_run=True)
# FIXME: 'aiohttp.request', 'TCPConnector.connect' on second
# run of the test, might do with cassettes
def test_integration_async():
    """OpenAI uses requests for its synchronous requests.

    Running in a subprocess with ddtrace-run should produce traces
    with both OpenAI and requests spans.
    """
    import asyncio

    import openai

    import ddtrace
    from tests.contrib.openai.test_openai import FilterOrg
    from tests.contrib.openai.test_openai import get_openai_vcr

    pin = ddtrace.Pin.get_from(openai)
    pin.tracer.configure(settings={"FILTERS": [FilterOrg()]})

    async def task():
        with get_openai_vcr().use_cassette("acompletion_2.yaml"):
            await openai.Completion.acreate(model="ada", prompt="hello world")

    asyncio.run(task())


@pytest.mark.parametrize(
    "ddtrace_config_openai", [dict(span_prompt_completion_sample_rate=r) for r in [0, 0.25, 0.75, 1]]
)
def test_completion_sample(openai, openai_vcr, ddtrace_config_openai, mock_tracer):
    """Test functionality for DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE for completions endpoint"""
    num_completions = 100

    for _ in range(num_completions):
        with openai_vcr.use_cassette("completion_sample_rate.yaml"):
            openai.Completion.create(model="ada", prompt="hello world")

    traces = mock_tracer.pop_traces()
    sampled = 0
    assert len(traces) == 100, len(traces)
    for trace in traces:
        for span in trace:
            if span.get_tag("response.choices.0.text"):
                sampled += 1
    if ddtrace.config.openai.span_prompt_completion_sample_rate == 0:
        assert sampled == 0
    elif ddtrace.config.openai.span_prompt_completion_sample_rate == 1:
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = ddtrace.config.openai["span_prompt_completion_sample_rate"] * num_completions
        assert (rate - 15) < sampled < (rate + 15)


@pytest.mark.parametrize(
    "ddtrace_config_openai", [dict(span_prompt_completion_sample_rate=r) for r in [0, 0.25, 0.75, 1]]
)
def test_chat_completion_sample(openai, openai_vcr, ddtrace_config_openai, mock_tracer):
    """Test functionality for DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE for chat completions endpoint"""
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")
    num_completions = 100

    for _ in range(num_completions):
        with openai_vcr.use_cassette("chat_completion_sample_rate.yaml"):
            openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "what is your name?"},
                ],
            )

    traces = mock_tracer.pop_traces()
    sampled = 0
    assert len(traces) == num_completions
    for trace in traces:
        for span in trace:
            if span.get_tag("response.choices.0.message.role"):
                sampled += 1
    if ddtrace.config.openai["span_prompt_completion_sample_rate"] == 0:
        assert sampled == 0
    elif ddtrace.config.openai["span_prompt_completion_sample_rate"] == 1:
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = ddtrace.config.openai["span_prompt_completion_sample_rate"] * num_completions
        assert (rate - 15) < sampled < (rate + 15)


@pytest.mark.parametrize("ddtrace_config_openai", [dict(truncation_threshold=t) for t in [0, 10, 10000]])
def test_completion_truncation(openai, openai_vcr, mock_tracer):
    """Test functionality of DD_OPENAI_TRUNCATION_THRESHOLD for completions"""
    if not hasattr(openai, "ChatCompletion"):
        pytest.skip("ChatCompletion not supported for this version of openai")

    prompt = "1, 2, 3, 4, 5, 6, 7, 8, 9, 10"

    with openai_vcr.use_cassette("completion_truncation.yaml"):
        openai.Completion.create(model="ada", prompt=prompt)

    with openai_vcr.use_cassette("chat_completion_truncation.yaml"):
        openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Count from 1 to 100"},
            ],
        )

    traces = mock_tracer.pop_traces()
    assert len(traces) == 2

    limit = ddtrace.config.openai["span_char_limit"]
    for trace in traces:
        for span in trace:
            if span.get_tag("endpoint") == "completions":
                prompt = span.get_tag("request.prompt")
                completion = span.get_tag("response.choices.0.text")
                # +3 for the ellipsis
                assert len(prompt) <= limit + 3
                assert len(completion) <= limit + 3
                if "..." in prompt:
                    assert len(prompt.replace("...", "")) == limit
                if "..." in completion:
                    assert len(completion.replace("...", "")) == limit
            else:
                prompt = span.get_tag("request.messages.0.content")
                completion = span.get_tag("response.choices.0.message.content")
                assert len(prompt) <= limit + 3
                assert len(completion) <= limit + 3
                if "..." in prompt:
                    assert len(prompt.replace("...", "")) == limit
                if "..." in completion:
                    assert len(completion.replace("...", "")) == limit


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [
        dict(
            _api_key="<not-real-but-it's-something>",
            logs_enabled=True,
            log_prompt_completion_sample_rate=r,
        )
        for r in [0, 0.25, 0.75, 1]
    ],
)
def test_logs_sample_rate(openai, openai_vcr, ddtrace_config_openai, mock_logs, mock_tracer):
    total_calls = 100
    for _ in range(total_calls):
        with openai_vcr.use_cassette("completion.yaml"):
            openai.Completion.create(model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10)

    logs = mock_logs.enqueue.call_count
    if ddtrace.config.openai["log_prompt_completion_sample_rate"] == 0:
        assert logs == 0
    elif ddtrace.config.openai["log_prompt_completion_sample_rate"] == 1:
        assert logs == total_calls
    else:
        print(logs)
        rate = ddtrace.config.openai["log_prompt_completion_sample_rate"] * total_calls
        assert (rate - 15) < logs < (rate + 15)


def test_est_tokens():
    """
    Oracle numbers come from https://platform.openai.com/tokenizer
    """
    est = _patch._est_tokens
    assert est("hello world") == 2
    assert est("Hello world, how are you?") == 7 - 2
    assert est("hello") == 1
    assert est("") == 0
    assert (
        est(
            """
    A helpful rule of thumb is that one token generally corresponds to ~4 characters of text for common English text. This translates to roughly ¾ of a word (so 100 tokens ~= 75 words).

If you need a programmatic interface for tokenizing text, check out our tiktoken package for Python. For JavaScript, the gpt-3-encoder package for node.js works for most GPT-3 models."""
        )
        == 75
    )
