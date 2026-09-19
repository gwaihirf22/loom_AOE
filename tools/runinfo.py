"""
Loom — what a capture run is, written down at capture time.

A run folder is named `run_<timestamp>_<skin>[_<label>]` and that name
carries no scale. It cannot: the folder is named before the first frame
is saved, and the scale is a measurement rather than a setting. So the
corpus's most load-bearing variable was recoverable only by re-anchoring
a frame, and two runs at HUD slider 115% and 125% sat in `captures/` for
a day looking exactly like every other run in there.

That is the gap this closes. `grab_frames` measures the skin already -
it has to, to name the folder - and throws the scale and the score away
on the same line. Here they are kept.

ONE definition of the file, imported by everything that touches it,
because a writer and a reader that each know the key names separately
are two answers to one question and will drift.

Everything here is best-effort: a run with no facts file is every run
captured before this existed, and the tools fall back to re-measuring
rather than refusing.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
import os

# The file's name inside a run folder. Not "run_info" or "meta" - the
# frames are `frame_NNNN.png`, so this sorts beside them and reads as
# what it is.
FACTS_FILE = "run.json"


def write(run_dir, skin, scale, score, frame, label=None, top=None):
    """Write what this run IS, beside its frames. Silent on failure.

    Failure is deliberately not raised: this is a note about a capture,
    and a capture session that dies because it could not write a note
    would have thrown away the thing that mattered for the sake of the
    thing that did not.
    """
    facts = {
        "skin": skin,
        # None rather than 0.0 when the HUD was not found - "I did not
        # measure it" and "I measured zero" are different answers, and a
        # 0.0 here would be read as a real reading by anything that
        # averages these.
        "scale": None if scale is None else round(float(scale), 4),
        "score": None if score is None else round(float(score), 4),
        "frame": list(frame) if frame else None,
        "label": label,
        # The --top fraction, because a cropped run cannot be labelled
        # for production and nothing else on disk says it was cropped.
        "top": top,
    }
    try:
        with open(os.path.join(run_dir, FACTS_FILE), "w",
                  encoding="utf-8") as handle:
            json.dump(facts, handle, indent=1)
            handle.write("\n")
    except OSError:
        pass
    return facts


def read(run_dir):
    """This run's facts as a dict, or None if it has none.

    None means "captured before this existed, go and measure it", never
    "this run has no HUD".
    """
    try:
        with open(os.path.join(run_dir, FACTS_FILE), encoding="utf-8") as f:
            facts = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return facts if isinstance(facts, dict) else None


# How wide a scale bucket is when runs are pooled for reporting.
#
# 0.1 puts 1080p (0.735) and 1440p (0.98) in their own buckets and the
# two large-HUD runs (1.155, 1.26) in two more, which is the split that
# matters. Finer and every run is its own bucket, which is what the
# per-run tables already give.
SCALE_BUCKET = 0.1


def bucket(scale):
    """The reporting bucket one scale falls in, as a float, or None."""
    if scale is None:
        return None
    return round(round(float(scale) / SCALE_BUCKET) * SCALE_BUCKET, 1)
