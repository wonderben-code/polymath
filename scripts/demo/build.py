#!/usr/bin/env python3
"""Build the Polymath demo film from the seekable sources in film/src.

Two cuts from one shot list: A (best bits, with holds and jumps) and B (unfold,
each feature continuous with speed ramps). Silent unless --voice DIR is given:
then the seven narration clips in DIR (named 1..7 in any audio format; clip 1
covers the title card and the home page, then Ask, Solve, Converge, Frontier,
Studio, end card) are laid at each block's start, silence fills the rest, and a
clip longer than its block lengthens that block (a card holds longer; a feature's
fastest unfold segment runs a little slower), never a cut.
Usage: build.py [A|B|both] [--voice DIR]
"""
import glob, os, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'src')
OUT = os.path.join(HERE, 'out')
SERIF = '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'
SANS = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
SANS_B = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
W, H, FPS = 1280, 800, 30
ENC = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-pix_fmt', 'yuv420p', '-r', str(FPS), '-an']
PAPER, INK, GOLD = '0xf7f5f0', '0x1f1d1a', '0xb86e00'

def run(args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *args], check=True)

def textfile(tmp, name, text):
    path = os.path.join(tmp, name + '.txt')
    with open(path, 'w') as f: f.write(text)
    return path

def overlays(tmp, key, pill, caption):
    """Pill top-left, caption bottom: drawtext filters reading from text files (no escaping worries)."""
    parts = []
    if pill:
        p = textfile(tmp, key + '-pill', pill)
        parts.append(f"drawtext=fontfile={SANS_B}:textfile={p}:fontsize=22:fontcolor=white:box=1:boxcolor=0x2e2c29@0.92:boxborderw=12:x=36:y=28")
    if caption:
        c = textfile(tmp, key + '-cap', caption)
        parts.append(f"drawtext=fontfile={SANS}:textfile={c}:fontsize=26:fontcolor=white:box=1:boxcolor=0x2e2c29@0.9:boxborderw=14:x=36:y=h-th-40")
    return parts

def segment(tmp, key, src, start, end, speed, pill, caption, fade_in=False):
    out = os.path.join(tmp, key + '.mp4')
    vf = [f"setpts=PTS/{speed}", f"fps={FPS}", f"scale={W}:{H}:force_original_aspect_ratio=decrease", f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=white"]
    if fade_in: vf.append('fade=t=in:st=0:d=0.35')
    vf += overlays(tmp, key, pill, caption)
    run(['-ss', str(start), '-t', str(end - start), '-i', os.path.join(SRC, src + '.mp4'), '-vf', ','.join(vf), *ENC, out])
    return out

def still(tmp, key, src, at, seconds, pill, caption, fade_in=False):
    img = os.path.join(tmp, key + '.png')
    run(['-ss', str(at), '-i', os.path.join(SRC, src + '.mp4'), '-frames:v', '1', img])
    out = os.path.join(tmp, key + '.mp4')
    vf = [f"scale={W}:{H}:force_original_aspect_ratio=decrease", f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=white"]
    if fade_in: vf.append('fade=t=in:st=0:d=0.35')
    vf += overlays(tmp, key, pill, caption)
    run(['-loop', '1', '-i', img, '-t', str(seconds), '-vf', ','.join(vf), *ENC, out])
    return out

def card(tmp, key, seconds, lines, fade=True):
    """A paper-coloured card: first line serif headline, the rest smaller sans."""
    out = os.path.join(tmp, key + '.mp4')
    vf = []
    y = 250 if len(lines) > 2 else 300
    for i, (text, kind) in enumerate(lines):
        p = textfile(tmp, f'{key}-{i}', text)
        if kind == 'headline':
            vf.append(f"drawtext=fontfile={SERIF}:textfile={p}:fontsize=58:fontcolor={INK}:x=(w-tw)/2:y={y}"); y += 90
        elif kind == 'sub':
            vf.append(f"drawtext=fontfile={SERIF}:textfile={p}:fontsize=34:fontcolor={INK}:x=(w-tw)/2:y={y}"); y += 56
        elif kind == 'small':
            vf.append(f"drawtext=fontfile={SANS}:textfile={p}:fontsize=24:fontcolor=0x6b665d:x=(w-tw)/2:y={y}"); y += 44
        elif kind == 'gold':
            vf.append(f"drawtext=fontfile={SANS_B}:textfile={p}:fontsize=22:fontcolor={GOLD}:x=(w-tw)/2:y={y}"); y += 44
    if fade: vf.append(f'fade=t=in:st=0:d=0.6,fade=t=out:st={seconds-0.6}:d=0.6')
    run(['-f', 'lavfi', '-i', f'color=c={PAPER}:s={W}x{H}:r={FPS}', '-t', str(seconds), '-vf', ','.join(vf), *ENC, out])
    return out

def concat(parts, out):
    lst = out + '.txt'
    with open(lst, 'w') as f:
        for p in parts: f.write(f"file '{p}'\n")
    run(['-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', '-movflags', '+faststart', out])
    os.remove(lst)

TITLE = [('Polymath', 'headline'), ('Discoveries hide between fields of science.', 'sub'), ('Polymath finds them.', 'sub'), ('Built on Meta Muse Spark for the Meta Global AI Developer Hackathon', 'small')]
END = [('askpolymath.com', 'headline'), ('Meta Muse Spark  ×  Polymath', 'sub'), ('Reads arXiv and OpenAlex · publishes to Zenodo · free to try, on its own spending ledger', 'small'), ('Discoveries between fields, found with Meta', 'gold')]

def plan(cut):
    """(kind, key, src, start, end, speed, pill, caption). Times are seconds into the source."""
    A = cut == 'A'
    p = [('card', 'title', 8, TITLE)]
    p += [('seg', 'home', 'solve', 1, 9, 1, None, 'askpolymath.com, live, as any visitor sees it', True)]
    # Ask
    p += [('seg', 'ask-type', 'ask', 6.5, 10.5, 1, 'ASK', 'Ask an open question', True),
          ('seg', 'ask-run', 'ask', 10.5, 114, 25, 'ASK', 'Meta plans searches, reads papers from arXiv and OpenAlex', False),
          ('seg', 'ask-result', 'ask', 118, 146, 2, 'ASK', 'Numbered claims, labelled and backed by exact passages', False)]
    # Solve
    p += [('seg', 'solve-type', 'solve', 7, 11, 1, 'SOLVE', 'Describe a problem', True),
          ('seg', 'solve-run', 'solve', 11, 684, 90, 'SOLVE', 'An answer is found, fits from other fields are tried, a new key is forged', False),
          ('seg', 'solve-result', 'solve', 686, 700, 1.5, 'SOLVE', 'The lock, the key, and where each fit falls short', False)]
    if A: p += [('still', 'solve-hold', 'solve', 688, 3, 'SOLVE', 'A new key was forged', False)]
    # Converge
    if not A: p += [('seg', 'conv-type', 'converge', 3, 10, 1.5, 'CONVERGE', 'One theme, twelve distant fields', True)]
    p += [('seg', 'conv-unfold', 'converge', 10, 700, 40, 'CONVERGE', 'Each connection is tested against the literature; what holds is drawn inward', A),
          ('seg', 'conv-finish', 'converge', 700, 710, 1, 'CONVERGE', 'A level-2 synthesis in the centre, then paused at the spending ceiling', False)]
    # Frontier
    if not A: p += [('seg', 'front-type', 'frontier', 4, 10, 1.5, 'FRONTIER', 'An open problem nobody has solved yet', True)]
    p += [('seg', 'front-unfold', 'frontier', 10, 1150, 80, 'FRONTIER', 'Survey, gaps, solutions from other fields, steps', A),
          ('seg', 'front-result', 'frontier', 1150, 1157, 1, 'FRONTIER', 'A six-step plan for a space elevator, nineteen minutes in', False)]
    if A: p += [('still', 'front-hold', 'frontier', 1152, 3, 'FRONTIER', 'Every step is an ordinary investigation with its own evidence', False)]
    # Studio
    p += [('seg', 'studio-brief', 'whale', 4, 12, 1.5, 'STUDIO', 'A brief and its requirements', True),
          ('seg', 'studio-stages', 'whale', 12, 860, 70, 'STUDIO', 'Brief, inspirations from far fields, invention, engineering, stress test', False),
          ('seg', 'studio-art', 'whale', 860, 882, 2, 'STUDIO', 'HSSG-1000, painted by Meta Muse Image', False),
          ('seg', 'studio-sheet', 'whale', 2437, 2482, 2.5 if A else 3, 'STUDIO', 'Engineering with cited numbers, a stress test, and what is still to invent', A),
          ('seg', 'dyson', 'dyson', 1118, 1122, 1, 'STUDIO', 'A Dyson swarm of phyllotactic sail-statites', True)]
    p += [('card', 'end', 12, END)]
    return p

BLOCKS = [('intro', ('title', 'home')), ('ask', ('ask-type', 'ask-run', 'ask-result')),
          ('solve', ('solve-type', 'solve-run', 'solve-result', 'solve-hold')),
          ('converge', ('conv-type', 'conv-unfold', 'conv-finish')),
          ('frontier', ('front-type', 'front-unfold', 'front-result', 'front-hold')),
          ('studio', ('studio-brief', 'studio-stages', 'studio-art', 'studio-sheet', 'dyson')), ('end', ('end',))]
TAIL = 0.6  # seconds of quiet after a clip before the next block
MAX_SECONDS = 179.5  # the competition's three-minute ceiling, with a little margin

def item_seconds(item):
    kind = item[0]
    if kind == 'card': return item[2]
    if kind == 'still': return item[4]
    _, key, src, start, end, speed = item[:6]
    return (end - start) / speed

def probe_seconds(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path], capture_output=True, text=True).stdout.strip()
    return float(out)

def voice_clips(voice_dir):
    """Seven clips named 1..7 (any extension), in block order."""
    clips = []
    for n in range(1, 8):
        found = sorted(glob.glob(os.path.join(voice_dir, f'{n}.*')) + glob.glob(os.path.join(voice_dir, f'{n}-*')) + glob.glob(os.path.join(voice_dir, f'{n} *')))
        if not found: raise SystemExit(f'voice clip {n} missing in {voice_dir}')
        clips.append(found[0])
    return clips

def fit_plan(items, clips):
    """Lengthen any block whose clip overruns it. Returns (items, block_seconds)."""
    items = [list(i) for i in items]
    by_key = {i[1]: i for i in items}
    block_seconds = []
    for (name, keys), clip in zip(BLOCKS, clips):
        present = [by_key[k] for k in keys if k in by_key]
        have = sum(item_seconds(i) for i in present)
        need = probe_seconds(clip) + TAIL
        extra = max(0.0, need - have)
        if extra > 0:
            cards = [i for i in present if i[0] == 'card']
            if cards:
                cards[-1][2] = cards[-1][2] + extra
            else:
                seg = max((i for i in present if i[0] == 'seg'), key=lambda i: i[5])
                span = seg[4] - seg[3]
                seg[5] = span / (span / seg[5] + extra)
            print(f'{name}: clip {need - TAIL:.1f}s, picture {have:.1f}s, block lengthened by {extra:.1f}s', flush=True)
        block_seconds.append(sum(item_seconds(i) for i in present))
    total = sum(block_seconds)
    if total > MAX_SECONDS:
        # Take the overrun back from blocks whose clip finishes early: their fastest
        # unfold segment runs a little faster. Cards and the intro are left alone.
        over = total - MAX_SECONDS
        slack = []
        for n, ((name, keys), clip) in enumerate(zip(BLOCKS, clips)):
            present = [by_key[k] for k in keys if k in by_key]
            segs = [i for i in present if i[0] == 'seg']
            if not segs: continue
            spare = block_seconds[n] - (probe_seconds(clip) + TAIL)
            if spare > 0.2: slack.append((spare, n, max(segs, key=lambda i: i[5])))
        for spare, n, seg in sorted(slack, reverse=True):
            if over <= 0: break
            take = min(spare - 0.2, over)
            span = seg[4] - seg[3]
            seg[5] = span / max(1.0, span / seg[5] - take)
            block_seconds[n] -= take; over -= take
            print(f'{BLOCKS[n][0]}: {take:.1f}s taken back to stay under three minutes', flush=True)
        if over > 0.05:
            raise SystemExit(f'the narration does not fit in three minutes: {over:.1f}s too long; trim the longest clips')
    print(f'film {sum(block_seconds):.1f}s', flush=True)
    return [tuple(i) for i in items], block_seconds

def voice_track(tmp, clips, block_seconds):
    """One WAV: each clip at its block start, padded with silence to the block length, then loudness-normalised."""
    parts = []
    for n, (clip, seconds) in enumerate(zip(clips, block_seconds), 1):
        out = os.path.join(tmp, f'voice-{n}.wav')
        run(['-i', clip, '-af', f'apad,atrim=0:{seconds:.3f}', '-ar', '48000', '-ac', '2', out])
        parts.append(out)
    lst = os.path.join(tmp, 'voice.txt')
    with open(lst, 'w') as f:
        for p in parts: f.write(f"file '{p}'\n")
    joined = os.path.join(tmp, 'voice.wav')
    run(['-f', 'concat', '-safe', '0', '-i', lst, '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', '-ar', '48000', joined])
    return joined

def build(cut, voice_dir=None):
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=f'film-{cut}-')
    items = plan(cut)
    clips = block_seconds = None
    if voice_dir:
        clips = voice_clips(voice_dir)
        items, block_seconds = fit_plan(items, clips)
    parts = []
    for item in items:
        kind = item[0]
        if kind == 'card': parts.append(card(tmp, item[1], item[2], item[3]))
        elif kind == 'seg': parts.append(segment(tmp, *item[1:]))
        elif kind == 'still': parts.append(still(tmp, *item[1:]))
        print(cut, item[1], flush=True)
    out = os.path.join(OUT, f'polymath-demo-{cut}{"-voiced" if voice_dir else ""}.mp4')
    if voice_dir:
        silent = os.path.join(tmp, 'silent.mp4')
        concat(parts, silent)
        voice = voice_track(tmp, clips, block_seconds)
        run(['-i', silent, '-i', voice, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', out])
    else:
        concat(parts, out)
    shutil.rmtree(tmp)
    dur = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:format=size', '-of', 'csv=p=0', out], capture_output=True, text=True).stdout.strip()
    print(cut, 'done', dur, flush=True)

if __name__ == '__main__':
    args = sys.argv[1:]
    voice_dir = None
    if '--voice' in args:
        at = args.index('--voice'); voice_dir = args[at + 1]; del args[at:at + 2]
    which = args[0] if args else 'both'
    for cut in (['A', 'B'] if which == 'both' else [which]): build(cut, voice_dir)
