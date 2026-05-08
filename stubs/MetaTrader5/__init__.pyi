from datetime import datetime
from typing import Any

def initialize(
    path: str = ...,
    login: int | str = ...,
    password: str = ...,
    server: str = ...,
    timeout: int = ...,
    portable: bool = ...,
) -> bool: ...

def shutdown() -> None: ...
def last_error() -> tuple[int, str]: ...

def copy_rates_from_pos(
    symbol: str,
    timeframe: int,
    start_pos: int,
    count: int,
) -> Any: ...

def copy_rates_from(
    symbol: str,
    timeframe: int,
    date_from: datetime,
    count: int,
) -> Any: ...