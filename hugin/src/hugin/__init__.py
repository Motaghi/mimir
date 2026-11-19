"""Hugin: a shadow router and the harness that measures it.

``hugin.router`` holds the routing helpers: the decision-string grammar,
the result envelope, the sensitivity classifier and the provider choice.
``hugin.shadow`` holds the shadow-replay side: staging, replay, comparison
and the recorder that counts divergence.

The two subpackages have no ``__init__`` module on purpose; each file in
them is a module with its own test file.
"""

__version__ = "0.1.0"
