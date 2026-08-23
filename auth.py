"""Authentication and the user registry: identity comes from Authelia
(ForwardAuth headers), reconciled against the `users` table on every
request — see `register_user`."""

from typing import List, Optional

from fastapi import Cookie, Depends, Header, HTTPException
from pydantic import BaseModel, Field, computed_field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import auth_mode, dev_groups, dev_user, reviewers_group
from models import User, get_db


class Identity(BaseModel):
    username: str = Field(description="Authelia username.")
    groups: List[str] = []
    email: Optional[str] = None
    full_name: Optional[str] = Field(None, description="Remote-Name, if Authelia sends it.")
    id: Optional[int] = Field(None, description="Set by registered_user.")

    @property
    def display_name(self) -> str:
        """Name to show: the display name if there is one, otherwise the
        username, which is readable regardless."""
        return self.full_name or self.username

    @computed_field
    @property
    def is_reviewer(self) -> bool:
        return reviewers_group() in self.groups

    @computed_field
    @property
    def is_dev_mode(self) -> bool:
        return auth_mode() == "dev"


def current_user(
    remote_user:   Optional[str] = Header(None, alias="Remote-User"),
    remote_groups: str           = Header("",   alias="Remote-Groups"),
    remote_email:  Optional[str] = Header(None, alias="Remote-Email"),
    remote_name:   Optional[str] = Header(None, alias="Remote-Name"),
    dev_role:      Optional[str] = Cookie(None),
) -> Identity:
    """User identity, from the headers Nginx sets via Authelia.

    The headers are trustworthy only if the service isn't reachable by
    bypassing Nginx: whoever hits port 8010 directly can claim whatever
    they want. The container must therefore not expose the port externally.
    """
    if auth_mode() == "dev":
        if not remote_user and dev_role == "socio":
            remote_user, remote_groups = "socio-dev", "soci"
            remote_name = remote_name or "Luca Bertani"
        elif not remote_user:
            remote_name = remote_name or "Marta Conti"
        remote_user   = remote_user   or dev_user()
        remote_groups = remote_groups or dev_groups()
        remote_email  = remote_email  or f"{remote_user}@example.test"

    if not remote_user:
        raise HTTPException(status_code=401, detail="Autenticazione richiesta.")

    return Identity(
        username=remote_user,
        groups=[g.strip() for g in remote_groups.split(",") if g.strip()],
        email=remote_email,
        full_name=remote_name,
    )


def register_user(db: Session, user: "Identity") -> int:
    """Aligns the registry with the identity coming from Authelia and
    returns its id.

    Writes only if the record is missing or name/email changed: ordinary
    requests cost a SELECT, not a write.
    """
    record = db.scalar(select(User).where(User.username == user.username))

    if record is None:
        return _insert_or_reconcile_user(db, user)

    if (record.name, record.email) != (user.display_name, user.email):
        _update_name_and_email(db, record.id, user)
    return record.id


def _insert_or_reconcile_user(db: Session, user: "Identity") -> int:
    try:
        record = User(username=user.username, name=user.display_name, email=user.email)
        db.add(record)
        db.commit()
        return record.id
    except IntegrityError:
        db.rollback()
        return _reconcile_after_registry_conflict(db, user)


def _reconcile_after_registry_conflict(db: Session, user: "Identity") -> int:
    """Another INSERT violated UNIQUE between the initial SELECT and this one:
    username or email is already in the registry for a different reason, to
    be told apart case by case."""
    record = db.scalar(select(User).where(User.username == user.username))
    if record is not None:
        return record.id  # another request from the same user won the race

    co_observer_to_promote = db.scalar(
        select(User).where(User.email == user.email, User.username.is_(None))
    )
    if co_observer_to_promote is not None:
        return _promote_co_observer(db, co_observer_to_promote.id, user)

    return _register_without_email(db, user)


def _promote_co_observer(db: Session, user_id: int, user: "Identity") -> int:
    """A co-observer entered by hand (#40), now recognized by email: the
    record gets updated instead of duplicated, so the observations they
    already took part in stay linked to them."""
    record = db.get(User, user_id)
    record.username = user.username
    record.name = user.display_name
    db.commit()
    return user_id


def _register_without_email(db: Session, user: "Identity") -> int:
    """The email already belongs to another verified account — Authelia
    requires them unique, so this is a pathological case. Register without
    an address instead of denying access."""
    print(
        f"[registry] '{user.username}': email {user.email!r} already "
        f"assigned to another verified user, registered without an address",
        flush=True,
    )
    record = User(username=user.username, name=user.display_name, email=None)
    db.add(record)
    db.commit()
    return record.id


def _update_name_and_email(db: Session, user_id: int, user: "Identity") -> None:
    try:
        record = db.get(User, user_id)
        record.name = user.display_name
        record.email = user.email
        db.commit()
    except IntegrityError:
        db.rollback()
        _update_name_only(db, user_id, user)


def _update_name_only(db: Session, user_id: int, user: "Identity") -> None:
    """The email moved to another account: only the name gets aligned here."""
    record = db.get(User, user_id)
    record.name = user.display_name
    db.commit()


def registered_user(
    user: Identity = Depends(current_user),
    db: Session = Depends(get_db),
) -> Identity:
    """Current user, with the id of their record in the registry."""
    user.id = register_user(db, user)
    return user


def reviewers_only(user: Identity = Depends(current_user)) -> Identity:
    if not user.is_reviewer:
        raise HTTPException(
            status_code=403,
            detail=f"Operazione riservata al gruppo '{reviewers_group()}'.",
        )
    return user
