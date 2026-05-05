#!/usr/bin/env python3
"""Render a markdown build summary from per-image JSON sidecars.

Reads every `*.json` file in the directory passed as argv[1] and writes
markdown to stdout suitable for `$GITHUB_STEP_SUMMARY`.

Each sidecar is expected to look like:

    {
      "name": "webapp",
      "primary_tag": "ghcr.io/owner/repo/webapp:sha-abc1234",
      "all_tags": ["ghcr.io/.../webapp:latest", "ghcr.io/.../webapp:main", ...],
      "digest": "sha256:...",
      "sizes": { "linux/amd64": 412345678, "linux/arm64": 401234567 },
      "build_date": "2026-05-05 14:22 UTC"
    }
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def short_digest(d: str) -> str:
    if not d:
        return "—"
    if d.startswith("sha256:"):
        return d[:7] + d[7:14] + "…"
    return d[:12] + "…"


def tag_suffix(tag: str) -> str:
    """Return the part after the final ':' (the actual tag name)."""
    return tag.rsplit(":", 1)[-1] if ":" in tag else tag


def primary_sha_tag(info: dict) -> str:
    """Find the sha-XXXXXXX tag for an image, falling back to primary_tag suffix."""
    for tag in info.get("all_tags", []):
        suffix = tag_suffix(tag)
        if suffix.startswith("sha-"):
            return suffix
    return tag_suffix(info.get("primary_tag", ""))


def render(images: list[dict]) -> str:
    if not images:
        return "## 📦 Built Images\n\n_No image artifacts were collected._\n"

    images = sorted(images, key=lambda i: i["name"])
    platforms = sorted({p for img in images for p in img.get("sizes", {})})

    out: list[str] = []
    out.append("## 📦 Built Images\n")

    header = ["Image", "Tag (sha)", *platforms, "Digest"]
    align = ["---", "---", *["--:" for _ in platforms], "---"]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join(align) + " |")

    for img in images:
        sizes = img.get("sizes", {})
        row = [
            img["name"],
            f"`{primary_sha_tag(img)}`",
            *[fmt_size(sizes.get(p)) for p in platforms],
            f"`{short_digest(img.get('digest', ''))}`",
        ]
        out.append("| " + " | ".join(row) + " |")

    example = images[0]["primary_tag"]
    out.append("")
    out.append("**Pull and inspect any image** (swap the tag for any row above):")
    out.append("")
    out.append("```bash")
    out.append("# Pull")
    out.append(f"docker pull {example}")
    out.append("")
    out.append("# Open a shell inside (override entrypoint if needed)")
    out.append(
        f"docker run --rm -it --entrypoint /bin/bash {example}"
    )
    out.append("")
    out.append("# Inspect metadata locally")
    out.append(f"docker image inspect {example}")
    out.append("")
    out.append("# Inspect remotely without pulling (per-platform manifests, sizes, labels)")
    out.append(f"docker buildx imagetools inspect {example}")
    out.append("```")
    out.append("")

    out.append("<details><summary>All tags pushed</summary>")
    out.append("")
    for img in images:
        tag_list = ", ".join(f"`{tag_suffix(t)}`" for t in img.get("all_tags", []))
        out.append(f"- **{img['name']}**: {tag_list}")
    out.append("")
    out.append("</details>")
    out.append("")

    build_dates = {img.get("build_date") for img in images if img.get("build_date")}
    if build_dates:
        out.append(f"_Built: {', '.join(sorted(build_dates))}_")

    return "\n".join(out) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render_build_summary.py <dir>", file=sys.stderr)
        return 2

    src = Path(sys.argv[1])
    if not src.is_dir():
        print(f"not a directory: {src}", file=sys.stderr)
        return 2

    images: list[dict] = []
    for path in sorted(src.glob("*.json")):
        try:
            images.append(json.loads(path.read_text()))
        except json.JSONDecodeError as e:
            print(f"warning: skipping malformed {path.name}: {e}", file=sys.stderr)

    sys.stdout.write(render(images))
    return 0


if __name__ == "__main__":
    sys.exit(main())
