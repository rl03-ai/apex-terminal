from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User

DBSession = Annotated[Session, Depends(get_db)]
security = HTTPBearer(auto_error=False)


def get_current_user(
    db: DBSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
) -> User:
    """Temporary dev auth.

    For the skeleton, if no token is supplied, the first user in the DB is used.
    Replace with real JWT decoding in production.
    """
    user = db.query(User).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='No user available. Register first.')
    return user
