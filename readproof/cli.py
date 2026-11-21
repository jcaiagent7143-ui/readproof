"""`readproof read <url-or-share-text> [--profile P1] [--no-speech] [--json]` — one read, summarised.

The summary says whether the read was served and by which reader, or which wall stopped it and the evidence for
that verdict; how many Records each channel produced; and how many snapshots were stored. `--json` prints the
whole ReadResult (Records, fetches, gate decisions) instead; bodies are never printed, they are in the store.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="readproof", description="ReadProof: what did the agent actually read?")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("read", help="read one post (a URL or app share text)")
    r.add_argument("input", help="a post URL, or share text that contains one")
    r.add_argument("--profile", default="P1", help="access profile: P0 strict, P1 user-initiated (default), P2")
    r.add_argument("--root", default="runs", help="evidence root: logs/, store/, work/ (default ./runs)")
    r.add_argument("--origin", default="residential:unknown", help="network origin label, e.g. residential:MY")
    r.add_argument("--no-speech", action="store_true", help="skip speech recognition")
    r.add_argument("--no-on-screen", action="store_true", help="skip frame OCR")
    r.add_argument("--no-images", action="store_true", help="skip carousel/cover image OCR")
    r.add_argument("--max-media-s", type=float, default=600, help="process at most this many seconds of media")
    r.add_argument("--keep-media", action="store_true", help="keep media and frames in the work directory")
    r.add_argument("--vlm", action="store_true", help="describe frames/images with the configured :free VLM")
    r.add_argument("--json", action="store_true", help="print the full ReadResult as JSON")
    return p


def _human(s: dict[str, Any]) -> str:
    lines = []
    if s["served"]:
        lines.append(f"served    yes, by the {s['reader']} reader ({s['wall_signal']})")
    else:
        lines.append(f"served    no, wall {s['wall']}: {s['wall_signal']}")
    post = s.get("post")
    if post:
        lines.append(f"post      {post['platform']}:{post['post_id']} ({post['form']}) {post['url']}")
    per = s["records_per_channel"]
    lines.append("records   " + (", ".join(f"{k} {v}" for k, v in sorted(per.items())) if per else "none"))
    lines.append(f"snapshots {len(s['snapshots'])}" + (f" (first {s['snapshots'][0][:12]}…)" if s["snapshots"] else ""))
    lines.append(f"fetches   {len(s['fetches'])}, gate decisions {len(s['gate'])}")
    lines.append(f"cost      ${s['cost_usd']:.4f}, {s['seconds']:.1f} s")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "read":
        return 2
    from .harness import Harness, summary

    options = {"speech": not args.no_speech, "on_screen": not args.no_on_screen, "images": not args.no_images,
               "max_media_s": args.max_media_s, "keep_media": args.keep_media, "vlm": args.vlm}
    with Harness(args.profile, root=args.root, origin=args.origin, options=options) as h:
        result = h.read(args.input)
    s = summary(result)
    if args.json:
        json.dump(s, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(_human(s))
    return 0 if result.served else 1


if __name__ == "__main__":
    raise SystemExit(main())
