"""ipm CLI."""

import typer

from ipm.build import build
from ipm.init import init

app = typer.Typer(
    name="ipm",
    help="Ignition Package Maker - convert Ignition script-python modules into publishable Python packages.",
    no_args_is_help=True,
    add_completion=False,
)

app.command()(init)
app.command()(build)


if __name__ == "__main__":
    app()
