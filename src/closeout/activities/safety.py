"""Errors from activities reach workflow histories, so they carry a type name only (design 3.8, rule 7).

Database errors include the statement's parameters (which can be names or emails), so they are never
passed on as they are: the activity raises a fresh, retryable error with just the exception's class name.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from temporalio.exceptions import ApplicationError

F = TypeVar("F", bound=Callable[..., Any])
log = logging.getLogger("closeout.activities")


def scrubbed(fn: F) -> F:
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*a: Any, **kw: Any) -> Any:
            try:
                return await fn(*a, **kw)
            except ApplicationError:
                raise
            except Exception as e:
                log.warning("%s failed: %s", fn.__name__, type(e).__name__)
                raise ApplicationError(type(e).__name__, type="StepError") from None

        return awrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except ApplicationError:
            raise
        except Exception as e:
            log.warning("%s failed: %s", fn.__name__, type(e).__name__)
            raise ApplicationError(type(e).__name__, type="StepError") from None

    return wrapper  # type: ignore[return-value]
