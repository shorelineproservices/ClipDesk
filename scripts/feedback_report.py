"""View-count feedback report for the Clip Desk. Run on the laptop:
    python scripts/feedback_report.py
    python scripts/feedback_report.py --record <video_id> --window 24h --views 1234 --viewed-pct 68 --avd-pct 55

Reads ONLY real numbers: data/clips/feedback.json (written by Pia from the YouTube API
at 1h/24h/72h/7d) and the posted sidecars. The --record form lets the owner paste real
YouTube Studio reads (viewed-vs-swiped %, average % viewed, CTR, subs) into the same
file — nothing is invented here, and a clip with no readings is reported as "no data",
never estimated.

Verdict rules are the A/B Testing Lab's (LeonidaAfterDark experiments/ab-testing.md):
minimum 1,000 views / 72 hours, winner needs >=15% lift, retention guardrail (no AVD
drop), >=4/5 matched pairs. This script does NOT call verdicts on pairs — it ranks
clips against the channel median and flags promotion candidates, because experiment
assignment lives in the lab sheet, not in ClipDesk. Exit code 0 always; a report, not a gate.
"""
import argparse, json, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from departments.clips import common as C

WINDOWS = ["1h", "24h", "72h", "7d"]


def median_views(posted):
    views = [int(m.get("views", 0)) for m in posted if m.get("views")]
    return statistics.median(views) if views else 0


def build_report():
    f = C.folders()
    posted = [C.read_clip(p) or {} for p in sorted(f["posted"].glob("*.json"))]
    posted = [m for m in posted if m.get("status") == "posted"]
    fb = C.load_feedback()
    med = median_views(posted)
    rows = []
    for m in posted:
        vid = m.get("video_id", "")
        entry = fb["clips"].get(vid, {})
        readings = entry.get("readings", {})
        studio = entry.get("studio", {})
        views = int(m.get("views", 0))
        rows.append({"title": m.get("title", "")[:60], "video_id": vid, "file": m.get("file", ""),
                     "views": views, "likes": int(m.get("likes", 0)), "comments": int(m.get("comments", 0)),
                     "multiple": round(views / med, 2) if med and views else 0.0,
                     "windows": sorted(readings, key=WINDOWS.index),
                     "studio": studio, "posted_at": m.get("posted_at", 0)})
    rows.sort(key=lambda r: -r["views"])
    return rows, med, fb


def main():
    ap = argparse.ArgumentParser(description="ClipDesk view-count feedback report (real numbers only).")
    ap.add_argument("--record", metavar="VIDEO_ID", help="paste a real YouTube Studio read for this video id")
    ap.add_argument("--window", choices=WINDOWS, help="measurement window for --record (1h/24h/72h/7d)")
    ap.add_argument("--views", type=int, help="views at that window (Studio read)")
    ap.add_argument("--viewed-pct", type=float, help="viewed-vs-swiped % (Studio Reach, feed surface)")
    ap.add_argument("--avd-pct", type=float, help="average % viewed (Studio Engagement)")
    ap.add_argument("--ctr", type=float, help="impressions CTR % (search/grid surface only)")
    ap.add_argument("--subs", type=int, help="subs gained at that window")
    args = ap.parse_args()

    if args.record:
        if not args.window or args.views is None:
            ap.error("--record needs --window and --views (real Studio numbers)")
        data = C.load_feedback()
        entry = data["clips"].setdefault(args.record, {"title": "", "file": "", "posted_at": 0,
                                                       "readings": {}, "studio": {}})
        entry["studio"][args.window] = {"ts": time.time(), "views": args.views,
                                        "viewed_pct": args.viewed_pct, "avd_pct": args.avd_pct,
                                        "ctr": args.ctr, "subs": args.subs}
        C.save_feedback(data)
        print(f"Recorded Studio read for {args.record} at {args.window}: {args.views} views"
              + (f", viewed {args.viewed_pct}%, AVD {args.avd_pct}%" if args.viewed_pct is not None else ""))
        return 0

    rows, med, fb = build_report()
    if not rows:
        print("No posted clips yet — nothing to report. Pia records view counts hourly once clips are posted.")
        return 0
    print(f"Feedback — {len(rows)} posted clip(s), channel median views: {int(med) if med else 'n/a (no view counts yet)'}")
    print(f"{'VIEWS':>8} {'xMED':>5} {'LIKES':>6} {'CMTS':>5}  TITLE  (windows recorded)")
    for r in rows:
        wins = ",".join(r["windows"]) or "none yet"
        print(f"{r['views']:>8} {r['multiple']:>5} {r['likes']:>6} {r['comments']:>5}  {r['title'][:52]}  [{wins}]")
    print()
    print("Top 3 by views:")
    for r in rows[:3]:
        print(f"  {r['multiple']}x median — {r['title']} ({r['views']} views, {r['video_id']})")
    print("Bottom 3 by views:")
    for r in rows[-3:]:
        print(f"  {r['multiple']}x median — {r['title']} ({r['views']} views, {r['video_id']})")
    print()
    print("A/B-lab suggestions (rules from experiments/ab-testing.md — apply in the lab sheet, not here):")
    promo = [r for r in rows if r["multiple"] >= 3]
    if promo:
        for r in promo:
            avd = (r["studio"].get("72h") or r["studio"].get("7d") or {}).get("avd_pct")
            note = " — AVD recorded, check >=70% before promoting" if avd is None else (
                " — PROMOTE to long-form candidate (AVD >=70%)" if avd >= 70 else f" — AVD {avd}% <70%, do not promote yet")
            print(f"  {r['title']}: {r['multiple']}x median{note}")
    else:
        print("  No clip at >=3x channel median yet — no long-form promotion candidate.")
    no_data = [r for r in rows if not r["windows"] and not r["studio"]]
    if no_data:
        print(f"  {len(no_data)} clip(s) have no 1h/24h/72h/7d readings yet — Pia records them hourly; "
              "do not call a verdict before 72h and 1,000 views.")
    print("  Verdicts need matched pairs (5 pairs, >=15% lift, >=4/5 pairs, retention guardrail) — "
          "log them in the A/B lab, not from this ranking alone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
