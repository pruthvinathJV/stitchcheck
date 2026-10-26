import numpy as np
import pandas as pd

from stitchcheck.eval.bootstrap import cluster_bootstrap, recall


def _pairs(n_pairs, photos_per_pair, hit_pairs):
    rows = [dict(pair_id=f"p{i}", label="broken_stitch", pred="broken_stitch" if i < hit_pairs else "good")
            for i in range(n_pairs) for _ in range(photos_per_pair)]
    return pd.DataFrame(rows)


def test_pair_bootstrap_is_wider_than_photo_bootstrap():
    df = _pairs(n_pairs=9, photos_per_pair=4, hit_pairs=6)
    m = lambda d: recall(d, {"broken_stitch"})
    by_pair = cluster_bootstrap(df, m, group="pair_id")
    by_photo = cluster_bootstrap(df.reset_index(), m, group="index")
    assert np.isclose(by_pair["value"], 6 / 9) and by_pair["n_groups"] == 9
    assert (by_pair["hi"] - by_pair["lo"]) > (by_photo["hi"] - by_photo["lo"])


def test_recall_with_no_positives_is_nan_and_skipped():
    df = pd.DataFrame(dict(pair_id=["a", "b"], label=["good", "good"], pred=["good", "good"]))
    out = cluster_bootstrap(df, lambda d: recall(d, {"broken_stitch"}), n_boot=50)
    assert np.isnan(out["value"]) and out["n_boot_valid"] == 0
