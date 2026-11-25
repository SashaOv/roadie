import ast
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "src"
_ORDER = (
    (ast.Import, ast.ImportFrom),
    (ast.ClassDef, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.TypeAlias),
    (ast.FunctionDef, ast.AsyncFunctionDef),
)
_LABELS = ("import", "class or module variable", "function")


def test_source_modules_order_imports_classes_and_variables_then_functions() -> None:
    modules = sorted(_SOURCE.rglob("*.py"))
    assert modules, f"No Python modules found in {_SOURCE}"
    violations: list[str] = []
    for path in modules:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        latest = 0
        for member in module.body:
            for position, kinds in enumerate(_ORDER):
                if isinstance(member, kinds):
                    if position < latest:
                        relative = path.relative_to(_SOURCE.parent)
                        violations.append(
                            f"{relative}:{member.lineno}: {_LABELS[position]} after {_LABELS[latest]}"
                        )
                    latest = max(position, latest)
                    break
    assert not violations, "\n" + "\n".join(violations)
