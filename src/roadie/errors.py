class WrightError(Exception):
    pass


class StepFailed(WrightError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name} failed: {reason}")
