"""GARIS — autonomous personal AI agent.

The user states an outcome; GARIS chooses the method, executes it, recovers from
failures and reports back. Nothing in this package should ever require the user
to pick a model, tool, command or API.

Layering (lower layers never import upper ones):

    platform / crypto / store        infrastructure
    memory / vault / audit           state
    runtime                          the single controlled execution path
    models                           provider-agnostic reasoning
    tools                            capabilities, registered into runtime
    agent                            goal -> plan -> act -> verify -> report
    tasks                            durable, concurrent, resumable work
    voice / api / host               surfaces
"""

__version__ = "0.1.2"

APP_NAME = "GARIS"

__all__ = ["APP_NAME", "__version__"]
