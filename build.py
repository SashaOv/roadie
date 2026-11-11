from roadie.build import *

sources = Files("src", "tests", "pyproject.toml", "build.py")
environment = PythonEnv()

ruff = Check("uv", "run", "ruff", "check", inputs=(sources, environment))
mypy = Check("uv", "run", "mypy", "src", "build.py", inputs=(sources, environment))
pytest = Pytest(inputs=(sources, environment))
checks = default_step(Group(ruff, mypy, pytest))
approve = UserApproval("Release main?")
release = Release(
    ".",
    requires=(checks, approve),
    message=Param("message", default=None),
)
