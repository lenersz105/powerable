"""AdminAction model — an audit trail for administrative operations.

Only actions that actually happened are stored. The actor's username is
snapshotted so the record stays readable even if the account is deleted.
No pairing codes, passwords, or session tokens are ever written here.
"""

from app.extensions import db
from app.utils import utcnow


class AdminAction(db.Model):
    __tablename__ = "admin_actions"

    id = db.Column(db.Integer, primary_key=True)

    # Nullable so a deleted actor cannot break the audit history.
    actor_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_name = db.Column(db.String(80), nullable=False)

    # Short machine-readable verb, e.g. "outlet.provisioned".
    action = db.Column(db.String(40), nullable=False, index=True)
    target = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), nullable=False, default="")

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    actor = db.relationship("User")

    def __repr__(self):
        return f"<AdminAction {self.action} target={self.target}>"
