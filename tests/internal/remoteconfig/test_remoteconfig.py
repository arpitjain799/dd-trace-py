# -*- coding: utf-8 -*-
import base64
import datetime
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import warnings

import mock
import pytest
import tenacity

from ddtrace.internal.compat import PY2
from ddtrace.internal.remoteconfig import RemoteConfigPoller
from ddtrace.internal.remoteconfig import get_poll_interval_seconds
from ddtrace.internal.remoteconfig import remoteconfig_poller
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.constants import ASM_FEATURES_PRODUCT
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.vendor import psutil
from tests.internal.test_utils_version import _assert_and_get_version_agent_format
from tests.utils import override_env
from tests.webclient import Client


def to_bytes(string):
    if PY2:
        return bytes(string)
    else:
        return bytes(string, encoding="utf-8")


def to_str(bytes_string):
    if PY2:
        return str(bytes_string)
    else:
        return str(bytes_string, encoding="utf-8")


def get_mock_encoded_msg(msg):
    expires_date = datetime.datetime.strftime(
        datetime.datetime.now() + datetime.timedelta(days=1), "%Y-%m-%dT%H:%M:%SZ"
    )
    path = "datadog/2/%s/asm_features_activation/config" % ASM_FEATURES_PRODUCT
    data = {
        "signatures": [{"keyid": "", "sig": ""}],
        "signed": {
            "_type": "targets",
            "custom": {"opaque_backend_state": ""},
            "expires": expires_date,
            "spec_version": "1.0.0",
            "targets": {
                path: {
                    "custom": {"c": [""], "v": 0},
                    "hashes": {"sha256": hashlib.sha256(msg).hexdigest()},
                    "length": 24,
                }
            },
            "version": 0,
        },
    }
    return {
        "roots": [
            to_str(
                base64.b64encode(
                    to_bytes(
                        json.dumps(
                            {
                                "signatures": [],
                                "signed": {
                                    "_type": "root",
                                    "consistent_snapshot": True,
                                    "expires": "1986-12-11T00:00:00Z",
                                    "keys": {},
                                    "roles": {},
                                    "spec_version": "1.0",
                                    "version": 2,
                                },
                            }
                        ),
                    )
                )
            )
        ],
        "targets": to_str(base64.b64encode(to_bytes(json.dumps(data)))),
        "target_files": [
            {
                "path": path,
                "raw": to_str(base64.b64encode(msg)),
            }
        ],
        "client_configs": [path],
    }


@mock.patch.object(remoteconfig_poller, "_check_remote_config_enable_in_agent")
def test_remote_config_register_auto_enable(mock_check_remote_config_enable_in_agent):
    # ASM_FEATURES product is enabled by default, but LIVE_DEBUGGER isn't
    assert remoteconfig_poller._worker is None

    mock_check_remote_config_enable_in_agent.return_value = True
    remoteconfig_poller.register("LIVE_DEBUGGER", lambda m, c: None)

    assert remoteconfig_poller._client._products["LIVE_DEBUGGER"] is not None

    remoteconfig_poller.disable()

    assert remoteconfig_poller._worker is None


@pytest.mark.subprocess
def test_remote_config_forksafe():
    import mock

    from ddtrace.internal.remoteconfig import remoteconfig_poller

    with mock.patch.object(
        remoteconfig_poller, "_check_remote_config_enable_in_agent"
    ) as mock_check_remote_config_enable_in_agent:
        mock_check_remote_config_enable_in_agent.return_value = True

        import os

        remoteconfig_poller.enable()

        parent_worker = remoteconfig_poller._worker
        assert parent_worker is not None

        if os.fork() == 0:
            assert remoteconfig_poller._worker is not None
            assert remoteconfig_poller._worker is parent_worker
            exit(0)


@mock.patch.object(RemoteConfigClient, "_send_request")
@mock.patch.object(RemoteConfigPoller, "_check_remote_config_enable_in_agent")
def test_remote_configuration_1_click(mock_check_remote_config_enable_in_agent, mock_send_request):
    class Callback:
        features = {}

        def _reload_features(self, metadata, features):
            self.features = features

    callback = Callback()

    mock_check_remote_config_enable_in_agent.return_value = True
    mock_send_request.return_value = get_mock_encoded_msg(b'{"asm":{"enabled":true}}')
    remoteconfig_poller.register(ASM_FEATURES_PRODUCT, callback._reload_features)
    remoteconfig_poller.periodic()
    mock_send_request.assert_called_once()
    assert callback.features == {"asm": {"enabled": True}}


def test_remote_configuration_check_deprecated_var():
    with override_env(dict(DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS="0.1")):
        with warnings.catch_warnings(record=True) as capture:
            get_poll_interval_seconds()
            assert len(capture) == 0


def test_remote_configuration_check_deprecated_var_message():
    with override_env(dict(DD_REMOTECONFIG_POLL_SECONDS="0.1")):
        with warnings.catch_warnings(record=True) as capture:
            get_poll_interval_seconds()
            assert len(capture) == 1
            assert str(capture[0].message).startswith("Using environment")


def test_remote_configuration_check_deprecated_override():
    with override_env(dict(DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS="0.1", DD_REMOTECONFIG_POLL_SECONDS="0.5")):
        with warnings.catch_warnings(record=True) as capture:
            assert get_poll_interval_seconds() == 0.1
            assert len(capture) == 1
            assert str(capture[0].message).startswith("Using environment")


@mock.patch.object(RemoteConfigClient, "_send_request")
@mock.patch.object(RemoteConfigPoller, "_check_remote_config_enable_in_agent")
def test_remote_configuration_ip_blocking(mock_check_remote_config_enable_in_agent, mock_send_request):
    class Callback:
        features = {}

        def _reload_features(self, metadata, features):
            self.features = features

    callback = Callback()

    with override_env(dict(DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS="0.1")):
        mock_check_remote_config_enable_in_agent.return_value = True
        mock_send_request.return_value = get_mock_encoded_msg(
            b'{"rules_data": [{"data": [{"expiration": 1662804872, "value": "127.0.0.0"}, '
            b'{"expiration": 1662804872, "value": "52.80.198.1"}], "id": "blocking_ips", '
            b'"type": "ip_with_expiration"}]}'
        )
        remoteconfig_poller.register(ASM_FEATURES_PRODUCT, callback._reload_features)
        remoteconfig_poller.periodic()
        assert callback.features == {
            "rules_data": [
                {
                    "data": [
                        {"expiration": 1662804872, "value": "127.0.0.0"},
                        {"expiration": 1662804872, "value": "52.80.198.1"},
                    ],
                    "id": "blocking_ips",
                    "type": "ip_with_expiration",
                }
            ]
        }


def test_remoteconfig_semver():
    _assert_and_get_version_agent_format(RemoteConfigClient()._client_tracer["tracer_version"])


@pytest.mark.parametrize(
    "result,expected",
    [
        (None, False),
        ({}, False),
        ({"endpoints": []}, False),
        ({"endpoints": ["/info"]}, False),
        ({"endpoints": ["/info", "/errors"]}, False),
        ({"endpoints": ["/info", "/errors", REMOTE_CONFIG_AGENT_ENDPOINT]}, True),
        ({"endpoints": ["/info", "/errors", "/" + REMOTE_CONFIG_AGENT_ENDPOINT]}, True),
    ],
)
@mock.patch("ddtrace.internal.agent._healthcheck")
def test_remote_configuration_check_remote_config_enable_in_agent_errors(mock_healthcheck, result, expected):
    mock_healthcheck.return_value = result
    assert remoteconfig_poller._check_remote_config_enable_in_agent() is expected


def _count_running_processes(canary_str):
    found = 0
    for proc in psutil.process_iter():
        if canary_str in " ".join(proc.cmdline()):
            found += 1

    return found


@pytest.mark.skipif(sys.version_info < (3, 6, 0), reason="Python versions below 3.6 sort dictionaries differently")
def test_gevent_no_stuck_processes():  # type: () -> None
    port = 8000
    flask_app = "tests.internal.remoteconfig.stuck_test_app:app"
    gunicorn_cmd = "gunicorn -b 0.0.0.0:%s -w 3 -k gevent %s" % (port, flask_app)
    flask_env = os.environ.copy()
    flask_env.update(
        {
            # Avoid noisy database spans being output on app startup/teardown.
            "DD_TRACE_SQLITE3_ENABLED": "0",
            "DD_GEVENT_PATCH_ALL": "true",
            "DD_REMOTE_CONFIGURATION_ENABLED": "true",
        }
    )

    proc = subprocess.Popen(
        gunicorn_cmd.split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=flask_env,
        preexec_fn=os.setsid,
    )

    try:
        client = Client("http://0.0.0.0:%s" % port)
        # Wait for the server to start up
        try:
            client.wait(max_tries=5, path="/start", delay=0.2)
            client.wait(max_tries=5, delay=0.1)
        except tenacity.RetryError:
            # if proc.returncode is not None: process failed
            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
            raise TimeoutError(
                "Server failed to start\n======STDOUT=====%s\n\n======STDERR=====%s\n" % (stdout, stderr)
            )

        # Do a bunch of requests and check that the number of processes doesn't increase
        for i in range(20):
            client.get("/")

        time.sleep(2)
        try:
            nprocesses = _count_running_processes(gunicorn_cmd)
            assert nprocesses == 4

            client.get_ignored("/shutdown")
        except Exception:
            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
            print("Server failed to start\n======STDOUT=====%s\n\n======STDERR=====%s\n" % (stdout, stderr))
            raise

    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()

    nprocesses = _count_running_processes(gunicorn_cmd)
    assert nprocesses == 0
