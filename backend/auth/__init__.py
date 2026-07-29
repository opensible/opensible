"""backend.auth subpackage.

Re-exports symbols from auth.service so existing code that uses
`from auth import generate_token, ...` continues to work after the
restructure into auth/{service,middleware,models,validators,seed}.py.
"""
from .service import *  # noqa: F401,F403
