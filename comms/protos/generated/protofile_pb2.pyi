from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class User(_message.Message):
    __slots__ = ("name", "id", "email", "phonenumber")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    PHONENUMBER_FIELD_NUMBER: _ClassVar[int]
    name: str
    id: str
    email: str
    phonenumber: int
    def __init__(self, name: _Optional[str] = ..., id: _Optional[str] = ..., email: _Optional[str] = ..., phonenumber: _Optional[int] = ...) -> None: ...

class GetUsersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetUsersResponse(_message.Message):
    __slots__ = ("user",)
    USER_FIELD_NUMBER: _ClassVar[int]
    user: User
    def __init__(self, user: _Optional[_Union[User, _Mapping]] = ...) -> None: ...
