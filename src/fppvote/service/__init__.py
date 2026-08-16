"""The FastAPI service: rounds, votes, WebSocket."""
from .config import Config
from .follower import Follower, FollowerState
from .server import Hub, build_state, create_app

__all__ = ["create_app", "build_state", "Hub", "Config", "Follower",
           "FollowerState"]


def __getattr__(name):
    """`uvicorn fppvote.service:app` builds the app on demand.

    Note the module is server.py, not app.py: a submodule named `app` would be
    bound as an attribute of this package by the import above, shadowing this
    __getattr__ entirely — module attributes win over __getattr__, which is
    only consulted when normal lookup fails. uvicorn would then be handed the
    module object and fail with "'module' object is not callable" at the first
    request, on the Pi, at night.

    `uvicorn --factory fppvote.service:create_app` also works.
    """
    if name == "app":
        return create_app()
    raise AttributeError(name)
