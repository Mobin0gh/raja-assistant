"""Domain layer.

Dependency rule (enforced by ``tests/unit/test_domain_purity.py``): this package
imports only the Python standard library and itself. No network client, no
database driver, no settings framework, no UI toolkit, no provider adapter.
"""
