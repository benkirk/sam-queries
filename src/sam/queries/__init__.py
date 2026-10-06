"""Read-side query functions, one module per domain.

Import the submodule you need (``from sam.queries.expirations import ...``).
This package re-exports nothing on purpose: an eager re-export list put every
submodule's import graph (the XRAS client, ``sam.notify``, ``requests``) behind
every ``from sam.queries import ...`` in the tree.
``tests/unit/gates/test_layer_imports.py`` holds it empty.
"""
