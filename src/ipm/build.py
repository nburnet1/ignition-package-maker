"""Build Ignition script-python modules into a Python package.

ipm.json is minimal:
{
  "projects": {"global": "ignition/global/ignition/script-python"},
  "packages": {
    "slowrm": {
      "source": "global",
      "exports": ["slowrm"]
    }
  }
}

pyproject.toml is the source of truth for version, description, dependencies, etc.
If it doesn't exist on first build, the user is prompted to create one.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.prompt import Prompt

console = Console()

CONFIG_FILE = "ipm.json"


@dataclass
class PackageConfig:
    """Configuration for a single package."""

    name: str
    source: str = ""
    exports: list[str] = field(default_factory=list)


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

        packages: dict[str, PackageConfig] = {}
        if "packages" in data:
            for name, pkg_data in data["packages"].items():
                packages[name] = PackageConfig(
                    name=name,
                    source=pkg_data.get("source", ""),
                    exports=pkg_data.get("exports", []),
                )

        return cls(projects=projects, packages=packages)

    def resolve_source_path(self, pkg: PackageConfig) -> str:
        """Resolve a package's source to a filesystem path."""
        if pkg.source in self.projects:
            return self.projects[pkg.source]
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
    """Build a single package."""
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

    console.print(f"\n[bold]Building {pkg.name}[/bold]\n")
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

    # Clean previous src/ but preserve pyproject.toml and other user files
    if src_path.exists():
        shutil.rmtree(src_path)
    src_path.mkdir(parents=True)

    # Convert modules directly into src/
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

    # Write pyproject.toml only if it doesn't exist - prompt for details
    pyproject_path = output_path / "pyproject.toml"
    if pyproject_path.exists():
        console.print(f"\n  [dim]pyproject.toml already exists, skipping[/dim]")
    else:
        _create_pyproject(output_path, pkg, module_dirs)

    console.print(f"\n[bold green]Built {pkg.name}[/bold green]")
    console.print(f"  {total_files} modules converted")
    console.print(f"\n  To publish:")
    console.print(f"    cd {output_path.relative_to(root)}")
    console.print(f"    python -m build")
    console.print(f"    twine upload dist/*")


def _create_pyproject(output_path: Path, pkg: PackageConfig, module_dirs: list[str]) -> None:
    """Create pyproject.toml interactively on first build."""
    console.print(f"\n  [bold]Creating pyproject.toml[/bold]")

    version = Prompt.ask("  Version", default="0.0.0")
    description = Prompt.ask("  Description", default="")
    ignition_version = Prompt.ask("  Ignition version", default="8.3")

    packages_list = ", ".join(f'"src/{d}"' for d in module_dirs)

    content = f'''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{pkg.name}"
version = "{version}"
description = "{description}"
keywords = ["jython", "ignition", "ipm"]
classifiers = [
    "Programming Language :: Python :: 2.7",
    "Programming Language :: Python :: Implementation :: Jython",
    "Framework :: Ignition :: {ignition_version}",
]

[tool.hatch.build.targets.wheel]
packages = [{packages_list}]
'''
    (output_path / "pyproject.toml").write_text(content.strip() + "\n", encoding="utf-8")
    console.print(f"  [green]Created pyproject.toml[/green]")


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
            if any(child.rglob("code.py")):
                modules.append(child.name)
    return modules


def _convert_module(source_dir: Path, target_dir: Path, module_name: str) -> int:
    """Convert an Ignition script module tree into Python package layout.

    Returns count of .py files written (excluding empty __init__.py).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    # Write __init__.py for this directory
    init_content = ""

    # Check if this directory itself has a code.py
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
            child_count = _convert_module(child, target_dir / child.name, child.name)
            count += child_count
        elif child_code.exists():
            target_file = target_dir / f"{child.name}.py"
            target_file.write_text(child_code.read_text(encoding="utf-8"), encoding="utf-8")
            count += 1

    return count
