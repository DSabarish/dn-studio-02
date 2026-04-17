#!/usr/bin/env python3
# zip_it.py — Backup + Claude-bundle encoder/decoder (DN-Studio tuned)
#
# MODES
# -----
#   python zip_it.py
#       → backup: creates zip AND a _bundle.txt for Claude (under code_backup/)
#
#   python zip_it.py --decode <bundle.txt>
#   python zip_it.py --decode claudecode.txt [--root /path/to/project]
#       → writes all files from the bundle back to disk
#
#   python zip_it.py --suffix mytag
#       → non-interactive backup filenames (no input() prompt)
#
# Workflow
# --------
#   1. python zip_it.py
#   2. Send code_backup/code_backup_*_bundle.txt to Claude with instructions
#   3. Save the edited bundle, then:
#      python zip_it.py --decode updated_bundle.txt

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# DN-STUDIO: include everything needed to run & develop the app
# (backend, frontend source, prompts, config, templates, scripts, docs, Docker).
# Exclude secrets, venv, node_modules, build outputs, runtime outputs.
# ─────────────────────────────────────────────────────────────────────────────

INCLUDED_EXTENSIONS = {
    ".py",
    ".ipynb",
    ".md",
    ".txt",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".css",
    ".ts",
    ".tsx",
    ".jsx",
    ".js",
    ".svg",
    # frontend/index.html (do not blanket-exclude *.html)
    ".html",
}

EXCLUDED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".ipynb_checkpoints",
    ".git",
    ".svn",
    ".hg",
    "venv",
    "env",
    "ENV",
    ".venv",
    "venv.bak",
    "node_modules",
    ".cursor",
    ".vscode",
    ".idea",
    ".settings",
    "build",
    "dist",  # frontend/dist, any build output
    "downloads",
    "eggs",
    ".eggs",
    "htmlcov",
    ".tox",
    ".hypothesis",
    "logs",
    ".secrets",
    ".vincent",  # Kiro/Vincent IDE config
    ".kiro",     # Kiro IDE config
    "code_backup",  # do not recurse into previous backups
    "outputs",  # runtime: diarisation, generated docs
    "gcs",
    "catboost_info",
    "lightgbm_cache",
    "temp",
    "tmp",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "coverage.xml",
}

# Basename or glob — never bundle (secrets + env)
SENSITIVE_FILES = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*credentials*.json",
    "*service-account*.json",
    "*service_account*.json",
    "client_secret*.json",
    "token.json",
    "token.pickle",
    "id_rsa",
    "id_rsa.pub",
    "known_hosts",
    "*.log",
    "backend.log",
    "frontend.log",
    "lt_backend.log",
    "lt_frontend.log",
]

# Extra path globs to exclude (never ship)
SENSITIVE_PATH_GLOBS = [
    ".env",
    ".env.*",
    "**/.env",
    "**/secrets/**",
    "**/.aws/**",
    "**/.ssh/**",
    "**/.vincent/**",
    "**/.kiro/**",
    "**/logs/**",
    "**/*.log",
]

# Exclude noisy / binary / huge patterns (narrow — do not use blanket *.html)
EXCLUDED_PATTERNS = [
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*$py.class",
    "*.so",
    "*.dll",
    "*.dylib",
    "*.egg-info",
    ".installed.cfg",
    "*.egg",
    "MANIFEST",
    "*.swp",
    "*.swo",
    "*~",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.log",
    "*.log.*",
    "*.tmp",
    "*.temp",
    "*.bak",
    "*.zip",
    "*.pptx",
    "*.docx",
    "*.pptm",
    "*.doc",
    "*.pkl",
    "*.h5",
    "*.ckpt",
    "*.pt",
    "*.pth",
    "*.onnx",
    "*.safetensors",
    "*.bin",
    "*.parquet",
    "*.xls",
    "*.pdf",
    # built / vendor inside tree
    "**/node_modules/**",
    "**/frontend/dist/**",
    "**/dist/**",
    "outputs/**",
    "code_backup/**",
    # large media (diarisation uploads live under outputs/ too)
    "*.mp3",
    "*.mp4",
    "*.wav",
    "*.m4a",
    "*.webm",
    "*.mkv",
    "*.ogg",
    "*.flac",
    "*.avi",
    "*.mov",
    # IDE and system files
    "**/.vincent/**",
    "**/.kiro/**",
    "**/.vscode/**",
    "**/.idea/**",
    "**/.cursor/**",
    # Runtime logs
    "backend.log",
    "frontend.log",
    "lt_backend.log",
    "lt_frontend.log",
    # Temporary Word files
    "~$*.docx",
    "~$*.doc",
]

# JSON: exclude known secret shapes; normal app JSON still included (package.json, etc.)
EXCLUDED_JSON_PATTERNS = [
    "*.key.json",
    "*service-account*.json",
    "*service_account*.json",
    "*credentials*.json",
    "client_secret*.json",
    "token.json",
]

# If nothing else matched, these paths are always included (critical for DN-Studio)
INCLUDED_PATH_PREFIXES = (
    "backend/",
    "frontend/",
    "config/",
    "prompts/",
    "templates/",
    "scripts/",
)

INCLUDED_ROOT_FILES = (
    "package.json",
    "package-lock.json",
    "requirements.txt",
    "requirements-docker.txt",
    "docker-compose.yml",
    "Dockerfile",
    "Dockerfile.optimized",
    "Dockerfile.simple",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    "README.md",
    "QUICK_START.md",
    "INSTALL.md",
    "DOCKERIZATION_GUIDE.md",
    "COLAB_LOCALTUNNEL.md",
    "zip_it.py",
    "colab_localtunnel_launcher.py",
)

INCLUDED_ROOT_GLOBS = (
    "*.bat",
    "*.md",
)

# ─────────────────────────────────────────────────────────────────────────────
# BUNDLE FORMAT
# ─────────────────────────────────────────────────────────────────────────────

BUNDLE_FILE_START = "<<<FILE: {path}>>>"
BUNDLE_FILE_END = "<<<END_FILE>>>"

COMMENT_CHARS = {
    ".py": "#",
    ".sh": "#",
    ".yaml": "#",
    ".yml": "#",
    ".toml": "#",
    ".rb": "#",
    ".r": "#",
    ".csv": "#",
    ".txt": "#",
    ".md": "<!--",
    ".js": "//",
    ".jsx": "//",
    ".ts": "//",
    ".tsx": "//",
    ".json": "//",
    ".java": "//",
    ".c": "//",
    ".cpp": "//",
    ".cs": "//",
    ".go": "//",
    ".swift": "//",
    ".html": "<!--",
    ".xml": "<!--",
    ".svg": "<!--",
    ".css": "/*",
    ".tex": "%",
    ".m": "%",
    ".ini": ";",
    ".cfg": ";",
    ".sql": "--",
    ".lua": "--",
}


def path_comment(rel_path: str, ext: str) -> str:
    """First-line path marker (decoder strips this when possible)."""
    char = COMMENT_CHARS.get(ext.lower(), "#")
    if char == "<!--":
        return f"<!-- {rel_path} -->"
    if char == "/*":
        return f"/* {rel_path} */"
    return f"{char} {rel_path}"


def _matches_any_glob(rel_posix: str, name: str, globs: tuple[str, ...]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(rel_posix, g) or fnmatch.fnmatch(name, g):
            return True
    return False


def _is_under_prefix(rel_posix: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel_posix == p.rstrip("/") or rel_posix.startswith(p) for p in prefixes)


def should_exclude_path(file_path: Path, root: Path) -> bool:
    try:
        rel_path = file_path.relative_to(root)
    except ValueError:
        return True

    rel_posix = str(rel_path).replace("\\", "/")
    name = file_path.name
    parts = rel_path.parts

    for part in parts:
        if part in EXCLUDED_DIRS:
            return True
        if part == "dist" and "frontend" in parts:
            return True

    if _matches_any_glob(rel_posix, name, tuple(SENSITIVE_FILES)):
        return True
    if _matches_any_glob(rel_posix, name, SENSITIVE_PATH_GLOBS):
        return True

    # Explicit allow: core source trees
    if _is_under_prefix(rel_posix, INCLUDED_PATH_PREFIXES):
        if file_path.suffix.lower() == ".json":
            for ep in EXCLUDED_JSON_PATTERNS:
                if fnmatch.fnmatch(rel_posix, ep) or fnmatch.fnmatch(name, ep):
                    return True
        for pattern in EXCLUDED_PATTERNS:
            if fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(name, pattern):
                return True
        if file_path.suffix.lower() in INCLUDED_EXTENSIONS or name.endswith(".d.ts"):
            return False
        return True

    # Root-level project files
    if len(parts) == 1:
        if name in INCLUDED_ROOT_FILES:
            return False
        for g in INCLUDED_ROOT_GLOBS:
            if fnmatch.fnmatch(name, g):
                return False

    # JSON secrets
    if file_path.suffix.lower() == ".json":
        for ep in EXCLUDED_JSON_PATTERNS:
            if fnmatch.fnmatch(rel_posix, ep) or fnmatch.fnmatch(name, ep):
                return True

    for pattern in EXCLUDED_PATTERNS:
        if fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(name, pattern):
            return True

    ext = file_path.suffix.lower()
    if ext not in INCLUDED_EXTENSIONS and not name.endswith(".d.ts"):
        return True

    return False


def collect_files(project_root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in EXCLUDED_DIRS and not d.startswith(".")
        ]

        root_path = Path(dirpath)
        for fname in filenames:
            fp = root_path / fname
            if "code_backup" in fp.parts:
                continue
            if should_exclude_path(fp, project_root):
                continue
            files.append(fp)

    files.sort(key=lambda p: str(p).lower())
    return files


def encode_bundle(files: list[Path], project_root: Path) -> str:
    parts: list[str] = []
    parts.append("# ═══════════════════════════════════════════════════════════")
    parts.append("# CLAUDE CODE BUNDLE (DN-Studio)")
    parts.append("# Generated: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    parts.append("# Files: " + str(len(files)))
    parts.append("#")
    parts.append("# HOW TO USE:")
    parts.append("#   1. Send this file to Claude with your instructions.")
    parts.append("#   2. Claude edits sections inside <<<FILE>>> blocks.")
    parts.append("#   3. Save the full bundle and run:")
    parts.append("#      python zip_it.py --decode <bundle.txt>")
    parts.append("# ═══════════════════════════════════════════════════════════")
    parts.append("")

    for fp in files:
        rel = str(fp.relative_to(project_root)).replace("\\", "/")
        ext = fp.suffix.lower() if fp.suffix else ""
        try:
            content = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            content = f"# [SKIPPED — could not read as UTF-8: {e}]\n"

        comment = path_comment(rel, ext or ".txt")
        parts.append(BUNDLE_FILE_START.format(path=rel))
        parts.append(comment)
        if content and not content.endswith("\n"):
            content = content + "\n"
        parts.append(content.rstrip("\n"))
        parts.append(BUNDLE_FILE_END)
        parts.append("")

    return "\n".join(parts)


def _strip_injected_path_comment(rel_path: str, content: str) -> str:
    """Remove the first line if it looks like our path comment."""
    lines = content.split("\n")
    if not lines:
        return content
    first = lines[0]
    norm = rel_path.replace("\\", "/")
    base = norm.split("/")[-1]
    merged = first.replace("\\", "/")
    if norm in merged or base in merged:
        return "\n".join(lines[1:])
    stripped = first.strip()
    if stripped.startswith(("#", "//", "/*", "<!--", "%", ";", "--")):
        return "\n".join(lines[1:])
    return content


def decode_bundle(bundle_path: Path, target_root: Path) -> None:
    raw = bundle_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<<<FILE:\s*(.+?)>>>\s*\n(.*?)<<<END_FILE>>>",
        re.DOTALL,
    )
    matches = pattern.findall(raw)
    if not matches:
        print("[ERROR] No <<<FILE: ...>>> blocks found. Check the bundle format.")
        sys.exit(1)

    print(f"\nDecoding bundle: {bundle_path}")
    print(f"Target root:     {target_root.resolve()}")
    print(f"Files found:     {len(matches)}\n")

    written = 0
    errors = 0
    for rel_path_raw, body in matches:
        rel_path = rel_path_raw.strip().replace("\\", "/")
        if ".." in rel_path.split("/"):
            print(f"  [SKIP] unsafe path: {rel_path}")
            errors += 1
            continue
        content = _strip_injected_path_comment(rel_path, body)
        out_path = target_root / rel_path
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")
            print(f"  [OK]   {rel_path}")
            written += 1
        except OSError as e:
            print(f"  [ERR]  {rel_path}: {e}")
            errors += 1

    print(f"\n{'=' * 50}")
    print(f"Done — {written} written, {errors} skipped/errors")
    print(f"{'=' * 50}")


def create_backup(project_root: Path, suffix: str | None) -> None:
    backup_dir = project_root / "code_backup"
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if suffix is None:
        raw = input("Enter suffix for filenames (or press Enter for none): ").strip()
        suffix_clean = re.sub(r"[^\w\-]+", "_", raw).strip("_") if raw else ""
    else:
        suffix_clean = re.sub(r"[^\w\-]+", "_", suffix).strip("_")
    suffix = suffix_clean

    base_name = f"code_backup_{timestamp}_{suffix}" if suffix else f"code_backup_{timestamp}"
    zip_path = backup_dir / f"{base_name}.zip"
    bundle_path = backup_dir / f"{base_name}_bundle.txt"

    print("\n" + "=" * 60)
    print("DN-STUDIO — CODE BACKUP + CLAUDE BUNDLE")
    print("=" * 60)
    print(f"Project root:    {project_root}")
    print(f"Zip:             {zip_path}")
    print(f"Bundle:          {bundle_path}")
    print("\nScanning...")

    files = collect_files(project_root)
    total_size = sum(fp.stat().st_size for fp in files if fp.exists())

    print(f"Files: {len(files)}  (~{total_size / (1024 * 1024):.2f} MB raw)")

    if not files:
        print("\n[WARNING] No files matched — check EXCLUDED_* rules in zip_it.py.")
        return

    print(f"\nCreating zip: {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in files:
            arcname = fp.relative_to(project_root)
            try:
                zf.write(fp, arcname)
                print(f"  [zip]  {arcname}")
            except OSError as e:
                print(f"  [zip!] {fp}: {e}")

    print(f"\nBundle: {bundle_path.name}")
    bundle_text = encode_bundle(files, project_root)
    bundle_path.write_text(bundle_text, encoding="utf-8")

    zm = zip_path.stat().st_size / (1024 * 1024)
    bm = bundle_path.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print("BACKUP COMPLETE")
    print("=" * 60)
    print(f"  Files : {len(files)}")
    print(f"  Zip   : {zm:.2f} MB  →  {zip_path}")
    print(f"  Bundle: {bm:.2f} MB  →  {bundle_path}")
    print("\nNext: send *_bundle.txt to Claude; then python zip_it.py --decode <file.txt>")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DN-Studio — zip backup + Claude text bundle encode/decode.",
    )
    parser.add_argument(
        "--decode",
        metavar="BUNDLE.txt",
        help="Decode a bundle and write files under --root (default: directory of zip_it.py).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="Target root for decode (default: directory containing zip_it.py).",
    )
    parser.add_argument(
        "--suffix",
        default=None,
        metavar="TAG",
        help="Non-interactive backup filename suffix (omit flag for interactive prompt).",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Backup without suffix prompt (empty suffix in filename).",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent

    if args.decode:
        bundle_file = Path(args.decode)
        if not bundle_file.is_file():
            print(f"[ERROR] Not found: {bundle_file}")
            sys.exit(1)
        target = (args.root or project_root).resolve()
        try:
            decode_bundle(bundle_file, target)
        except KeyboardInterrupt:
            print("\n[CANCELLED]")
            sys.exit(130)
        return

    try:
        if args.no_prompt:
            create_backup(project_root, suffix="")
        elif args.suffix is not None:
            create_backup(project_root, suffix=args.suffix)
        else:
            create_backup(project_root, suffix=None)
    except KeyboardInterrupt:
        print("\n[CANCELLED]")
        sys.exit(130)


if __name__ == "__main__":
    main()
