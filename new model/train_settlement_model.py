"""
Machine Learning Training & Calibration Pipeline for Expected Settlement Calculator.
Data Source: C:\\Users\\avgan\\OneDrive\\Desktop\\Test run data

Trains:
1. Multi-Class Settlement Health Classifier (ON_TIME_SETTLED vs LEGITIMATELY_DELAYED vs FAILED vs SLA_BREACHED)
2. Settlement Duration Regressor (Predicts expected hours to bank funds availability)
3. Parameter Calibration (Empirical cutoffs, rail latencies, holiday rollover buffers)

Follows ML Best Practices:
- Chronological train/test split to reflect real-world financial deployment.
- Domain-informed temporal, rail, and calendar feature engineering.
- Comprehensive evaluation: Accuracy, Precision, Recall, F1, Confusion Matrix, MAE, RMSE, R2.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import zoneinfo

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, mean_absolute_error, root_mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from settlement_calculator.banking_calendar import BankingCalendar
from settlement_calculator.models import RailType, SettlementHealth, DelayReason


def extract_features(t_df: pd.DataFrame, b_df: pd.DataFrame, calendar: BankingCalendar) -> pd.DataFrame:
    """Joins gateway transactions and settlement batches, performing domain feature engineering."""
    print("Joining transactions with settlement batches...")
    df = t_df.merge(b_df, on="settlement_id", how="left")

    # 1. Parse timestamps
    print("Parsing timestamps and converting to local IST timezone...")
    df["created_utc"] = pd.to_datetime(df["created_at"], utc=True)
    df["captured_utc"] = pd.to_datetime(df["captured_at"], utc=True)
    df["cutoff_utc"] = pd.to_datetime(df["cutoff_timestamp"], utc=True)
    df["credit_utc"] = pd.to_datetime(df["bank_credit_timestamp"], utc=True)

    # Convert event time to Indian Standard Time (IST)
    ist = zoneinfo.ZoneInfo("Asia/Kolkata")
    created_ist = df["created_utc"].dt.tz_convert(ist)
    
    df["hour_ist"] = created_ist.dt.hour
    df["day_of_week"] = created_ist.dt.dayofweek  # 0=Monday, 6=Sunday
    df["day_of_month"] = created_ist.dt.day

    # 2. Indian Banking Calendar Features
    print("Computing 2nd/4th Saturday and Indian banking calendar features...")
    # Saturdays: weekday == 5, check if 2nd or 4th Saturday
    is_sat = df["day_of_week"] == 5
    nth_sat = (df["day_of_month"] - 1) // 7 + 1
    df["is_second_or_fourth_saturday"] = is_sat & (nth_sat.isin([2, 4]))
    df["is_sunday"] = df["day_of_week"] == 6
    df["is_weekend_closure"] = df["is_sunday"] | df["is_second_or_fourth_saturday"]

    # Public / RBI holidays in dataset range (March - August 2026)
    rbi_holidays = {
        (2026, 4, 1): "RBI Accounts Closing",
        (2026, 4, 3): "Good Friday",
        (2026, 4, 14): "Dr. Ambedkar Jayanti",
        (2026, 5, 1): "May Day",
        (2026, 8, 15): "Independence Day",
    }
    date_tuples = list(zip(created_ist.dt.year, created_ist.dt.month, created_ist.dt.day))
    df["is_rbi_holiday"] = [t in rbi_holidays for t in date_tuples]

    # Daily Cutoff distance (standard Razorpay nodal cutoff is 16:00 IST / 21:00 IST)
    df["is_post_16_cutoff"] = df["hour_ist"] >= 16
    df["is_post_21_cutoff"] = df["hour_ist"] >= 21
    df["hours_to_16_cutoff"] = 16 - df["hour_ist"]

    # 3. Financial & Rail Attributes
    df["amount_inr_abs"] = df["amount_inr"].abs().fillna(0.0)
    df["log_amount"] = np.log1p(df["amount_inr_abs"])
    df["method"] = df["method"].fillna("unknown")
    df["method_subtype"] = df["method_subtype"].fillna("standard")
    df["clearing_rail"] = df["clearing_rail"].fillna("UNKNOWN_OR_UNBATCHED")
    df["settlement_cycle"] = df["settlement_cycle"].fillna("T+2")
    df["is_holiday_rollover"] = df["is_holiday_rollover"].fillna(False).astype(bool)

    # 4. Target Definition for Classification
    def define_target(row):
        if (
            row["settlement_status"] == "unbatchable_failed"
            or row["payout_status"] == "failed"
            or row["reconciliation_status"] == "nodal_transfer_failed"
        ):
            return "FAILED"
        if row["settlement_status"] == "pending_batch_post_cutoff":
            return "LEGITIMATELY_DELAYED"
        if row["is_holiday_rollover"] is True:
            return "LEGITIMATELY_DELAYED"
        if row["payout_status"] == "created" or row["reconciliation_status"] == "pending_bank_ack":
            return "SLA_BREACHED"
        if row["payout_status"] == "processed":
            return "ON_TIME_SETTLED"
        return "OTHER_RECOVERY"

    df["target_class"] = df.apply(define_target, axis=1)

    # 5. Target Definition for Delay Regression (in hours)
    # Only defined for processed batches where cutoff and credit exist
    has_valid_delay = (df["payout_status"] == "processed") & df["credit_utc"].notna() & df["cutoff_utc"].notna()
    df["settlement_delay_hours"] = np.nan
    df.loc[has_valid_delay, "settlement_delay_hours"] = (
        (df.loc[has_valid_delay, "credit_utc"] - df.loc[has_valid_delay, "cutoff_utc"]).dt.total_seconds() / 3600.0
    )

    return df


def train_and_evaluate(data_dir: str):
    print("=" * 90)
    print("     STARTING SETTLEMENT CALCULATOR MODEL TRAINING & CALIBRATION")
    print(f"     Data Source: {data_dir}")
    print("=" * 90)

    calendar = BankingCalendar(timezone_name="Asia/Kolkata", jurisdiction="IN")

    # Ingest CSVs
    t_path = os.path.join(data_dir, "razorpay_gateway_transactions.csv")
    b_path = os.path.join(data_dir, "razorpay_settlement_batches.csv")
    
    print(f"Reading {t_path}...")
    t_df = pd.read_csv(t_path)
    print(f"Reading {b_path}...")
    b_df = pd.read_csv(b_path)

    # Feature extraction
    df = extract_features(t_df, b_df, calendar)

    # Chronological sort for strict temporal train/test split
    df = df.sort_values("created_utc").reset_index(drop=True)
    n = len(df)
    split_idx = int(n * 0.8)

    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()

    print(f"\nChronological Split:")
    print(f"  Training Set : {len(train_df):,} samples ({train_df['created_utc'].min()} to {train_df['created_utc'].max()})")
    print(f"  Test Set     : {len(test_df):,} samples ({test_df['created_utc'].min()} to {test_df['created_utc'].max()})")

    # -------------------------------------------------------------
    # Step 1: Engine Parameter Calibration
    # -------------------------------------------------------------
    print("\n[Step 1] Calibrating Empirical Engine Parameters...")
    processed_train = train_df[train_df["payout_status"] == "processed"]
    
    calibration_metrics = {}
    for rail in ["NEFT", "RTGS", "IMPS", "UPI_PAYOUT", "NACH_E_MANDATE"]:
        rail_sub = processed_train[processed_train["clearing_rail"] == rail]
        if len(rail_sub) == 0:
            continue
        
        cycle_stats = {}
        for cycle in ["T+1", "T+2", "T+3"]:
            c_sub = rail_sub[rail_sub["settlement_cycle"] == cycle]
            if len(c_sub) == 0:
                continue
            
            normal_delays = c_sub[~c_sub["is_holiday_rollover"]]["settlement_delay_hours"].dropna()
            rollover_delays = c_sub[c_sub["is_holiday_rollover"]]["settlement_delay_hours"].dropna()
            
            cycle_stats[cycle] = {
                "sample_count": len(c_sub),
                "normal_median_hours": float(normal_delays.median()) if len(normal_delays) > 0 else 24.0,
                "normal_p95_hours": float(normal_delays.quantile(0.95)) if len(normal_delays) > 0 else 40.0,
                "holiday_rollover_median_hours": float(rollover_delays.median()) if len(rollover_delays) > 0 else 72.0,
                "holiday_rollover_p95_hours": float(rollover_delays.quantile(0.95)) if len(rollover_delays) > 0 else 120.0,
            }
        calibration_metrics[rail] = cycle_stats

    print("Calibrated Settlement Duration Matrix (Normal vs Holiday Rollover):")
    for rail, c_data in calibration_metrics.items():
        print(f"  Rail: {rail}")
        for c, stats in c_data.items():
            print(f"    [{c}] Normal Median: {stats['normal_median_hours']:.1f}h | Holiday Rollover Median: {stats['holiday_rollover_median_hours']:.1f}h")

    # -------------------------------------------------------------
    # Step 2: Multi-Class Settlement Health Classifier
    # -------------------------------------------------------------
    print("\n[Step 2] Training Settlement Health Classifier...")
    categorical_features = ["method", "clearing_rail", "settlement_cycle"]
    numerical_features = [
        "log_amount",
        "hour_ist",
        "day_of_week",
        "is_second_or_fourth_saturday",
        "is_sunday",
        "is_weekend_closure",
        "is_rbi_holiday",
        "is_post_16_cutoff",
        "is_post_21_cutoff",
        "hours_to_16_cutoff",
        "is_holiday_rollover",
    ]

    all_features = categorical_features + numerical_features
    target_col = "target_class"

    X_train = train_df[all_features].copy()
    y_train = train_df[target_col].copy()
    X_test = test_df[all_features].copy()
    y_test = test_df[target_col].copy()

    clf_preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical_features),
            ("num", StandardScaler(), numerical_features),
        ]
    )

    clf = HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.08,
        max_leaf_nodes=31,
        random_state=42,
    )

    clf_pipeline = Pipeline([
        ("preprocessor", clf_preprocessor),
        ("classifier", clf),
    ])

    print("Fitting HistGradientBoostingClassifier...")
    clf_pipeline.fit(X_train, y_train)

    y_pred = clf_pipeline.predict(X_test)
    y_prob = clf_pipeline.predict_proba(X_test)
    labels = sorted(list(y_train.unique()))

    cls_report = classification_report(y_test, y_pred, target_names=labels, output_dict=True)
    print("\nClassification Report (Test Set):")
    print(classification_report(y_test, y_pred, target_names=labels))

    conf_mat = confusion_matrix(y_test, y_pred, labels=labels)
    print("Confusion Matrix:")
    print(conf_mat)

    # -------------------------------------------------------------
    # Step 3: Settlement Delay Regressor (Predicting Settlement Latency)
    # -------------------------------------------------------------
    print("\n[Step 3] Training Settlement Duration Regressor...")
    train_reg = train_df[train_df["settlement_delay_hours"].notna()].copy()
    test_reg = test_df[test_df["settlement_delay_hours"].notna()].copy()

    X_train_reg = train_reg[all_features]
    y_train_reg = train_reg["settlement_delay_hours"]
    X_test_reg = test_reg[all_features]
    y_test_reg = test_reg["settlement_delay_hours"]

    reg_preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical_features),
            ("num", StandardScaler(), numerical_features),
        ]
    )

    regressor = HistGradientBoostingRegressor(
        max_iter=150,
        learning_rate=0.08,
        max_leaf_nodes=31,
        random_state=42,
    )

    reg_pipeline = Pipeline([
        ("preprocessor", reg_preprocessor),
        ("regressor", regressor),
    ])

    reg_pipeline.fit(X_train_reg, y_train_reg)

    y_pred_reg = reg_pipeline.predict(X_test_reg)
    mae = mean_absolute_error(y_test_reg, y_pred_reg)
    rmse = root_mean_squared_error(y_test_reg, y_pred_reg)
    r2 = r2_score(y_test_reg, y_pred_reg)

    print(f"Regression Performance (Test Set):")
    print(f"  MAE  : {mae:.2f} hours")
    print(f"  RMSE : {rmse:.2f} hours")
    print(f"  R^2  : {r2:.4f}")

    # -------------------------------------------------------------
    # Step 4: Save Model Artifacts & Integrator
    # -------------------------------------------------------------
    artifacts_dir = Path(__file__).resolve().parent
    clf_path = os.path.join(artifacts_dir, "trained_settlement_classifier.joblib")
    reg_path = os.path.join(artifacts_dir, "trained_settlement_regressor.joblib")
    config_path = os.path.join(artifacts_dir, "calibrated_settlement_parameters.json")

    print(f"\n[Step 4] Saving model artifacts to {artifacts_dir}...")
    joblib.dump(clf_pipeline, clf_path)
    joblib.dump(reg_pipeline, reg_path)

    calib_export = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset": "Razorpay Indian Payment Gateway & Settlement Batches",
        "training_samples": len(train_df),
        "test_samples": len(test_df),
        "calibration_matrix": calibration_metrics,
        "classification_metrics": {
            "accuracy": cls_report["accuracy"],
            "macro_f1": cls_report["macro avg"]["f1-score"],
            "weighted_f1": cls_report["weighted avg"]["f1-score"],
            "class_reports": {k: cls_report[k] for k in labels},
        },
        "regression_metrics": {
            "mae_hours": mae,
            "rmse_hours": rmse,
            "r2_score": r2,
        },
        "features": all_features,
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(calib_export, f, indent=2)

    print(f"  Saved Classifier : {clf_path}")
    print(f"  Saved Regressor  : {reg_path}")
    print(f"  Saved Config     : {config_path}")

    # -------------------------------------------------------------
    # Step 5: Export Test Set Predictions CSV
    # -------------------------------------------------------------
    print("\n[Step 5] Exporting Test Set Predictions to CSV...")
    test_eval_df = test_df[[
        "payment_id",
        "order_id",
        "amount_inr",
        "method",
        "clearing_rail",
        "settlement_cycle",
        "created_at",
        "captured_at",
        "cutoff_timestamp",
        "bank_credit_timestamp",
        "target_class",
    ]].copy()

    test_eval_df["predicted_health_class"] = y_pred
    # Add predicted probabilities
    for i, label in enumerate(labels):
        test_eval_df[f"prob_{label}"] = y_prob[:, i]

    # Add predicted delay hours for test set
    test_eval_df["predicted_delay_hours"] = reg_pipeline.predict(X_test)
    test_eval_df["is_prediction_correct"] = test_eval_df["target_class"] == test_eval_df["predicted_health_class"]

    # Save to both scratch and Test run data directory
    csv_scratch = os.path.join(artifacts_dir, "test_run_predictions.csv")
    csv_desktop = os.path.join(data_dir, "test_run_predictions.csv")

    test_eval_df.to_csv(csv_scratch, index=False)
    test_eval_df.to_csv(csv_desktop, index=False)
    print(f"  Saved Predictions CSV to: {csv_scratch}")
    print(f"  Saved Predictions CSV to: {csv_desktop}")

    print("\n" + "=" * 90)
    print(f"  TRAINING COMPLETE! Classifier Accuracy: {cls_report['accuracy'] * 100:.2f}% | Regressor R^2: {r2:.4f}")
    print("=" * 90)


if __name__ == "__main__":
    data_directory = r"C:\Users\avgan\OneDrive\Desktop\Test run data"
    if len(sys.argv) > 1:
        data_directory = sys.argv[1]
    train_and_evaluate(data_directory)
