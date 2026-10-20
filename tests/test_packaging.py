"""tests/test_packaging.py - The wheel built from pyproject.toml carries what the CLI and MCP server need.

100% Python Standard Library unittest. Zero external dependencies.
pyproject.toml is the only file a build backend reads, so a slip in it reaches PyPI without any other
test noticing. Verifies:
1. License (PEP 639): `license = "MIT"` is an SPDX expression, `license-files` names a LICENSE file that
   exists, and no `License ::` classifier sits beside the expression (setuptools 77+ fails the build on
   that pair). `[build-system]` asks for setuptools 77+, the first release that reads these fields.
2. Package data: every non-Python file under smart_drive/ is covered by a `[tool.setuptools.package-data]`
   pattern, so a data file the package reads at run time cannot silently miss the wheel (today only the
   `py.typed` marker). The check also runs against a throwaway package to prove that it can fail.
3. Entry points: every `[project.scripts]` target imports and is callable, so an installed command cannot
   die on start, and one script is named like the package: `uvx <package>` and `pipx run <package>` start
   the console script named like the package, and MCP registry entries use `uvx smart-drive-os`.
4. Description: one short line that names what the tool mainly does (organize, relink, clean, search);
   PyPI shows it under the project name and in search results.
5. One metadata source: no setup.py at the repository root, and no setup.cfg but the bare `[egg_info]`
   stanza that sdist adds to every source archive. pip builds from pyproject.toml alone (PEP 517/660,
   pip 21.3+), and a second source is one more place for the version, license or classifiers to drift
   away from it.
"""

from __future__ import annotations

import configparser
import glob
import importlib
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Any, List, Sequence, Set, Tuple

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import tomllib
except ImportError:  # Python < 3.11 fallback
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:  # no TOML parser available: the pyproject metadata tests are skipped
        tomllib = None  # type: ignore

_NO_TOML = "TOML parsing needs Python 3.11+ (tomllib) or the tomli package"

from tests.helpers import SmartDriveTestCase

PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
PACKAGE_DIR = PROJECT_ROOT / "smart_drive"

MAX_DESCRIPTION_LENGTH = 200

# Python source and bytecode are shipped by `[tool.setuptools.packages.find]`, never by a package-data pattern.
_CODE_SUFFIXES = frozenset({".py", ".pyc", ".pyo"})
# What an operating system leaves in a checkout (macOS writes `._*` AppleDouble files onto exFAT drives):
# litter, never a data file of the package.
_OS_JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def unpackaged_data_files(package_dir: Path, patterns: Sequence[str]) -> List[str]:
    """The non-Python files under package_dir that no package-data pattern covers, as 'a/b.ext' paths.

    The patterns are expanded the way setuptools' build_py does it, with
    glob.glob(<package dir>/<pattern>, recursive=True): `*` stays inside one directory (fnmatch's `*`
    would also cross '/' and let `data/*.json` seem to cover `data/v2/x.json`) and `**` spans directories.
    """
    covered: Set[str] = set()
    for pattern in patterns:
        for match in glob.glob(os.path.join(glob.escape(str(package_dir)), *pattern.split("/")), recursive=True):
            covered.add(Path(match).relative_to(package_dir).as_posix())

    uncovered: List[str] = []
    for dirpath, dirnames, filenames in os.walk(package_dir):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for filename in filenames:
            if os.path.splitext(filename)[1] in _CODE_SUFFIXES:
                continue
            if filename in _OS_JUNK_NAMES or filename.startswith("._"):
                continue
            rel_path = (Path(dirpath) / filename).relative_to(package_dir).as_posix()
            if rel_path not in covered:
                uncovered.append(rel_path)
    return sorted(uncovered)


@unittest.skipIf(tomllib is None, _NO_TOML)
class TestPyprojectPackaging(unittest.TestCase):
    """The pyproject.toml fields that a build backend turns into the wheel's metadata and contents."""

    @classmethod
    def setUpClass(cls) -> None:
        with open(PYPROJECT_PATH, "rb") as f:
            cls.data = tomllib.load(f)
        cls.project = cls.data.get("project", {})

    # --- License (PEP 639) ---

    def test_license_is_an_spdx_expression(self) -> None:
        """`license = "MIT"` is a plain SPDX string.

        The `{ text = "MIT" }` table form is deprecated and makes every build print a warning.
        """
        self.assertEqual(self.project.get("license"), "MIT")

    def test_license_files_names_an_existing_license(self) -> None:
        """`license-files` lists LICENSE and that file exists, so the wheel carries the license text."""
        license_files = self.project.get("license-files")
        self.assertIsInstance(license_files, list, "[project] needs license-files = [\"LICENSE\"]")
        self.assertIn("LICENSE", license_files)
        self.assertTrue((PROJECT_ROOT / "LICENSE").is_file(), "LICENSE is missing at the repository root")

    def test_no_license_classifier_beside_the_license_expression(self) -> None:
        """setuptools 77+ raises InvalidConfigError for a `License ::` classifier next to a license expression."""
        license_classifiers = [c for c in self.project.get("classifiers", []) if c.startswith("License ::")]
        self.assertEqual(license_classifiers, [], "the SPDX license expression replaces the License classifiers")

    def test_build_requires_setuptools_77_or_newer(self) -> None:
        """setuptools 77.0 is the first release that reads a license expression and `license-files`."""
        requires = self.data.get("build-system", {}).get("requires", [])
        minimums: List[Tuple[int, ...]] = []
        for requirement in requires:
            bound = re.match(r"\s*setuptools\s*>=\s*(\d+(?:\.\d+)*)", requirement, re.IGNORECASE)
            if bound:
                minimums.append(tuple(int(part) for part in bound.group(1).split(".")))
        self.assertTrue(minimums, f"[build-system].requires needs a 'setuptools>=<version>' entry, got {requires}")
        message = f"[build-system].requires must ask for setuptools>=77, got {requires}"
        self.assertGreaterEqual(max(minimums), (77,), message)

    # --- Package data ---

    def test_every_data_file_of_the_package_is_in_package_data(self) -> None:
        """A file under smart_drive/ that is not Python and not in package-data silently misses the wheel."""
        patterns = self.data.get("tool", {}).get("setuptools", {}).get("package-data", {}).get("smart_drive")
        self.assertIsInstance(patterns, list, "[tool.setuptools.package-data] needs a 'smart_drive' list")
        self.assertTrue(PACKAGE_DIR.is_dir(), "smart_drive package directory not found")
        missing = unpackaged_data_files(PACKAGE_DIR, patterns)
        self.assertEqual(
            missing,
            [],
            "The wheel would leave these files out. Add a pattern, relative to smart_drive/, "
            f"to [tool.setuptools.package-data] smart_drive: {missing}",
        )

    # --- Entry points ---

    def test_every_console_script_target_imports_and_is_callable(self) -> None:
        """Each `[project.scripts]` value is `module:function`; a typo installs a command that dies on start."""
        scripts = self.project.get("scripts", {})
        self.assertTrue(scripts, "[project.scripts] is empty: the wheel would install no command")
        for name, target in sorted(scripts.items()):
            with self.subTest(script=name):
                self.assertRegex(target, r"^[\w.]+:[\w.]+$", f"[project.scripts] {name} must be 'module:function'")
                module_name, _, attr_path = target.partition(":")
                obj: Any = importlib.import_module(module_name)
                for attr in attr_path.split("."):
                    obj = getattr(obj, attr)
                self.assertTrue(callable(obj), f"{target} (script {name}) is not callable")

    def test_a_console_script_is_named_like_the_package(self) -> None:
        """`uvx <package>` and `pipx run <package>` start the console script named like the package.

        MCP registry entries use `uvx smart-drive-os`, so the entry named exactly like [project].name must stay.
        """
        name = self.project.get("name")
        self.assertIsInstance(name, str, "[project] needs a name")
        scripts = self.project.get("scripts", {})
        self.assertIn(
            name,
            scripts,
            f"[project.scripts] needs an entry named exactly like [project].name ({name!r}), has {sorted(scripts)}",
        )

    # --- Description ---

    def test_description_is_one_short_line(self) -> None:
        """PyPI shows the description as one line under the project name; core metadata allows no line break."""
        description = self.project.get("description")
        self.assertIsInstance(description, str, "[project] needs a description")
        self.assertTrue(description, "description is empty")
        self.assertEqual(description, description.strip(), "description has leading or trailing whitespace")
        self.assertNotRegex(description, r"[\r\n]", "description must be a single line")
        self.assertLessEqual(
            len(description),
            MAX_DESCRIPTION_LENGTH,
            f"description is {len(description)} characters long; keep it to {MAX_DESCRIPTION_LENGTH} or fewer",
        )

    def test_description_names_what_the_tool_mainly_does(self) -> None:
        """Someone looking to organize a drive, relink moved paths, clean junk or search files must find it."""
        description = str(self.project.get("description", "")).lower()
        for word in ("organize", "relink", "clean", "search"):
            with self.subTest(word=word):
                self.assertIn(word, description)


class TestSingleMetadataSource(unittest.TestCase):
    """pyproject.toml is the only packaging metadata in the repository."""

    def test_no_setup_py_at_the_root(self) -> None:
        """pip builds from pyproject.toml alone (PEP 517/660, pip 21.3+); a second source can only drift from it."""
        self.assertFalse(
            (PROJECT_ROOT / "setup.py").exists(),
            "delete setup.py: pyproject.toml is the only packaging metadata",
        )

    def test_setup_cfg_holds_nothing_but_the_sdist_egg_info_stanza(self) -> None:
        """No setup.cfg either, bar the bare `[egg_info]` stanza that sdist writes into every source archive.

        The archive carries this suite too, so a run from an unpacked sdist meets that generated file.
        """
        setup_cfg = PROJECT_ROOT / "setup.cfg"
        if not setup_cfg.exists():
            return
        parser = configparser.ConfigParser()
        parser.read(setup_cfg, encoding="utf-8")
        extra_sections = [section for section in parser.sections() if section != "egg_info"]
        self.assertEqual(
            extra_sections,
            [],
            "setup.cfg may only hold the [egg_info] stanza that sdist adds; the metadata belongs in pyproject.toml",
        )


class TestPackageDataCheck(SmartDriveTestCase):
    """unpackaged_data_files must report a data file no pattern covers, or the package-data test proves nothing."""

    def make_package(self) -> Path:
        """A throwaway package: Python files, bytecode and OS litter (never data), plus three data files."""
        package = self.test_dir / "pkg"
        for rel_path in (
            "__init__.py",
            "core/engine.py",
            "core/__pycache__/engine.cpython-39.pyc",
            ".DS_Store",
            "._py.typed",
            "py.typed",
            "data/schema.json",
            "data/v2/schema.json",
        ):
            path = package / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        return package

    def test_reports_the_data_files_no_pattern_covers(self) -> None:
        """Python files, bytecode and OS litter are not data; `data/*.json` does not reach into data/v2/."""
        package = self.make_package()
        self.assertEqual(
            unpackaged_data_files(package, ["py.typed"]),
            ["data/schema.json", "data/v2/schema.json"],
        )
        self.assertEqual(
            unpackaged_data_files(package, ["py.typed", "data/*.json"]),
            ["data/v2/schema.json"],
        )

    def test_reports_nothing_when_every_data_file_is_covered(self) -> None:
        """Explicit patterns and a `**` pattern (zero or more directories) both cover the data files."""
        package = self.make_package()
        self.assertEqual(unpackaged_data_files(package, ["py.typed", "data/*.json", "data/v2/*.json"]), [])
        self.assertEqual(unpackaged_data_files(package, ["py.typed", "data/**/*.json"]), [])


if __name__ == "__main__":
    unittest.main()
