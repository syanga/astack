#!/usr/bin/env python3
import argparse, re, subprocess, sys
from pathlib import Path


def run(*cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {(r.stderr or r.stdout).strip()[-800:]}")
    return r.stdout


def upscale(src, dst, width=1080, sharpen=0.6, grain=6, bitrate="12M"):
    m = re.fullmatch(r"(\d+)([MmKk]?)", bitrate)
    if not m:
        raise ValueError(f"bitrate {bitrate!r} must be a whole number with an optional M or K, like 12M")
    num, unit = int(m.group(1)), m.group(2)
    dims = run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "csv=s=x:p=0", src)
    w, h = (int(x) for x in dims.split()[0].split("x")[:2])
    size = f"{width}:{width * 16 // 9}" if abs(w / h - 9 / 16) < 0.01 else f"{width}:-2"
    run("ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf",
        f"scale={size}:flags=lanczos,unsharp=5:5:{sharpen}:5:5:0,noise=alls={grain}:allf=t,format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-b:v", bitrate, "-maxrate", f"{num * 7 // 6}{unit}",
        "-bufsize", f"{num * 2}{unit}", "-tune", "grain", "-c:a", "copy", "-movflags", "+faststart", dst)
    return dst


def main():
    ap = argparse.ArgumentParser(description="Upscale a video with Lanczos, a light sharpen, and phone-like grain.")
    ap.add_argument("src", metavar="in.mp4")
    ap.add_argument("dst", metavar="out.mp4", nargs="?", help="default <in>-resize-grain.mp4")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--sharpen", type=float, default=0.6)
    ap.add_argument("--grain", type=int, default=6)
    ap.add_argument("--bitrate", default="12M")
    a = ap.parse_args()
    dst = a.dst or str(Path(a.src).with_suffix("")) + "-resize-grain.mp4"
    try:
        upscale(a.src, dst, a.width, a.sharpen, a.grain, a.bitrate)
    except ValueError as e:
        ap.error(str(e))
    except RuntimeError as e:
        sys.exit(str(e))
    print(dst)
    print(run("ffprobe", "-v", "error", "-show_entries",
              "stream=codec_type,width,height,r_frame_rate:format=duration,size", "-of", "compact", dst), end="")


if __name__ == "__main__":
    main()
