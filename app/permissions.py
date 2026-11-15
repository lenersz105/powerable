"""Role-based access control helpers.

Admin routes are always protected twice:
* ``login_required`` — an authenticated session is mandatory.
* ``admin_required`` — the account must hold the explicit ``admin`` role.

Templates only hide links; the server always re-checks every request.
"""

from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def admin_required(view):
    """Allow the request only for a signed-in account with the admin role."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


__all__ = ["admin_required"]
