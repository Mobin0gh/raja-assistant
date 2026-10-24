"""Adapter layer: concrete implementations of application ports.

Adapters may import the application layer and the domain. The reverse is
forbidden and checked by ``tests/unit/test_layer_boundaries.py``.

Phase P2 contains exactly one adapter, the local simulator. There is no Raja
adapter, and none may be added without a documented authorization and an agreed
interface contract.
"""
