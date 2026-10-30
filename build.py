from roadie.build import Check, Files, Group, PyProject, Pytest, PythonEnv, Release

sources = Files("src", "tests", "pyproject.toml", "build.py")
environment = PythonEnv()

ruff = Check("uv", "run", "ruff", "check", inputs=(sources, environment))
mypy = Check("uv", "run", "mypy", "src", inputs=(sources, environment))
pytest = Pytest(inputs=(sources, environment))
release = Release(PyProject("pyproject.toml"), requires=(ruff, mypy, pytest))
checks = Group(ruff, mypy, pytest)
