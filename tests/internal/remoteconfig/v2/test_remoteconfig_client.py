# -*- coding: utf-8 -*-
import time

import mock
from mock.mock import MagicMock
import pytest

from ddtrace.internal.remoteconfig.v2.client import ConfigMetadata
from ddtrace.internal.remoteconfig.v2.client import PublisherListenerProxy
from ddtrace.internal.remoteconfig.v2.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.v2.client import RemoteConfigError
from ddtrace.internal.remoteconfig.v2.client import RemoteConfigPublisher
from ddtrace.internal.remoteconfig.v2.client import RemoteConfigPublisherAfterMerge
from ddtrace.internal.remoteconfig.v2.client import TargetFile


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_update_applied_configs(mock_extract_target_file):
    mock_config_content = {"test": "content"}
    mock_extract_target_file.return_value = mock_config_content
    mock_callback = MagicMock()
    mock_config = ConfigMetadata(id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5)

    applied_configs = {}
    payload = {}
    client_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_FEATURES", mock_callback)

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_extract_target_file.assert_called_with(payload, "mock/ASM_FEATURES", mock_config)
    mock_callback.publisher.assert_called_once_with(mock_config, mock_config_content)
    assert applied_configs == client_configs


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_dispatch_applied_configs(mock_extract_target_file):
    mock_callback = MagicMock()

    def _mock_appsec_callback(features):
        mock_callback(dict(features))

    class MockExtractFile:
        counter = 1

        def __call__(self, payload, target, config):
            self.counter += 1
            result = {"test{}".format(self.counter): [target]}
            expected_results.update(result)
            return result

    mock_extract_target_file.side_effect = MockExtractFile()

    expected_results = {}
    applied_configs = {}
    payload = {}
    client_configs = {
        "mock/ASM_FEATURES": ConfigMetadata(
            id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5
        ),
        "mock/ASM_DATA": ConfigMetadata(
            id="", product_name="ASM_DATA", sha256_hash="sha256_hash", length=5, tuf_version=5
        ),
    }

    asm_callback = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, _mock_appsec_callback)
    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_DATA", asm_callback)
    rc_client.register_product("ASM_FEATURES", asm_callback)
    asm_callback.listener.start()
    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)
    time.sleep(1)
    mock_callback.assert_called_once_with(expected_results)
    assert applied_configs == client_configs
    rc_client._products = {}
    asm_callback.listener.stop()


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_config_exists(mock_extract_target_file):
    mock_callback = MagicMock()
    mock_config = ConfigMetadata(id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5)

    applied_configs = {}
    payload = {}
    client_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_FEATURES", mock_callback)
    rc_client._applied_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_extract_target_file.assert_not_called()
    mock_callback.assert_not_called()
    assert applied_configs == {}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_error_extract_target_file(mock_extract_target_file):
    mock_extract_target_file.return_value = None
    mock_callback = MagicMock()
    mock_config = ConfigMetadata(id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5)

    applied_configs = {}
    payload = {}
    client_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_FEATURES", mock_callback)

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_extract_target_file.assert_called_with(payload, "mock/ASM_FEATURES", mock_config)
    mock_callback.assert_not_called()
    assert applied_configs == {}


@mock.patch.object(RemoteConfigClient, "_extract_target_file")
def test_load_new_configurations_error_callback(mock_extract_target_file):
    class RemoteConfigCallbackTestException(Exception):
        pass

    def exception_callback():
        raise RemoteConfigCallbackTestException("error")

    mock_config_content = {"test": "content"}
    mock_extract_target_file.return_value = mock_config_content
    mock_config = ConfigMetadata(id="", product_name="ASM_FEATURES", sha256_hash="sha256_hash", length=5, tuf_version=5)

    applied_configs = {}
    payload = {}
    client_configs = {"mock/ASM_FEATURES": mock_config}

    rc_client = RemoteConfigClient()
    rc_client.register_product("ASM_FEATURES", exception_callback)

    rc_client._load_new_configurations(applied_configs, client_configs, payload=payload)

    mock_extract_target_file.assert_called_with(payload, "mock/ASM_FEATURES", mock_config)

    # An exception prevents the configuration from being applied
    assert applied_configs["mock/ASM_FEATURES"].apply_state in (1, 3)


@pytest.mark.parametrize(
    "payload_client_configs,num_payload_target_files,cache_target_files,expected_result_ok",
    [
        (
            [
                "target/path/0",
            ],
            1,
            {},
            True,
        ),
        (
            [
                "target/path/0",
            ],
            3,
            {},
            True,
        ),
        (
            [
                "target/path/2",
            ],
            3,
            {},
            True,
        ),
        (
            [
                "target/path/6",
            ],
            3,
            {},
            False,
        ),
        (
            [
                "target/path/0",
            ],
            3,
            [{"path": "target/path/1"}],
            True,
        ),
        (
            [
                "target/path/0",
            ],
            3,
            [{"path": "target/path/1"}, {"path": "target/path/2"}],
            True,
        ),
        (
            [
                "target/path/1",
            ],
            0,
            [{"path": "target/path/1"}],
            True,
        ),
        (
            [
                "target/path/2",
            ],
            0,
            [{"path": "target/path/1"}],
            False,
        ),
        (
            [
                "target/path/0",
                "target/path/1",
            ],
            1,
            [{"path": "target/path/1"}],
            True,
        ),
        (["target/path/0", "target/path/1", "target/path/6"], 2, [{"path": "target/path/6"}], True),
    ],
)
def test_validate_config_exists_in_target_paths(
    payload_client_configs, num_payload_target_files, cache_target_files, expected_result_ok
):
    def build_payload_target_files(num_payloads):
        payload_target_files = []
        for i in range(num_payloads):
            mock = TargetFile(path="target/path/%s" % i, raw="")
            payload_target_files.append(mock)
        return payload_target_files

    rc_client = RemoteConfigClient()
    rc_client.cached_target_files = cache_target_files

    payload_target_files = build_payload_target_files(num_payload_target_files)

    if expected_result_ok:
        rc_client._validate_config_exists_in_target_paths(payload_client_configs, payload_target_files)
    else:
        with pytest.raises(RemoteConfigError):
            rc_client._validate_config_exists_in_target_paths(payload_client_configs, payload_target_files)


# @pytest.mark.subprocess(env={"DD_TAGS": "env:foo,version:bar"})
# def test_remote_config_client_tags():
#
#     from ddtrace.internal.remoteconfig.v2.client import RemoteConfigClient
#
#     tags = dict(_.split(":", 1) for _ in RemoteConfigClient()._client_tracer["tags"])
#
#     assert tags["env"] == "foo"
#     assert tags["version"] == "bar"


# @pytest.mark.subprocess(
#     env={"DD_TAGS": "env:foooverridden,version:baroverridden", "DD_ENV": "foo", "DD_VERSION": "bar"}
# )
# def test_remote_config_client_tags_override():
#
#     from ddtrace.internal.remoteconfig.v2.client import RemoteConfigClient
#
#     tags = dict(_.split(":", 1) for _ in RemoteConfigClient()._client_tracer["tags"])
#
#     assert tags["env"] == "foo"
#     assert tags["version"] == "bar"


def test_apply_default_callback():
    class callbackClass:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    callback_content = {"a": 1}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    callback = PublisherListenerProxy(RemoteConfigPublisher, None, callbackClass._mock_appsec_callback)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback, callback_content, target, config)
    callback.listener.start()
    time.sleep(1)
    assert callbackClass.result == {"a": 1}
    assert test_list_callbacks == []
    callback.listener.stop()


def test_apply_merge_callback():
    class callbackClass:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    callback_content = {"b": [1, 2, 3]}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    callback = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, callbackClass._mock_appsec_callback)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback, callback_content, target, config)
    callback.listener.start()
    for callback_to_dispach in test_list_callbacks:
        callback_to_dispach.dispatch()
    time.sleep(1)
    assert callbackClass.result == {"b": [1, 2, 3]}
    assert len(test_list_callbacks) > 0
    callback.listener.stop()


def test_apply_merge_multiple_callback():
    class callbackClass:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    callback1 = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, callbackClass._mock_appsec_callback)
    callback2 = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, callbackClass._mock_appsec_callback)
    callback_content1 = {"a": [1]}
    callback_content2 = {"b": [2]}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, target, config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content2, target, config)
    callback1.listener.start()
    callback2.listener.start()
    assert len(test_list_callbacks) == 1
    test_list_callbacks[0].dispatch()
    time.sleep(2)

    assert callbackClass.result == ({"a": [1], "b": [2]})
    callback1.listener.stop()
    callback2.listener.stop()


def test_apply_merge_different_callback():
    class Callback1And2Class:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    class Callback3Class:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    callback1 = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, Callback1And2Class._mock_appsec_callback)
    callback3 = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, Callback3Class._mock_appsec_callback)
    callback_content1 = {"a": [1]}
    callback_content2 = {"b": [2]}
    callback_content3 = {"c": [2]}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, target, config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content2, target, config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback3, callback_content3, target, config)
    callback1.listener.start()
    callback3.listener.start()
    assert len(test_list_callbacks) == 2
    test_list_callbacks[0].dispatch()
    test_list_callbacks[1].dispatch()
    time.sleep(2)

    assert Callback1And2Class.result == ({"a": [1], "b": [2]})
    assert Callback3Class.result == ({"c": [2]})
    callback1.listener.stop()
    callback3.listener.stop()


def test_apply_merge_different_target_callback():
    class Callback1And2Class:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    class Callback3Class:
        result = None

        @classmethod
        def _mock_appsec_callback(cls, *args, **kwargs):
            cls.result = dict(args[0])

    callback1 = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, Callback1And2Class._mock_appsec_callback)
    callback3 = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, Callback3Class._mock_appsec_callback)
    callback_content1 = {"a": [1]}
    callback_content2 = {"b": [2]}
    callback_content3 = {"b": [3]}
    target = "1/ASM/2"
    config = {"Config": "data"}
    test_list_callbacks = []
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content1, target, config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback1, callback_content2, target, config)
    RemoteConfigClient._apply_callback(test_list_callbacks, callback3, callback_content3, target, config)
    callback1.listener.start()
    callback3.listener.start()
    assert len(test_list_callbacks) == 2
    test_list_callbacks[0].dispatch()
    test_list_callbacks[1].dispatch()
    time.sleep(2)

    assert Callback1And2Class.result == ({"a": [1], "b": [2]})
    assert Callback3Class.result == ({"b": [3]})
    callback1.listener.stop()
    callback3.listener.stop()
