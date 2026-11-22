"""Score the labeled bank with Pangram and say whether its short-text results can be claimed.

  PANGRAM_API_KEY=... python -m card.pangram_eval [results.json]
"""
import json
import os
import sys

from . import bank, gate


def main(out=None):
    if not os.environ.get("PANGRAM_API_KEY"):
        sys.exit("PANGRAM_API_KEY is not set")
    rows = [{**x, "pangram": gate.ai_check(x["text"])} for x in bank.load()]
    scored = [r for r in rows if r["pangram"].get("checked")]
    rate = lambda label: (lambda s: sum(r["pangram"].get("prediction") == "AI" for r in s) / max(1, len(s)))(
        [r for r in scored if r["label"] == label])
    result = {"scored": len(scored), "total": len(rows), "human_flagged_as_ai": rate("human_public"),
              "ai_caught": rate("ai_generated"), "rewrites_caught": rate("ai_rewritten")}
    result["ready_to_claim"] = len(scored) == len(rows) and result["ai_caught"] >= 0.8 and result["human_flagged_as_ai"] <= 0.1
    print(json.dumps(result, indent=2))
    if out:
        with open(out, "w") as f:
            json.dump({"summary": result, "rows": rows}, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
