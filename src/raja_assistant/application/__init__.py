"""Application layer: use-case contracts that sit between domain and adapters.

This layer may import the domain. It must never import an adapter, a network
library, a database driver, or a UI framework; the boundary is enforced by
``tests/unit/test_layer_boundaries.py``.

Phase P2 contains only the provider port: the contract a booking adapter must
satisfy. No orchestration, scheduling, or persistence lives here yet.
"""
