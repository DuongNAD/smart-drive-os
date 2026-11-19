"""smart_drive.core.exfat_shield - Root-Cause Prevention for Large-Cluster exFAT Bloat.

On exFAT volumes formatted with large clusters (e.g., Kingston XS2000 512 KB allocation units),
micro-file build artifacts and tool caches (Cargo incremental `.o`/`.rlib`, Ruff `.ruff_cache`,
Mypy JSON caches, uv/pip/npm caches) expand up to 100x-900x on disk due to cluster slack and
directory cluster overhead.

`exfat_shield` provides:
1. Read-only audit (`audit_exfat_shield`) inspecting toolchain environment variables and host
   storage volumes against the target drive's filesystem & cluster geometry.
2. Fast NTFS cache-host recommendation (`recommend_cache_base`) preferring secondary fixed NTFS
   drives with ample free space (e.g. `E:\\.smart_drive_caches`), falling back cleanly to local
   OS cache folders on `C:` or POSIX `~/.cache/smart-drive-dev`.
3. Persistent opt-in configuration (`apply_exfat_shield` / `revert_exfat_shield`) with manifest
   tracking so every change can be cleanly undone, plus exclusion of cache directories from
   Windows Search indexing on NTFS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from smart_drive.core.fsinfo import FilesystemInfo, cluster_size_text, detect_filesystem

LARGE_CLUSTER_THRESHOLD = 65_536  # 64 KB
DEFAULT_MIN_CACHE_FREE_BYTES = 20 * (1024 ** 3)  # 20 GiB
_CACHE_DIR_NAME = ".smart_drive_caches"
_LOCAL_CACHE_DIR_NAME = "smart_drive_caches"
_MANIFEST_NAME = "shield_manifest.json"
_POSIX_ENV_SCRIPT = "exfat-shield.sh"
_EXFAT_ROOT_GUARD = "Never point dev build caches at a large-cluster exFAT volume."


@dataclass(frozen=True)
class ShieldEnvSpec:
    """One environment variable controlling where a build/package/lint tool writes caches."""

    var: str
    subdir: str
    label: str
    description: str


SHIELD_ENV_SPECS: Tuple[ShieldEnvSpec, ...] = (
    ShieldEnvSpec(
        var="CARGO_TARGET_DIR",
        subdir="cargo-target",
        label="Rust Cargo target directory",
        description="Redirects Cargo build & incremental artifacts to a shared NTFS target directory across repos and worktrees.",
    ),
    ShieldEnvSpec(
        var="RUFF_CACHE_DIR",
        subdir="ruff-cache",
        label="Ruff linter cache",
        description="Redirects Ruff micro-file cache off exFAT (prevents ~99% cluster slack waste).",
    ),
    ShieldEnvSpec(
        var="MYPY_CACHE_DIR",
        subdir="mypy-cache",
        label="Mypy type-check cache",
        description="Redirects Mypy incremental JSON cache files onto small-cluster NTFS.",
    ),
    ShieldEnvSpec(
        var="UV_CACHE_DIR",
        subdir="uv-cache",
        label="uv Python package cache",
        description="Keeps Astral uv unpacked wheel and metadata trees on NTFS.",
    ),
    ShieldEnvSpec(
        var="PIP_CACHE_DIR",
        subdir="pip-cache",
        label="pip package cache",
        description="Keeps pip download and wheel build caches on NTFS.",
    ),
    ShieldEnvSpec(
        var="npm_config_cache",
        subdir="npm-cache",
        label="npm global _cacache",
        description="Redirects npm content-addressable cache micro-files off exFAT.",
    ),
    ShieldEnvSpec(
        var="PNPM_STORE_DIR",
        subdir="pnpm-store",
        label="pnpm global store",
        description="Keeps pnpm shared package store on NTFS.",
    ),
)

ADVISORY_ENV_VARS: Tuple[str, ...] = (
    "RUSTUP_HOME",
    "CARGO_HOME",
    "GRADLE_USER_HOME",
)


class EnvStore(Protocol):
    """Abstraction over persistent user environment storage (Win32 HKCU or test fake)."""

    def get(self, name: str) -> Optional[str]: ...
    def get_all(self) -> Dict[str, str]: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...
    def commit(self) -> None: ...


class _Win32UserEnvStore:
    """Reads/writes HKCU\\Environment on Windows using stdlib winreg and broadcasts WM_SETTINGCHANGE."""

    _SUBKEY = "Environment"

    def get_all(self) -> Dict[str, str]:
        if sys.platform != "win32":
            return {}
        import winreg

        out: Dict[str, str] = {}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._SUBKEY, 0, winreg.KEY_READ) as key:
                # QueryInfoKey returns (subkey_count, value_count, last_modified_ns).
                _, value_count, _ = winreg.QueryInfoKey(key)
                for idx in range(value_count):
                    try:
                        name, val, _ = winreg.EnumValue(key, idx)
                        if isinstance(name, str) and isinstance(val, str):
                            out[name] = val
                    except OSError:
                        continue
        except OSError:
            pass
        return out

    def get(self, name: str) -> Optional[str]:
        return self.get_all().get(name)

    def set(self, name: str, value: str) -> None:
        if sys.platform != "win32":
            return
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._SUBKEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))

    def delete(self, name: str) -> None:
        if sys.platform != "win32":
            return
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def commit(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            HWND_BROADCAST = wintypes.HWND(0xFFFF)
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            res = ctypes.c_size_t(0)
            ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
                HWND_BROADCAST,
                WM_SETTINGCHANGE,
                0,
                "Environment",
                SMTO_ABORTIFHUNG,
                1000,
                ctypes.byref(res),
            )
        except Exception:
            pass


class _PosixEnvStore:
    """In-memory collector for POSIX shell export script generation."""

    def __init__(self, script_path: Optional[Path] = None) -> None:
        self._vars: Dict[str, str] = {}
        self.script_path = script_path

    def get_all(self) -> Dict[str, str]:
        return dict(self._vars)

    def get(self, name: str) -> Optional[str]:
        return self._vars.get(name)

    def set(self, name: str, value: str) -> None:
        self._vars[name] = value

    def delete(self, name: str) -> None:
        self._vars.pop(name, None)

    def commit(self) -> None:
        if self.script_path is None:
            return
        self.script_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Generated by SmartDrive-OS exFAT Cache Shield — source from ~/.bashrc or ~/.zshrc",
        ]
        for key in sorted(self._vars):
            escaped = self._vars[key].replace('"', '\\"')
            lines.append(f'export {key}="{escaped}"')
        self.script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_home(home: Optional[Path] = None) -> Path:
    if home is not None:
        return Path(os.path.abspath(str(home)))
    for key in ("USERPROFILE", "HOME"):
        val = os.environ.get(key)
        if val:
            return Path(os.path.abspath(val))
    try:
        return Path.home()
    except Exception:
        return Path("C:/Users/Default") if sys.platform == "win32" else Path("/tmp")


def is_large_cluster_volume(info: FilesystemInfo) -> bool:
    """True when volume is large-cluster exFAT or has allocation unit >= 64 KB."""
    if info.block_size >= LARGE_CLUSTER_THRESHOLD:
        return True
    if info.is_exfat and info.block_size == 0:
        return True
    return False


def _detect_path_fs(
    path_str: str,
    fs_detector: Callable[[str], FilesystemInfo],
) -> FilesystemInfo:
    """Detects filesystem for a target path that may not exist yet by walking to its deepest existing ancestor."""
    direct = fs_detector(path_str)
    if direct.known:
        return direct
    curr = os.path.abspath(path_str)
    while curr and not os.path.exists(curr):
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return fs_detector(curr or path_str)


def discover_cache_host_candidates(
    volumes: Optional[Sequence[Dict[str, Any]]] = None,
    home: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Fast, zero-subprocess discovery of mounted volumes suitable for hosting micro-file build caches."""
    if volumes is not None:
        normalized: List[Dict[str, Any]] = []
        for v in volumes:
            normalized.append({
                "mount_point": str(v.get("mount_point") or v.get("path") or ""),
                "fs_type": str(v.get("fs_type", "unknown")).lower(),
                "block_size": int(v.get("block_size", 0) or 0),
                "free_bytes": int(v.get("free_bytes", 0) or 0),
                "total_bytes": int(v.get("total_bytes", 0) or 0),
                "is_system": bool(v.get("is_system", False)),
                "is_fixed": bool(v.get("is_fixed", True)),
            })
        return normalized

    resolved_home = _safe_home(home)
    if sys.platform != "win32":
        fs = _detect_path_fs(str(resolved_home), detect_filesystem)
        try:
            usage = shutil.disk_usage(str(resolved_home))
            free_b, total_b = int(usage.free), int(usage.total)
        except OSError:
            free_b, total_b = 0, 0
        return [{
            "mount_point": fs.mount_point or str(resolved_home),
            "fs_type": fs.fs_type,
            "block_size": fs.block_size,
            "free_bytes": free_b,
            "total_bytes": total_b,
            "is_system": True,
            "is_fixed": True,
        }]

    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mask = int(kernel32.GetLogicalDrives())
    sys_drive_raw = os.environ.get("SystemDrive", "C:").upper().rstrip("\\/")
    sys_letter = sys_drive_raw if sys_drive_raw.endswith(":") else sys_drive_raw + ":"

    candidates: List[Dict[str, Any]] = []
    DRIVE_FIXED = 3
    for idx in range(26):
        if not (mask & (1 << idx)):
            continue
        letter = f"{chr(65 + idx)}:"
        mount = f"{letter}\\"
        drive_type = int(kernel32.GetDriveTypeW(mount))
        is_fixed = (drive_type == DRIVE_FIXED)
        fs = detect_filesystem(mount)
        if not fs.known:
            continue
        try:
            usage = shutil.disk_usage(mount)
            free_b, total_b = int(usage.free), int(usage.total)
        except OSError:
            continue
        candidates.append({
            "mount_point": mount,
            "fs_type": fs.fs_type,
            "block_size": fs.block_size,
            "free_bytes": free_b,
            "total_bytes": total_b,
            "is_system": (letter.upper() == sys_letter),
            "is_fixed": is_fixed,
        })
    return candidates


def _is_small_cluster_local_candidate(vol: Mapping[str, Any]) -> bool:
    fs_type = str(vol.get("fs_type", "")).lower()
    unit_bytes = int(vol.get("block_size", 0) or 0)
    if fs_type == "exfat":
        return False
    if unit_bytes >= LARGE_CLUSTER_THRESHOLD:
        return False
    return bool(vol.get("is_fixed", True))


def recommend_cache_base(
    root_fs: FilesystemInfo,
    home: Optional[Path] = None,
    volumes: Optional[Sequence[Dict[str, Any]]] = None,
    min_free_bytes: int = DEFAULT_MIN_CACHE_FREE_BYTES,
) -> Dict[str, Any]:
    """Selects the best NTFS / small-cluster path to host redirected dev build caches."""
    resolved_home = _safe_home(home)
    vols = discover_cache_host_candidates(volumes=volumes, home=resolved_home)

    secondary = [
        v for v in vols
        if _is_small_cluster_local_candidate(v)
        and not v.get("is_system", False)
        and int(v.get("free_bytes", 0)) >= min_free_bytes
    ]
    if secondary:
        best = max(secondary, key=lambda v: int(v.get("free_bytes", 0)))
        mount = str(best["mount_point"]).rstrip("\\/")
        sep = "\\" if (len(mount) == 2 and mount[1] == ":") or "\\" in str(best["mount_point"]) else "/"
        rec_path = f"{mount}{sep}{_CACHE_DIR_NAME}"
        return {
            "path": rec_path,
            "filesystem": best["fs_type"],
            "free_bytes": int(best["free_bytes"]),
            "source": "secondary_ntfs",
            "reason": (
                f"Secondary small-cluster {best['fs_type'].upper()} volume ({mount}) "
                f"has {int(best['free_bytes']) / (1024 ** 3):.1f} GB free."
            ),
        }

    system_vols = [
        v for v in vols
        if _is_small_cluster_local_candidate(v) and v.get("is_system", False)
    ]
    if sys.platform == "win32" or (volumes and any("\\" in str(v.get("mount_point", "")) for v in vols)):
        local_app = os.environ.get("LOCALAPPDATA")
        base_local = Path(local_app) if local_app else (resolved_home / "AppData" / "Local")
        rec_path = str(base_local / _LOCAL_CACHE_DIR_NAME)
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base_cache = Path(xdg_cache) if xdg_cache else (resolved_home / ".cache")
        rec_path = str(base_cache / "smart-drive-dev")

    free_b = int(system_vols[0].get("free_bytes", 0)) if system_vols else 0
    fs_label = str(system_vols[0].get("fs_type", "local")) if system_vols else "local"
    return {
        "path": rec_path,
        "filesystem": fs_label,
        "free_bytes": free_b,
        "source": "system_local",
        "reason": "System small-cluster local storage fallback (no non-system NTFS volume >= 20 GB free).",
    }


def _read_cargo_config_target_dir(home: Path) -> Optional[str]:
    for name in ("config.toml", "config"):
        cfg_path = home / ".cargo" / name
        if not cfg_path.is_file():
            continue
        try:
            text = cfg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if line.lower().startswith("target-dir") and "=" in line:
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return None


def _default_env_store(cache_base: Optional[str] = None) -> EnvStore:
    if sys.platform == "win32":
        return _Win32UserEnvStore()
    script_path = Path(cache_base) / _POSIX_ENV_SCRIPT if cache_base else None
    return _PosixEnvStore(script_path=script_path)


def audit_exfat_shield(
    root: str,
    env: Optional[Mapping[str, str]] = None,
    cache_dir: Optional[str] = None,
    home: Optional[Path] = None,
    fs_info: Optional[FilesystemInfo] = None,
    fs_detector: Optional[Callable[[str], FilesystemInfo]] = None,
    volumes: Optional[Sequence[Dict[str, Any]]] = None,
    env_store: Optional[EnvStore] = None,
) -> Dict[str, Any]:
    """Read-only inspection of build-cache env vars and volume geometry around `root`."""
    root_abs = os.path.abspath(root)
    detector = fs_detector or detect_filesystem
    root_fs = fs_info if fs_info is not None else detector(root_abs)
    large_cluster = is_large_cluster_volume(root_fs)
    resolved_home = _safe_home(home)

    # Merge persisted HKCU\Environment behind process env when running live on Windows
    merged_env: Dict[str, str] = {}
    registry_vars: Dict[str, str] = {}
    if env is not None:
        merged_env = {str(k): str(v) for k, v in env.items() if v is not None}
    else:
        if env_store is not None:
            registry_vars = env_store.get_all()
        elif sys.platform == "win32" and home is None:
            registry_vars = _Win32UserEnvStore().get_all()
        merged_env.update(registry_vars)
        merged_env.update({k: v for k, v in os.environ.items()})

    rec = recommend_cache_base(root_fs, home=resolved_home, volumes=volumes)
    target_base = os.path.abspath(cache_dir) if cache_dir else rec["path"]

    variables: List[Dict[str, Any]] = []
    unprotected_count = 0
    risk_on_exfat_count = 0
    protected_count = 0

    root_drive = os.path.splitdrive(os.path.realpath(root_abs))[0].upper()

    for spec in SHIELD_ENV_SPECS:
        raw_val = merged_env.get(spec.var, "").strip()
        recommended_val = str(Path(target_base) / spec.subdir)
        source = "unset"
        if raw_val:
            if env is None and spec.var in os.environ:
                source = "process_env"
            elif spec.var in registry_vars:
                source = "user_registry"
            else:
                source = "env"

        if not raw_val:
            status = "unprotected" if large_cluster else "ok_local_fs"
            if large_cluster:
                unprotected_count += 1
            target_fs_summary = None
        else:
            target_fs = _detect_path_fs(raw_val, detector)
            if target_fs.known:
                points_onto_large = is_large_cluster_volume(target_fs)
            else:
                target_drive = os.path.splitdrive(os.path.abspath(raw_val))[0].upper()
                points_onto_large = large_cluster and bool(root_drive) and target_drive == root_drive
            target_fs_summary = target_fs.summary()
            if points_onto_large:
                status = "risk_on_exfat"
                risk_on_exfat_count += 1
            else:
                status = "protected"
                protected_count += 1

        variables.append({
            "var": spec.var,
            "label": spec.label,
            "description": spec.description,
            "status": status,
            "current_value": raw_val or None,
            "source": source,
            "target_filesystem": target_fs_summary,
            "recommended_value": recommended_val,
        })

    advisory_risks: List[Dict[str, Any]] = []
    for adv_var in ADVISORY_ENV_VARS:
        adv_val = merged_env.get(adv_var, "").strip()
        if not adv_val:
            continue
        adv_fs = _detect_path_fs(adv_val, detector)
        if adv_fs.known:
            adv_on_large = is_large_cluster_volume(adv_fs)
        else:
            adv_drive = os.path.splitdrive(os.path.abspath(adv_val))[0].upper()
            adv_on_large = large_cluster and bool(root_drive) and adv_drive == root_drive
        if adv_on_large:
            advisory_risks.append({
                "var": adv_var,
                "current_value": adv_val,
                "filesystem": adv_fs.summary(),
                "message": (
                    f"{adv_var}={adv_val} points to a large-cluster exFAT drive; "
                    "move it back to NTFS or unset it to avoid tiny-file slack explosion (e.g. rustup HTML docs)."
                ),
            })

    cargo_cfg_target = _read_cargo_config_target_dir(resolved_home)
    needs_attention = large_cluster and (unprotected_count > 0 or risk_on_exfat_count > 0 or len(advisory_risks) > 0)

    return {
        "root": root_abs,
        "filesystem": root_fs.to_dict(),
        "cluster_size_label": cluster_size_text(root_fs.block_size) if root_fs.block_size > 0 else "unknown",
        "is_large_cluster": large_cluster,
        "recommended_cache_base": rec,
        "target_cache_base": target_base,
        "variables": variables,
        "advisory_risks": advisory_risks,
        "cargo_config_target_dir": cargo_cfg_target,
        "summary": {
            "total_vars": len(SHIELD_ENV_SPECS),
            "protected_count": protected_count,
            "unprotected_count": unprotected_count,
            "risk_on_exfat_count": risk_on_exfat_count,
            "advisory_risk_count": len(advisory_risks),
            "needs_attention": needs_attention,
        },
        "needs_attention": needs_attention,
        "notes": [
            "Environment variables protect Rust Cargo targets (including multi-worktree builds), Ruff, Mypy, uv, pip, npm global cache, and pnpm store globally.",
            "Per-project node_modules/ folders cannot be relocated by an env var alone; prefer 'pnpm' (links from PNPM_STORE_DIR) or run 'smart-drive bloat --kind node-modules' periodically.",
        ],
    }


def apply_exfat_shield(
    root: str,
    cache_dir: Optional[str] = None,
    dry_run: bool = True,
    force: bool = False,
    env: Optional[Mapping[str, str]] = None,
    env_store: Optional[EnvStore] = None,
    fs_info: Optional[FilesystemInfo] = None,
    fs_detector: Optional[Callable[[str], FilesystemInfo]] = None,
    volumes: Optional[Sequence[Dict[str, Any]]] = None,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    """Configures persistent NTFS cache-redirection environment variables; rejects exFAT targets."""
    detector = fs_detector or detect_filesystem
    store = env_store if env_store is not None else _default_env_store()
    audit = audit_exfat_shield(
        root=root,
        env=env,
        cache_dir=cache_dir,
        home=home,
        fs_info=fs_info,
        fs_detector=detector,
        volumes=volumes,
        env_store=store,
    )
    target_base = str(audit["target_cache_base"])
    target_fs = _detect_path_fs(target_base, detector)

    # Hard guard: NEVER point cache base at a large-cluster exFAT volume or inside an unknown large root
    root_abs = os.path.abspath(root)
    if target_fs.known:
        target_is_unsafe = is_large_cluster_volume(target_fs) or target_fs.is_exfat
    else:
        target_is_unsafe = (
            bool(audit["is_large_cluster"])
            and os.path.splitdrive(root_abs)[0].upper() == os.path.splitdrive(os.path.abspath(target_base))[0].upper()
            and bool(os.path.splitdrive(root_abs)[0])
        )
    if target_is_unsafe:
        raise ValueError(
            f"Refusing cache directory '{target_base}': volume is {target_fs.summary()}. "
            f"{_EXFAT_ROOT_GUARD} Select an NTFS or small-cluster local volume instead."
        )

    planned_vars: Dict[str, str] = {}
    preserved_vars: List[str] = []
    skipped_risk_vars: List[str] = []

    for item in audit["variables"]:
        var = item["var"]
        status = item["status"]
        new_val = str(Path(target_base) / next(s.subdir for s in SHIELD_ENV_SPECS if s.var == var))
        if status == "protected":
            preserved_vars.append(var)
        elif status == "risk_on_exfat" and not force:
            skipped_risk_vars.append(var)
        elif status in ("unprotected", "risk_on_exfat", "ok_local_fs"):
            planned_vars[var] = new_val

    if dry_run:
        return {
            "status": "dry_run",
            "cache_base": target_base,
            "target_filesystem": target_fs.to_dict(),
            "planned_vars": planned_vars,
            "preserved_vars": preserved_vars,
            "skipped_risk_vars": skipped_risk_vars,
        }

    # On POSIX default store, bind script output path under target_base
    if env_store is None and isinstance(store, _PosixEnvStore) and store.script_path is None:
        store.script_path = Path(target_base) / _POSIX_ENV_SCRIPT

    Path(target_base).mkdir(parents=True, exist_ok=True)
    for new_val in planned_vars.values():
        Path(new_val).mkdir(parents=True, exist_ok=True)

    # Exclude cache root from Windows Search indexing on NTFS best-effort
    if target_fs.fs_type == "ntfs":
        try:
            from smart_drive.core.drive_detector import FilesystemType, get_filesystem_adapter

            get_filesystem_adapter(FilesystemType.NTFS, target_fs.block_size or 4096).set_content_indexing(
                target_base, exclude=True
            )
        except Exception:
            pass

    manifest_path = Path(target_base) / _MANIFEST_NAME
    previous_values: Dict[str, Optional[str]] = {}
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("previous_values"), dict):
                previous_values = dict(loaded["previous_values"])
        except Exception:
            previous_values = {}

    for var, new_val in planned_vars.items():
        if var not in previous_values:
            previous_values[var] = store.get(var)
        store.set(var, new_val)
        if env is None:
            os.environ[var] = new_val
    store.commit()

    manifest_payload = {
        "version": "1.0.0",
        "status": "active",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cache_base": target_base,
        "applied_vars": dict(planned_vars),
        "previous_values": previous_values,
    }
    manifest_path.write_text(json.dumps(manifest_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "status": "applied",
        "cache_base": target_base,
        "target_filesystem": target_fs.to_dict(),
        "applied_vars": planned_vars,
        "preserved_vars": preserved_vars,
        "skipped_risk_vars": skipped_risk_vars,
        "manifest_path": str(manifest_path),
        "posix_script": str(Path(target_base) / _POSIX_ENV_SCRIPT) if sys.platform != "win32" else None,
    }


def revert_exfat_shield(
    cache_dir: str,
    dry_run: bool = True,
    env_store: Optional[EnvStore] = None,
    mutate_process_env: Optional[bool] = None,
) -> Dict[str, Any]:
    """Reverts env vars recorded in `<cache_dir>/shield_manifest.json` back to their previous values."""
    base = Path(os.path.abspath(cache_dir))
    manifest_path = base / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Shield manifest not found at '{manifest_path}'. Nothing to revert.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    previous_values: Dict[str, Optional[str]] = dict(manifest.get("previous_values") or {})
    if dry_run:
        return {
            "status": "dry_run",
            "cache_base": str(base),
            "planned_restore": previous_values,
        }

    store = env_store if env_store is not None else _default_env_store(str(base))
    do_proc_env = (env_store is None) if mutate_process_env is None else mutate_process_env

    for var, prev_val in previous_values.items():
        if prev_val is None:
            store.delete(var)
            if do_proc_env:
                os.environ.pop(var, None)
        else:
            store.set(var, prev_val)
            if do_proc_env:
                os.environ[var] = prev_val
    store.commit()

    manifest["status"] = "reverted"
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "status": "reverted",
        "cache_base": str(base),
        "restored_vars": previous_values,
        "manifest_path": str(manifest_path),
    }


__all__ = [
    "ADVISORY_ENV_VARS",
    "LARGE_CLUSTER_THRESHOLD",
    "SHIELD_ENV_SPECS",
    "ShieldEnvSpec",
    "apply_exfat_shield",
    "audit_exfat_shield",
    "discover_cache_host_candidates",
    "is_large_cluster_volume",
    "recommend_cache_base",
    "revert_exfat_shield",
]
