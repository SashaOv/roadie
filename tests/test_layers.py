import ast
from pathlib import Path

import roadie

PACKAGE = Path(roadie.__file__).resolve().parent


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


# (spec) Packages
def test_the_system_package_imports_nothing_else_from_roadie() -> None:
    offending = {
        f"{path.relative_to(PACKAGE).as_posix()} imports {module}"
        for path in (PACKAGE / "system").rglob("*.py")
        for module in _imports(path)
        if (module == "roadie" or module.startswith("roadie."))
        and module != "roadie.system"
        and not module.startswith("roadie.system.")
    }
    assert not offending


# (spec) Packages
def test_the_engine_does_not_import_the_build_file_api() -> None:
    offending = {
        f"{path.relative_to(PACKAGE).as_posix()} imports {module}"
        for path in (PACKAGE / "engine").rglob("*.py")
        for module in _imports(path)
        if module == "roadie.build" or module.startswith(("roadie.build.", "roadie.cli"))
    }
    assert not offending
