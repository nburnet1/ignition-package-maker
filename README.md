# ipm - Ignition Package Maker

Convert Ignition `script-python` modules into publishable Python packages.

## Install

```bash
pip install ignition-package-maker
```

## Getting Started

```bash
# Initialize - detects your Ignition projects and scaffolds ipm.json
ipm init

# Build - converts script-python modules into a standard Python package
ipm build

# Publish
cd ipm/your-package && python -m build && twine upload dist/*
```

## What it does

ipm takes Ignition's `code.py` / `resource.json` directory convention and outputs a standard Python `src/` layout with a `pyproject.toml`. The distribution name and import name are independent - your code stays unchanged.

Consumers install with `pip install --target ./site-packages your-package` and mount `site-packages` into the gateway.

Import validation catches missing modules before you ship.

## Documentation

[https://nburnet1.github.io/ignition-package-maker](https://nburnet1.github.io/ignition-package-maker)
