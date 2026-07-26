#!/usr/bin/env python3
import hashlib
import json
import subprocess
from pathlib import Path

MUSIC = Path("assets/music")


def md5_prefix(path: Path, n: int = 2_000_000) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(n))
    return h.hexdigest()[:12]


def mean_volume(path: Path, af: str, t: float = 8.0) -> float | None:
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-t",
            str(t),
            "-i",
            str(path),
            "-af",
            af,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=45,
    )
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    for line in err.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    return None


def main() -> None:
    data = json.loads((MUSIC / "_audit.json").read_text(encoding="utf-8"))
    # Prefer 15–90s first for short-video pack, then re-rank
    candidates = []
    for r in data["scored"]:
        if r["dur"] < 12 or r["dur"] > 240:
            continue
        if r["sil"] > 0.12:
            continue
        if r["mean"] is None or r["mean"] > -9:  # too hot
            continue
        if r["name"].startswith("demo_"):
            continue
        path = MUSIC / r["name"]
        if not path.is_file():
            continue
        hp = mean_volume(path, "highpass=f=1200,volumedetect")
        full = mean_volume(path, "volumedetect")
        # Popular/poppy tracks keep more energy after highpass (hp closer to full)
        brightness = 0.0
        if hp is not None and full is not None:
            brightness = max(0.0, min(30.0, (hp - full) + 20))  # rough 0..30
        # Boost short loopable + bright + high score
        pop = r["score"] + brightness
        if 18 <= r["dur"] <= 75:
            pop += 12
        elif 75 < r["dur"] <= 130:
            pop += 6
        candidates.append(
            {
                **r,
                "hash": md5_prefix(path),
                "hp": hp,
                "full": full,
                "brightness": round(brightness, 1),
                "pop": round(pop, 1),
            }
        )

    # de-dupe by hash keep best pop
    best: dict[str, dict] = {}
    for c in candidates:
        prev = best.get(c["hash"])
        if prev is None or c["pop"] > prev["pop"]:
            best[c["hash"]] = c
    ranked = sorted(best.values(), key=lambda x: (-x["pop"], x["name"]))

    print("=== 完播友好 · 流行向（去重后 Top 10）===")
    for i, r in enumerate(ranked[:10], 1):
        print(
            f"{i:2d}. pop={r['pop']:5.1f} score={r['score']:3d} bright={r['brightness']:4.1f} "
            f"dur={r['dur']:6.1f}s mean={r['mean']:6.1f}  {r['name']}"
        )

    picks = ranked[:6]
    (MUSIC / "_picks.json").write_text(
        json.dumps(picks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {MUSIC / '_picks.json'} n={len(picks)}")


if __name__ == "__main__":
    main()
