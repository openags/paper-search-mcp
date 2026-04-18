from contextvars import ContextVar

_request_user_email: ContextVar[str | None] = ContextVar('user_email', default=None)
