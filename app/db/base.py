from app.core.validators import to_snake
from sqlalchemy.orm import DeclarativeBase, declared_attr


class Base(DeclarativeBase):
    @declared_attr.directive
    def __tablename__(cls,) -> str:
        return to_snake(cls.__class__)
