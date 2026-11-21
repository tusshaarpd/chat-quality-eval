"""Run the Copilot-style chat quality eval end to end.

    python harness/run_eval.py --variants v1,v2,v3 --challenger v3            # dev set, 3 samples/case
    python harness/run_eval.py --set heldout --variants v1,v3 --challenger v3 # held-out set

Writes web/data/results.json -> runs[<set>] (consumed by the dashboard). All model calls are cached,
so re-runs only pay for new prompts / variants / samples.
"""
import argparse
import json
import random
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from judge import DIMENSIONS, FAILURE_MODES, judge_pairwise, judge_pointwise
from llm import BOT_MODEL, JUDGE_MODEL, chat
from variants import VARIANTS, build_messages

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "harness" / "data"
OUT = ROOT / "web" / "data" / "results.json"
SETS = {"dev": "golden_set.jsonl", "heldout": "heldout_set.jsonl"}
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
TEMPERATURE = 0.3


# ---- Deterministic (code) graders: code where a rule is exact, the LLM judge where it isn't.
def _is_json(t):
    try:
        d = json.loads(t.strip())
        return d.get("hotel_tier1_inr") in (7000, "7000", "7,000") and d.get("hotel_other_inr") in (4500, "4500", "4,500")
    except Exception:
        return False


def _lines(t):
    return [l for l in t.strip().splitlines() if l.strip()]


CODE_CHECKS = {
    "instruction_following-01": ("exactly 3 bullets, nothing else", lambda t: len(_lines(t)) == 3 and all(re.match(r"^\s*([-*•]|\d+\.)\s", l) for l in _lines(t))),
    "instruction_following-02": ("one sentence < 20 words", lambda t: len(t.split()) < 20 and len(re.findall(r"[.!?](\s|$)", t.strip())) <= 1),
    "instruction_following-04": ("no word 'policy'", lambda t: "polic" not in t.lower()),
    "instruction_following-05": ("raw JSON, right values", _is_json),
    "instruction_following-06": ("exactly 2 lines, no emoji", lambda t: len(_lines(t)) == 2 and not EMOJI.search(t)),
}


def pmap(fn, items, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def bootstrap_ci(values, iters=5000, seed=7):
    rnd = random.Random(seed)
    n = len(values)
    means = sorted(sum(rnd.choice(values) for _ in range(n)) / n for _ in range(iters))
    return [round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters)], 4)]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def gen_kwargs(s):
    kw = {"temperature": TEMPERATURE, "max_tokens": 800}
    if s > 0:
        kw["seed"] = s  # sample 0 = original run (no seed), samples 1..k-1 = extra draws
    return kw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="dev", choices=SETS)
    ap.add_argument("--variants", default="v1,v2,v3")
    ap.add_argument("--baseline", default="v1")
    ap.add_argument("--challenger", default="v3")
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()
    vkeys, K = args.variants.split(","), args.samples

    kb = (DATA / "kb.md").read_text(encoding="utf-8")
    cases = [json.loads(l) for l in (DATA / SETS[args.set]).read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"[{args.set}] {len(cases)} cases x {len(vkeys)} variants x {K} samples | bot={BOT_MODEL} judge={JUDGE_MODEL}")

    # 1) Generate K samples per (variant, case)
    jobs = [(v, c, s) for v in vkeys for c in cases for s in range(K)]
    gens = pmap(lambda j: chat(VARIANTS[j[0]].get("model", BOT_MODEL), build_messages(j[0], j[1], kb), **gen_kwargs(j[2])), jobs)
    resp = {(v, c["id"], s): g for (v, c, s), g in zip(jobs, gens)}
    print("generated", len(gens))

    # 2) Pointwise judge every sample
    grades = pmap(lambda j: judge_pointwise(j[1], resp[(j[0], j[1]["id"], j[2])]["text"], kb), jobs)
    grade = {(v, c["id"], s): g for (v, c, s), g in zip(jobs, grades)}
    print("graded", len(grades))

    def case_pass(v, cid):  # mean pass over samples
        return sum(grade[(v, cid, s)]["pass"] for s in range(K)) / K

    # 3) Pairwise baseline vs challenger on sample 0, both orders (controls position bias)
    b, ch = args.baseline, args.challenger
    pj = [(c, order) for c in cases for order in ("bc", "cb")]

    def run_pair(item):
        c, order = item
        tb, tc = resp[(b, c["id"], 0)]["text"], resp[(ch, c["id"], 0)]["text"]
        r = judge_pairwise(c, tb, tc, kb) if order == "bc" else judge_pairwise(c, tc, tb, kb)
        w = r["winner"]
        if w != "tie":
            w = (b if w == "A" else ch) if order == "bc" else (ch if w == "A" else b)
        return w, r["reason"]

    pres = pmap(run_pair, pj)
    pair = defaultdict(dict)
    for (c, order), (w, reason) in zip(pj, pres):
        pair[c["id"]][order] = {"winner": w, "reason": reason}
    print("pairwise done")

    # 4) Aggregate
    summary = {}
    for v in vkeys:
        per_case = [case_pass(v, c["id"]) for c in cases]
        by_intent = defaultdict(list)
        for c, p in zip(cases, per_case):
            by_intent[c["intent"]].append(p)
        allk = [(c, s) for c in cases for s in range(K)]
        fm = Counter(f for c, s in allk for f in grade[(v, c["id"], s)]["failure_modes"])
        lat = [resp[(v, c["id"], s)]["latency_ms"] for c, s in allk]
        toks = [resp[(v, c["id"], s)]["out_tokens"] or 0 for c, s in allk]
        code = [CODE_CHECKS[c["id"]][1](resp[(v, c["id"], s)]["text"]) for c, s in allk if c["id"] in CODE_CHECKS]
        flaky = sum(1 for p in per_case if 0 < p < 1)
        summary[v] = {
            "name": VARIANTS[v]["name"], "model": VARIANTS[v].get("model", BOT_MODEL), "hypothesis": VARIANTS[v]["hypothesis"], "system_prompt": VARIANTS[v]["system"],
            "pass_rate": round(statistics.mean(per_case), 4), "pass_ci95": bootstrap_ci(per_case),
            "flaky_cases": flaky,
            "dimensions": {d: round(statistics.mean(grade[(v, c["id"], s)]["scores"][d] for c, s in allk), 2) for d in DIMENSIONS},
            "by_intent": {k: round(statistics.mean(x), 4) for k, x in by_intent.items()},
            "failure_modes": {f: fm.get(f, 0) for f in FAILURE_MODES},
            "latency_ms_p50": pct(lat, 0.5), "latency_ms_p95": pct(lat, 0.95),
            "avg_out_tokens": round(statistics.mean(toks)),
            "code_checks_pass_rate": round(sum(code) / len(code), 3) if code else None,
        }

    deltas = [case_pass(ch, c["id"]) - case_pass(b, c["id"]) for c in cases]
    flips = {"fixed": sum(1 for c in cases if case_pass(b, c["id"]) < 0.5 <= case_pass(ch, c["id"])),
             "broke": sum(1 for c in cases if case_pass(ch, c["id"]) < 0.5 <= case_pass(b, c["id"]))}
    consistent, inconsistent = Counter(), 0
    for c in cases:
        w1, w2 = pair[c["id"]]["bc"]["winner"], pair[c["id"]]["cb"]["winner"]
        if w1 == w2:
            consistent[w1] += 1
        else:
            inconsistent += 1
            consistent["tie"] += 1

    jc = [(grade[(v, cid, s)]["pass"], fn(resp[(v, cid, s)]["text"]))
          for v in vkeys for cid, (_, fn) in CODE_CHECKS.items() if any(c["id"] == cid for c in cases) for s in range(K)]
    judge_code_agree = round(sum(1 for a, b2 in jc if a == b2) / len(jc), 3) if jc else None

    records = []
    for c in cases:
        records.append({
            "id": c["id"], "intent": c["intent"], "messages": c["messages"], "expectation": c["expectation"],
            "code_check": CODE_CHECKS[c["id"]][0] if c["id"] in CODE_CHECKS else None,
            "variants": {v: {
                "pass_rate": round(case_pass(v, c["id"]), 3),
                "samples": [{"response": resp[(v, c["id"], s)]["text"], "grade": grade[(v, c["id"], s)],
                             "latency_ms": resp[(v, c["id"], s)]["latency_ms"],
                             "code_check_pass": CODE_CHECKS[c["id"]][1](resp[(v, c["id"], s)]["text"]) if c["id"] in CODE_CHECKS else None}
                            for s in range(K)]} for v in vkeys},
            "pairwise": pair[c["id"]],
        })

    result = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if "runs" not in result:  # migrate old single-run format
        result = {"calibration": result.get("calibration"), "runs": {}}
    result["runs"][args.set] = {
        "meta": {"date": date.today().isoformat(), "bot_model": BOT_MODEL, "judge_model": JUDGE_MODEL,
                 "n_cases": len(cases), "samples_per_case": K, "bot_temperature": TEMPERATURE,
                 "baseline": b, "challenger": ch, "variants": vkeys},
        "summary": summary,
        "comparison": {
            "pass_delta": round(statistics.mean(deltas), 4), "pass_delta_ci95": bootstrap_ci(deltas),
            "flips": flips,
            "pairwise": {"challenger_wins": consistent[ch], "baseline_wins": consistent[b], "ties": consistent["tie"],
                         "position_inconsistent": inconsistent},
            "judge_vs_code_agreement": judge_code_agree,
        },
        "cases": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")

    for v in vkeys:
        s = summary[v]
        print(f"{v} {s['name']:14s} pass={s['pass_rate']:.1%} CI{s['pass_ci95']} flaky={s['flaky_cases']} "
              f"p50={s['latency_ms_p50']}ms tok={s['avg_out_tokens']} code={s['code_checks_pass_rate']} by_intent={s['by_intent']}")
    cm = result["runs"][args.set]["comparison"]
    print(f"{ch} vs {b}: delta={cm['pass_delta']:+.1%} CI{cm['pass_delta_ci95']} flips={flips} pairwise={cm['pairwise']} judge~code={judge_code_agree}")


if __name__ == "__main__":
    main()
