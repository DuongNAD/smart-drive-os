import os

KNOWN_CACHES = {
    "huggingface": {
        "title": "HuggingFace Hub Cache",
        "category": "AI Models & Weights",
        "env_var": "HF_HOME",
        "default_rel": os.path.join(".cache", "huggingface"),
        "base_env": "USERPROFILE",
    },
    "ollama": {
        "title": "Ollama Model Weights",
        "category": "AI Models & Weights",
        "env_var": "OLLAMA_MODELS",
        "default_rel": os.path.join(".ollama", "models"),
        "base_env": "USERPROFILE",
    },
    "pytorch": {
        "title": "PyTorch Cache",
        "category": "AI Models & Weights",
        "env_var": "TORCH_HOME",
        "default_rel": os.path.join(".cache", "torch"),
        "base_env": "USERPROFILE",
    },
    "pip": {
        "title": "pip Package Cache",
        "category": "Package Managers",
        "env_var": "PIP_CACHE_DIR",
        "default_rel": os.path.join("pip", "cache"),
        "base_env": "LOCALAPPDATA",
        "alt_default_rel": "pip",
    },
    "npm": {
        "title": "npm Cache",
        "category": "Package Managers",
        "env_var": "npm_config_cache",
        "default_rel": "npm-cache",
        "base_env": "APPDATA",
    },
    "uv": {
        "title": "uv Cache",
        "category": "Package Managers",
        "env_var": "UV_CACHE_DIR",
        "default_rel": os.path.join("uv", "cache"),
        "base_env": "LOCALAPPDATA",
        "alt_default_rel": "uv",
    },
    "conda_user": {
        "title": "Conda User Cache",
        "category": "Package Managers",
        "default_rel": os.path.join(".conda", "pkgs"),
        "base_env": "USERPROFILE",
    },
    "miniconda": {
        "title": "Miniconda Packages",
        "category": "Package Managers",
        "default_rel": os.path.join("miniconda3", "pkgs"),
        "base_env": "USERPROFILE",
    },
    "anaconda": {
        "title": "Anaconda Packages",
        "category": "Package Managers",
        "default_rel": os.path.join("anaconda3", "pkgs"),
        "base_env": "USERPROFILE",
    },
    "gradle": {
        "title": "Gradle Cache",
        "category": "Build Systems",
        "env_var": "GRADLE_USER_HOME",
        "default_rel": os.path.join(".gradle", "caches"),
        "base_env": "USERPROFILE",
    },
    "docker_wsl": {
        "title": "Docker Desktop WSL2 Virtual Disks",
        "category": "Containerization",
        "default_rel": os.path.join("Docker", "wsl"),
        "base_env": "LOCALAPPDATA",
    },
    "cargo": {
        "title": "Cargo Package Cache",
        "category": "Package Managers",
        "default_rel": os.path.join(".cargo", "registry", "cache"),
        "base_env": "USERPROFILE",
    },
}

def resolve_path(cfg):
    env_v = cfg.get("env_var")
    if env_v and os.environ.get(env_v):
        p = os.environ[env_v]
        if os.path.exists(p):
            return os.path.abspath(p)
    base = os.environ.get(cfg.get("base_env", "USERPROFILE"), os.path.expanduser("~"))
    primary = os.path.abspath(os.path.join(base, cfg["default_rel"]))
    if os.path.exists(primary):
        return primary
    alt = cfg.get("alt_default_rel")
    if alt:
        secondary = os.path.abspath(os.path.join(base, alt))
        if os.path.exists(secondary):
            return secondary
    return primary

def is_junction(path):
    if not os.path.exists(path):
        return False
    try:
        st = os.lstat(path)
        return bool(getattr(st, "st_file_attributes", 0) & 0x0400)
    except OSError:
        return False

def get_dir_size_fast(path, max_depth=4):
    total = 0
    count = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    count += 1
                elif entry.is_dir(follow_symlinks=False):
                    sub_total, sub_count = get_dir_size_fast(entry.path, max_depth - 1) if max_depth > 0 else (0, 0)
                    total += sub_total
                    count += sub_count
            except OSError:
                pass
    except OSError:
        pass
    return total, count

print(f"{'Cache ID':12} | {'Category':18} | {'Status':12} | {'Size':>10} | {'Path'}")
print("-" * 90)
for cid, cfg in KNOWN_CACHES.items():
    p = resolve_path(cfg)
    exists = os.path.exists(p)
    junc = is_junction(p)
    status = "OFFLOADED" if junc else ("FOUND" if exists else "NOT FOUND")
    if exists and not junc:
        sz, cnt = get_dir_size_fast(p, max_depth=3)
        size_str = f"{sz / (1024*1024):.1f} MB"
    elif junc:
        target = os.readlink(p).replace("\\\\?\\", "")
        size_str = f"-> {target[:20]}..."
    else:
        size_str = "0 MB"
    print(f"{cid:12} | {cfg['category']:18} | {status:12} | {size_str:>10} | {p}")
