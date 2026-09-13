#!/usr/bin/env python3
from typing import Any, ClassVar, TypeVar, cast

_InstanceType = TypeVar("_InstanceType")


class Singleton(type):

    """
    单例元类
    """

    _instances: ClassVar[dict[Any, Any]] = {}

    def __call__(cls: type[_InstanceType], *args: Any, **kwargs: Any) -> _InstanceType:

        _meta = cast(Singleton, cls)
        if _meta not in _meta._instances:

            _meta._instances[_meta] = super(Singleton, _meta).__call__(*args, **kwargs)

        return cast(_InstanceType, _meta._instances[_meta])
