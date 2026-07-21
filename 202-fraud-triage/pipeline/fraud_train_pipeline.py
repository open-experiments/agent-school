"""202 fraud-model training pipeline — RHOAI Data Science Pipelines (KFP v2).

The agentification of the manual Telco-AIX revenueassurance notebook flow:
what the notebooks did cell by cell becomes a platform-run, repeatable,
observable pipeline. v1 scope (per the course README): HF dataset ->
preprocess -> train Balanced Random Forest -> evaluate -> register in
MLflow. Feast billing features and KServe serving land in later steps.

Stages
------
1. load_data     — pull hf.co/datasets/fenar/revenue_assurance
                   (1,000,000 billing records, 1.7% fraud) as-is.
2. preprocess    — encode Plan_Type, stratified train/test split.
3. train         — imbalanced-learn BalancedRandomForestClassifier
                   (the model family the source experiment used).
4. evaluate      — fraud-class precision/recall/F1 + ROC-AUC on the held-out
                   split; emits KFP Metrics so the run page shows them.
5. register      — logs params/metrics/model to the RHOAI managed MLflow
                   (workspace agent-school) and registers a version of
                   `revassurance-fraud-brf`. Runs with the pod's
                   ServiceAccount token; needs the pipeline-runner-mlflow
                   RoleBinding (deploy/ocp/rome).

Compile:  python fraud_train_pipeline.py   -> fraud_train_pipeline.yaml
Import the YAML in the RHOAI dashboard (Develop & train -> Pipelines) or
POST it to the DSPA API. Runs appear per-stage in the dashboard with logs,
and the register stage links the run to Experiments/Models/Registry.
"""

from kfp import dsl
from kfp import compiler

BASE_IMAGE = "registry.access.redhat.com/ubi9/python-311:latest"
DATASET_URL = ("https://huggingface.co/datasets/fenar/revenue_assurance/"
               "resolve/main/telecom_revass_data.csv.xz")


@dsl.component(base_image=BASE_IMAGE, packages_to_install=["pandas==2.2.3"])
def load_data(dataset_url: str, raw_data: dsl.Output[dsl.Dataset]):
    """Pull the published billing dataset (CSV.xz) and store it as an artifact."""
    import pandas as pd

    df = pd.read_csv(dataset_url)
    df.to_parquet(raw_data.path)
    raw_data.metadata["rows"] = len(df)
    raw_data.metadata["source"] = dataset_url
    print(f"[load] {len(df)} rows, fraud rate {df['Fraud'].mean():.4f}")


@dsl.component(base_image=BASE_IMAGE,
               packages_to_install=["pandas==2.2.3", "scikit-learn==1.5.2"])
def preprocess(raw_data: dsl.Input[dsl.Dataset],
               train_data: dsl.Output[dsl.Dataset],
               test_data: dsl.Output[dsl.Dataset],
               test_fraction: float = 0.3):
    """Encode categoricals and split stratified on the fraud label."""
    import pandas as pd
    from sklearn.model_selection import train_test_split

    df = pd.read_parquet(raw_data.path)
    df["Plan_Type"] = (df["Plan_Type"] == "prepaid").astype(int)
    train, test = train_test_split(
        df, test_size=test_fraction, stratify=df["Fraud"], random_state=42)
    train.to_parquet(train_data.path)
    test.to_parquet(test_data.path)
    print(f"[preprocess] train={len(train)} test={len(test)} "
          f"(fraud {train['Fraud'].mean():.4f}/{test['Fraud'].mean():.4f})")


@dsl.component(base_image=BASE_IMAGE,
               packages_to_install=["pandas==2.2.3", "scikit-learn==1.5.2",
                                    "imbalanced-learn==0.12.4", "joblib"])
def train(train_data: dsl.Input[dsl.Dataset],
          model: dsl.Output[dsl.Model],
          n_estimators: int = 100):
    """Balanced Random Forest — the revenueassurance model, pipeline-run."""
    import joblib
    import pandas as pd
    from imblearn.ensemble import BalancedRandomForestClassifier

    df = pd.read_parquet(train_data.path)
    X, y = df.drop(columns=["Fraud"]), df["Fraud"]
    clf = BalancedRandomForestClassifier(
        n_estimators=n_estimators, random_state=42, n_jobs=-1,
        sampling_strategy="all", replacement=True, bootstrap=False)
    clf.fit(X, y)
    joblib.dump({"model": clf, "features": list(X.columns)}, model.path)
    model.metadata["algorithm"] = "BalancedRandomForestClassifier"
    model.metadata["n_estimators"] = n_estimators
    print(f"[train] fitted on {len(df)} rows, {len(X.columns)} features")


@dsl.component(base_image=BASE_IMAGE,
               packages_to_install=["pandas==2.2.3", "scikit-learn==1.5.2",
                                    "imbalanced-learn==0.12.4", "joblib"])
def evaluate(model: dsl.Input[dsl.Model],
             test_data: dsl.Input[dsl.Dataset],
             metrics: dsl.Output[dsl.Metrics]) -> dict:
    """Fraud-class metrics on the held-out split."""
    import joblib
    import pandas as pd
    from sklearn.metrics import (f1_score, precision_score, recall_score,
                                 roc_auc_score)

    bundle = joblib.load(model.path)
    df = pd.read_parquet(test_data.path)
    X, y = df[bundle["features"]], df["Fraud"]
    pred = bundle["model"].predict(X)
    proba = bundle["model"].predict_proba(X)[:, 1]
    result = {
        "fraud_precision": float(precision_score(y, pred)),
        "fraud_recall": float(recall_score(y, pred)),
        "fraud_f1": float(f1_score(y, pred)),
        "roc_auc": float(roc_auc_score(y, proba)),
        "test_rows": int(len(y)),
    }
    for k, v in result.items():
        metrics.log_metric(k, v)
    print("[evaluate] " + " ".join(f"{k}={v:.4f}" for k, v in result.items()
                                   if isinstance(v, float)))
    return result


@dsl.component(base_image=BASE_IMAGE,
               packages_to_install=["pandas==2.2.3", "scikit-learn==1.5.2",
                                    "imbalanced-learn==0.12.4", "joblib",
                                    "mlflow==3.4.0"])
def register(model: dsl.Input[dsl.Model],
             eval_metrics: dict,
             dataset_url: str,
             n_estimators: int,
             mlflow_tracking_uri: str,
             mlflow_workspace: str,
             registered_name: str = "revassurance-fraud-brf"):
    """Log the run + model to RHOAI managed MLflow and register a version.

    Same workspace-header/ServiceAccount-token handshake the 101/201
    workloads use (see 101 agent/_enable_mlflow) — the pipeline pod's SA
    must pass the server's SubjectAccessReview (pipeline-runner-mlflow
    RoleBinding).
    """
    import os
    from pathlib import Path

    os.environ.setdefault("MLFLOW_TRACKING_INSECURE_TLS", "true")
    sa = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    if sa.exists() and not os.environ.get("MLFLOW_TRACKING_TOKEN"):
        os.environ["MLFLOW_TRACKING_TOKEN"] = sa.read_text().strip()

    from mlflow.utils import rest_utils
    _orig = rest_utils.http_request

    def _shim(host_creds, endpoint, method, *a, **kw):
        headers = dict(kw.pop("extra_headers", None) or {})
        headers["X-MLFLOW-WORKSPACE"] = mlflow_workspace
        return _orig(host_creds, endpoint, method, *a,
                     extra_headers=headers, **kw)

    rest_utils.http_request = _shim
    from mlflow.store.tracking import rest_store
    rest_store.http_request = _shim

    import joblib
    import mlflow
    import pandas as pd

    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment("revassurance-fraud")

    bundle = joblib.load(model.path)

    class FraudModel(mlflow.pyfunc.PythonModel):
        def load_context(self, context):
            import joblib as _joblib
            b = _joblib.load(context.artifacts["bundle"])
            self.model, self.features = b["model"], b["features"]

        def predict(self, context, model_input, params=None):
            proba = self.model.predict_proba(model_input[self.features])[:, 1]
            return pd.DataFrame({"fraud_probability": proba,
                                 "fraud_flag": (proba >= 0.5).astype(int)})

    with mlflow.start_run(run_name="brf-revassurance-dsp") as run:
        mlflow.log_params({
            "algorithm": "BalancedRandomForestClassifier",
            "n_estimators": n_estimators,
            "dataset": dataset_url,
            "pipeline": "data-science-pipelines (kfp v2, dspa)",
        })
        mlflow.log_metrics({k: v for k, v in eval_metrics.items()
                            if isinstance(v, (int, float))})
        mlflow.pyfunc.log_model(
            name="fraud-detector",
            python_model=FraudModel(),
            artifacts={"bundle": model.path},
            registered_model_name=registered_name,
        )
        print(f"[register] run={run.info.run_id} -> {registered_name}")


@dsl.pipeline(name="fraud-brf-training",
              description="202 fraud-triage: revenue_assurance -> preprocess "
                          "-> Balanced Random Forest -> evaluate -> register "
                          "(MLflow, workspace agent-school)")
def fraud_train_pipeline(
        dataset_url: str = DATASET_URL,
        n_estimators: int = 100,
        test_fraction: float = 0.3,
        mlflow_tracking_uri: str = "https://mlflow.redhat-ods-applications.svc:8443/mlflow",
        mlflow_workspace: str = "agent-school"):
    loaded = load_data(dataset_url=dataset_url)
    split = preprocess(raw_data=loaded.outputs["raw_data"],
                       test_fraction=test_fraction)
    trained = train(train_data=split.outputs["train_data"],
                    n_estimators=n_estimators)
    scored = evaluate(model=trained.outputs["model"],
                      test_data=split.outputs["test_data"])
    register(model=trained.outputs["model"],
             eval_metrics=scored.outputs["Output"],
             dataset_url=dataset_url,
             n_estimators=n_estimators,
             mlflow_tracking_uri=mlflow_tracking_uri,
             mlflow_workspace=mlflow_workspace)


if __name__ == "__main__":
    compiler.Compiler().compile(fraud_train_pipeline,
                                "fraud_train_pipeline.yaml")
    print("compiled -> fraud_train_pipeline.yaml")
