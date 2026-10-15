"""One module per SKU; each calls ``register_plan`` on import. Importing this package loads them all."""
from __future__ import annotations

import importlib
import pkgutil

for _mod in sorted(m.name for m in pkgutil.iter_modules(__path__)):
    importlib.import_module(f"{__name__}.{_mod}")
