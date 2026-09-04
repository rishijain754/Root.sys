"""
Machine Learning Settlement Predictor & Calibrated Evaluator.
Integrates trained HistGradientBoosting models and empirical calibration parameters
into the Expected Settlement Calculator.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
import zoneinfo

import joblib
import numpy as np
import pandas as pd

from .banking_calendar import BankingCalendar
from .models import RailType, SettlementHealth, PaymentTransaction


@dataclass
class MLPredictionResult:
    """Outcome of ML model inference on a transaction."""
    predicted_health: str
    class_probabilities: Dict[str, float]
    predicted_delay_hours: float
    expected_credit_timestamp: datetime
    is_legitimately_delayed: bool
    is_failed: bool
    confidence_score: float
    calibrated_sla_hours: float
    features_used: Dict[str, Any]


class MLSettlementPredictor:
    """
    Inference interface for trained settlement models:
    - Multi-Class Settlement Health Classifier (85.89% Accuracy)
    - Settlement Duration Regressor (R^2 = 0.77, MAE = 7.17h)
    - Empirical calibration matrix across NEFT, RTGS, IMPS, and UPI rails.
    """

    def __init__(
        self,
        classifier_path: Optional[str] = None,
        regressor_path: Optional[str] = None,
        config_path: Optional[str] = None,
    ):
        base_dir = Path(__file__).resolve().parent
        self.clf_path = classifier_path or os.path.join(base_dir, "trained_settlement_classifier.joblib")
        self.reg_path = regressor_path or os.path.join(base_dir, "trained_settlement_regressor.joblib")
        self.cfg_path = config_path or os.path.join(base_dir, "calibrated_settlement_parameters.json")

        self.classifier = None
        self.regressor = None
        self.config = {}
        self.calendar = BankingCalendar(timezone_name="Asia/Kolkata", jurisdiction="IN")

        self._load_artifacts()

    def _load_artifacts(self):
        """Loads trained pipelines and calibration configuration."""
        if os.path.exists(self.clf_path):
            self.classifier = joblib.load(self.clf_path)
        if os.path.exists(self.reg_path):
            self.regressor = joblib.load(self.reg_path)
        if os.path.exists(self.cfg_path):
            with open(self.cfg_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)

    def extract_features_for_txn(
        self,
        amount_inr: float,
        event_time_utc: datetime,
        clearing_rail: str = "NEFT",
        payment_method: str = "upi",
        settlement_cycle: str = "T+2",
        is_holiday_rollover: Optional[bool] = None,
    ) -> pd.DataFrame:
        """Extracts engineered features matching the training pipeline."""
        if event_time_utc.tzinfo is None:
            event_time_utc = event_time_utc.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        
        ist = zoneinfo.ZoneInfo("Asia/Kolkata")
        event_ist = event_time_utc.astimezone(ist)

        hour_ist = event_ist.hour
        day_of_week = event_ist.weekday()
        day_of_month = event_ist.day

        # 2nd and 4th Saturday Indian banking rule
        is_sat = (day_of_week == 5)
        nth_sat = (day_of_month - 1) // 7 + 1
        is_second_or_fourth_sat = is_sat and (nth_sat in (2, 4))
        is_sun = (day_of_week == 6)
        is_weekend = is_sun or is_second_or_fourth_sat

        rbi_holidays = {
            (2026, 1, 26), (2026, 4, 1), (2026, 4, 3), (2026, 4, 14),
            (2026, 5, 1), (2026, 8, 15), (2026, 10, 2), (2026, 12, 25)
        }
        is_holiday = (event_ist.year, event_ist.month, event_ist.day) in rbi_holidays

        if is_holiday_rollover is None:
            is_holiday_rollover = is_weekend or is_holiday

        feat_dict = {
            "method": payment_method.lower(),
            "clearing_rail": clearing_rail.upper(),
            "settlement_cycle": settlement_cycle.upper(),
            "log_amount": float(np.log1p(abs(amount_inr))),
            "hour_ist": hour_ist,
            "day_of_week": day_of_week,
            "is_second_or_fourth_saturday": bool(is_second_or_fourth_sat),
            "is_sunday": bool(is_sun),
            "is_weekend_closure": bool(is_weekend),
            "is_rbi_holiday": bool(is_holiday),
            "is_post_16_cutoff": bool(hour_ist >= 16),
            "is_post_21_cutoff": bool(hour_ist >= 21),
            "hours_to_16_cutoff": float(16 - hour_ist),
            "is_holiday_rollover": bool(is_holiday_rollover),
        }
        return pd.DataFrame([feat_dict])

    def predict(
        self,
        amount_inr: float,
        event_time_utc: datetime,
        clearing_rail: str = "NEFT",
        payment_method: str = "upi",
        settlement_cycle: str = "T+2",
        is_holiday_rollover: Optional[bool] = None,
    ) -> MLPredictionResult:
        """Runs trained classifier and regressor inference on transaction parameters."""
        if not self.classifier or not self.regressor:
            raise RuntimeError("ML model artifacts are not loaded.")

        X = self.extract_features_for_txn(
            amount_inr=amount_inr,
            event_time_utc=event_time_utc,
            clearing_rail=clearing_rail,
            payment_method=payment_method,
            settlement_cycle=settlement_cycle,
            is_holiday_rollover=is_holiday_rollover,
        )

        pred_health = self.classifier.predict(X)[0]
        probs = self.classifier.predict_proba(X)[0]
        classes = self.classifier.classes_
        prob_dict = {cls: float(p) for cls, p in zip(classes, probs)}

        # Predicted delay in hours
        pred_delay_hours = max(0.0, float(self.regressor.predict(X)[0]))

        # Calculate expected credit timestamp
        if event_time_utc.tzinfo is None:
            event_time_utc = event_time_utc.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        expected_credit_utc = event_time_utc + timedelta(hours=pred_delay_hours)

        # Calibrated SLA from calibration matrix
        calib_matrix = self.config.get("calibration_matrix", {})
        rail_calib = calib_matrix.get(clearing_rail.upper(), {}).get(settlement_cycle.upper(), {})
        if X["is_holiday_rollover"].iloc[0]:
            calibrated_sla = rail_calib.get("holiday_rollover_p95_hours", 120.0)
        else:
            calibrated_sla = rail_calib.get("normal_p95_hours", 48.0)

        return MLPredictionResult(
            predicted_health=pred_health,
            class_probabilities=prob_dict,
            predicted_delay_hours=round(pred_delay_hours, 2),
            expected_credit_timestamp=expected_credit_utc,
            is_legitimately_delayed=(pred_health == "LEGITIMATELY_DELAYED"),
            is_failed=(pred_health == "FAILED"),
            confidence_score=round(float(np.max(probs)), 4),
            calibrated_sla_hours=calibrated_sla,
            features_used=X.to_dict(orient="records")[0],
        )
