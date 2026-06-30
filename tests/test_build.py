"""Tests for the build module."""

import json
from pathlib import Path

from ipm.build import _convert_module, _discover_modules, Config, PackageConfig


class TestDiscoverModules:
    def test_finds_modules_with_code_py(self, tmp_path: Path):
        (tmp_path / "infrastructure" / "repo").mkdir(parents=True)
        (tmp_path / "infrastructure" / "repo" / "code.py").write_text("x = 1")
        (tmp_path / "entity" / "node").mkdir(parents=True)
        (tmp_path / "entity" / "node" / "code.py").write_text("y = 2")
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "empty_dir").mkdir()

        modules = _discover_modules(tmp_path)
        assert "infrastructure" in modules
        assert "entity" in modules
        assert ".hidden" not in modules
        assert "empty_dir" not in modules

    def test_ignores_dirs_without_code_py(self, tmp_path: Path):
        (tmp_path / "nocode" / "sub").mkdir(parents=True)
        (tmp_path / "nocode" / "sub" / "readme.txt").write_text("hi")

        modules = _discover_modules(tmp_path)
        assert modules == []


class TestConvertModule:
    def test_leaf_modules_become_py_files(self, tmp_path: Path):
        """script-python/infra/repo/code.py -> infra/repo.py"""
        source = tmp_path / "source" / "infra"
        target = tmp_path / "target" / "infra"
        (source / "repo").mkdir(parents=True)
        (source / "repo" / "code.py").write_text("class Repo:\n    pass\n")
        (source / "cache").mkdir(parents=True)
        (source / "cache" / "code.py").write_text("class Cache:\n    pass\n")

        count = _convert_module(source, target, "infra")

        assert count == 2
        assert (target / "__init__.py").exists()
        assert (target / "repo.py").exists()
        assert (target / "cache.py").exists()
        assert "class Repo" in (target / "repo.py").read_text()
        assert "class Cache" in (target / "cache.py").read_text()

    def test_nested_modules_become_subpackages(self, tmp_path: Path):
        """script-python/entity/node/node/code.py -> entity/node/__init__.py + node.py"""
        source = tmp_path / "source" / "entity"
        target = tmp_path / "target" / "entity"
        (source / "node" / "node").mkdir(parents=True)
        (source / "node" / "node" / "code.py").write_text("class Node: pass")
        (source / "node" / "inventory").mkdir(parents=True)
        (source / "node" / "inventory" / "code.py").write_text("class Inv: pass")

        count = _convert_module(source, target, "entity")

        assert (target / "__init__.py").exists()
        assert (target / "node" / "__init__.py").exists()
        assert (target / "node" / "node.py").exists()
        assert (target / "node" / "inventory.py").exists()
        assert "class Node" in (target / "node" / "node.py").read_text()

    def test_code_py_at_package_level(self, tmp_path: Path):
        """If a dir has both code.py AND subdirs, code.py becomes __init__.py content."""
        source = tmp_path / "source" / "util"
        target = tmp_path / "target" / "util"
        (source / "sub").mkdir(parents=True)
        (source / "sub" / "code.py").write_text("helper = 1")
        (source / "code.py").write_text("# util top-level code\nVERSION = 1\n")

        count = _convert_module(source, target, "util")

        assert (target / "__init__.py").read_text() == "# util top-level code\nVERSION = 1\n"
        assert (target / "sub.py").read_text() == "helper = 1"
        assert count == 2  # __init__.py with content + sub.py

    def test_empty_init_when_no_code_py(self, tmp_path: Path):
        """Package dirs without their own code.py get empty __init__.py."""
        source = tmp_path / "source" / "infra"
        target = tmp_path / "target" / "infra"
        (source / "repo").mkdir(parents=True)
        (source / "repo" / "code.py").write_text("x = 1")

        _convert_module(source, target, "infra")

        assert (target / "__init__.py").read_text() == ""


class TestConfig:
    def test_load_multi_package(self, tmp_path: Path):
        config_data = {
            "projects": {
                "global": "ignition/global/ignition/script-python",
            },
            "packages": {
                "verkor-infrastructure": {
                    "version": "1.0.0",
                    "description": "Core infra",
                    "source": "global",
                    "exports": ["infrastructure"],
                    "dependencies": ["verkor-entity>=0.1.0"],
                },
                "verkor-entity": {
                    "version": "0.5.0",
                    "source": "global",
                    "exports": ["entity"],
                },
            },
        }
        (tmp_path / "ipm.json").write_text(json.dumps(config_data))

        config = Config.load(tmp_path)
        assert "verkor-infrastructure" in config.packages
        assert "verkor-entity" in config.packages
        assert config.packages["verkor-infrastructure"].version == "1.0.0"
        assert config.packages["verkor-infrastructure"].dependencies == ["verkor-entity>=0.1.0"]
        assert config.packages["verkor-entity"].exports == ["entity"]

    def test_resolve_source_path(self, tmp_path: Path):
        config = Config(
            projects={"global": "ignition/global/ignition/script-python"},
            packages={},
        )
        pkg = PackageConfig(name="test", version="1.0.0", source="global")
        assert config.resolve_source_path(pkg) == "ignition/global/ignition/script-python"

    def test_resolve_source_path_direct(self, tmp_path: Path):
        config = Config(projects={}, packages={})
        pkg = PackageConfig(name="test", version="1.0.0", source="some/path/script-python")
        assert config.resolve_source_path(pkg) == "some/path/script-python"

    def test_load_legacy_format(self, tmp_path: Path):
        """Backwards compat with old single-package format."""
        config_data = {
            "package": {
                "name": "old-style",
                "version": "0.1.0",
                "source": "script-python",
                "exports": ["mymod"],
            }
        }
        (tmp_path / "ipm.json").write_text(json.dumps(config_data))

        config = Config.load(tmp_path)
        assert "old-style" in config.packages
        assert config.packages["old-style"].exports == ["mymod"]


class TestNoHoisting:
    """Exports keep their original module name - no wrapper package."""

    def test_single_export_preserves_module(self, tmp_path: Path):
        """
        infrastructure/repo/code.py -> src/infrastructure/repo.py
        (NOT src/infra/infrastructure/repo.py)
        """
        source = tmp_path / "script-python" / "infrastructure"
        src = tmp_path / "output" / "src"
        target = src / "infrastructure"
        (source / "repo").mkdir(parents=True)
        (source / "repo" / "code.py").write_text("class Repo: pass")
        (source / "orm").mkdir(parents=True)
        (source / "orm" / "code.py").write_text("class Orm: pass")

        src.mkdir(parents=True)
        _convert_module(source, target, "infrastructure")

        assert (target / "__init__.py").exists()
        assert (target / "repo.py").exists()
        assert (target / "orm.py").exists()
        # No wrapper directory
        assert not (src / "infra").exists()
