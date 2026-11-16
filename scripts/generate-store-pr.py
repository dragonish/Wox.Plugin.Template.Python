from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

I18N_PREFIX = "i18n:"
PLACEHOLDER_PATTERN = re.compile(r"\{\{\.[A-Za-z]+\}\}")
GITHUB_REPO_PATTERN = re.compile(r"^(?:https?://|git@)?(?:www\.)?github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$")
WOX_ASSET_PATTERN = re.compile(r"wox\.plugin\.(.+?)\.wox")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SCREENSHOT_DIRECTORIES = ("", "assets", "images", "screenshots")

OS_NAME_MAP = {
    "windows": "Windows",
    "win": "Windows",
    "darwin": "Darwin",
    "macos": "Darwin",
    "mac": "Darwin",
    "macosx": "Darwin",
    "osx": "Darwin",
    "linux": "Linux",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Wox store entry and pull request body in docs/.",
    )
    parser.add_argument("--root", default=".", help="Plugin project root directory (default: current directory)")
    parser.add_argument("--output-dir", default="docs", help="Output directory relative to root (default: docs)")
    parser.add_argument("--branch", default="main", help="Default branch used for raw URLs (default: main)")
    parser.add_argument(
        "--screenshot",
        action="append",
        default=[],
        metavar="PATH_OR_URL",
        help="Screenshot path or URL. Repeat for multiple screenshots; overrides auto-detection.",
    )
    parser.add_argument("--icon", metavar="PATH_OR_URL", help="Icon path or URL; overrides plugin.json")
    parser.add_argument("--repo", metavar="URL", help="Repository URL; overrides plugin.json and git remote")
    parser.add_argument("--asset", metavar="NAME", help="Release asset file name; overrides derived name")
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD HH:MM:SS",
        help="Timestamp for DateCreated and DateUpdated (default: current local time)",
    )
    return parser.parse_args()


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER_PATTERN.fullmatch(value.strip()))


def print_message(text: str, file=sys.stdout) -> None:
    try:
        print(text, file=file)
    except UnicodeEncodeError:
        encoding = getattr(file, "encoding", None) or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding), file=file)


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_repo_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    match = GITHUB_REPO_PATTERN.match(value)
    if match:
        return f"https://github.com/{match.group(1)}/{match.group(2)}"
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    return None


def read_git_remote_url(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def resolve_repo_url(root: Path, plugin_data: dict[str, object], override: str | None) -> str:
    candidates = (override, plugin_data.get("Website"))
    for candidate in candidates:
        if isinstance(candidate, str):
            normalized = normalize_repo_url(candidate)
            if normalized:
                return normalized

    remote = read_git_remote_url(root)
    if remote:
        normalized = normalize_repo_url(remote)
        if normalized:
            return normalized

    raise SystemExit(
        "Could not determine the GitHub repository URL. "
        "Set Website in plugin.json or pass --repo https://github.com/<owner>/<repo>."
    )


def asset_name_from_text(text: str) -> str | None:
    for match in WOX_ASSET_PATTERN.finditer(text):
        name = match.group(1).strip()
        if name and "{{" not in name and not is_placeholder(name):
            return name
    return None


def read_package_name(root: Path, display_name: str) -> str | None:
    makefile = root / "Makefile"
    if makefile.exists():
        name = asset_name_from_text(makefile.read_text(encoding="utf-8"))
        if name:
            return name

    scripts = root / "scripts"
    if scripts.is_dir():
        for path in sorted(scripts.rglob("*.py")):
            name = asset_name_from_text(path.read_text(encoding="utf-8", errors="ignore"))
            if name:
                return name

    wox_files = sorted(root.glob("wox.plugin.*.wox"))
    if len(wox_files) == 1:
        filename = wox_files[0].name
        return filename[len("wox.plugin.") : -len(".wox")]

    if display_name:
        normalized = re.sub(r"[^a-z0-9]+", "", display_name.lower())
        if normalized:
            return normalized
    return None


def i18n_key(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith(I18N_PREFIX):
        return None
    key = value[len(I18N_PREFIX) :].strip()
    return key or None


def i18n_value(plugin_data: dict[str, object], key: str) -> str | None:
    i18n = plugin_data.get("I18n")
    if not isinstance(i18n, dict):
        return None
    for locale in ("en_US", "zh_CN"):
        values = i18n.get(locale)
        if isinstance(values, dict) and isinstance(values.get(key), str) and values[key]:
            return values[key]
    for values in i18n.values():
        if isinstance(values, dict) and isinstance(values.get(key), str) and values[key]:
            return values[key]
    return None


def display_text(plugin_data: dict[str, object], field: str) -> str:
    raw = plugin_data.get(field)
    if not isinstance(raw, str) or not raw.strip():
        return ""
    key = i18n_key(raw)
    if key:
        return i18n_value(plugin_data, key) or raw
    return raw


def build_i18n(plugin_data: dict[str, object]) -> dict[str, dict[str, str]]:
    name_key = i18n_key(plugin_data.get("Name"))
    description_key = i18n_key(plugin_data.get("Description"))
    if not name_key and not description_key:
        return {}

    source = plugin_data.get("I18n")
    if not isinstance(source, dict):
        return {}

    result: dict[str, dict[str, str]] = {}
    for locale, values in source.items():
        if not isinstance(values, dict):
            continue
        entry: dict[str, str] = {}
        if name_key and isinstance(values.get(name_key), str) and values[name_key]:
            entry[name_key] = values[name_key]
        if description_key and isinstance(values.get(description_key), str) and values[description_key]:
            entry[description_key] = values[description_key]
        if entry:
            result[locale] = entry
    return result


def parse_github_repo(repo_url: str) -> tuple[str, str] | None:
    match = GITHUB_REPO_PATTERN.match(repo_url)
    if not match:
        return None
    return match.group(1), match.group(2)


def normalize_relative_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def raw_url(repo_url: str, branch: str, path: str) -> str | None:
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    normalized_path = normalize_relative_path(path)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{normalized_path}"


def is_remote(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def resolve_asset(root: Path, value: str, repo_url: str, branch: str, label: str) -> str:
    if is_remote(value):
        return value
    relative = normalize_relative_path(value)
    if not (root / relative).exists():
        print_message(f"Warning: {label} file not found locally: {relative}", file=sys.stderr)
    url = raw_url(repo_url, branch, relative)
    if not url:
        raise SystemExit(f"Cannot build a public URL for {label}: {relative}")
    return url


def resolve_icon(
    root: Path,
    plugin_data: dict[str, object],
    repo_url: str,
    branch: str,
    override: str | None,
) -> str:
    if override:
        return resolve_asset(root, override, repo_url, branch, "icon")

    raw_icon = plugin_data.get("Icon")
    if isinstance(raw_icon, str) and raw_icon.startswith("relative:"):
        return resolve_asset(root, raw_icon[len("relative:") :], repo_url, branch, "icon")
    if isinstance(raw_icon, str) and is_remote(raw_icon):
        return raw_icon

    candidates = (
        "images/app.svg",
        "images/app.png",
        "images/icon.png",
        "assets/app.svg",
        "assets/app.png",
        "assets/icon.png",
        "app.svg",
        "app.png",
        "icon.png",
    )
    for candidate in candidates:
        if (root / candidate).exists():
            return resolve_asset(root, candidate, repo_url, branch, "icon")

    raise SystemExit(
        "Could not determine a public icon URL. "
        "Add Icon to plugin.json or pass --icon <path-or-url>."
    )


def detect_screenshots(root: Path) -> list[str]:
    found: list[Path] = []
    for directory in SCREENSHOT_DIRECTORIES:
        base = root / directory if directory else root
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if directory == "screenshots" or path.name.lower().startswith("screenshot"):
                found.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return [path.relative_to(root).as_posix() for path in unique]


def resolve_screenshots(
    root: Path,
    repo_url: str,
    branch: str,
    overrides: list[str],
) -> list[str]:
    if overrides:
        return [resolve_asset(root, value, repo_url, branch, "screenshot") for value in overrides]

    detected = detect_screenshots(root)
    if not detected:
        raise SystemExit(
            "No screenshot found. Add a public screenshot of the plugin running in Wox "
            "(for example screenshots/screenshot.jpg, assets/screenshot.png) "
            "or pass --screenshot <path-or-url>."
        )
    return [resolve_asset(root, path, repo_url, branch, "screenshot") for path in detected]


def normalize_supported_os(plugin_data: dict[str, object]) -> list[str]:
    raw = plugin_data.get("SupportedOS")
    if not isinstance(raw, list):
        return ["Windows", "Darwin", "Linux"]

    normalized: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        value = OS_NAME_MAP.get(item.strip().lower(), item.strip())
        if value not in normalized:
            normalized.append(value)
    return normalized or ["Windows", "Darwin", "Linux"]


def timestamp(override: str | None) -> str:
    if override:
        try:
            datetime.strptime(override, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise SystemExit("--date must use the YYYY-MM-DD HH:MM:SS format.")
        return override
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_entry(
    plugin_data: dict[str, object],
    *,
    repo_url: str,
    icon_url: str,
    download_url: str,
    screenshots: list[str],
    date: str,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "Id": plugin_data.get("Id", ""),
        "Name": plugin_data.get("Name", ""),
        "Author": plugin_data.get("Author", ""),
        "Version": plugin_data.get("Version", ""),
        "MinWoxVersion": plugin_data.get("MinWoxVersion", "2.0.0"),
        "Runtime": str(plugin_data.get("Runtime", "python")).lower(),
        "Description": plugin_data.get("Description", ""),
        "IconUrl": icon_url,
        "Website": repo_url,
        "DownloadUrl": download_url,
        "ScreenshotUrls": screenshots,
        "SupportedOS": normalize_supported_os(plugin_data),
        "DateCreated": date,
        "DateUpdated": date,
    }

    i18n = build_i18n(plugin_data)
    if i18n:
        entry["I18n"] = i18n
    return entry


def build_pull_request(entry: dict[str, object], display_name: str, display_description: str) -> str:
    screenshots = entry.get("ScreenshotUrls")
    screenshot_lines: list[str] = []
    if isinstance(screenshots, list):
        if len(screenshots) == 1:
            screenshot_lines.append(f"- Screenshot: {screenshots[0]}")
        elif screenshots:
            screenshot_lines.append("- Screenshots:")
            screenshot_lines.extend(f"  - {url}" for url in screenshots)

    lines = [
        f"<!-- Suggested PR title: Add {display_name} to store -->",
        "",
        "## Summary",
        "",
        f"Add **{display_name}** plugin to `store-plugin.json`.",
        "",
        f"- Plugin ID: `{entry['Id']}`",
        f"- Runtime: {entry['Runtime']}",
        f"- Repository: {entry['Website']}",
        f"- Download: {entry['DownloadUrl']}",
        f"- Icon: {entry['IconUrl']}",
        f"- Description: {display_description}",
        *screenshot_lines,
        "",
    ]
    return "\n".join(lines)


def validate_plugin_data(plugin_data: dict[str, object]) -> None:
    for field in ("Id", "Name", "Description"):
        value = plugin_data.get(field)
        if not isinstance(value, str) or not value.strip() or is_placeholder(value):
            raise SystemExit(f"plugin.json has no usable {field}. Run 'make init' first.")


def main() -> int:
    args = parse_args()

    root = Path(args.root).resolve()
    plugin_json_path = root / "plugin.json"
    if not plugin_json_path.exists():
        raise SystemExit(f"plugin.json not found under {root}")

    plugin_data = load_json(plugin_json_path)
    validate_plugin_data(plugin_data)

    repo_url = resolve_repo_url(root, plugin_data, args.repo)
    display_name = display_text(plugin_data, "Name") or plugin_data["Name"]
    display_description = display_text(plugin_data, "Description")

    package_name = args.asset
    if not package_name:
        package = read_package_name(root, display_name)
        if not package:
            raise SystemExit(
                "Could not determine the release asset name. "
                "Set a wox.plugin.<name>.wox archive name in the Makefile or pass --asset."
            )
        package_name = f"wox.plugin.{package}.wox"

    icon_url = resolve_icon(root, plugin_data, repo_url, args.branch, args.icon)
    screenshots = resolve_screenshots(root, repo_url, args.branch, args.screenshot)
    download_url = f"{repo_url}/releases/latest/download/{package_name}"
    date = timestamp(args.date)

    entry = build_entry(
        plugin_data,
        repo_url=repo_url,
        icon_url=icon_url,
        download_url=download_url,
        screenshots=screenshots,
        date=date,
    )
    pull_request = build_pull_request(entry, display_name, display_description)

    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    entry_path = output_dir / "store-plugin-entry.json"
    pr_path = output_dir / "pull-request.md"
    entry_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pr_path.write_text(pull_request, encoding="utf-8")

    def display_path(path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return str(path)

    print_message("Generated:")
    print_message(f"  {display_path(entry_path)}")
    print_message(f"  {display_path(pr_path)}")
    print_message(f"Suggested PR title: Add {display_name} to store")
    return 0


if __name__ == "__main__":
    sys.exit(main())
