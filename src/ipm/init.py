"""ipm init - Scaffold an ipm.json config file."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Prompt, Confirm

console = Console()

CONFIG_FILE = "ipm.json"


def init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing ipm.json"),
) -> None:
    """Initialize ipm.json configuration for this project."""
    root = _find_root()
    config_path = root / CONFIG_FILE

    if config_path.exists() and not force:
        console.print(f"[yellow]{CONFIG_FILE} already exists.[/yellow] Use --force to overwrite.")
        raise typer.Exit(0)

    console.print("[bold]Initializing ipm[/bold]\n")

    # Detect and choose project
    projects = _detect_projects(root)

    if not projects:
        console.print("  [yellow]No Ignition projects detected.[/yellow]")
        source_name = "custom"
        source_path = Prompt.ask("  Path to script-python directory")
    else:
        console.print("  [bold]Available projects:[/bold]")
        for i, (name, path) in enumerate(projects.items(), 1):
            console.print(f"    {i}. [cyan]{name}[/cyan]  ({path})")
        console.print(f"    {len(projects) + 1}. [dim]custom path[/dim]")

        choice = Prompt.ask(
            "\n  Select project",
            choices=[str(i) for i in range(1, len(projects) + 2)],
        )
        choice_idx = int(choice) - 1

        if choice_idx < len(projects):
            source_name = list(projects.keys())[choice_idx]
            source_path = projects[source_name]
            console.print(f"\n  Using [cyan]{source_name}[/cyan] ({source_path})")
        else:
            source_name = Prompt.ask("  Project name")
            source_path = Prompt.ask("  Path to script-python directory")

    # Package name
    name = Prompt.ask("\n  Package name", default=f"{root.name}-{source_name}")

    # Version
    version = Prompt.ask("  Version", default="0.0.0")

    # Description
    description = Prompt.ask("  Description", default="")

    # Auto-discover modules
    full_source_path = root / source_path
    exports: list[str] = []
    if full_source_path.exists():
        for child in sorted(full_source_path.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and any(child.rglob("code.py")):
                exports.append(child.name)
        if exports:
            console.print(f"\n  Discovered modules: [cyan]{', '.join(exports)}[/cyan]")
            if not Confirm.ask("  Export all?", default=True):
                exports_str = Prompt.ask("  Modules to export (comma-separated)")
                exports = [e.strip() for e in exports_str.split(",")]

    # Warn about nested module paths
    nested = [e for e in exports if "/" in e]
    if nested:
        console.print(f"\n  [yellow]Warning:[/yellow] Nested module path(s) detected:")
        for n in nested:
            console.print(f"    [yellow]{n}[/yellow]")
        console.print(
            "\n  Nested exports (e.g. 'infrastructure/orm') reference a submodule rather"
            "\n  than a top-level package. Internal imports like"
            "\n  'from infrastructure.gateway import X' will NOT resolve in the built"
            "\n  package because only the nested path is included."
            "\n"
            "\n  This is fine if the module is self-contained (no sibling imports)."
            "\n  Otherwise, export the top-level module instead (e.g. 'infrastructure')."
        )

    # Build config
    config: dict = {
        "projects": {source_name: source_path},
        "packages": {
            name: {
                "version": version,
                "description": description,
                "source": source_name,
                "exports": exports,
            }
        },
    }

    # Add other detected projects to the projects map
    for pname, ppath in projects.items():
        if pname not in config["projects"]:
            config["projects"][pname] = ppath

    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    console.print(f"\n[green]Created {CONFIG_FILE}[/green]")
    console.print(f"\n  Next: [bold]ipm build[/bold] or [bold]ipm build {name}[/bold]")


def _find_root() -> Path:
    """Find project root."""
    current = Path.cwd()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path.cwd()


def _detect_projects(root: Path) -> dict[str, str]:
    """Detect Ignition projects and return name -> script-python path mapping."""
    projects: dict[str, str] = {}

    ignition_dir = root / "ignition"
    if not ignition_dir.exists():
        # Check for script-python at root
        if (root / "script-python").exists():
            projects["default"] = "script-python"
        return projects

    for project_dir in sorted(ignition_dir.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        script_path = project_dir / "ignition" / "script-python"
        if script_path.exists():
            projects[project_dir.name] = str(script_path.relative_to(root))

    return projects
