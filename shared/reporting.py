"""Terminal reporting kept outside business and transport contracts."""


def log(layer: str, message: str) -> None:
    print(f"[{layer}] {message}", flush=True)


def error_details(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "\n".join(error_details(item) for item in exc.exceptions)
    return str(exc)
