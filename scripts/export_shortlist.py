#!/usr/bin/env python3
import json
import shutil
import subprocess
from pathlib import Path

MUSIC = Path("assets/music")
data = json.loads((MUSIC / "_rhythmic_shortlist.json").read_text(encoding="utf-8"))
out = MUSIC / "shortlist"
if out.exists():
    shutil.rmtree(out)
out.mkdir()

lines = [
    "# 节奏向纯音乐短名单",
    "",
    "算法已尽量剔除**疑似人声对话**；请你本地试听 `01.mp3`～`12.mp3`，挑喜欢的写入配置。",
    "",
    "| # | 试听文件 | 时长 | rhythm | speech(越低越好) | 源文件 |",
    "|---|----------|------|--------|------------------|--------|",
]

for i, r in enumerate(data["shortlist"][:12], 1):
    src = MUSIC / r["name"]
    mp3 = out / f"{i:02d}.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-t",
            "45",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(mp3),
        ],
        check=True,
    )
    lines.append(
        f"| {i} | `{mp3.name}` | {r['dur']:.0f}s | {r['rhythm_strength']:.2f} | "
        f"{r['speech_score']:+.2f} | `{r['name']}` |"
    )
    print("export", mp3.name, "<-", r["name"][:40])

lines += [
    "",
    "## 用法",
    "",
    "```yaml",
    "audio:",
    '  bgm: "assets/music/shortlist/01.mp3"',
    "  volume: 0.78",
    "```",
    "",
    f"- 疑似人声剔除: {len(data['rejected_speech'])}",
    f"- 其它不合格: {data['rejected_other_count']}",
    f"- 音乐池: {len(data['all_music'])}",
    "",
]
(out / "README.md").write_text("\n".join(lines), encoding="utf-8")
print("done ->", out)
