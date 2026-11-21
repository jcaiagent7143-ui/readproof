"""Regenerate paper/results.tex: one LaTeX macro per number the paper reports.

Every macro defaults to a visible red TBD placeholder. Experiments fill numbers by writing a JSON object
{macro_name_without_backslash: "formatted value"} and running

    python3 gen_results.py values.json      # fills what the JSON names; the rest stay TBD
    python3 gen_results.py                  # all placeholders

Values are inserted verbatim (format them first, e.g. "41.2 [38.0, 44.5]"). Names are letters only, because
LaTeX macro names cannot contain digits: P0 -> PZero, P1 -> POne.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SYSTEMS_A = ["NaiveFetch", "ReaderService", "BrowserText", "ReadProof"]  # systems that read links
SYSTEMS_B = ["ClosedBook"] + SYSTEMS_A
PLATFORMS = ["Douyin", "Xiaohongshu", "Bilibili", "Youtube", "Tiktok", "Weibo", "Toutiao", "X", "Wechat"]
FORMS = ["Desktop", "Mobile", "Short", "ShareText", "TokenKept", "TokenStripped", "TokenAltered", "AltDomain", "AltPath"]
WALL_TYPES = ["Deleted", "SelfVisible", "SoftNotFound", "Challenge", "Login", "Token", "AppOnly"]
K_ROWS = ["Speech", "OnScreen", "Caption", "PinnedComment", "CarouselImage", "Fabricated", "Abstained"]
K_SYSTEMS = SYSTEMS_A + ["Commercial"]
B_COLS = ["Speech", "OnScreen", "Frame", "ImageText", "Caption", "Comment", "All", "QuoteValid", "Locator"]
ABL_ROWS = ["ReadProof", "NoWalls", "NoCanonical", "NoSpeech", "NoOnScreen", "NoVerify", "PZero", "Datacenter"]
ABL_COLS = ["ACov", "AFW", "KAcc", "BAcc", "QuoteValid", "CostPerCorrect"]
C_ROWS = ["Prose", "CodeCount", "Adjusted"]
C_COLS = ["MAE", "RelErr", "Coverage", "PostF"]
COST_ROWS = SYSTEMS_A + ["ExtractAll"]
COST_COLS = ["ExtractUSD", "TokenUSD", "TotalUSD", "Seconds"]
COMP_ROWS = ["NaiveFetch", "BrowserText", "ReadProofPZero", "ReadProof"]
COMP_COLS = ["Requests", "DeclaredUA", "Cookies", "RetryAfterRefusal", "RobotsDisallowed", "MaxRate"]
STAGES = ["Acquire", "Detect", "Extract", "Ground"]


def names() -> list[tuple[str, str]]:
    """(macro name, comment) for every number in the paper, grouped by the table that prints it."""
    out: list[tuple[str, str]] = []
    add = lambda n, c="": out.append((n, c))  # noqa: E731
    # Dataset and run parameters (Sections 4 and 6).
    for n, c in [
        ("NAPosts", "Track A public posts"), ("NAForms", "Track A link forms"), ("NAControls", "Track A control forms"),
        ("NKPosts", "Track K canary posts"), ("NKCodes", "Track K planted codes"),
        ("NBQuestions", "Track B questions"), ("NBPosts", "Track B posts"), ("NBZhShare", "Track B share of Chinese items"),
        ("NCSets", "Track C sets"), ("NCPosts", "Track C labelled posts"), ("NCKappa", "Track C Cohen's kappa"),
        ("NCalib", "calibration items for alpha/beta"), ("NAudit", "author-audited Track B items"),
        ("ParamLiveDays", "live-arm days"), ("ParamRuns", "runs per configuration"),
        ("ParamResidential", "residential origin label"), ("ParamDatacenter", "datacenter origin label"),
        ("ParamFrameTol", "on-screen/frame locator tolerance, seconds"), ("ParamCollection", "collection dates"),
        ("ParamBudgetUSD", "per-question USD cap"), ("ParamTokenPrice", "list price used for tokens"),
    ]:
        add(n, c)
    # Headline numbers (abstract, introduction, analysis).
    for n in ["HeadCovNaive", "HeadCovReadProof", "HeadAFWBaselines", "HeadKReadProof", "HeadKBestBaseline",
              "HeadPenalty", "HeadStrictExcluded", "HeadExtractShare", "HeadOrigin", "HeadChannelGap"]:
        add("Res" + n, "headline")
    # Table: Track A main.
    for s in SYSTEMS_A:
        for m in ["Cov", "AFW", "AbsP", "AbsR"]:
            add(f"ResA{m}{s}", "Track A main")
    add("ResAWallAcc", "Track A wall-type accuracy (ReadProof)")
    add("ResAWallMacroF", "Track A wall-type macro-F1 (ReadProof)")
    # Table: Track A by platform and by link form.
    for s in SYSTEMS_A:
        for p in PLATFORMS:
            add(f"ResAPlat{s}{p}", "Track A coverage by platform")
    for s in SYSTEMS_A:
        for f in FORMS:
            add(f"ResAForm{s}{f}", "Track A coverage by link form")
    # Table: honesty penalty and origin.
    for p in PLATFORMS + ["All"]:
        for m in ["PZero", "POne", "Pen", "Strict", "Origin"]:
            add(f"ResH{m}{p}", "honesty penalty / origin")
    # Table: wall typing on controls.
    for w in WALL_TYPES:
        add(f"ResWallRec{w}", "wall-type recall on controls")
    add("ResWallN", "number of control forms")
    # Table: Track K.
    for r in K_ROWS:
        for s in K_SYSTEMS:
            add(f"ResK{r}{s}", "Track K")
    # Table: Track B.
    for s in SYSTEMS_B:
        for c in B_COLS:
            add(f"ResB{c}{s}", "Track B")
    # Table: ablations.
    for r in ABL_ROWS:
        for c in ABL_COLS:
            add(f"ResAbl{c}{r}", "ablation")
    # Table: Track C.
    for r in C_ROWS:
        for c in C_COLS:
            add(f"ResC{c}{r}", "Track C")
    add("ResCAlpha", "estimated false-positive rate")
    add("ResCBeta", "estimated false-negative rate")
    # Table: cost per correct answer.
    for r in COST_ROWS:
        for c in COST_COLS:
            add(f"ResCost{c}{r}", "cost per correct answer")
    # Table: compliance log.
    for r in COMP_ROWS:
        for c in COMP_COLS:
            add(f"ResComp{c}{r}", "compliance log")
    # Table: failure stage attribution.
    for s in SYSTEMS_A:
        for st in STAGES:
            add(f"ResStage{st}{s}", "failure stage share")
    return out


def main() -> None:
    values: dict[str, str] = {}
    if len(sys.argv) > 1:
        values = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rows = names()
    known = {n for n, _ in rows}
    unknown = sorted(set(values) - known)
    if unknown:
        raise SystemExit(f"unknown macro names in values file: {unknown}")
    lines = [
        "% results.tex -- GENERATED by gen_results.py; do not edit by hand.",
        "% Every number the paper reports is a macro here. Unfilled values print a red TBD.",
        "\\providecommand{\\TBD}{\\textcolor{red}{TBD}}",
        "",
    ]
    group = None
    for n, c in rows:
        if c != group:
            lines.append(f"% -- {c}")
            group = c
        v = values.get(n, "\\TBD")
        lines.append(f"\\newcommand{{\\{n}}}{{{v}}}")
    filled = sum(1 for n, _ in rows if n in values)
    lines.append(f"% {len(rows)} macros, {filled} filled.")
    Path(__file__).with_name("results.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote results.tex: {len(rows)} macros, {filled} filled")


if __name__ == "__main__":
    main()
