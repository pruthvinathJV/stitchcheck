"""Host the StitchCheck API on Modal: modal deploy scripts/cloud/serve_modal.py

One container at most (SQLite has a single writer), scaling to zero when idle. The ONNX ensemble lives in the
"stitchcheck-models" volume (upload once: modal volume put stitchcheck-models models/demo_ensemble.onnx
demo_ensemble.onnx); the database and photos live in "stitchcheck-app". MODEL_API_KEY comes from the Modal secret
"stitchcheck-meta" (MODEL_API_KEY, plus MODAL_PROXY_TOKEN_ID and MODAL_PROXY_TOKEN_SECRET for the Muse Spark Open
fallback). The URL is public; spend is bounded by the per-IP limit and the daily caps per brain (backend/brains.py).
"""
from pathlib import Path

import modal

from stitchcheck.data.stitchingnet import SEG_DIR

LOCKED_REF = SEG_DIR / "F. Satin-Core/0. Normal/image/F_00_131.jpg"  # results/test/lock.json (CC BY 4.0)

image = (modal.Image.debian_slim(python_version="3.12")
         .apt_install("ffmpeg")
         .pip_install("fastapi==0.162.0", "python-multipart==0.0.32", "onnxruntime==1.31.1", "numpy==2.5.4",
                      "pillow==13.0.0", "pandas==3.1.0", "openai==3.39.0", "requests==2.34.2", "meta-sam-parser==0.1.0",
                      "python-dotenv==1.2.4")
         .env({"STITCHCHECK_DB": "/vol/stitchcheck.db", "STITCHCHECK_STORAGE": "/vol/photos",
               "STITCHCHECK_ONNX": "/models/demo_ensemble.onnx", "STITCHCHECK_REFS": "/vol/ref_tiles",
               "STITCHCHECK_FALLBACK_REF": "/app/assets/reference_good.jpg"})
         .add_local_file("results/test/lock.json", "/app/results/test/lock.json")
         .add_local_file(str(LOCKED_REF), "/app/assets/reference_good.jpg")
         .add_local_python_source("stitchcheck"))

app = modal.App("stitchcheck-web")
models = modal.Volume.from_name("stitchcheck-models", create_if_missing=True)
appdata = modal.Volume.from_name("stitchcheck-app", create_if_missing=True)


@app.function(image=image, secrets=[modal.Secret.from_name("stitchcheck-meta")], volumes={"/models": models, "/vol": appdata},
              cpu=2, memory=2048, min_containers=0, max_containers=1, scaledown_window=300, timeout=600)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def web():
    import os

    os.chdir("/app")  # relative paths (lock file, Spark cache) resolve here
    from stitchcheck.backend.app import default_app
    api = default_app()

    import threading
    import time

    def commit_loop():  # persist the database and photos beyond this container, including background reviews
        while True:
            time.sleep(10)
            try:
                appdata.commit()
            except Exception as ex:
                print("volume commit failed:", ex, flush=True)

    threading.Thread(target=commit_loop, daemon=True).start()

    @api.get("/health/memory")
    def memory():
        import resource
        return {"max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}

    return api
