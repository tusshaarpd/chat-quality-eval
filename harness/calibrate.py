"""Judge calibration: does the LLM judge agree with a human?

    python harness/calibrate.py export    # writes harness/labels/to_label.csv (judge verdict hidden)
    # open the CSV in Excel, fill human_pass with 1 (pass) or 0 (fail), save as CSV
    python harness/calibrate.py score     # agreement + Cohen's kappa -> results.json["calibration"]
"""
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "web" / "data" / "results.json"
LBL = ROOT / "harness" / "labels" / "to_label.csv"
N = 30


def export():
    run = json.loads(RES.read_text(encoding="utf-8"))["runs"]["dev"]
    rnd = random.Random(42)
    items = [(c, v) for c in run["cases"] for v in c["variants"]]
    fails = [x for x in items if not x[0]["variants"][x[1]]["samples"][0]["grade"]["pass"]]
    passes = [x for x in items if x[0]["variants"][x[1]]["samples"][0]["grade"]["pass"]]
    # oversample judge-fails so disagreements on both sides are visible
    pick = rnd.sample(fails, min(len(fails), N // 2)) + rnd.sample(passes, N - min(len(fails), N // 2))
    rnd.shuffle(pick)
    LBL.parent.mkdir(parents=True, exist_ok=True)
    with LBL.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "variant", "intent", "conversation", "expectation", "response", "human_pass", "notes"])
        for c, v in pick:
            convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in c["messages"])
            w.writerow([c["id"], v, c["intent"], convo, c["expectation"], c["variants"][v]["samples"][0]["response"], "", ""])
    print(f"wrote {LBL} ({len(pick)} rows). Fill human_pass with 1/0 - judge verdicts are hidden on purpose.")


def score():
    res = json.loads(RES.read_text(encoding="utf-8"))
    run = res["runs"]["dev"]
    judge = {(c["id"], v): c["variants"][v]["samples"][0]["grade"] for c in run["cases"] for v in c["variants"]}
    rows = [r for r in csv.DictReader(LBL.open(encoding="utf-8-sig")) if r["human_pass"].strip() in ("0", "1")]
    if not rows:
        sys.exit("No labelled rows yet - fill the human_pass column with 1 or 0.")
    tp = fp = fn = tn = 0
    disagreements = []
    for r in rows:
        h = r["human_pass"].strip() == "1"
        j = judge[(r["case_id"], r["variant"])]["pass"]
        tp += h and j; tn += (not h) and (not j); fp += (not h) and j; fn += h and (not j)
        if h != j:
            disagreements.append({"case_id": r["case_id"], "variant": r["variant"], "human_pass": h, "judge_pass": j,
                                  "judge_rationale": judge[(r["case_id"], r["variant"])]["rationale"], "human_notes": r["notes"]})
    n = len(rows)
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (tn + fn) * (tn + fp)) / (n * n)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    res["calibration"] = {"n_labelled": n, "agreement": round(po, 3), "cohens_kappa": round(kappa, 3),
                          "confusion": {"both_pass": tp, "both_fail": tn, "judge_pass_human_fail": fp, "judge_fail_human_pass": fn},
                          "disagreements": disagreements}
    RES.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"n={n} agreement={po:.0%} kappa={kappa:.2f} (>0.6 = substantial). Disagreements: {len(disagreements)}")


if __name__ == "__main__":
    {"export": export, "score": score}[sys.argv[1] if len(sys.argv) > 1 else "export"]()
