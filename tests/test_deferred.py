"""Tests for deferred.

Notes
-----
A proxy's presence in a namespace is checked via stringifying the namespace and then substring matching with the
expected proxy repr, as that's the only way to inspect it without causing it to resolve.
"""

import ast
import importlib.util
import io
import sys
import tokenize
from pathlib import Path
from typing import cast

import pytest

from deferred._core import (
    DEFERRED_PATH_HOOK,
    DeferredFileLoader,
    DeferredInstrumenter,
    install_defer_import_hook,
    uninstall_defer_import_hook,
)


def create_sample_module(path: Path, source: str, loader_type: type):
    """Utility function for creating a sample module with the given path, source code, and loader."""

    tmp_file = path / "sample.py"
    tmp_file.write_text(source, encoding="utf-8")

    module_name = "sample"
    module_path = tmp_file.resolve()

    loader = loader_type(module_name, str(module_path))
    spec = importlib.util.spec_from_file_location(module_name, module_path, loader=loader)
    assert spec
    return spec, importlib.util.module_from_spec(spec)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(
            """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    import inspect
""",
            """\
from deferred._core import DeferredImportKey as @DeferredImportKey, DeferredImportProxy as @DeferredImportProxy
from deferred import defer_imports_until_use
with defer_imports_until_use:
    @local_ns = locals()
    @temp_proxy = None
    import inspect
    if type(inspect) is @DeferredImportProxy:
        @temp_proxy = @local_ns.pop('inspect')
        @local_ns[@DeferredImportKey('inspect', @temp_proxy)] = @temp_proxy
    del @temp_proxy
    del @local_ns
del @DeferredImportKey
del @DeferredImportProxy
""",
            id="regular import",
        ),
        pytest.param(
            """\
from deferred import defer_imports_until_use


with defer_imports_until_use:
    import importlib
    import importlib.abc
""",
            """\
from deferred._core import DeferredImportKey as @DeferredImportKey, DeferredImportProxy as @DeferredImportProxy
from deferred import defer_imports_until_use
with defer_imports_until_use:
    @local_ns = locals()
    @temp_proxy = None
    import importlib
    if type(importlib) is @DeferredImportProxy:
        @temp_proxy = @local_ns.pop('importlib')
        @local_ns[@DeferredImportKey('importlib', @temp_proxy)] = @temp_proxy
    import importlib.abc
    if type(importlib) is @DeferredImportProxy:
        @temp_proxy = @local_ns.pop('importlib')
        @local_ns[@DeferredImportKey('importlib', @temp_proxy)] = @temp_proxy
    del @temp_proxy
    del @local_ns
del @DeferredImportKey
del @DeferredImportProxy
""",
            id="mixed import 1",
        ),
        pytest.param(
            """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    from . import a
""",
            """\
from deferred._core import DeferredImportKey as @DeferredImportKey, DeferredImportProxy as @DeferredImportProxy
from deferred import defer_imports_until_use
with defer_imports_until_use:
    @local_ns = locals()
    @temp_proxy = None
    from . import a
    if type(a) is @DeferredImportProxy:
        @temp_proxy = @local_ns.pop('a')
        @local_ns[@DeferredImportKey('a', @temp_proxy)] = @temp_proxy
    del @temp_proxy
    del @local_ns
del @DeferredImportKey
del @DeferredImportProxy
""",
            id="relative import 1",
        ),
    ],
)
def test_instrumentation(before: str, after: str):
    """Test what code is generated by the instrumentation side of deferred."""

    before_bytes = before.encode()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(before_bytes).readline)
    transformer = DeferredInstrumenter("<unknown>", before_bytes, encoding)
    transformed_tree = ast.fix_missing_locations(transformer.visit(ast.parse(before)))

    assert f"{ast.unparse(transformed_tree)}\n" == after


def test_path_hook_installation():
    """Test the API for putting/removing the deferred path hook from sys.path_hooks."""

    # It shouldn't be on there by default.
    assert DEFERRED_PATH_HOOK not in sys.path_hooks
    before_length = len(sys.path_hooks)

    # It should be present after calling install.
    install_defer_import_hook()
    assert DEFERRED_PATH_HOOK in sys.path_hooks
    assert len(sys.path_hooks) == before_length + 1

    # Calling install shouldn't do anything if it's already on sys.path_hooks.
    install_defer_import_hook()
    assert DEFERRED_PATH_HOOK in sys.path_hooks
    assert len(sys.path_hooks) == before_length + 1

    # Calling uninstall should remove it.
    uninstall_defer_import_hook()
    assert DEFERRED_PATH_HOOK not in sys.path_hooks
    assert len(sys.path_hooks) == before_length

    # Calling uninstall if it's not present should do nothing to sys.path_hooks.
    uninstall_defer_import_hook()
    assert DEFERRED_PATH_HOOK not in sys.path_hooks
    assert len(sys.path_hooks) == before_length


def test_empty(tmp_path: Path):
    source = ""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)


def test_regular_import(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    import inspect
"""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)

    expected_inspect_repr = "<key for 'inspect' import>: <proxy for 'import inspect'>"
    assert expected_inspect_repr in repr(vars(module))
    assert module.inspect
    assert expected_inspect_repr not in repr(vars(module))

    assert module.inspect is sys.modules["inspect"]

    def sample_func(a: int, c: float) -> float:
        return c

    assert str(module.inspect.signature(sample_func)) == "(a: int, c: float) -> float"


def test_regular_import_with_rename(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    import inspect as gin
"""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)

    expected_gin_repr = "<key for 'gin' import>: <proxy for 'import inspect'>"

    assert expected_gin_repr in repr(vars(module))

    with pytest.raises(NameError):
        exec("inspect", vars(module))

    with pytest.raises(AttributeError):
        assert module.inspect

    assert expected_gin_repr in repr(vars(module))
    assert module.gin
    assert expected_gin_repr not in repr(vars(module))

    assert sys.modules["inspect"] is module.gin

    def sample_func(a: int, b: str) -> str:
        return b

    assert str(module.gin.signature(sample_func)) == "(a: int, b: str) -> str"


def test_regular_import_nested(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    import importlib.abc
"""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)

    expected_importlib_repr = "<key for 'importlib' import>: <proxy for 'import importlib.abc'>"
    assert expected_importlib_repr in repr(vars(module))

    assert module.importlib
    assert module.importlib.abc
    assert module.importlib.abc.MetaPathFinder

    assert expected_importlib_repr not in repr(vars(module))


def test_regular_import_nested_with_rename(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    import collections.abc as xyz
"""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)

    # Make sure the right proxy is in the namespace.
    expected_xyz_repr = "<key for 'xyz' import>: <proxy for 'import collections.abc as ...'>"
    assert expected_xyz_repr in repr(vars(module))

    # Make sure the intermediate imports or proxies for them aren't in the namespace.
    with pytest.raises(NameError):
        exec("collections", vars(module))

    with pytest.raises(AttributeError):
        assert module.collections

    with pytest.raises(NameError):
        exec("collections.abc", vars(module))

    with pytest.raises(AttributeError):
        assert module.collections.abc

    # Make sure xyz resolves properly.
    assert expected_xyz_repr in repr(vars(module))
    assert module.xyz
    assert expected_xyz_repr not in repr(vars(module))
    assert module.xyz is sys.modules["collections"].abc

    # Make sure only the resolved xyz remains in the namespace.
    with pytest.raises(NameError):
        exec("collections", vars(module))

    with pytest.raises(AttributeError):
        assert module.collections

    with pytest.raises(NameError):
        exec("collections.abc", vars(module))

    with pytest.raises(AttributeError):
        assert module.collections.abc


def test_from_import(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    from inspect import isfunction, signature
"""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)

    expected_isfunction_repr = "<key for 'isfunction' import>: <proxy for 'from inspect import isfunction'>"
    expected_signature_repr = "<key for 'signature' import>: <proxy for 'from inspect import signature'>"
    assert expected_isfunction_repr in repr(vars(module))
    assert expected_signature_repr in repr(vars(module))

    with pytest.raises(NameError):
        exec("inspect", vars(module))

    assert expected_isfunction_repr in repr(vars(module))
    assert module.isfunction
    assert expected_isfunction_repr not in repr(vars(module))
    assert module.isfunction is sys.modules["inspect"].isfunction

    assert expected_signature_repr in repr(vars(module))
    assert module.signature
    assert expected_signature_repr not in repr(vars(module))
    assert module.signature is sys.modules["inspect"].signature


def test_from_import_with_rename(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    from inspect import Signature as MySignature
"""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)

    expected_my_signature_repr = "<key for 'MySignature' import>: <proxy for 'from inspect import Signature'>"
    assert expected_my_signature_repr in repr(vars(module))

    with pytest.raises(NameError):
        exec("inspect", vars(module))

    with pytest.raises(NameError):
        exec("Signature", vars(module))

    assert expected_my_signature_repr in repr(vars(module))
    assert str(module.MySignature) == "<class 'inspect.Signature'>"  # Resolves on use.
    assert expected_my_signature_repr not in repr(vars(module))
    assert module.MySignature is sys.modules["inspect"].Signature


def test_error_if_non_import(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    print("Hello world")
"""

    # Boilerplate to dynamically create and load this module.
    tmp_file = tmp_path / "sample.py"
    tmp_file.write_text(source, encoding="utf-8")

    module_name = "sample"
    path = tmp_file.resolve()

    spec = importlib.util.spec_from_file_location(module_name, path, loader=DeferredFileLoader(module_name, str(path)))

    assert spec
    assert spec.loader

    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SyntaxError) as exc_info:
        spec.loader.exec_module(module)

    assert exc_info.value.filename == str(path)
    assert exc_info.value.lineno == 4
    assert exc_info.value.offset == 5
    assert exc_info.value.text == 'print("Hello world")'


def test_error_if_import_in_class(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

class Example:
    with defer_imports_until_use:
        from inspect import signature
"""

    # Boilerplate to dynamically create and load this module.
    tmp_file = tmp_path / "sample.py"
    tmp_file.write_text(source, encoding="utf-8")

    module_name = "sample"
    path = tmp_file.resolve()

    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
        loader=DeferredFileLoader(module_name, str(path)),
    )

    assert spec
    assert spec.loader

    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SyntaxError) as exc_info:
        spec.loader.exec_module(module)

    assert exc_info.value.filename == str(path)
    assert exc_info.value.lineno == 4
    assert exc_info.value.offset == 5
    assert exc_info.value.text == "    with defer_imports_until_use:\n        from inspect import signature"


def test_error_if_import_in_function(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

def test():
    with defer_imports_until_use:
        import inspect

    return inspect.signature(test)
"""

    # Boilerplate to dynamically create and load this module.
    tmp_file = tmp_path / "sample.py"
    tmp_file.write_text(source, encoding="utf-8")

    module_name = "sample"
    path = tmp_file.resolve()

    spec = importlib.util.spec_from_file_location(module_name, path, loader=DeferredFileLoader(module_name, str(path)))

    assert spec
    assert spec.loader

    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SyntaxError) as exc_info:
        spec.loader.exec_module(module)

    assert exc_info.value.filename == str(path)
    assert exc_info.value.lineno == 4
    assert exc_info.value.offset == 5
    assert exc_info.value.text == "    with defer_imports_until_use:\n        import inspect"


def test_error_if_wildcard_import(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    from typing import *
"""

    # Boilerplate to dynamically create and load this module.
    tmp_file = tmp_path / "sample.py"
    tmp_file.write_text(source, encoding="utf-8")

    module_name = "sample"
    path = tmp_file.resolve()

    spec = importlib.util.spec_from_file_location(module_name, path, loader=DeferredFileLoader(module_name, str(path)))

    assert spec
    assert spec.loader

    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SyntaxError) as exc_info:
        spec.loader.exec_module(module)

    assert exc_info.value.filename == str(path)
    assert exc_info.value.lineno == 4
    assert exc_info.value.offset == 5
    assert exc_info.value.text == "from typing import *"


def test_top_level_and_submodules_1(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    import importlib
    import importlib.abc
    import importlib.util
"""
    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)

    # Prevent the caching of these from interfering with the test.
    sys.modules.pop("importlib", None)
    sys.modules.pop("importlib.abc", None)
    sys.modules.pop("importlib.util", None)

    expected_importlib_repr = "<key for 'importlib' import>: <proxy for 'import importlib'>"
    expected_importlib_abc_repr = "<key for 'abc' import>: <proxy for 'import importlib.abc as ...'>"
    expected_importlib_util_repr = "<key for 'util' import>: <proxy for 'import importlib.util as ...'>"

    # Test that the importlib proxy is here and then resolves.
    assert expected_importlib_repr in repr(vars(module))
    assert module.importlib
    assert expected_importlib_repr not in repr(vars(module))

    # Test that the nested proxies carry over to the resolved importlib.
    module_importlib_vars = vars(module.importlib)  # pyright: ignore [reportUnknownVariableType]
    module_importlib_vars = cast(dict[str, object], module_importlib_vars)
    assert expected_importlib_abc_repr in repr(module_importlib_vars)
    assert expected_importlib_util_repr in repr(module_importlib_vars)

    assert expected_importlib_abc_repr in repr(module_importlib_vars)
    assert module.importlib.abc
    assert expected_importlib_abc_repr not in repr(module_importlib_vars)

    assert expected_importlib_util_repr in repr(module_importlib_vars)
    assert module.importlib.util
    assert expected_importlib_util_repr not in repr(module_importlib_vars)


def test_top_level_and_submodules_2(tmp_path: Path):
    source = """\
from pprint import pprint

from deferred import defer_imports_until_use

with defer_imports_until_use:
    import asyncio
    import asyncio.base_events
    import asyncio.base_futures
    import asyncio.base_subprocess
    import asyncio.base_tasks
    import asyncio.constants
    import asyncio.coroutines
    import asyncio.events
    import asyncio.format_helpers
    import asyncio.futures
    import asyncio.locks
    import asyncio.log
    import asyncio.proactor_events
    import asyncio.protocols
    import asyncio.queues
    import asyncio.runners
    import asyncio.selector_events
    import asyncio.sslproto
    import asyncio.streams
    import asyncio.subprocess
    import asyncio.tasks
    import asyncio.transports
    import asyncio.unix_events
"""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)


def test_mixed_from_same_module(tmp_path: Path):
    source = """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    import asyncio
    from asyncio import base_events
    from asyncio import base_futures
"""

    spec, module = create_sample_module(tmp_path, source, DeferredFileLoader)
    assert spec.loader
    spec.loader.exec_module(module)

    expected_asyncio_repr = "<key for 'asyncio' import>: <proxy for 'import asyncio'>"
    expected_asyncio_base_events_repr = "<key for 'base_events' import>: <proxy for 'from asyncio import base_events'>"
    expected_asyncio_base_futures_repr = (
        "<key for 'base_futures' import>: <proxy for 'from asyncio import base_futures'>"
    )

    # Make sure the right proxies are present.
    assert expected_asyncio_repr in repr(vars(module))
    assert expected_asyncio_base_events_repr in repr(vars(module))
    assert expected_asyncio_base_futures_repr in repr(vars(module))

    # Make sure resolving one proxy doesn't resolve or void the others.
    assert module.base_futures
    assert module.base_futures is sys.modules["asyncio.base_futures"]
    assert expected_asyncio_base_futures_repr not in repr(vars(module))
    assert expected_asyncio_base_events_repr in repr(vars(module))
    assert expected_asyncio_repr in repr(vars(module))

    assert module.base_events
    assert module.base_events is sys.modules["asyncio.base_events"]
    assert expected_asyncio_base_events_repr not in repr(vars(module))
    assert expected_asyncio_base_futures_repr not in repr(vars(module))
    assert expected_asyncio_repr in repr(vars(module))

    assert module.asyncio
    assert module.asyncio is sys.modules["asyncio"]
    assert expected_asyncio_base_events_repr not in repr(vars(module))
    assert expected_asyncio_base_futures_repr not in repr(vars(module))
    assert expected_asyncio_repr not in repr(vars(module))


def test_relative_imports_1(tmp_path: Path):
    """Test a synthetic package that uses relative imports within defer_imports_until_use blocks.

    The package has the following structure:
        .
        └───sample_package
            ├───__init__.py
            ├───a.py
            └───b.py
    """

    sample_package_path = tmp_path / "sample_package"
    sample_package_path.mkdir()
    sample_package_path.joinpath("__init__.py").write_text(
        """\
from deferred import defer_imports_until_use

with defer_imports_until_use:
    from . import a
    from .a import A
    from .b import B

# A
"""
    )
    sample_package_path.joinpath("a.py").write_text(
        """\
class A:
    def __init__(val: object):
        self.val = val
"""
    )
    sample_package_path.joinpath("b.py").write_text(
        """\
class B:
    def __init__(val: object):
        self.val = val
"""
    )

    package_name = "sample_package"
    package_init_path = str(sample_package_path / "__init__.py")

    loader = DeferredFileLoader(package_name, package_init_path)
    spec = importlib.util.spec_from_file_location(
        package_name,
        package_init_path,
        loader=loader,
        submodule_search_locations=[],  # A signal that this is a package.
    )
    assert spec
    assert spec.loader

    module = importlib.util.module_from_spec(spec)
    # Is sample_package not being manually put in sys.modules a problem?
    spec.loader.exec_module(module)

    module_locals_repr = repr(vars(module))
    assert "<key for 'a' import>: <proxy for 'from sample_package import a'>" in module_locals_repr
    assert "<key for 'A' import>: <proxy for 'from sample_package.a import A'>" in module_locals_repr
    assert "<key for 'B' import>: <proxy for 'from sample_package.b import B'>" in module_locals_repr

    assert module.A


def test_false_circular_imports():
    """TODO"""


def test_true_circular_imports():
    """TODO"""


def test_thread_safety():
    """TODO"""
