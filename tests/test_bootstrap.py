import numpy as np
import pandas as pd

from stitchcheck.eval.bootstrap import cluster_bootstrap
from stitchcheck.eval.metrics import checker_workload, major_recall


def _pairs(n_pairs, photos_per_pair, hit_pairs):
    rows = [dict(pair_id=f"p{i}", label="broken_stitch", pred="broken_stitch" if i < hit_pairs else "good")
            for i in range(n_pairs) for _ in range(photos_per_pair)]
    return pd.DataFrame(rows)


def test_pair_bootstrap_is_wider_than_photo_bootstrap():
    df = _pairs(n_pairs=9, photos_per_pair=4, hit_pairs=6)
    by_pair = cluster_bootstrap(df, major_recall, group="pair_id")
    by_photo = cluster_bootstrap(df.reset_index(), major_recall, group="index")
    assert np.isclose(by_pair["value"], 6 / 9) and by_pair["n_groups"] == 9
    assert (by_pair["hi"] - by_pair["lo"]) > (by_photo["hi"] - by_photo["lo"])


def test_metric_with_no_positives_is_nan_and_skipped():
    df = pd.DataFrame(dict(pair_id=["a", "b"], label=["good", "good"], pred=["good", "good"]))
    out = cluster_bootstrap(df, major_recall, n_boot=50)
    assert np.isnan(out["value"]) and out["n_boot_valid"] == 0


def test_headline_counts_any_flag_auto_recall_excludes_deferral():
    df = pd.DataFrame(dict(
        label=["broken_stitch", "skipped_stitch", "stain_damage", "open_seam", "broken_stitch", "crooked_seam"],
        pred=["crooked_seam", "unknown_defect", "ask_checker", "good", "broken_stitch", "good"]))
    # majors: wrong defect class, unknown_defect and ask_checker are caught; "good" is missed; crooked is not major
    assert major_recall(df) == 4 / 5
    assert major_recall(df, include_deferral=False) == 3 / 5


def test_deferring_everything_shows_up_in_auto_recall_and_workload():
    df = pd.DataFrame(dict(label=["broken_stitch", "skipped_stitch", "good", "good"], pred=["ask_checker"] * 4))
    assert major_recall(df) == 1.0
    assert major_recall(df, include_deferral=False) == 0.0
    assert checker_workload(df) == 1.0


def test_per_class_report():
    from stitchcheck.eval.metrics import per_class_report
    df = pd.DataFrame(dict(label=["a", "a", "b", "b"], pred=["a", "b", "b", "b"]))
    r = per_class_report(df, ["a", "b"]).set_index("cls")
    assert r.loc["a", "precision"] == 1.0 and r.loc["a", "recall"] == 0.5
    assert r.loc["b", "precision"] == round(2 / 3, 4) and r.loc["b", "recall"] == 1.0
    assert r.loc["macro", "support"] == 4
