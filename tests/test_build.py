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
        source = tmp_path / "source" / "util"
        target = tmp_path / "target" / "util"
        (source / "sub").mkdir(parents=True)
        (source / "sub" / "code.py").write_text("helper = 1")
        (source / "code.py").write_text("# util top-level code\nVERSION = 1\n")

        count = _convert_module(source, target, "util")

        assert (target / "__init__.py").read_text() == "# util top-level code\nVERSION = 1\n"
        assert (target / "sub.py").read_text() == "helper = 1"
        assert count == 2

    def test_empty_init_when_no_code_py(self, tmp_path: Path):
        source = tmp_path / "source" / "infra"
        target = tmp_path / "target" / "infra"
        (source / "repo").mkdir(parents=True)
        (source / "repo" / "code.py").write_text("x = 1")

        _convert_module(source, target, "infra")

        assert (target / "__init__.py").read_text() == ""


class TestConfig:
    def test_load_minimal(self, tmp_path: Path):
        config_data = {
            "projects": {
                "global": "ignition/global/ignition/script-python",
            },
            "packages": {
                "slowrm": {
                    "source": "global",
                    "exports": ["slowrm"],
                },
            },
        }
        (tmp_path / "ipm.json").write_text(json.dumps(config_data))

        config = Config.load(tmp_path)
        assert "slowrm" in config.packages
        assert config.packages["slowrm"].source == "global"
        assert config.packages["slowrm"].exports == ["slowrm"]

    def test_resolve_source_path(self, tmp_path: Path):
        config = Config(
            projects={"global": "ignition/global/ignition/script-python"},
            packages={},
        )
        pkg = PackageConfig(name="test", source="global")
        assert config.resolve_source_path(pkg) == "ignition/global/ignition/script-python"

    def test_resolve_source_path_direct(self, tmp_path: Path):
        config = Config(projects={}, packages={})
        pkg = PackageConfig(name="test", source="some/path/script-python")
        assert config.resolve_source_path(pkg) == "some/path/script-python"

    def test_multiple_packages(self, tmp_path: Path):
        config_data = {
            "projects": {"global": "ignition/global/ignition/script-python"},
            "packages": {
                "pkg-a": {"source": "global", "exports": ["mod_a"]},
                "pkg-b": {"source": "global", "exports": ["mod_b"]},
            },
        }
        (tmp_path / "ipm.json").write_text(json.dumps(config_data))

        config = Config.load(tmp_path)
        assert "pkg-a" in config.packages
        assert "pkg-b" in config.packages


class TestNoHoisting:
    def test_single_export_preserves_module(self, tmp_path: Path):
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
        assert not (src / "infra").exists()
