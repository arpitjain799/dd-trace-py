#!/usr/bin/env python3
import pytest
from six import PY2


if not PY2:
    import astunparse

    from ddtrace.appsec.iast._ast.ast_patching import astpatch_source
    from ddtrace.appsec.iast._ast.ast_patching import visit_ast

from ddtrace.appsec.iast._ast.ast_patching import _should_iast_patch


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print('hi')", "test.py", "test"),
        ("print('str')", "test.py", "test"),
        ("str", "test.py", "test"),
        ("print('hi' + 'bye')", "test.py", "test"),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_visit_ast_unchanged(source_text, module_path, module_name):
    """
    Source texts not containing:
    - str() calls
    - [...]  // To be filled with more aspects
    won't be modified by ast patching, so will return empty string
    """
    assert visit_ast(source_text, module_path, module_name) is None


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print(str('hi'))", "test.py", "test"),
        ("print(str('hi' + 'bye'))", "test.py", "test"),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_visit_ast_changed(source_text, module_path, module_name):
    """
    Source texts containing:
    - str() calls
    - [...]  // To be filled with more aspects
    will be modified by ast patching, so will not return empty string
    """
    assert visit_ast(source_text, module_path, module_name) is not None


@pytest.mark.parametrize(
    "module_path, module_name",
    [
        ("tests/appsec/iast/fixtures/aspects/str/function_str.py", "function_str"),
        ("tests/appsec/iast/fixtures/aspects/str/class_str.py", "class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.function_str"),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_astpatch_source_changed(module_path, module_name):
    module_path, new_source = astpatch_source(module_name, module_path)
    assert ("", "") != (module_path, new_source)
    new_code = astunparse.unparse(new_source)
    assert new_code.startswith("\nimport ddtrace.appsec.iast._ast.aspects as ddtrace_aspects")
    assert "ddtrace_aspects.str_aspect(" in new_code


@pytest.mark.parametrize(
    "module_path, module_name",
    [
        ("tests/appsec/iast/fixtures/aspects/str/future_import_function_str.py", "function_str"),
        ("tests/appsec/iast/fixtures/aspects/str/future_import_class_str.py", "class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.future_import_class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.future_import_function_str"),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_astpatch_source_changed_with_future_imports(module_path, module_name):
    module_path, new_source = astpatch_source(module_name, module_path)
    assert ("", "") != (module_path, new_source)
    new_code = astunparse.unparse(new_source)
    assert new_code.startswith(
        """
from __future__ import absolute_import
from __future__ import annotations
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import ddtrace.appsec.iast._ast.aspects as ddtrace_aspects
import html"""
    )
    assert "ddtrace_aspects.str_aspect(" in new_code


@pytest.mark.parametrize(
    "module_path, module_name",
    [
        ("tests/appsec/iast/fixtures/aspects/str/function_no_str.py", "function_str"),
        ("tests/appsec/iast/fixtures/aspects/str/class_no_str.py", "class_str"),
        ("tests/appsec/iast/fixtures/aspects/str/non_existent_file.py", "class_str"),
        ("tests/appsec/iast/fixtures/aspects/str/invented_extension.cppy", "class_str"),
        ("tests/appsec/iast/fixtures/aspects/str/empty_file.py", "class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.class_no_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.function_no_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str"),  # Empty __init__.py
        (None, "tests.appsec.iast.fixtures.aspects.str.non_utf8_content"),  # EUC-JP file content
        (None, None),
    ],
)
@pytest.mark.skipif(PY2, reason="Python 3 only")
def test_astpatch_source_unchanged(module_path, module_name):
    assert ("", "") == astpatch_source(module_name, module_path)


def test_module_should_iast_patch():
    assert not _should_iast_patch("ddtrace.internal.module")
    assert not _should_iast_patch("ddtrace.appsec.iast")
    assert not _should_iast_patch("django")
    assert not _should_iast_patch("Flask")
    assert not _should_iast_patch("http")
    assert _should_iast_patch("tests.appsec.iast.integration.main")
    assert _should_iast_patch("tests.appsec.iast.integration.print_str")
