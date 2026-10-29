class RoadieError(Exception):
    pass


# (spec) Failures
class StepFailed(RoadieError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name} failed: {reason}")
