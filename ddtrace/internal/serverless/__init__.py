from os import environ
from os import path


def in_aws_lambda():
    # type: () -> bool
    """Returns whether the environment is an AWS Lambda.
    This is accomplished by checking if the AWS_LAMBDA_FUNCTION_NAME environment
    variable is defined.
    """
    return bool(environ.get("AWS_LAMBDA_FUNCTION_NAME", False))


def has_aws_lambda_agent_extension():
    # type: () -> bool
    """Returns whether the environment has the AWS Lambda Datadog Agent
    extension available.
    """
    return path.exists("/opt/extensions/datadog-agent")


def in_gcp_function():
    # type: () -> bool
    """Returns whether the environment is a GCP Function.
    This is accomplished by checking if the FUNCTION_NAME or K_SERVICE
    environment variables are defined.
    """
    has_function_name = bool(environ.get("FUNCTION_NAME", False))
    has_k_service = bool(environ.get("K_SERVICE", False))
    return has_function_name or has_k_service
