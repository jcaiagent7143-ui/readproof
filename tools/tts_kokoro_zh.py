"""Mandarin narration with Kokoro-82M-v1.1-zh (Apache-2.0), on this Mac, for free.

usage: kokoro_zh.py VOICE SPEED OUT.wav "text"   (one line of narration)
       kokoro_zh.py VOICE SPEED OUTDIR --lines lines.json   ([{id, text}] → OUTDIR/<id>.wav + OUTDIR/<id>.json)
Writes 24 kHz mono WAV. The JSON per line holds the duration and the per-chunk timings
(the pipeline yields one chunk per clause, so chunk boundaries fall on the commas).
"""
import json
import sys
import warnings

import numpy as np
import soundfile as sf
import torch
from kokoro import KModel, KPipeline

warnings.filterwarnings("ignore")
REPO = "hexgrad/Kokoro-82M-v1.1-zh"
voice, speed = sys.argv[1], float(sys.argv[2])
model = KModel(repo_id=REPO).to("cpu").eval()
en = KPipeline(lang_code="a", repo_id=REPO, model=False)


def en_callable(text):
    return next(en(text)).phonemes


zh = KPipeline(lang_code="z", repo_id=REPO, model=model, en_callable=en_callable)
SR = 24000


def say(text):
    parts, spans, t = [], [], 0.0
    for res in zh(text, voice=voice, speed=speed, split_pattern=r"(?<=[。！？!?])"):
        a = res.audio.numpy() if isinstance(res.audio, torch.Tensor) else np.asarray(res.audio)
        parts.append(a)
        spans.append({"text": res.graphemes, "start": round(t, 3), "end": round(t + len(a) / SR, 3)})
        t += len(a) / SR
    return np.concatenate(parts), spans


if len(sys.argv) > 5 and sys.argv[4] == "--lines":
    outdir = sys.argv[3]
    for line in json.load(open(sys.argv[5])):
        audio, spans = say(line["text"])
        sf.write(f"{outdir}/{line['id']}.wav", audio, SR)
        json.dump({"id": line["id"], "duration_s": round(len(audio) / SR, 3), "chunks": spans},
                  open(f"{outdir}/{line['id']}.json", "w"), ensure_ascii=False)
        print(line["id"], round(len(audio) / SR, 2), "s", len(spans), "chunks")
else:
    audio, spans = say(sys.argv[4])
    sf.write(sys.argv[3], audio, SR)
    print(round(len(audio) / SR, 2), "s", [s["text"] for s in spans])
