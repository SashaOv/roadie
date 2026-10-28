class RoadieError(Exception):
    pass


class StepFailed(RoadieError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name} failed: {reason}")
