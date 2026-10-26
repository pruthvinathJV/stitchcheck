"""Headline metrics, defined in docs/design.md before any test-set run.

Predictions use the class labels plus two system outputs: "unknown_defect" and "ask_checker".
A photo is flagged when the prediction is anything other than "good".
"""
GOOD = "good"
ASK_CHECKER = "ask_checker"
UNKNOWN_DEFECT = "unknown_defect"
# Fail an audit wherever they appear. open_seam exists only in the phone set.
MAJOR_DEFECTS = frozenset({"skipped_stitch", "broken_stitch", "stain_damage", "open_seam"})


def _flagged(pred, include_deferral):
    flagged = pred != GOOD
    return flagged if include_deferral else flagged & (pred != ASK_CHECKER)


def major_recall(df, include_deferral=True, majors=MAJOR_DEFECTS, y_true="label", y_pred="pred"):
    """Share of major-defect photos the system did not pass as good.

    include_deferral=True is the headline (any defect class, unknown_defect or ask_checker counts as caught);
    False gives auto-caught recall, where ask_checker counts as a miss.
    """
    pos = df[df[y_true].isin(majors)]
    return float(_flagged(pos[y_pred], include_deferral).mean()) if len(pos) else float("nan")


def checker_workload(df, y_true="label", y_pred="pred"):
    """Share of good seams that are flagged or deferred: the false-alarm cost in checker taps."""
    good = df[df[y_true] == GOOD]
    return float((good[y_pred] != GOOD).mean()) if len(good) else float("nan")
