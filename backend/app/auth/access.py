"""Per-user access to the 112 operator mode (call intake).

The mode stays in the product but is hidden from users the administrator has not granted it
to: they cannot create lessons or scenarios of that mode. Old attempts of the mode still open.
"""

from app.errors import ApiError
from app.models import User


def require_call_intake_access(user: User) -> None:
    if not user.call_intake_access:
        raise ApiError(
            403,
            "call_intake_forbidden",
            "Режим оператора 112 вам не открыт. Доступ выдаёт администратор.",
        )
