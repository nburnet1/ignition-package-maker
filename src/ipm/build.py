"""Build Ignition script-python modules into a Python package.

Conversion (single export):
    script-python/infrastructure/repo/code.py -> src/vkrinfra/repo.py
    script-python/infrastructure/orm/code.py  -> src/vkrinfra/orm.py

Conversion (multiple exports):
    script-python/infrastructure/repo/code.py -> src/vkrinfra/infrastructure/repo.py
    script-python/entity/node/code.py         -> src/vkrinfra/entity/node.py

Consumer installs with:
    pip install --target ./site-packages vkrinfra

And imports:
    from vkrinfra.orm import OrmRepository
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

console = Console()

CONFIG_FILE = "ipm.json"


@dataclass
class PackageConfig:
    """Configuration for a single package."""

    name: str
    version: str
    description: str = ""
    source: str = ""  # project name (key into projects map)
    exports: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # pip dependencies (written to pyproject.toml, skipped by scanner)
    authors: list[dict[str, str]] = field(default_factory=list)
    license: str = ""
    python_requires: str = ">=2.7"
    exclude: list[str] = field(default_factory=list)


@dataclass
class Config:
    """Full ipm.json configuration."""

    projects: dict[str, str] = field(default_factory=dict)
    packages: dict[str, PackageConfig] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> Config:
        config_path = root / CONFIG_FILE
        if not config_path.exists():
            raise FileNotFoundError(f"No {CONFIG_FILE} found. Run 'ipm init' first.")
        data = json.loads(config_path.read_text(encoding="utf-8"))

        projects = data.get("projects", {})

        # Support both old single-package format and new multi-package format
        packages: dict[str, PackageConfig] = {}
        if "packages" in data:
            for name, pkg_data in data["packages"].items():
                packages[name] = PackageConfig(
                    name=name,
                    version=pkg_data.get("version", "0.0.0"),
                    description=pkg_data.get("description", ""),
                    source=pkg_data.get("source", ""),
                    exports=pkg_data.get("exports", []),
                    dependencies=pkg_data.get("dependencies", []),
                    authors=pkg_data.get("authors", []),
                    license=pkg_data.get("license", ""),
                    python_requires=pkg_data.get("python_requires", ">=2.7"),
                    exclude=pkg_data.get("exclude", []),
                )
        elif "package" in data:
            # Legacy single-package format
            pkg_data = data["package"]
            name = pkg_data["name"]
            packages[name] = PackageConfig(
                name=name,
                version=pkg_data.get("version", "0.0.0"),
                description=pkg_data.get("description", ""),
                source=pkg_data.get("source", ""),
                exports=pkg_data.get("exports", []),
                dependencies=pkg_data.get("dependencies", []),
                authors=pkg_data.get("authors", []),
                license=pkg_data.get("license", ""),
                python_requires=pkg_data.get("python_requires", ">=2.7"),
                exclude=pkg_data.get("exclude", []),
            )

        return cls(projects=projects, packages=packages)

    def resolve_source_path(self, pkg: PackageConfig) -> str:
        """Resolve a package's source to a filesystem path."""
        # If source is a project name, look it up
        if pkg.source in self.projects:
            return self.projects[pkg.source]
        # Otherwise treat it as a direct path
        return pkg.source


def build(
    package: str = typer.Argument(None, help="Package to build (omit to build all)"),
    output: Path = typer.Option("ipm", "--output", "-o", help="Output directory"),
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to ipm.json"),
) -> None:
    """Build Ignition script-python modules into a publishable Python package."""
    root = _find_root(config_path)
    config = Config.load(root)

    if not config.packages:
        console.print(f"[red]No packages defined in {CONFIG_FILE}[/red]")
        raise typer.Exit(1)

    # Determine which packages to build
    if package:
        if package not in config.packages:
            available = ", ".join(config.packages.keys())
            console.print(f"[red]Package '{package}' not found. Available: {available}[/red]")
            raise typer.Exit(1)
        packages_to_build = [config.packages[package]]
    else:
        packages_to_build = list(config.packages.values())

    for pkg in packages_to_build:
        _build_package(pkg, config, root, output)


def _build_package(pkg: PackageConfig, config: Config, root: Path, output: Path) -> None:
    """Build a single package.

    The distribution name (pip install <name>) and the import name can differ.
    For example: pip install infra -> import infrastructure
    This is standard Python (like pip install Pillow -> import PIL).

    Exported modules are placed directly under src/ so internal imports
    between sibling modules resolve without any wrapper package.
    """
    source_rel = config.resolve_source_path(pkg)
    if not source_rel:
        console.print(f"[red]No source defined for package '{pkg.name}'[/red]")
        raise typer.Exit(1)

    source_path = root / source_rel
    if not source_path.exists():
        console.print(f"[red]Source path not found: {source_path}[/red]")
        raise typer.Exit(1)

    output_path = root / output / pkg.name
    src_path = output_path / "src"

    console.print(f"\n[bold]Building {pkg.name}@{pkg.version}[/bold]\n")
    console.print(f"  Source:  {source_rel}")
    console.print(f"  Output:  {output_path.relative_to(root)}")

    # Discover modules
    if pkg.exports:
        top_level_modules = pkg.exports
    else:
        top_level_modules = _discover_modules(source_path)

    if not top_level_modules:
        console.print("[red]No modules found to export.[/red]")
        raise typer.Exit(1)

    console.print(f"  Exports: {', '.join(top_level_modules)}\n")

    # Scan for external imports before building
    # Dependencies declared in ipm.json are pip packages - skip them in the scan
    dep_modules = set()
    for dep in pkg.dependencies:
        # "SQLAlchemy>=1.3" -> "sqlalchemy"
        dep_name = dep.split(">")[0].split("<")[0].split("=")[0].split("!")[0].strip()
        dep_modules.add(dep_name.lower().replace("-", "_"))

    external_deps = _scan_external_imports(source_path, top_level_modules, dep_modules)

    if external_deps["sibling"]:
        console.print(f"  [red]External imports detected that are not in exports:[/red]")
        for mod, locations in sorted(external_deps["sibling"].items()):
            console.print(f"    [red]{mod}[/red]  ({len(locations)} references)")
        exports_suggestion = ", ".join(f'"{m}"' for m in sorted(external_deps["sibling"]))
        current_exports = ", ".join(f'"{m}"' for m in top_level_modules)
        console.print(
            f"\n  Add these modules to 'exports' or 'dependencies' in ipm.json:"
            f"\n"
            f"\n    \"exports\": [{current_exports}, {exports_suggestion}]"
        )
        raise typer.Exit(1)

    if external_deps["java"]:
        console.print(f"  [dim]Java/Jython imports: {', '.join(sorted(external_deps['java']))}[/dim]\n")

    # Clean previous src/ but preserve pyproject.toml and other user files
    if src_path.exists():
        shutil.rmtree(src_path)
    src_path.mkdir(parents=True)

    # Convert modules directly into src/ (no wrapper package)
    total_files = 0
    module_dirs = []
    for module in top_level_modules:
        module_source = source_path / module
        if not module_source.exists():
            console.print(f"  [yellow]Skipping '{module}' (not found)[/yellow]")
            continue
        count = _convert_module(module_source, src_path / module, module)
        total_files += count
        module_dirs.append(module)
        console.print(f"  [green]{module}/[/green] ({count} modules)")

    # Write pyproject.toml only if it doesn't exist
    pyproject_path = output_path / "pyproject.toml"
    if pyproject_path.exists():
        console.print(f"\n  [dim]pyproject.toml already exists, skipping[/dim]")
    else:
        _write_pyproject(output_path, pkg, module_dirs)

    console.print(f"\n[bold green]Built {pkg.name}@{pkg.version}[/bold green]")
    console.print(f"  {total_files} modules converted")
    console.print(f"\n  To publish:")
    console.print(f"    cd {output_path.relative_to(root)}")
    console.print(f"    pip install build && python -m build")
    console.print(f"    twine upload dist/*")


# Modules that are provided by the Ignition/Jython runtime (not pip-installable)
IGNITION_BUILTINS = frozenset({
    "system", "designer", "client", "project",
})

JYTHON_BUILTINS = frozenset({
    "java", "javax", "com", "org",
})

PYTHON_STDLIB = frozenset({
    "__builtin__", "__future__", "_thread",
    "abc", "ast", "base64", "binascii", "bisect", "calendar",
    "cPickle", "cStringIO", "codecs", "collections", "contextlib", "copy",
    "csv", "datetime", "decimal", "difflib", "email", "enum", "errno",
    "fnmatch", "fractions", "ftplib", "functools",
    "glob", "gzip", "hashlib", "heapq",
    "inspect", "io", "itertools",
    "json", "locale", "logging",
    "math", "mimetypes", "numbers",
    "operator", "optparse", "os",
    "pickle", "platform", "posixpath", "pprint",
    "Queue", "queue",
    "random", "re",
    "select", "shelve", "shutil", "signal", "socket",
    "string", "StringIO", "struct", "subprocess", "sys",
    "tempfile", "textwrap", "threading", "time", "token", "tokenize",
    "traceback", "types",
    "unicodedata", "unittest", "urllib", "urllib2", "urlparse",
    "uuid", "warnings", "weakref", "xml", "zipfile", "zlib",
})


def _scan_external_imports(
    source_path: Path,
    exported_modules: list[str],
    dep_modules: set[str] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """Scan exported modules for imports that aren't part of the package.

    Args:
        source_path: Path to script-python directory.
        exported_modules: List of modules being exported.
        dep_modules: Set of module names from declared pip dependencies (skipped).

    Returns a dict with:
        "sibling": {module_name: [file_locations]} - other script-python modules
        "java": {module_name} - Java/Jython imports
    """
    import ast
    import re

    exported_set = set(exported_modules)
    dep_modules = dep_modules or set()
    sibling_imports: dict[str, list[str]] = {}
    java_imports: set[str] = set()

    for module in exported_modules:
        module_dir = source_path / module
        for code_file in module_dir.rglob("code.py"):
            rel_path = str(code_file.relative_to(source_path))
            source_text = code_file.read_text(encoding="utf-8")

            # Extract imports
            imports = _extract_imports(source_text)

            for top_level, full_path in imports:
                if top_level in exported_set:
                    continue  # internal
                if top_level in IGNITION_BUILTINS:
                    continue
                if top_level in PYTHON_STDLIB:
                    continue
                if top_level in JYTHON_BUILTINS:
                    java_imports.add(full_path)
                    continue
                if top_level.lower().replace("-", "_") in dep_modules:
                    continue  # declared pip dependency
                # It's an external script-python module
                if top_level not in sibling_imports:
                    sibling_imports[top_level] = []
                sibling_imports[top_level].append(rel_path)

    return {"sibling": sibling_imports, "java": java_imports}


def _extract_imports(source: str) -> list[tuple[str, str]]:
    """Extract (top_level_module, full_import_path) from Python source.

    Falls back to regex if AST parsing fails.
    """
    import ast

    results: list[tuple[str, str]] = []
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    results.append((top, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    results.append((top, node.module))
    except SyntaxError:
        # Jython-specific syntax fallback
        import re
        pattern = re.compile(
            r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
        )
        for match in pattern.finditer(source):
            full = match.group(1) or match.group(2)
            if full:
                results.append((full.split(".")[0], full))
    return results


def _find_root(config_path: Path | None) -> Path:
    """Find project root."""
    if config_path:
        return config_path.parent
    current = Path.cwd()
    while current != current.parent:
        if (current / CONFIG_FILE).exists():
            return current
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path.cwd()


def _discover_modules(source_path: Path) -> list[str]:
    """Auto-discover top-level modules in script-python."""
    modules = []
    for child in sorted(source_path.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            # Check if it contains any code.py files (at any depth)
            if any(child.rglob("code.py")):
                modules.append(child.name)
    return modules


def _convert_module(source_dir: Path, target_dir: Path, module_name: str) -> int:
    """Convert an Ignition script module tree into Python package layout.

    Ignition structure:
        module/
          sub_a/code.py
          sub_b/code.py
          sub_b/nested/code.py

    Python output:
        module/
          __init__.py
          sub_a.py           (from sub_a/code.py)
          sub_b/
            __init__.py      (from sub_b/code.py)
            nested.py        (from nested/code.py)

    Returns count of .py files written (excluding empty __init__.py).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    # Write __init__.py for this directory
    init_content = ""

    # Check if this directory itself has a code.py (it's both a package and a module)
    own_code = source_dir / "code.py"
    if own_code.exists():
        init_content = own_code.read_text(encoding="utf-8")

    (target_dir / "__init__.py").write_text(init_content, encoding="utf-8")
    if init_content:
        count += 1

    # Process children
    for child in sorted(source_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        if child.name == "__pycache__":
            continue

        child_code = child / "code.py"
        has_subdirs = any(
            d.is_dir() and (d / "code.py").exists()
            for d in child.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )

        if has_subdirs:
            # This child is a sub-package (has its own nested modules)
            child_count = _convert_module(child, target_dir / child.name, child.name)
            count += child_count
        elif child_code.exists():
            # This child is a leaf module - becomes a .py file
            target_file = target_dir / f"{child.name}.py"
            target_file.write_text(child_code.read_text(encoding="utf-8"), encoding="utf-8")
            count += 1

    return count


def _write_pyproject(output_path: Path, pkg: PackageConfig, module_dirs: list[str]) -> None:
    """Write pyproject.toml for the built package."""
    authors_toml = ""
    if pkg.authors:
        author_entries = []
        for a in pkg.authors:
            parts = []
            if "name" in a:
                parts.append(f'name = "{a["name"]}"')
            if "email" in a:
                parts.append(f'email = "{a["email"]}"')
            author_entries.append("{" + ", ".join(parts) + "}")
        authors_toml = f"authors = [{', '.join(author_entries)}]"

    deps_toml = ""
    if pkg.dependencies:
        deps_lines = ",\n".join(f'    "{d}"' for d in pkg.dependencies)
        deps_toml = f"dependencies = [\n{deps_lines},\n]"

    license_toml = ""
    if pkg.license:
        license_toml = f'license = "{pkg.license}"'

    packages_list = ", ".join(f'"src/{d}"' for d in module_dirs)

    content = f'''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{pkg.name}"
version = "{pkg.version}"
description = "{pkg.description}"
requires-python = "{pkg.python_requires}"
keywords = ["jython", "ignition", "ipm"]
{license_toml}
{authors_toml}
{deps_toml}

[tool.hatch.build.targets.wheel]
packages = [{packages_list}]
'''
    (output_path / "pyproject.toml").write_text(content.strip() + "\n", encoding="utf-8")
