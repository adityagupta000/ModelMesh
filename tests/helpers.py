import importlib.util
import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_module_from_path(unique_name, rel_path, stubs=None, monkeypatch=None):
    """Load a worker module with temporary dependency stubs."""
    for name, stub in (stubs or {}).items():
        if monkeypatch is not None:
            monkeypatch.setitem(sys.modules, name, stub)
        else:
            sys.modules[name] = stub

    path = os.path.join(ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
