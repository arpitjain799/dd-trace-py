import logging
import os
from subprocess import Popen

from . import in_gcp_function
from ..logger import get_logger


log = get_logger(__name__)


def maybe_start_serverless_mini_agent():
    rust_binary_path = os.getenv("DD_MINI_AGENT_PATH")
    if not in_gcp_function():
        return
    if not rust_binary_path:
        log.log(
            logging.ERROR,
            "Serverless Mini Agent did not start. Please provide a DD_MINI_AGENT_PATH environment variable.",
        )
        return

    try:
        Popen(rust_binary_path)
    except Exception as e:
        log.log(logging.ERROR, "Error spawning Serverless Mini Agent process: %s", repr(e))
