#!/usr/bin/env python3
"""Upload files to the public Cloudflare R2 bucket and print embeddable URLs."""

import argparse
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

KEYS_ENV = Path.home() / ".claude" / "api_keys.env"
SETTINGS = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT",
            "R2_PUBLIC_BUCKET", "R2_PUBLIC_BASE")

EXTRA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".log": "text/plain; charset=utf-8",
}

INLINE_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".apng"}


def load_config():
    """Read R2 settings from ~/.claude/api_keys.env (or the environment)."""
    values = {}
    if KEYS_ENV.exists():
        for line in KEYS_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")

    missing = [k for k in SETTINGS if not (values.get(k) or os.environ.get(k))]
    if missing:
        sys.exit(f"Missing R2 settings in {KEYS_ENV}: {', '.join(missing)}\n"
                 "See the skill's SKILL.md for how to create the bucket and fill these in.")

    def get(k):
        return values.get(k) or os.environ[k]

    env = os.environ.copy()
    env.update({
        "AWS_ACCESS_KEY_ID": get("R2_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": get("R2_SECRET_ACCESS_KEY"),
        "AWS_DEFAULT_REGION": "auto",
        # R2 rejects the newer default checksum headers.
        "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required",
        "AWS_RESPONSE_CHECKSUM_VALIDATION": "when_required",
    })
    return SimpleNamespace(env=env, endpoint=get("R2_ENDPOINT"),
                           bucket=get("R2_PUBLIC_BUCKET"),
                           base=get("R2_PUBLIC_BASE").rstrip("/"))


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "file"


def content_type(path):
    ext = path.suffix.lower()
    return EXTRA_TYPES.get(ext) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def make_key(path, prefix, name):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    stem = slugify(name or path.stem)
    now = datetime.now(timezone.utc)
    parts = [f"{now:%Y}", f"{now:%m}"]
    if prefix:
        parts = [slugify(prefix)] + parts
    return "/".join(parts + [f"{stem}-{digest}{path.suffix.lower()}"])


def grab_clipboard():
    """Write the macOS clipboard image to a temp PNG; return the path."""
    out = Path(tempfile.mkdtemp()) / "clipboard.png"
    script = (
        'set f to (open for access POSIX file "%s" with write permission)\n'
        'try\n'
        '  write (the clipboard as «class PNGf») to f\n'
        '  close access f\n'
        'on error e\n'
        '  close access f\n'
        '  error e\n'
        'end try' % out
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        sys.exit("No image on the clipboard (copy a screenshot with Cmd+Ctrl+Shift+4 first).")
    return out


def upload(path, key, cfg):
    cmd = [
        "aws", "s3", "cp", str(path), f"s3://{cfg.bucket}/{key}",
        "--endpoint-url", cfg.endpoint,
        "--content-type", content_type(path),
        "--cache-control", "public, max-age=31536000, immutable",
        "--only-show-errors",
    ]
    r = subprocess.run(cmd, env=cfg.env, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Upload failed for {path}:\n{r.stderr.strip()}")


def verify(url):
    """HEAD the public URL, retrying once — a fresh object can take a moment to serve."""
    for attempt in range(2):
        if attempt:
            time.sleep(2)
        r = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-I", url],
                           capture_output=True, text=True)
        if r.stdout.strip() == "200":
            return True
    return False


def to_gif(path, fps, width):
    """Convert a video to an animated GIF, which GitHub can embed. Returns the new path."""
    if not shutil.which("ffmpeg"):
        sys.exit("--gif needs ffmpeg: brew install ffmpeg")
    out = Path(tempfile.mkdtemp()) / (path.stem + ".gif")
    vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
          "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5")
    r = subprocess.run(["ffmpeg", "-y", "-i", str(path), "-vf", vf, "-loop", "0", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        sys.exit(f"GIF conversion failed:\n{r.stderr.strip()[-1500:]}")
    mb = out.stat().st_size / 1e6
    if mb > 10:
        print(f"warning: GIF is {mb:.1f} MB — lower --fps or --width for a smaller file.",
              file=sys.stderr)
    return out


def snippet(path, url, label):
    if path.suffix.lower() in INLINE_IMAGE:
        return f"![{label}]({url})"
    return f"[{label}{path.suffix.lower()}]({url})"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="Files to upload")
    ap.add_argument("--clipboard", action="store_true", help="Upload the image on the macOS clipboard")
    ap.add_argument("--prefix", help="Optional key prefix, e.g. a project name")
    ap.add_argument("--name", help="Base name for the object (single file only)")
    ap.add_argument("--gif", action="store_true",
                    help="Convert video input to an animated GIF before uploading, so it embeds inline")
    ap.add_argument("--fps", type=int, default=10, help="GIF frame rate (default 10)")
    ap.add_argument("--width", type=int, default=900, help="GIF width in pixels (default 900)")
    ap.add_argument("--delete", metavar="KEY", help="Delete an object by key and exit")
    ap.add_argument("--list", action="store_true", help="List stored objects and exit")
    args = ap.parse_args()

    cfg = load_config()

    if args.delete:
        subprocess.run(["aws", "s3", "rm", f"s3://{cfg.bucket}/{args.delete}",
                        "--endpoint-url", cfg.endpoint], env=cfg.env, check=False)
        return
    if args.list:
        subprocess.run(["aws", "s3", "ls", f"s3://{cfg.bucket}/", "--recursive",
                        "--endpoint-url", cfg.endpoint], env=cfg.env, check=False)
        return

    paths = [Path(f).expanduser().resolve() for f in args.files]
    if args.clipboard:
        paths.insert(0, grab_clipboard())
    if not paths:
        ap.error("Give at least one file, or --clipboard")
    if args.name and len(paths) > 1:
        ap.error("--name works with a single file only")

    for path in paths:
        if not path.is_file():
            sys.exit(f"Not a file: {path}")
        label = args.name or path.stem
        if args.gif:
            path = to_gif(path, args.fps, args.width)
        key = make_key(path, args.prefix, args.name)
        upload(path, key, cfg)
        url = f"{cfg.base}/{key}"
        if not verify(url):
            sys.exit(f"Uploaded {key} but {url} did not return 200.")
        print(url)
        print(snippet(path, url, label))
        if path.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}:
            print("note: GitHub blocks external video playback — this renders as a link, not a player.")


if __name__ == "__main__":
    main()
