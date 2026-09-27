#!/usr/bin/env python3
"""
Zhang Yiming Bookshelf — Audio Generator (Self-Contained)
==========================================================
Downloads course text from kangboacademy.cn, generates Moss-TTS-Nano MP3s.

Usage:
  python3 gen_mp3.py              # Generate all 100
  python3 gen_mp3.py --check      # Check progress only  
  python3 gen_mp3.py 5            # Generate lesson 5 only
  python3 gen_mp3.py 1 20         # Generate lessons 1-20

Requirements:
  - Python 3.10+, ffmpeg, git
  - 16GB+ RAM (Moss model needs ~5GB)
  - Internet (to fetch course text from kangboacademy.cn)
"""

import os, sys, re, subprocess, time, json, argparse, urllib.request
from pathlib import Path

BASE_URL = "https://kangboacademy.cn/zhangyiming"
BASE_DIR = Path(__file__).parent
AUDIO_DIR = BASE_DIR / "audio"
PROGRESS_FILE = AUDIO_DIR / "progress.json"
MOSS_DIR = BASE_DIR / "MOSS-TTS-Nano"

parser = argparse.ArgumentParser()
parser.add_argument("range", nargs="*", type=int, help="Lesson range: single number or start end")
parser.add_argument("--check", action="store_true", help="Only check progress")
parser.add_argument("--voice", default="Junhao", help="Voice name (default: Junhao)")
args = parser.parse_args()

# ─── Progress ───────────────────────────────────────
def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"done": [], "fail": [], "total": 100}

def save_progress(p):
    AUDIO_DIR.mkdir(exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(p, indent=2))

# ─── Fetch text from website ────────────────────────
def fetch_text(n):
    """Download lesson HTML and extract readable text."""
    url = f"{BASE_URL}/lesson{n}.html"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZY-AudioGen/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8")
    except Exception as e:
        print(f"    ⚠️  Failed to fetch lesson {n}: {e}")
        return ""
    
    # Strip non-content
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL)
    
    containers = re.findall(r'<div class="container"[^>]*>(.*?)</body>', html, re.DOTALL)
    if not containers:
        containers = re.findall(r'<div class="container"[^>]*>(.*?)</div>', html, re.DOTALL)[:3]
    
    text = ' '.join(containers)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\{[^}]*\}', ' ', text)
    text = re.sub(r'var a=.*?;', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ─── Moss-TTS setup ─────────────────────────────────
def ensure_moss():
    if not (MOSS_DIR / "onnx_tts_runtime.py").exists():
        print("📦 Cloning Moss-TTS-Nano...")
        subprocess.run(["git", "clone", "--depth", "1",
            "https://github.com/nazdrojj/MOSS-TTS-Nano.git", str(MOSS_DIR)], check=True)
    
    venv_py = MOSS_DIR / "moss_venv" / "bin" / "python3"
    if sys.platform == "win32":
        venv_py = MOSS_DIR / "moss_venv" / "Scripts" / "python.exe"
    
    if not venv_py.exists():
        print("📦 Setting up virtualenv + installing deps...")
        subprocess.run([sys.executable, "-m", "venv", str(MOSS_DIR / "moss_venv")], check=True)
        subprocess.run([str(venv_py), "-m", "pip", "install", "--quiet",
            "onnxruntime", "numpy", "soundfile", "wetext", "transformers"],
            cwd=str(MOSS_DIR), check=True)
    
    return str(venv_py)

# ─── Generate one MP3 ───────────────────────────────
def generate_one(n, text, moss_py):
    wav = BASE_DIR / f"tmp_{n:03d}.wav"
    mp3 = AUDIO_DIR / f"lesson{n}.mp3"
    
    if mp3.exists() and mp3.stat().st_size > 5000:
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-i", str(mp3), "-show_entries", "format=duration",
                 "-v", "quiet", "-of", "csv=p=0"],
                capture_output=True, text=True, timeout=10
            ).stdout.strip())
            if dur > 30:
                return True, f"skip ({dur:.0f}s)"
        except: pass
    
    clean = re.sub(r'[《》「」『』〈〉①②③④⑤⑥⑦⑧⑨⑩—…►]', '', text)
    clean = re.sub(r'\s+', ' ', clean).strip()
    
    print(f"  [{time.strftime('%H:%M:%S')}] L{n:03d}: {len(clean)}c →", end=' ', flush=True)
    t0 = time.time()
    
    # Write text to file to avoid shell escaping issues
    tf = BASE_DIR / f"tmp_t{n:03d}.txt"
    tf.write_text(clean, encoding='utf-8')
    
    moss_code = f"""
import sys, os
os.environ['OMP_NUM_THREADS'] = str(min(4, os.cpu_count() or 2))
sys.path.insert(0, '{MOSS_DIR}')
from onnx_tts_runtime import OnnxTtsRuntime
rt = OnnxTtsRuntime()
with open('{tf}', 'r', encoding='utf-8') as f:
    text = f.read().strip()
rt.synthesize(text=text, voice='{args.voice}', output_audio_path='{wav}')
print('SYNTH_DONE')
"""
    
    try:
        r = subprocess.run(
            [moss_py, "-c", moss_code],
            capture_output=True, text=True, timeout=3600, cwd=str(MOSS_DIR)
        )
        
        if r.returncode != 0 or not wav.exists():
            err = r.stderr[-200:] if r.stderr else "no wav produced"
            print(f"FAIL")
            tf.unlink(missing_ok=True)
            return False, err
        
        synth_time = time.time() - t0
        
        subprocess.run([
            "ffmpeg", "-y", "-i", str(wav),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=9",
            "-ar", "24000", "-ac", "1", "-b:a", "48k",
            str(mp3)
        ], capture_output=True, timeout=60)
        
        wav.unlink(missing_ok=True)
        tf.unlink(missing_ok=True)
        
        dur = float(subprocess.run(
            ["ffprobe", "-i", str(mp3), "-show_entries", "format=duration",
             "-v", "quiet", "-of", "csv=p=0"],
            capture_output=True, text=True, timeout=10
        ).stdout.strip())
        
        kb = mp3.stat().st_size / 1024
        total = time.time() - t0
        print(f"✓ {kb:.0f}KB {dur:.0f}s (synth={synth_time:.0f}s)")
        return True, f"{kb:.0f}KB {dur:.0f}s"
        
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        return False, "timeout"
    except Exception as e:
        print(f"ERR: {e}")
        return False, str(e)

# ─── Main ───────────────────────────────────────────
def main():
    AUDIO_DIR.mkdir(exist_ok=True)
    prog = load_progress()
    done = set(prog["done"])
    failed = set(prog.get("fail", []))
    
    # Parse range
    if args.range:
        if len(args.range) == 1:
            lesson_range = range(args.range[0], args.range[0] + 1)
        else:
            lesson_range = range(args.range[0], args.range[1] + 1)
    else:
        lesson_range = range(1, 101)
    
    if args.check:
        mp3s = sorted(AUDIO_DIR.glob("lesson*.mp3"))
        total_mb = sum(f.stat().st_size for f in mp3s) / 1024 / 1024
        print(f"📊 {len(done)}/100 done, {len(failed)} failed")
        print(f"   MP3s: {len(mp3s)}, {total_mb:.0f}MB")
        return
    
    moss_py = ensure_moss()
    print(f"🎙️  Moss-TTS-Nano ready (voice: {args.voice})")
    print(f"📚 Lessons {min(lesson_range)}-{max(lesson_range)}")
    print(f"📁 Output: {AUDIO_DIR}/")
    print(f"🌐 Fetching texts from {BASE_URL}/")
    print(f"⏱️  ~2-5 min/lesson on 16GB+\n")
    
    total_start = time.time()
    generated = 0
    
    for n in lesson_range:
        if n in done:
            continue
        
        text = fetch_text(n)
        if len(text) < 100:
            print(f"  L{n:03d}: TEXT TOO SHORT ({len(text)} chars), skipping")
            failed.add(n)
            continue
        
        success, msg = generate_one(n, text, moss_py)
        
        if success:
            done.add(n)
            failed.discard(n)
            generated += 1
        else:
            failed.add(n)
        
        prog = {"done": sorted(list(done)), "fail": sorted(list(failed)), "total": 100}
        save_progress(prog)
        time.sleep(2)
    
    elapsed = (time.time() - total_start) / 60
    mp3s = sorted(AUDIO_DIR.glob("lesson*.mp3"))
    total_mb = sum(f.stat().st_size for f in mp3s) / 1024 / 1024
    
    print(f"\n{'='*50}")
    print(f"✅ Generated {generated} MP3s in {elapsed:.0f} min")
    print(f"📁 {len(mp3s)} files, {total_mb:.0f}MB → {AUDIO_DIR}/")
    
    if failed:
        print(f"⚠️  {len(failed)} failed: {sorted(failed)}")
        print(f"   Retry: python3 gen_mp3.py {min(failed)} {max(failed)}")
    
    print(f"\n📤 Upload MP3s back to GitHub:")
    print(f"   git add audio/ && git commit -m 'audio: {len(mp3s)} MP3s' && git push")

if __name__ == "__main__":
    main()
