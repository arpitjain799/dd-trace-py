import logging
import os
from platform import python_version_tuple
from subprocess import Popen

from . import in_gcp_function
from ..logger import get_logger


log = get_logger(__name__)


def maybe_start_serverless_mini_agent():
    rust_binary_path = os.getenv("DD_MINI_AGENT_PATH")
    if rust_binary_path is None:
        (major, minor, _) = python_version_tuple()
        rust_binary_path = (
            "/workspace/venv/lib/python"
            + major
            + "."
            + minor
            + "/datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
        )
    if not in_gcp_function():
        return

    try:
        Popen(rust_binary_path)
    except Exception as e:
        log.log(logging.ERROR, "Error spawning Serverless Mini Agent process: %s", repr(e))
