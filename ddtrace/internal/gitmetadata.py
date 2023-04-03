import typing

from envier import Env

from ddtrace.ext.git import COMMIT_SHA
from ddtrace.ext.git import REPOSITORY_URL
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import formats


_GITMETADATA_TAGS = None  # type: typing.Optional[typing.Tuple[str, str]]

log = get_logger(__name__)


class GitMetadataConfig(Env):
    __prefix__ = "dd"

    # DD_TRACE_GIT_METADATA_ENABLED
    enabled = Env.var(bool, "trace_git_metadata_enabled", default=True)

    # DD_GIT_REPOSITORY_URL
    repository_url = Env.var(str, "git_repository_url", default="")

    # DD_GIT_COMMIT_SHA
    commit_sha = Env.var(str, "git_commit_sha", default="")

    # DD_MAIN_PACKAGE
    main_package = Env.var(str, "main_package", default="")

    # DD_TAGS
    tags = Env.var(str, "tags", default="")


def _get_tags_from_env(config):
    # type: (GitMetadataConfig) -> typing.Tuple[str, str]
    """
    Get git metadata from environment variables.
    Returns tuple (repository_url, commit_sha)
    """
    repository_url = config.repository_url
    commit_sha = config.commit_sha

    tags = formats.parse_tags_str(config.tags)
    if not repository_url:
        repository_url = tags.get(REPOSITORY_URL, "")
    if not commit_sha:
        commit_sha = tags.get(COMMIT_SHA, "")

    return repository_url, commit_sha


def _get_tags_from_package(config):
    # type: (GitMetadataConfig) -> typing.Tuple[str, str]
    """
    Extracts git metadata from python package's medatada field Project-URL:
    e.g: Project-URL: source_code_link, https://github.com/user/repo#gitcommitsha&someoptions
    Returns tuple (repository_url, commit_sha)
    """
    if not config.main_package:
        return "", ""
    try:
        try:
            import importlib.metadata as importlib_metadata
        except ImportError:
            import importlib_metadata  # type: ignore[no-redef]

        source_code_link = ""
        for val in importlib_metadata.metadata(config.main_package).get_all("Project-URL"):
            capt_val = val.split(", ")
            if len(capt_val) > 1 and capt_val[0] == "source_code_link":
                source_code_link = capt_val[1].strip()
                break

        if source_code_link and "#" in source_code_link:
            repository_url, commit_sha = source_code_link.split("#")
            commit_sha = commit_sha.split("&")[0]
            return repository_url, commit_sha
        return "", ""
    except importlib_metadata.PackageNotFoundError:
        return "", ""


def get_git_tags():
    # type: () -> typing.Tuple[str, str]
    """
    Returns git metadata tags tuple (repository_url, commit_sha)
    """
    try:
        global _GITMETADATA_TAGS
        if _GITMETADATA_TAGS is not None:
            return _GITMETADATA_TAGS

        config = GitMetadataConfig()

        if config.enabled:
            repository_url, commit_sha = _get_tags_from_env(config)
            log.debug("git tags from env: %s %s", repository_url, commit_sha)
            if not repository_url or not commit_sha:
                pkg_repository_url, pkg_commit_sha = _get_tags_from_package(config)
                log.debug("git tags from package: %s %s", pkg_repository_url, pkg_commit_sha)
                if not repository_url:
                    repository_url = pkg_repository_url
                if not commit_sha:
                    commit_sha = pkg_commit_sha

            log.debug("git tags: %s %s", repository_url, commit_sha)
            _GITMETADATA_TAGS = repository_url, commit_sha
        else:
            log.debug("git tags disabled")
            _GITMETADATA_TAGS = ("", "")
        return _GITMETADATA_TAGS
    except Exception:
        log.debug("git tags failed", exc_info=True)
        return "", ""


def clean_tags(tags):
    # type: (typing.Dict[str, str]) -> typing.Dict[str, str]
    """
    Cleanup tags from git metadata
    """
    tags.pop(REPOSITORY_URL, None)
    tags.pop(COMMIT_SHA, None)

    return tags
