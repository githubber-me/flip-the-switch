#!/usr/bin/env python3
"""The film crew's tool. Agents in the Switch room call this; it does the media work.

Dry mode (the default) makes placeholder stills, clips and audio locally and spends nothing.
Live mode ("mode": "live" in studio.json) spends real credits through the Gulab MCP tools. Agents run the same
commands: each one prints the exact Gulab call to make and the save command to run after it.

Settings live in a file because Switch passes agents only a fixed set of environment variables.

  studio.py status
  studio.py still SLUG SHOT --prompt "..." [--ref bible/sheet_heroes.png ...]
  studio.py quote SLUG [SHOT ...]
  studio.py clip  SLUG SHOT --prompt "..."
  studio.py voice SLUG SHOT --text "..."
  studio.py music SLUG --prompt "..." --seconds 25
  studio.py save  SLUG still|clip|voice|music [SHOT] --url URL
  studio.py cut   SLUG
  studio.py gate  (Claude Code hook around the paid Gulab tools; see .claude/settings.json)
"""
import argparse
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRODUCTIONS = ROOT / "productions"
LEDGER = PRODUCTIONS / "ledger.jsonl"
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
FPS = 24
# Kling opens on a near-still copy of the start frame, so each clip is made longer than it is used.
LEAD_IN_SECONDS = 1
MIN_CLIP_SECONDS = 3

# Switch may start agents with a short PATH.
os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])


class StudioError(Exception):
    """A failure to report plainly in the room."""


def config():
    return json.loads((ROOT / "studio.json").read_text())


def is_live(cfg):
    return cfg["mode"] == "live"


def run_ffmpeg(*args):
    result = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True)
    if result.returncode:
        raise StudioError("ffmpeg failed: " + result.stderr.strip()[-600:])


def media_seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


# ---------- production files ----------

def production(slug):
    folder = PRODUCTIONS / slug
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_shots(slug):
    path = production(slug) / "shots.json"
    if not path.exists():
        raise StudioError(f"{path} does not exist yet. The writer creates it.")
    return json.loads(path.read_text())["shots"]


def find_shot(slug, shot_id):
    for shot in load_shots(slug):
        if shot["id"] == shot_id:
            return shot
    raise StudioError(f"No shot {shot_id} in {slug}/shots.json.")


def next_take(folder, shot_id, suffix):
    takes = [int(p.stem.rsplit("_t", 1)[1]) for p in folder.glob(f"{shot_id}_t*{suffix}")] if folder.exists() else []
    return max(takes, default=0) + 1


def latest(folder, shot_id, suffix):
    take = next_take(folder, shot_id, suffix) - 1
    return folder / f"{shot_id}_t{take}{suffix}" if take else None


def clip_seconds(shot):
    return max(MIN_CLIP_SECONDS, math.ceil(shot["seconds"]) + LEAD_IN_SECONDS)


# ---------- money ----------

def spent():
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(line)["cost_usd"] for line in LEDGER.read_text().splitlines() if line.strip())


def check_budget(cfg, cost):
    """Refuse before any paid call that would pass the cap."""
    if spent() + cost > cfg["budget_usd"]:
        raise StudioError(f"${cost:.2f} would pass the ${cfg['budget_usd']:.2f} cap "
                          f"(${spent():.2f} spent). Only the director can raise the cap in studio.json.")


def gulab_cost(cfg, tool, call):
    """What a paid Gulab tool call costs, or a StudioError if the crew may not make it."""
    models, rates, model = cfg["models"], cfg["rates_usd"], call.get("model")
    if tool.startswith("generate_image"):
        if model not in (models["still"], models["still_refs"]):
            raise StudioError(f"Stills use {models['still']} or {models['still_refs']}, not {model}.")
        if call.get("num_images", 1) != 1 or call.get("prompts"):
            raise StudioError("One still per call.")
        return rates["still"]
    if tool.startswith("generate_video"):
        if model != models["clip"]:
            raise StudioError(f"Clips use {models['clip']}, not {model}.")
        if not str(call.get("duration", "")).isdigit():
            raise StudioError("A clip call must state its duration in seconds.")
        return int(call["duration"]) * rates["clip_per_second"]
    if tool == "generate_audio":
        key = {"speech": "voice", "music": "music"}.get(call.get("kind"))
        if not key or model != models[key]:
            raise StudioError(f"Audio is speech with {models['voice']} or music with {models['music']}.")
        return rates[key]
    raise StudioError(f"{tool} is not a paid tool this crew uses.")


def hand_off(cfg, tool, call, save, poll=False):
    """Live mode: tell the agent the exact Gulab call to make, and how to save what comes back."""
    check_budget(cfg, gulab_cost(cfg, tool, call))
    print(f"LIVE: make this Gulab MCP call, exactly as written.\nTOOL {tool}\n{json.dumps(call, ensure_ascii=False, indent=1)}")
    if poll:
        print("It starts a job: poll get_job_status with the model and request_id until COMPLETED, then call fetch_result. Never resubmit.")
    print(f"THEN run: python3 tools/studio.py save {save} --url <the result URL>")


def download(url, out):
    if not url.startswith(("https://", "http://")):
        raise StudioError(f"Not a web URL: {url}")
    out.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "flip-the-switch-studio/1.0"})
    with urllib.request.urlopen(request, timeout=300) as response:
        out.write_bytes(response.read())


# ---------- dry-mode stand-ins ----------

def placeholder_still(out, shot_id, prompt, size):
    """Draw the shot and prompt on a card, so the human review in the room still means something."""
    from PIL import Image, ImageDraw, ImageFont
    width, height = size
    image = Image.new("RGB", size, (24, 32, 48))
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, width - 40, height - 40], outline=(230, 190, 90), width=4)
    try:
        big, small = ImageFont.load_default(72), ImageFont.load_default(38)
    except TypeError:
        big = small = ImageFont.load_default()
    draw.text((80, 80), f"DRY RUN  {shot_id}", font=big, fill=(230, 190, 90))
    draw.multiline_text((80, 200), textwrap.fill(prompt, width=max(20, width // 24)), font=small, fill=(225, 225, 225), spacing=12)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)


def placeholder_clip(still, out, seconds, size):
    width, height = size
    frames = seconds * FPS
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg("-loop", "1", "-i", str(still), "-t", str(seconds), "-r", str(FPS), "-vf",
               f"scale={width * 2}:{height * 2},zoompan=z='1+0.1*on/{frames}':d=1:s={width}x{height}:fps={FPS},format=yuv420p",
               "-c:v", "libx264", "-preset", "veryfast", str(out))


def placeholder_audio(out, seconds):
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", str(out))

# ---------- commands ----------

def cmd_status(args, cfg):
    print(f"mode: {cfg['mode']}")
    print(f"spent: ${spent():.2f} of ${cfg['budget_usd']:.2f} cap, ${cfg['budget_usd'] - spent():.2f} left (estimated from listed prices)")


def cmd_still(args, cfg):
    shot = find_shot(args.slug, args.shot)
    folder = production(args.slug) / "stills"
    refs = [ROOT / r for r in args.ref]
    missing = [str(r) for r in refs if not r.exists()]
    if missing:
        raise StudioError("Missing reference files: " + ", ".join(missing))
    if is_live(cfg):
        hosted = cfg.get("ref_urls", {})
        unhosted = [r for r in args.ref if r not in hosted]
        if unhosted:
            raise StudioError("No hosted URL in studio.json ref_urls for: " + ", ".join(unhosted))
        call = {"model": cfg["models"]["still_refs" if refs else "still"], "prompt": args.prompt, "aspect_ratio": cfg["aspect"], "num_images": 1}
        if refs:
            call["image_urls"] = [hosted[r] for r in args.ref]
        if cfg.get("gulab_board"):
            call["project_id"] = cfg["gulab_board"]
        hand_off(cfg, "generate_image", call, f"{args.slug} still {args.shot}")
    else:
        out = folder / f"{args.shot}_t{next_take(folder, args.shot, '.png')}.png"
        placeholder_still(out, shot["id"], args.prompt, SIZES[cfg["aspect"]])
        print(out.relative_to(ROOT))


def cmd_quote(args, cfg):
    shots = [s for s in load_shots(args.slug) if not args.shots or s["id"] in args.shots]
    rate = cfg["rates_usd"]["clip_per_second"]
    total = 0.0
    for shot in shots:
        seconds = clip_seconds(shot)
        cost = seconds * rate
        total += cost
        print(f"{shot['id']}: {seconds} s (used {shot['seconds']} s) ${cost:.2f}")
    left = cfg["budget_usd"] - spent()
    print(f"TOTAL ${total:.2f} for {len(shots)} clips. Spent ${spent():.2f}, cap ${cfg['budget_usd']:.2f}, ${left:.2f} left.")
    print("WITHIN CAP" if total <= left else "OVER CAP: the director must cut shots or raise the cap")


def cmd_clip(args, cfg):
    shot = find_shot(args.slug, args.shot)
    still = latest(production(args.slug) / "stills", args.shot, ".png")
    if not still:
        raise StudioError(f"No still for {args.shot}. Clips only come from approved stills.")
    folder = production(args.slug) / "clips"
    seconds = clip_seconds(shot)
    if is_live(cfg):
        source = still.with_suffix(".url")
        if not source.exists():
            raise StudioError(f"{still.name} has no hosted URL. Save stills with studio.py save.")
        call = {"model": cfg["models"]["clip"], "prompt": args.prompt, "image_url": source.read_text().strip(), "duration": str(seconds), "aspect_ratio": cfg["aspect"]}
        print(f"From {still.name}")
        hand_off(cfg, "generate_video", call, f"{args.slug} clip {args.shot}", poll=True)
    else:
        out = folder / f"{args.shot}_t{next_take(folder, args.shot, '.mp4')}.mp4"
        placeholder_clip(still, out, seconds, SIZES[cfg["aspect"]])
        print(f"{out.relative_to(ROOT)} from {still.name}")


def report_voice(shot, out):
    seconds = media_seconds(out)
    note = " LONGER THAN THE SHOT: shorten the line or extend the shot" if seconds > shot["seconds"] else ""
    print(f"{out.relative_to(ROOT)} {seconds:.1f} s{note}")


def cmd_voice(args, cfg):
    shot = find_shot(args.slug, args.shot)
    if is_live(cfg):
        call = {"kind": "speech", "model": cfg["models"]["voice"], "text": args.text}
        if cfg.get("voice"):
            call["voice"] = cfg["voice"]
        if cfg.get("gulab_board"):
            call["project_id"] = cfg["gulab_board"]
        hand_off(cfg, "generate_audio", call, f"{args.slug} voice {args.shot}")
    else:
        out = production(args.slug) / "audio" / f"{args.shot}.mp3"
        placeholder_audio(out, min(shot["seconds"], max(1.0, len(args.text.split()) * 0.4)))
        report_voice(shot, out)


def cmd_music(args, cfg):
    if is_live(cfg):
        call = {"kind": "music", "model": cfg["models"]["music"], "text": args.prompt, "duration": args.seconds}
        if cfg.get("gulab_board"):
            call["project_id"] = cfg["gulab_board"]
        hand_off(cfg, "generate_audio", call, f"{args.slug} music")
    else:
        out = production(args.slug) / "audio" / "music.mp3"
        placeholder_audio(out, args.seconds)
        print(out.relative_to(ROOT))


def cmd_save(args, cfg):
    """Keep a finished Gulab result in the production folder. A new still or clip is a new take, never an overwrite."""
    folder = production(args.slug)
    if args.kind == "music":
        out = folder / "audio" / "music.mp3"
    elif not args.shot:
        raise StudioError(f"save {args.kind} needs a shot id.")
    elif args.kind == "voice":
        out = folder / "audio" / f"{args.shot}.mp3"
    else:
        subfolder, suffix = (folder / "stills", ".png") if args.kind == "still" else (folder / "clips", ".mp4")
        out = subfolder / f"{args.shot}_t{next_take(subfolder, args.shot, suffix)}{suffix}"
    download(args.url, out)
    if args.kind == "still":
        out.with_suffix(".url").write_text(args.url)
    if args.kind == "voice":
        report_voice(find_shot(args.slug, args.shot), out)
    else:
        print(out.relative_to(ROOT))


def cmd_gate(args, cfg):
    """Claude Code hook around the paid Gulab tools. Exit 2 blocks the call and tells the agent why."""
    event = json.load(sys.stdin)
    tool = event["tool_name"].rsplit("__", 1)[-1]
    call = event.get("tool_input") or {}
    try:
        if not is_live(cfg):
            raise StudioError("Dry mode spends nothing. Use the tools/studio.py commands instead.")
        cost = gulab_cost(cfg, tool, call)
        if event.get("hook_event_name") == "PostToolUse":
            row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": tool, "model": call.get("model"), "cost_usd": round(cost, 4)}
            LEDGER.parent.mkdir(exist_ok=True)
            with open(LEDGER, "a") as handle:
                handle.write(json.dumps(row) + "\n")
        else:
            check_budget(cfg, cost)
    except StudioError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        sys.exit(2)

def cmd_cut(args, cfg):
    """Trim each newest clip past its static opening, join them, lay voice and music, and export at -14 LUFS."""
    folder = production(args.slug)
    shots = load_shots(args.slug)
    width, height = SIZES[cfg["aspect"]]
    inputs, video, audio = [], [], []
    start = 0.0
    for shot in shots:
        clip = latest(folder / "clips", shot["id"], ".mp4")
        if not clip:
            raise StudioError(f"No clip for {shot['id']}.")
        index = len(inputs) // 2
        inputs += ["-i", str(clip)]
        begin = min(LEAD_IN_SECONDS, max(0.0, media_seconds(clip) - shot["seconds"]))
        video.append(f"[{index}:v]trim=start={begin}:duration={shot['seconds']},setpts=PTS-STARTPTS,"
                     f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps={FPS},format=yuv420p,setsar=1[v{index}]")
    total = sum(s["seconds"] for s in shots)
    for shot in shots:
        line = folder / "audio" / f"{shot['id']}.mp3"
        if line.exists():
            index = len(inputs) // 2
            inputs += ["-i", str(line)]
            delay = int(start * 1000)
            audio.append(f"[{index}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={delay}|{delay}[a{index}]")
        start += shot["seconds"]
    lines = [part.rsplit("[", 1)[1][:-1] for part in audio]
    joined = "".join(f"[v{i}]" for i in range(len(shots))) + f"concat=n={len(shots)}:v=1:a=0[video]"
    music = folder / "audio" / "music.mp3"
    if music.exists():
        index = len(inputs) // 2
        inputs += ["-i", str(music)]
        audio.append(f"[{index}:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.22,atrim=duration={total}[music]")
        lines.append("music")
    if lines:
        labels = "".join(f"[{name}]" for name in lines)
        audio.append(f"{labels}amix=inputs={len(lines)}:normalize=0:duration=longest,apad=whole_dur={total},atrim=duration={total},loudnorm=I=-14:TP=-1.5[audio]")
    else:
        audio.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={total}[audio]")
    out = folder / "final.mp4"
    run_ffmpeg(*inputs, "-filter_complex", ";".join(video + [joined] + audio), "-map", "[video]", "-map", "[audio]",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(out))
    print(f"{out.relative_to(ROOT)} {media_seconds(out):.1f} s, {out.stat().st_size // 1024} KB, {len(shots)} shots")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(run=cmd_status)
    sub.add_parser("gate").set_defaults(run=cmd_gate)
    p = sub.add_parser("still")
    p.add_argument("slug"); p.add_argument("shot"); p.add_argument("--prompt", required=True); p.add_argument("--ref", action="append", default=[])
    p.set_defaults(run=cmd_still)
    p = sub.add_parser("quote")
    p.add_argument("slug"); p.add_argument("shots", nargs="*")
    p.set_defaults(run=cmd_quote)
    p = sub.add_parser("clip")
    p.add_argument("slug"); p.add_argument("shot"); p.add_argument("--prompt", required=True)
    p.set_defaults(run=cmd_clip)
    p = sub.add_parser("voice")
    p.add_argument("slug"); p.add_argument("shot"); p.add_argument("--text", required=True)
    p.set_defaults(run=cmd_voice)
    p = sub.add_parser("music")
    p.add_argument("slug"); p.add_argument("--prompt", required=True); p.add_argument("--seconds", type=int, required=True)
    p.set_defaults(run=cmd_music)
    p = sub.add_parser("save")
    p.add_argument("slug"); p.add_argument("kind", choices=["still", "clip", "voice", "music"]); p.add_argument("shot", nargs="?"); p.add_argument("--url", required=True)
    p.set_defaults(run=cmd_save)
    p = sub.add_parser("cut")
    p.add_argument("slug")
    p.set_defaults(run=cmd_cut)
    args = parser.parse_args()
    try:
        args.run(args, config())
    except StudioError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if sys.argv[1:2] != ["gate"] and not shutil.which("ffmpeg"):
        sys.exit("ERROR: ffmpeg is not on PATH. Install it first (brew install ffmpeg).")
    main()
