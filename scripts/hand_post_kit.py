"""Hand-post kit for TikTok / Instagram. Run on the laptop:
    python scripts/hand_post_kit.py
    python scripts/hand_post_kit.py --mark-posted <clip_stem> --platform tiktok|instagram
    python scripts/hand_post_kit.py --show <clip_stem>

TikTok and Instagram have no free open upload route, so ClipDesk never auto-posts
there. The clipper writes a <clip>.social.txt sidecar (TikTok + Instagram captions,
credit line, hashtags) next to every ready clip; this script lists which ready clips
still need a hand-post, shows the caption to copy, and records it afterwards with
--mark-posted. Exit code 0 always; a report, not a gate.
"""
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from departments.clips import common as C

PLATFORMS = ("tiktok", "instagram")


def list_ready():
    f = C.folders()
    out = []
    for p in sorted(f["ready"].glob("*.json")):
        m = C.read_clip(p) or {}
        if m.get("status") not in ("ready", "held"):
            continue
        stem = Path(m.get("file", p.stem)).stem
        out.append({"meta_path": p, "meta": m, "stem": stem,
                    "social": (f["ready"] / f"{stem}.social.txt"),
                    "mp4": (f["ready"] / m.get("file", ""))})
    return out


def main():
    ap = argparse.ArgumentParser(description="ClipDesk TikTok/Instagram hand-post kit (manual only).")
    ap.add_argument("--show", metavar="STEM", help="print the full TikTok + Instagram captions for one clip")
    ap.add_argument("--mark-posted", metavar="STEM", help="record a hand-post for one clip")
    ap.add_argument("--platform", choices=PLATFORMS, help="platform for --mark-posted")
    args = ap.parse_args()

    rows = list_ready()
    if args.mark_posted:
        if not args.platform:
            ap.error("--mark-posted needs --platform tiktok|instagram")
        for r in rows:
            if r["stem"] == args.mark_posted:
                m = r["meta"]
                posted = dict(m.get("posted_platforms") or {})
                posted[args.platform] = True
                m["posted_platforms"] = posted
                C.write_clip(r["meta_path"], m)
                print(f"Recorded: {r['stem']} posted to {args.platform}.")
                return 0
        print(f"No ready clip with stem '{args.mark_posted}'.")
        return 1

    if args.show:
        for r in rows:
            if r["stem"] == args.show:
                print(f"File: {r['meta'].get('file')}  Title: {r['meta'].get('title')}")
                if r["social"].is_file():
                    print(r["social"].read_text(encoding="utf-8"))
                else:
                    print("(no .social.txt sidecar yet — re-cut the clip to generate one)")
                return 0
        print(f"No ready clip with stem '{args.show}'.")
        return 1

    if not rows:
        print("No ready/held clips — nothing to hand-post. Cut some clips first (Dex picks up inbox footage).")
        return 0
    print(f"Hand-post kit — {len(rows)} clip(s) in ready/ (TikTok/Instagram are manual only):")
    print(f"{'CLIP':<46} {'TIKTOK':<8} {'INSTA':<8} SOCIAL SIDECAR")
    for r in rows:
        st = C.platform_status(r["meta"])
        print(f"{r['stem'][:46]:<46} {'done' if st['tiktok'] else 'TODO':<8} {'done' if st['instagram'] else 'TODO':<8} "
              f"{'yes' if r['social'].is_file() else 'missing'}")
    todo = [r for r in rows if not C.platform_status(r["meta"])["tiktok"] or not C.platform_status(r["meta"])["instagram"]]
    print(f"\n{len(todo)} clip(s) still need a hand-post. Copy the caption with:")
    print("  python scripts/hand_post_kit.py --show <clip_stem>")
    print("Then record it with:")
    print("  python scripts/hand_post_kit.py --mark-posted <clip_stem> --platform tiktok|instagram")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
