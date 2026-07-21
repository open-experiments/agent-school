"""Persist point-in-time training datasets into the Feast registry.

Creates one SavedDataset per NF from the same `get_historical_features`
retrieval the anomaly training uses — the RHOAI dashboard then lists them
under **Develop & train → Feature store → Datasets** (store `fivegprod`):
`amf_anomaly_training`, `smf_anomaly_training`, `upf_anomaly_training`.

Why this exists: training that consumes a retrieval dataframe directly is
correct but leaves no named artifact. `create_saved_dataset()` freezes the
exact point-in-time-correct rows under a name + tags, so a model version
can point back to the dataset it was trained on. Verified on Rome.

Run it the same way as the other feast Jobs on Rome — a Job that mounts
the `feast-fivegprod-registry` and `feast-fivegprod-online` PVCs and
targets the store files directly (EA2: remote apply/write does not
persist; see feature_repo/README.md). Two Rome specifics handled below:

- Registry FileSources use relative paths (`data/<nf>_features.parquet`),
  and the offline parquet is regenerated per Job rather than persisted —
  so this script rebuilds the engineered frame exactly as ingest.py does,
  deriving the KPI list from the registered feature schema itself.
- The saved-dataset parquet is written to the registry PVC
  (`/feast-registry/saved/`) so the artifact outlives the Job.

Laptop use (offline classroom): run from feature_repo/ after ingest.py,
with REG_GLOB/ONL_GLOB unset — it falls back to the local repo config.
"""
import glob
import os

import pandas as pd

HF = "https://huggingface.co/datasets/fenar/5gcore-prod/resolve/main"
WINDOW = 60  # samples ~= 1 hour at 1-minute resolution
NFS = ("amf", "smf", "upf")


def cluster_repo() -> str:
    """Build a direct-access repo config from the mounted store PVCs."""
    reg = [r for r in glob.glob("/feast-registry/**/*.db", recursive=True)
           if "registry" in r][0]
    onl = glob.glob("/feast-online/**/*.db", recursive=True)[0]
    os.makedirs("/tmp/repo/data", exist_ok=True)
    with open("/tmp/repo/feature_store.yaml", "w") as f:
        f.write(f"""project: fivegprod
provider: local
registry:
  path: {reg}
  cache_ttl_seconds: 0
online_store:
  type: sqlite
  path: {onl}
offline_store:
  type: file
entity_key_serialization_version: 3
""")
    return "/tmp/repo"


def main() -> None:
    repo = cluster_repo() if os.path.isdir("/feast-registry") else "."
    os.chdir(repo)

    from feast import FeatureStore
    from feast.infra.offline_stores.file_source import SavedDatasetFileStorage

    fs = FeatureStore(repo)
    saved_dir = ("/feast-registry/saved"
                 if os.path.isdir("/feast-registry") else "data")
    os.makedirs(saved_dir, exist_ok=True)

    for nf in NFS:
        view = fs.get_feature_view(f"{nf}_kpis")
        feats = [f.name for f in view.features]
        kpis = [n for n in feats
                if "_1h_" not in n and not n.startswith("anomaly")]

        # Rebuild the engineered offline frame (same as ingest.py) so the
        # registry's relative FileSource path resolves.
        raw = pd.read_csv(f"{HF}/{nf}_metrics.csv")
        raw["event_timestamp"] = pd.to_datetime(raw.pop("timestamp"))
        raw = raw.sort_values("event_timestamp").reset_index(drop=True)
        out = raw[["event_timestamp"] + kpis].copy()
        roll = out[kpis].rolling(WINDOW, min_periods=1)
        for agg in ("mean", "min", "max"):
            stats = getattr(roll, agg)()
            for k in kpis:
                out[f"{k}_1h_{agg}"] = stats[k]
        out["nf"] = nf
        out["anomaly_score"] = 0.0
        out["anomaly_flag"] = 0
        out.to_parquet(f"data/{nf}_features.parquet", index=False)

        entity_df = out[["event_timestamp"]].copy()
        entity_df["nf"] = nf
        job = fs.get_historical_features(
            entity_df=entity_df,
            features=[f"{view.name}:{n}" for n in feats])
        outp = os.path.join(saved_dir, f"{nf}_training_saved.parquet")
        fs.create_saved_dataset(
            from_=job, name=f"{nf}_anomaly_training",
            storage=SavedDatasetFileStorage(path=outp), allow_overwrite=True,
            tags={"course": "101-noc-assistant",
                  "consumer": "5gprod-anomaly-isolationforest",
                  "purpose": "point-in-time correct IsolationForest "
                             "training set"})
        print(f"[saved] {nf}: {len(out)} rows -> {outp}")

    print("datasets:", [d.name for d in fs.list_saved_datasets()])


if __name__ == "__main__":
    main()
