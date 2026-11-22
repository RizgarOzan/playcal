"""playcal calibrate: predict the human pass rate of each level from bot runs, calibrated on the
levels a small human panel played.

Both sides are reduced to a per-level attempt pass rate on the logit scale. Human logits are fit to
bot logits with a random-effects meta-regression (weighted least squares, DerSimonian-Laird tau^2),
so the between-level scatter the bots don't explain widens every prediction. Leave-one-out over the
calibration levels says how far to trust it.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

from .ingest import check_lines
from .registry import Module
from .report import _level_sort_key, check_targets, load_targets, summarize

# two-sided 95% t quantiles; above 30 df the normal value is close enough
T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
        2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t975(df: int) -> float:
    return T975[df - 1] if 1 <= df <= len(T975) else 1.96


def logit(k: int, n: int) -> tuple[float, float]:
    """Empirical logit of k/n with the 0.5 correction, and its sampling variance."""
    return math.log((k + 0.5) / (n - k + 0.5)), 1 / (k + 0.5) + 1 / (n - k + 0.5)


def expit(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _wls(xs, ys, ws):
    """Weighted least squares y = a + b x. Returns (a, b, cov) with cov the 2x2 inverse of X'WX."""
    sw = sum(ws)
    sx = sum(w * x for w, x in zip(ws, xs))
    sxx = sum(w * x * x for w, x in zip(ws, xs))
    sy = sum(w * y for w, y in zip(ws, ys))
    sxy = sum(w * x * y for w, x, y in zip(ws, xs, ys))
    det = sw * sxx - sx * sx
    if det <= 1e-12:
        raise ValueError("the bot pass rates of the calibration levels are all the same; nothing to fit")
    b = (sw * sxy - sx * sy) / det
    a = (sy - b * sx) / sw
    return a, b, ((sxx / det, -sx / det), (-sx / det, sw / det))


def fit(points: list[tuple[float, float, float]]) -> dict:
    """points: (bot logit x, human logit y, human sampling variance v), one per calibration level."""
    if len(points) < 3:
        raise ValueError(f"need humans on at least 3 levels to calibrate, got {len(points)}")
    xs, ys, vs = zip(*points)
    w = [1 / v for v in vs]
    a, b, cov = _wls(xs, ys, w)
    q = sum(wi * (y - a - b * x) ** 2 for wi, x, y in zip(w, xs, ys))
    df = len(points) - 2
    # C = sum(w) - trace((X'WX)^-1 X'W^2X)
    s2 = (sum(wi ** 2 for wi in w), sum(wi ** 2 * x for wi, x in zip(w, xs)), sum(wi ** 2 * x * x for wi, x in zip(w, xs)))
    c = sum(w) - (cov[0][0] * s2[0] + 2 * cov[0][1] * s2[1] + cov[1][1] * s2[2])
    tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    a, b, cov = _wls(xs, ys, [1 / (v + tau2) for v in vs])
    return {"a": a, "b": b, "cov": cov, "tau2": tau2, "df": df}


def predict(model: dict, x: float, extra_var: float = 0.0) -> tuple[float, float, float]:
    """Predicted human pass rate for a level with bot logit x, and its 95% interval. extra_var adds
    sampling noise when the interval should cover an observed rate rather than the true one."""
    (c00, c01), (_, c11) = model["cov"]
    eta = model["a"] + model["b"] * x
    se = math.sqrt(c00 + 2 * c01 * x + c11 * x * x + model["tau2"] + extra_var)
    t = t975(model["df"])
    return expit(eta), expit(eta - t * se), expit(eta + t * se)


def _loo_predictions(points) -> list[tuple[float, float, float]] | None:
    """Each level predicted from a fit on all the others: (p, lo, hi), or None when that can't be done."""
    if len(points) < 4:
        return None
    out = []
    for i, (x, _, v) in enumerate(points):
        try:
            m = fit(points[:i] + points[i + 1:])
        except ValueError:
            return None
        out.append(predict(m, x, extra_var=v))
    return out


def leave_one_out(points) -> dict | None:
    preds = _loo_predictions(points)
    if preds is None:
        return None
    hits = sum(lo <= expit(y) <= hi for (_, lo, hi), (_, y, _) in zip(preds, points))
    errs = [abs(p - expit(y)) for (p, _, _), (_, y, _) in zip(preds, points)]
    return {"levels": len(points), "inside_interval": hits, "median_abs_error": round(statistics.median(errs), 4)}


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(xs, ys) -> float | None:
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx, syy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


def _scores(pred, seen) -> dict:
    """Spearman and mean absolute error, with 95% bootstrap intervals over levels (fixed seed)."""
    rho, mae = spearman(pred, seen), statistics.fmean(abs(p - s) for p, s in zip(pred, seen))
    rnd, rhos, maes = random.Random(0), [], []
    for _ in range(2000):
        idx = [rnd.randrange(len(pred)) for _ in pred]
        r = spearman([pred[i] for i in idx], [seen[i] for i in idx])
        if r is not None:
            rhos.append(r)
        maes.append(statistics.fmean(abs(pred[i] - seen[i]) for i in idx))

    def ci(v):
        v = sorted(v)
        return [round(v[int(0.025 * len(v))], 4), round(v[int(0.975 * len(v)) - 1], 4)] if v else None

    return {"spearman": None if rho is None else round(rho, 4), "spearman_ci": ci(rhos),
            "mae": round(mae, 4), "mae_ci": ci(maes)}


def validate(points, seen: list[float]) -> dict | None:
    """Leave-one-out check of the bot model against a baseline that knows only the level order.
    seen: each calibration level's observed human pass rate, in the same order as points."""
    preds = _loo_predictions(points)
    base = _loo_predictions([(i, y, v) for i, (_, y, v) in enumerate(points)])
    if preds is None or base is None:
        return None
    return {"levels": len(points), "model": _scores([p for p, _, _ in preds], seen),
            "baseline_level_order": _scores([p for p, _, _ in base], seen)}


def check_prereg(val: dict | None, prereg: dict) -> list[str]:
    """Why the preregistered thresholds fail; empty means they pass. Keys, all optional:
    min_levels, spearman_min, mae_max (a fraction: 0.10 = 10 points), beat_baseline."""
    if val is None:
        return ["validation needs at least 4 calibration levels"]
    m, b, why = val["model"], val["baseline_level_order"], []
    if val["levels"] < prereg.get("min_levels", 0):
        why.append(f"{val['levels']} calibration levels, preregistered minimum {prereg['min_levels']}")
    if "spearman_min" in prereg and (m["spearman"] is None or m["spearman"] < prereg["spearman_min"]):
        why.append(f"spearman {m['spearman']} below {prereg['spearman_min']}")
    if "mae_max" in prereg and m["mae"] > prereg["mae_max"]:
        why.append(f"mean abs error {m['mae']} above {prereg['mae_max']}")
    if prereg.get("beat_baseline") and m["mae"] >= b["mae"]:
        why.append(f"mean abs error {m['mae']} does not beat the level-order baseline {b['mae']}")
    return why


def _stats(files: list[str]) -> dict:
    lines = [(f, line) for f in files for line in Path(f).read_text(encoding="utf-8").splitlines()]
    res = check_lines(lines)
    if not res.accepted:
        why = f" ({len(res.rejected)} rejected, first: {res.rejected[0]['reason']})" if res.rejected else ""
        raise ValueError(f"no valid events in {', '.join(files)}{why}")
    return {(s.game_version, s.level): s for s in summarize(res.accepted)}


def calibrate(human: dict, bot: dict, min_attempts: int = 5) -> dict:
    points, keys, seen = [], [], []
    for key in sorted(bot, key=_level_sort_key):
        h, b = human.get(key), bot[key]
        if h and h.attempts >= min_attempts and b.attempts:
            x, _ = logit(b.attempts_passed, b.attempts)
            y, v = logit(h.attempts_passed, h.attempts)
            points.append((x, y, v))
            keys.append(key)
            seen.append(h.attempts_passed / h.attempts)
    model = fit(points)
    levels = []
    for key in sorted(bot, key=_level_sort_key):
        b = bot[key]
        if not b.attempts:
            continue
        p, lo, hi = predict(model, logit(b.attempts_passed, b.attempts)[0])
        row = {"game_version": key[0], "level": key[1], "bot_pass_rate": round(b.attempts_passed / b.attempts, 4),
               "bot_attempts": b.attempts, "predicted": round(p, 4), "ci": [round(lo, 4), round(hi, 4)],
               "calibration": key in keys}
        h = human.get(key)
        if h and h.attempts:
            row["human_pass_rate"], row["human_attempts"] = round(h.attempts_passed / h.attempts, 4), h.attempts
        levels.append(row)
    return {"model": {"a": round(model["a"], 4), "b": round(model["b"], 4), "tau": round(math.sqrt(model["tau2"]), 4),
                      "calibration_levels": len(points)},
            "leave_one_out": leave_one_out(points), "validation": validate(points, seen), "levels": levels}


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--human", nargs="+", required=True, help="event JSONL from the human panel (accepted.jsonl from ingest)")
    p.add_argument("--bot", nargs="+", required=True, help="event JSONL from bot runs, same schema")
    p.add_argument("--min-attempts", type=int, default=5, help="human attempts a level needs to count for calibration (default 5)")
    p.add_argument("--targets", metavar="FILE", help='JSON {"4": [40, 60]}: predicted human pass-rate band per level; a level outside its band exits 1')
    p.add_argument("--prereg", metavar="FILE", help='JSON thresholds written before looking at the data, e.g. '
                   '{"min_levels": 15, "spearman_min": 0.6, "mae_max": 0.10, "beat_baseline": true}; a miss exits 1')
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _run(args: argparse.Namespace) -> int:
    try:
        out = calibrate(_stats(args.human), _stats(args.bot), args.min_attempts)
        targets = load_targets(args.targets) if getattr(args, "targets", None) else {}
        prereg = json.loads(Path(args.prereg).read_text(encoding="utf-8")) if getattr(args, "prereg", None) else None
    except (OSError, ValueError, TypeError) as e:
        print(json.dumps({"error": str(e)}) if args.json else f"playcal calibrate: {e}", file=None if args.json else sys.stderr)
        return 2
    misses = check_targets(((r["game_version"], r["level"], r["predicted"]) for r in out["levels"]), targets, "predicted")
    failed = check_prereg(out["validation"], prereg) if prereg is not None else []
    code = 1 if misses or failed else 0
    if targets:
        out["targets_missed"] = misses
    if prereg is not None:
        out["prereg"] = {"thresholds": prereg, "passed": not failed, "failed": failed}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return code
    m, loo = out["model"], out["leave_one_out"]
    print(f"{'version':<10} {'level':<8} {'bot':>5} {'predicted human':>20} {'human seen':>12}")
    for r in out["levels"]:
        pred = f"{_pct(r['predicted'])} [{_pct(r['ci'][0])}-{_pct(r['ci'][1])}]"
        seen = f"{_pct(r['human_pass_rate'])} n={r['human_attempts']}" if "human_pass_rate" in r else "-"
        print(f"{r['game_version']:<10} {r['level']:<8} {_pct(r['bot_pass_rate']):>5} {pred:>20} {seen:>12}"
              + ("  *" if r["calibration"] else ""))
    print(f"* calibration level ({m['calibration_levels']}). Attempt pass rates; brackets = 95% interval for the level's true human rate.")
    if loo:
        print(f"leave-one-out: {loo['inside_interval']}/{loo['levels']} human rates inside their interval, "
              f"median error {100 * loo['median_abs_error']:.0f} points.")
    else:
        print("leave-one-out needs at least 4 calibration levels; treat these numbers as a rough guess.")
    val = out["validation"]
    for name, sc in ((("bot model", val["model"]), ("level-order baseline", val["baseline_level_order"])) if val else ()):
        rho = "n/a" if sc["spearman"] is None else f"{sc['spearman']:.2f} [{sc['spearman_ci'][0]:.2f}, {sc['spearman_ci'][1]:.2f}]"
        print(f"{name}: spearman {rho}, mean error {100 * sc['mae']:.1f} points "
              f"[{100 * sc['mae_ci'][0]:.1f}, {100 * sc['mae_ci'][1]:.1f}] (leave-one-out, 95% bootstrap)")
    if prereg is not None:
        print("preregistered thresholds: " + ("PASS" if not failed else "FAIL: " + "; ".join(failed)))
    for miss in misses:
        print(miss["message"])
    return code


MODULE = Module(name="calibrate", summary="predict each level's human pass rate from bot runs, calibrated on a small human panel, with 95% intervals",
                add_args=_add_args, run=_run)
