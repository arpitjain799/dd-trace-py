import os

import mock

from ddtrace.internal.serverless.mini_agent import maybe_start_serverless_mini_agent


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_dont_spawn_mini_agent_if_not_cloud_function(mock_popen):
    os.environ["DD_MINI_AGENT_PATH"] = "fake_path"

    maybe_start_serverless_mini_agent()

    mock_popen.assert_not_called()

    del os.environ["DD_MINI_AGENT_PATH"]


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_dont_spawn_mini_agent_if_no_mini_agent_path(mock_popen):
    os.environ["K_SERVICE"] = "test_function"
    maybe_start_serverless_mini_agent()

    mock_popen.assert_not_called()

    del os.environ["K_SERVICE"]


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_spawn_mini_agent_if_FUNCTION_NAME_declared(mock_popen):
    os.environ["DD_MINI_AGENT_PATH"] = "fake_path"
    os.environ["FUNCTION_NAME"] = "test_function"

    maybe_start_serverless_mini_agent()

    mock_popen.assert_called_once()

    del os.environ["DD_MINI_AGENT_PATH"]
    del os.environ["FUNCTION_NAME"]


@mock.patch("ddtrace.internal.serverless.mini_agent.Popen")
def test_spawn_mini_agent_if_K_SERVICE_declared(mock_popen):
    os.environ["DD_MINI_AGENT_PATH"] = "fake_path"
    os.environ["K_SERVICE"] = "test_function"

    maybe_start_serverless_mini_agent()

    mock_popen.assert_called_once()

    del os.environ["DD_MINI_AGENT_PATH"]
    del os.environ["K_SERVICE"]
