from roadie.build import *

sources = Files("src", "tests", "pyproject.toml", "build.py")
environment = PythonEnv()

ruff = Check("uv", "run", "ruff", "check", inputs=(sources, environment))
mypy = Check("uv", "run", "mypy", "src", "build.py", inputs=(sources, environment))
pytest = Pytest(inputs=(sources, environment))
checks = default_step(Group(ruff, mypy, pytest))
release = Release("pyproject.toml", requires=checks, message=Param("message", default=None))
