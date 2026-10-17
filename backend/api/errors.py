"""App-wide handling for "the database can't be reached", so every DB-backed route answers 503.

Without this, an unreachable Neon (a TLS reset, a suspended compute, a timeout) surfaces as a bare
500 with a traceback, which looks like a bug in the route. 503 says what is true: the service is
up, a dependency is not. The frontend already treats any non-404 failure as "backend down" and
falls back, so this changes the log and the status code, not the card's behavior.

Only connection-level errors are caught, on purpose: a bad query or a missing table is a real bug
and should stay a loud 500. Mounted in backend/main.py via install().
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError

log = logging.getLogger(__name__)

# asyncpg raises the builtin ConnectionError family (e.g. ConnectionResetError) and TimeoutError
# straight through SQLAlchemy during connect; once connected, failures arrive wrapped as these two.
DB_UNAVAILABLE = (ConnectionError, TimeoutError, OperationalError, InterfaceError)


async def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # One line, no traceback: the cause is in `exc`, and this will repeat on every request.
    log.warning("Database unavailable for %s %s: %s: %s", request.method, request.url.path, type(exc).__name__, exc)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


def install(app: FastAPI) -> None:
    for exc_type in DB_UNAVAILABLE:
        app.add_exception_handler(exc_type, _db_unavailable)
