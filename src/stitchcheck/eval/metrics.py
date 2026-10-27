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


def per_class_report(df, labels, y_true="label", y_pred="pred"):
    """Precision, recall, F1 and support per class, plus a macro-average row (over `labels`)."""
    import pandas as pd

    rows = []
    for c in labels:
        tp = int(((df[y_true] == c) & (df[y_pred] == c)).sum())
        n_pred, n_true = int((df[y_pred] == c).sum()), int((df[y_true] == c).sum())
        p = tp / n_pred if n_pred else 0.0
        r = tp / n_true if n_true else float("nan")
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        rows.append(dict(cls=c, precision=p, recall=r, f1=f1, support=n_true))
    out = pd.DataFrame(rows)
    macro = out[["precision", "recall", "f1"]].mean().to_dict() | dict(cls="macro", support=int(out["support"].sum()))
    return pd.concat([out, pd.DataFrame([macro])], ignore_index=True).round(4)
