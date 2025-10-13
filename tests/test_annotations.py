from __future__ import annotations

import importlib
import pkgutil
import sys
import types

import pytest

import defer_imports


if sys.version_info >= (3, 14):  # pragma: >=3.14 cover
    from annotationlib import get_annotations
else:  # pragma: <3.14 cover
    from inspect import get_annotations


def can_have_annotations(obj: object) -> bool:
    return isinstance(obj, (type, types.ModuleType)) or callable(obj)


@pytest.mark.parametrize(
    "module",
    [
        importlib.import_module(mod_info.name)
        for mod_info in pkgutil.iter_modules(
            path=defer_imports.__spec__.submodule_search_locations,
            prefix=defer_imports.__spec__.name + ".",
        )
    ],
)
def test_library_annotations_are_valid(module: types.ModuleType):
    get_annotations(module, eval_str=True)

    for val in filter(can_have_annotations, module.__dict__.values()):
        get_annotations(val, eval_str=True)
