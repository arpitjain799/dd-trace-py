import logging
import os
from platform import python_version_tuple
import stat
from subprocess import Popen

from . import in_gcp_function
from ..logger import get_logger


log = get_logger(__name__)


def maybe_start_serverless_mini_agent():
    if not in_gcp_function():
        return

    rust_binary_path = os.getenv("DD_MINI_AGENT_PATH")
    if rust_binary_path is None:
        (major, minor, _) = python_version_tuple()
        rust_binary_path = (
            "/layers/google.python.pip/pip/lib/python"
            + major
            + "."
            + minor
            + "/site-packages/datadog-serverless-agent-linux-amd64/datadog-serverless-trace-mini-agent"
        )
    
    st = os.stat(rust_binary_path)
    os.chmod(rust_binary_path, st.st_mode | stat.S_IEXEC)

    try:
        Popen(rust_binary_path)
    except Exception as e:
        log.log(logging.ERROR, "Error spawning Serverless Mini Agent process: %s. Mini Agent binary path: %s", repr(e), rust_binary_path)
