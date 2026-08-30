"""Sweep how the game's own unit art should be cut (development tool).

The queue templates are cut from a wiki icon library I maintain by hand. The
game ships its own portraits - units/NNN_50730.DDS - and if those could be cut
well enough, the hand-maintained half of the library would stop needing me.

They cannot, yet. Measured on real capture cells they TRACK the hand-cut
templates within 0.05-0.08 rather than beating them, at 1080p and at 1440p
alike, so today they ride along as extra variants and as the only source for
units the wiki set lacks. The open question is whether that gap is the ART or
the CUT, and only the cut has ever been varied - by zoom, at a handful of
values, always taking the window from the middle of the frame. A centred
window is an assumption; the game crops these to fit a cell and nothing says
the art it keeps is centred in the file.

So this sweeps zoom AND offset, and reports two numbers per candidate,
because one would lie:

  score      how well the RIGHT template matches its own cells. A cut can
             raise this and get worse - the notification font did exactly
             that, its corpus score rising while two real events stopped
             being read at all.
  junk       the best score anything reaches on a cell that should match
             NOTHING - terrain, a decorated empty slot. This column exists
             because the FIRST cut this tool recommended took top-1 from
             98.6% to 100% and pushed junk terrain from 0.500 to 0.521,
             far enough past the uncorroborated identity gate to be
             believed. The labelled cells cannot see that: junk has no
             label, so it is not in the population being optimised, and
             an existing invariant test caught it rather than this sweep.
             The pair one row down on the same table was (1.30, -2) -
             100% top-1 AND junk down to 0.476.
  top-1      how often the right template also BEATS the others. This is
             the number that means the reader would be right.

    python -m tools.cut_sweep --harvest      # read the corpus, cache cells
    python -m tools.cut_sweep                # sweep against the cache
    python -m tools.cut_sweep --zooms 1.2,1.3 --offsets 2

WHERE THE LABELS COME FROM, AND WHAT THAT COSTS. A cell is labelled only
when two independent witnesses agree: today's reader names it confidently
(clear score AND a clear margin, the same two gates the live reader uses to
refuse a guess), and the recorded game says the player ordered that thing in
that match. Pixels and a command log have no way to be wrong together.

The bias is real and is printed with the results rather than left in a
docstring: the labels come from cells today's templates ALREADY read well, so
this can measure whether the game's art closes a known gap. It cannot measure
whether the game's art would find cells today's templates miss entirely -
those cells have no label and nothing here can give them one.

And 4K is not in this at all, because there are no 4K captures. That is the
resolution the game's own art was most expected to help with.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import collections
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import anchor, hud, paths, queue, replay  # noqa: E402
from loom import reader as hud_reader  # noqa: E402
from tools import build_queue_templates as build  # noqa: E402
from tools import queue_report  # noqa: E402

CELLS_FILE = paths.PROJECT_ROOT / "captures" / "cut_cells.npz"

# One frame in this many. The reader's answer barely moves between adjacent
# frames - a queue item sits for many polls - so reading every one would buy
# near-duplicate cells at full price.
EVERY = 5

# At most this many cells for any one identity. Villagers are most of every
# queue, and without a cap the sweep would be a villager sweep with a few
# other units watching.
PER_IDENTITY = 80


def dds_unit_sources():
    """{identity: [DDS unit spec, ...]} - the art this sweep can cut.

    Only units with a game texture can be scored: an identity with no DDS
    source would lose every comparison for want of a template rather than
    for want of a good cut, and counting that as a fault would be measuring
    the library instead of the cut.
    """
    found = collections.defaultdict(list)
    for name, specs in build.SOURCES.items():
        for spec in ([specs] if isinstance(specs, str) else specs):
            if spec.startswith("DDS:units/"):
                found[name].append(spec)
    return dict(found)


# ---- harvesting labelled cells ---------------------------------------------

def cells_from_run(run_dir, record_path, wanted, every=EVERY):
    """Labelled cells from one capture run, as (label, gray cell, scale).

    Mirrors tools/queue_report.score: acquire the HUD once, then read each
    frame with the CURRENT reader. The boxes are recomputed from the reader's
    own wood position so the crop this returns is the same rectangle the
    identification looked at - a cell cut by different arithmetic would be
    measuring the arithmetic.
    """
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))
    if not frames:
        return []

    pop_templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    wood_templates = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    found = None
    for path in frames:
        frame = cv2.imread(path)
        if frame is None:
            continue
        found = anchor.identify_hud(frame, pop_templates,
                                    wood_templates=wood_templates)
        if found is not None and found["score"] >= hud_reader.MIN_ANCHOR_SCORE:
            break
        found = None
    if found is None:
        return []

    scale, profile = found["scale"], found["profile"]
    ordered = replay.harvest(record_path).queue_subjects()
    reader = queue.QueueReader(profile)
    harvested = []
    for path in frames[::every]:
        frame = cv2.imread(path)
        if frame is None:
            continue
        slots = reader.read(frame, scale)
        if not slots or reader._wood is None:
            continue
        boxes = queue.slot_boxes(reader._wood[0], reader._wood[1], scale,
                                 slot_one=profile.slot_one)
        strip_w, strip_h, _ = queue.strip_extent(scale, profile.slot_one)
        gray = cv2.cvtColor(
            frame[:min(strip_h, frame.shape[0]), :min(strip_w, frame.shape[1])],
            cv2.COLOR_BGR2GRAY)
        for slot in slots:
            if slot.identity not in wanted:
                continue
            # Both witnesses, or no label. The reader's two refusal gates
            # first - a name it would not stake the live overlay on is not
            # a name to measure a template against - and then the record.
            if slot.identity_score < queue.CLEAR_IDENTITY_SCORE:
                continue
            if (slot.identity_margin is None
                    or slot.identity_margin < queue.MIN_IDENTITY_MARGIN):
                continue
            if queue_report.record_name(slot.identity) not in ordered:
                continue
            x1, y1, x2, y2 = boxes[slot.index]
            cell = gray[y1:y2, x1:x2]
            if cell.size == 0:
                continue
            # The numeral is painted OVER the portrait and identification
            # already removes it; a template must be judged against what the
            # matcher sees, not against the batch count.
            harvested.append((slot.identity,
                              queue.without_numeral(cell, scale), scale))
    return harvested


def harvest(every=EVERY, per_identity=PER_IDENTITY):
    """Labelled cells from every capture run the author has paired."""
    wanted = set(dds_unit_sources())
    pairs = queue_report.read_pairs()
    kept = collections.Counter()
    cells = []
    for run, record in sorted(pairs.items()):
        run_dir = str(queue_report.CAPTURES / run)
        if not os.path.isdir(run_dir) or not os.path.exists(record):
            continue
        got = cells_from_run(run_dir, record, wanted, every)
        for label, cell, scale in got:
            if kept[label] >= per_identity:
                continue
            kept[label] += 1
            cells.append((label, cell, scale, run))
        print(f"  {run}: {len(got)} labelled")
    return cells


def save_cells(cells, path=CELLS_FILE):
    """Cache the harvest. Cells are ragged - the cell size follows the HUD
    scale - so they are stored one array per cell rather than as a stack."""
    payload = {"labels": np.array([c[0] for c in cells]),
               "scales": np.array([c[2] for c in cells], dtype=np.float32),
               "runs": np.array([c[3] for c in cells])}
    for index, cell in enumerate(c[1] for c in cells):
        payload[f"cell_{index}"] = cell
    np.savez_compressed(path, **payload)


def load_cells(path=CELLS_FILE):
    data = np.load(path, allow_pickle=False)
    labels, scales, runs = data["labels"], data["scales"], data["runs"]
    return [(str(labels[i]), data[f"cell_{i}"], float(scales[i]), str(runs[i]))
            for i in range(len(labels))]


# ---- sweeping ---------------------------------------------------------------

def templates_for(cut, art):
    """{identity: [40px greyscale template]} for one candidate cut.

    Greyscale because that is what the live reader matches: the builder
    writes a colour PNG and load_icon_templates reads it back with
    IMREAD_GRAYSCALE, which is the same conversion done a step later.
    """
    return {name: [cv2.cvtColor(cut(image), cv2.COLOR_BGR2GRAY)
                   for image in images]
            for name, images in art.items()}


def sized_for(templates, scale):
    """The template set as the live reader would size it for this HUD scale."""
    if abs(round(scale, 2) - 1.0) <= 0.02:
        return templates
    return {name: [cv2.resize(t, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_AREA) for t in variants]
            for name, variants in templates.items()}


def measure(cells, templates):
    """(mean score of the right template, top-1 accuracy) for one cut.

    Pooled over every HUD scale in the corpus. by_hud_scale() below asks the
    same question one scale at a time, which is the honest way to claim a
    cut holds at more than one resolution rather than that it wins on
    whichever resolution supplied the most cells.
    """
    by_scale = {}
    total, hits, scores = 0, 0, 0.0
    for label, cell, scale, _run in cells:
        key = round(scale, 2)
        if key not in by_scale:
            by_scale[key] = sized_for(templates, scale)
        sized = by_scale[key]
        best_name, best = None, -1.0
        for name, variants in sized.items():
            if any(t.shape[0] > cell.shape[0] or t.shape[1] > cell.shape[1]
                   for t in variants):
                continue
            score = queue._match_variants(cell, variants)
            if score > best:
                best_name, best = name, score
        if best_name is None:
            continue
        total += 1
        hits += best_name == label
        scores += queue._match_variants(cell, sized[label])
    if not total:
        return None, None, 0
    return scores / total, hits / total, total


def by_hud_scale(cells, templates):
    """{scale: (score, top-1, cells)} - the same measure, unpooled.

    The game does not draw the HUD by scaling one master, and a constant
    measured at one scale has been a latent bug in this project more times
    than any other single mistake. A cut that wins pooled and loses at 1080p
    is not a better cut, it is a cut that suits whichever resolution
    happened to contribute the most cells.
    """
    groups = collections.defaultdict(list)
    for cell in cells:
        groups[round(cell[2], 2)].append(cell)
    return {scale: measure(group, templates)
            for scale, group in sorted(groups.items())}


# Cells that must match nothing: plain terrain where a slot is not, and the
# civ decoration that hangs exactly where slot one sits. A cut is not allowed
# to make these more convincing, however well it does on real portraits -
# and only the negative fixtures can say so, because the labelled cells are
# all cells something IS drawn in.
JUNK_FIXTURES = ("junk_terrain_galley", "empty_terrain", "empty_terrain_2",
                 "decor_red_tapestry")


def junk_ceiling(templates):
    """The best score ANY template reaches on a cell that should match none.

    Returns (score, what won, which fixture). Lower is better; there is no
    threshold worth naming here, because what matters is only that a
    candidate cut does not raise it above what is already committed.
    """
    data = paths.PROJECT_ROOT / "tests" / "data" / "queue"
    worst = (-1.0, None, None)
    for name in JUNK_FIXTURES:
        image = cv2.imread(str(data / f"{name}.png"))
        if image is None:
            continue
        cell = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        for identity, variants in templates.items():
            if any(t.shape[0] > cell.shape[0] or t.shape[1] > cell.shape[1]
                   for t in variants):
                continue
            score = queue._match_variants(cell, variants)
            if score > worst[0]:
                worst = (score, identity, name)
    return worst


def parse_list(text, cast):
    return [cast(part) for part in text.split(",") if part.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--harvest", action="store_true",
                        help="re-read the corpus and cache labelled cells")
    parser.add_argument("--every", type=int, default=EVERY,
                        help="harvest one frame in this many")
    parser.add_argument("--zooms", default="1.0,1.1,1.2,1.25,1.3,1.4,1.5")
    parser.add_argument("--offsets", type=int, default=4,
                        help="largest window shift in template pixels")
    parser.add_argument("--step", type=int, default=2)
    args = parser.parse_args()

    if args.harvest or not CELLS_FILE.exists():
        print("Harvesting labelled cells:")
        cells = harvest(every=args.every)
        save_cells(cells)
        print(f"cached {len(cells)} cells in {paths.for_display(CELLS_FILE)}")
    else:
        cells = load_cells()

    if not cells:
        print("No labelled cells. Nothing here can be measured.")
        return 1

    counts = collections.Counter(label for label, _c, _s, _r in cells)
    widths = collections.Counter(round(scale, 2)
                                 for _l, _c, scale, _r in cells)
    print(f"\n{len(cells)} labelled cells over {len(counts)} identities, "
          f"HUD scales {dict(sorted(widths.items()))}")
    print("  " + ", ".join(f"{name} {n}"
                           for name, n in counts.most_common(10)))

    art = {}
    for name, specs in dds_unit_sources().items():
        images = [build.load_source(spec) for spec in specs]
        images = [image for image in images if image is not None]
        if images:
            art[name] = images
    if not art:
        print("The game's textures are not on this machine; nothing to cut.")
        return 1

    labelled = {label for label, _c, _s, _r in cells}
    print(f"{len(art)} identities have game art; {len(labelled)} of them "
          f"have labelled cells")

    offsets = list(range(-args.offsets, args.offsets + 1, args.step))
    print(f"\n{'zoom':>6} {'dx':>4} {'dy':>4} {'score':>8} {'top-1':>8}"
          f" {'junk':>7}")
    results = []
    for zoom in parse_list(args.zooms, float):
        for dy in offsets:
            for dx in offsets:
                def cut(image, zoom=zoom, dx=dx, dy=dy):
                    return build.cut_unit_dds(image, zoom, (dx, dy))
                templates = templates_for(cut, art)
                score, top1, n = measure(cells, templates)
                if score is None:
                    continue
                junk = junk_ceiling(templates)[0]
                results.append((top1, score, zoom, dx, dy, junk))
                mark = " <- today" if (zoom == build.UNIT_DDS_ZOOM
                                       and (dx, dy) == build.UNIT_DDS_OFFSET) \
                    else ""
                print(f"{zoom:>6.2f} {dx:>4} {dy:>4} {score:>8.3f} "
                      f"{top1:>7.1%} {junk:>7.3f}{mark}")

    # Ranked by accuracy, then by SEPARATION - how far a real cell sits
    # above what junk manages. Neither column alone is the objective and
    # both were tried: raw score picked exactly the old constants at every
    # zoom, and ranking by the quietest junk picked a cut whose templates
    # match their own cells 0.067 worse - margin given away in the
    # 450-template set the live reader actually chooses from. The reader's
    # question is "is this far enough above what terrain reaches", and
    # that is a difference, not either term of it.
    results.sort(key=lambda r: (r[0], r[1] - r[5]), reverse=True)
    top1, score, zoom, dx, dy, junk = results[0]
    today = [r for r in results if r[2] == build.UNIT_DDS_ZOOM
             and (r[3], r[4]) == build.UNIT_DDS_OFFSET]

    # The winner, and today, measured one HUD scale at a time.
    print()
    scales = sorted({round(c[2], 2) for c in cells})
    print(f"{'cut':>18} " + " ".join(f'{s:>16}' for s in scales))
    for label, (z, x, y) in (("today", (build.UNIT_DDS_ZOOM,) + build.UNIT_DDS_OFFSET),
                             ("best top-1", (zoom, dx, dy))):
        def cut(image, zoom=z, dx=x, dy=y):
            return build.cut_unit_dds(image, zoom, (dx, dy))
        per = by_hud_scale(cells, templates_for(cut, art))
        cells_at = " ".join(
            f"{sc:>7.3f} {t1:>7.1%}" if t1 is not None else f"{'-':>15}"
            for _scale, (sc, t1, _n) in sorted(per.items()))
        print(f"{label:>8} {z:>4} {x:>+3},{y:<+3} {cells_at}")
    print("  (each column is one HUD scale: score, then top-1)")
    print()
    print(f"Best: zoom {zoom} offset ({dx}, {dy}) - {top1:.1%} top-1, "
          f"score {score:.3f} against junk {junk:.3f} - a separation "
          f"of {score - junk:.3f}")
    if today and today[0][5] < junk:
        print(f"WARNING: this cut leaves junk at {junk:.3f} where what is "
              f"committed holds it to {today[0][5]:.3f}. A cut that makes "
              f"a cell of terrain more convincing is not an improvement, "
              f"whatever it did for top-1.")
    by_score = max(results, key=lambda r: r[1])
    if (by_score[2], by_score[3], by_score[4]) != (zoom, dx, dy):
        print(f"Best SCORE is a different cut: zoom {by_score[2]} offset "
              f"({by_score[3]}, {by_score[4]}) scores {by_score[1]:.3f} at "
              f"{by_score[0]:.1%} top-1. Score is not the thing to maximise.")

    print("\nWhat this measured, and what it did not:")
    print("  Labels come from cells today's templates already read well, so")
    print("  this says whether the game's art closes a known gap. It cannot")
    print("  say whether the game's art finds cells today's templates miss.")
    print("  4K is absent - there are no 4K captures - and that is the case")
    print("  the game's own art was most expected to help with.")
    print("\nAdopting a cut is a separate decision, gated by")
    print("  python -m tools.queue_report --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
