"""Build compact, static portfolio assets from the bundled Kaggle snapshots.

The exported Ridge model is intentionally small so inference can run in the
browser on GitHub Pages. It estimates log views among videos already observed
as trending; it is not a live YouTube forecast.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ASSET_DIR = ROOT / "docs" / "assets"
REGIONS = ["US", "GB", "CA", "DE", "FR", "IN", "JP", "KR", "MX", "RU"]
NUMERIC_FEATURES = [
    "title_chars",
    "title_words",
    "publish_hour",
    "publish_weekday",
    "video_age_days",
    "tag_count",
]
CATEGORICAL_FEATURES = ["region", "category_id"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES
READ_COLUMNS = [
    "video_id", "trending_date", "title", "category_id", "publish_time",
    "tags", "views", "likes", "comment_count",
]


def load_data() -> pd.DataFrame:
    """Read only the columns used by the dashboard/model from each CSV."""
    frames = []
    for region in REGIONS:
        path = DATA_DIR / f"{region}videos.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(
            path,
            usecols=lambda col: col in READ_COLUMNS,
            low_memory=False,
            encoding="utf-8",
            encoding_errors="replace",
        )
        frame["region"] = region
        frame["trending_date"] = pd.to_datetime(
            frame["trending_date"], format="%y.%d.%m", errors="coerce"
        )
        frame["publish_time"] = pd.to_datetime(frame["publish_time"], utc=True, errors="coerce")
        for column in ("views", "likes", "comment_count", "category_id"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frames.append(frame)

    if not frames:
        raise FileNotFoundError(f"No regional video CSV files found in {DATA_DIR}")

    data = pd.concat(frames, ignore_index=True)
    data = data.dropna(subset=["video_id", "trending_date", "views", "category_id"])
    data["views"] = data["views"].clip(lower=0)
    data["likes"] = data["likes"].fillna(0).clip(lower=0)
    data["comment_count"] = data["comment_count"].fillna(0).clip(lower=0)
    data["title"] = data["title"].fillna("").astype(str)
    data["tags"] = data["tags"].fillna("").astype(str)
    data["title_chars"] = data["title"].str.len().clip(upper=500)
    data["title_words"] = data["title"].str.split().str.len().clip(upper=100)
    data["publish_hour"] = data["publish_time"].dt.hour.fillna(12)
    data["publish_weekday"] = data["publish_time"].dt.dayofweek.fillna(3)
    data["video_age_days"] = (
        data["trending_date"] - data["publish_time"].dt.tz_localize(None)
    ).dt.total_seconds().div(86400).clip(lower=0, upper=3650).fillna(30)
    data["tag_count"] = data["tags"].map(
        lambda value: 0 if not value or value == "[none]" else value.count("|") + 1
    ).clip(upper=100)
    data["category_id"] = data["category_id"].astype(int).astype(str)
    data["engagement_rate"] = np.where(
        data["views"] > 0,
        (data["likes"] + data["comment_count"]) / data["views"] * 100,
        0,
    )
    return data


def build_analytics(data: pd.DataFrame) -> dict:
    """Create modest-sized region, category, time-series and video summaries."""
    category_names = {}
    for path in DATA_DIR.glob("*_category_id.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for item in payload.get("items", []):
                category_names[str(item.get("id"))] = item.get("snippet", {}).get("title", "Category")
        except (OSError, json.JSONDecodeError):
            continue

    regions = []
    categories = []
    daily = []
    top_videos = []

    for region, group in data.groupby("region", sort=True):
        unique_latest = group.sort_values("trending_date").drop_duplicates("video_id", keep="last")
        regions.append({
            "region": region,
            "records": int(len(group)),
            "videos": int(group["video_id"].nunique()),
            "total_views": int(group["views"].sum()),
            "median_views": int(group["views"].median()),
            "avg_engagement": round(float(group["engagement_rate"].mean()), 3),
        })

        for category_id, category in group.groupby("category_id", sort=True):
            categories.append({
                "region": region,
                "category_id": str(category_id),
                "category_name": category_names.get(str(category_id), f"Category {category_id}"),
                "records": int(len(category)),
                "videos": int(category["video_id"].nunique()),
                "avg_views": int(category["views"].mean()),
                "total_views": int(category["views"].sum()),
                "avg_engagement": round(float(category["engagement_rate"].mean()), 3),
            })

        for date, day in group.groupby(group["trending_date"].dt.strftime("%Y-%m-%d"), sort=True):
            daily.append({
                "region": region,
                "date": str(date),
                "videos": int(day["video_id"].nunique()),
                "views": int(day["views"].sum()),
                "avg_views": int(day["views"].mean()),
            })

        top = unique_latest.nlargest(8, "views")
        for row in top.itertuples(index=False):
            top_videos.append({
                "region": region,
                "video_id": str(row.video_id),
                "title": str(row.title)[:180],
                "category_id": str(row.category_id),
                "category_name": category_names.get(str(row.category_id), f"Category {row.category_id}"),
                "views": int(row.views),
                "likes": int(row.likes),
                "comments": int(row.comment_count),
                "engagement_rate": round(float(row.engagement_rate), 3),
                "publish_time": row.publish_time.isoformat() if pd.notna(row.publish_time) else None,
            })

    categories.sort(key=lambda row: (row["region"], row["category_id"]))
    daily.sort(key=lambda row: (row["region"], row["date"]))
    top_videos.sort(key=lambda row: (-row["views"], row["region"]))
    return {
        "metadata": {
            "source": "Kaggle YouTube Trending Videos snapshots",
            "period_start": data["trending_date"].min().strftime("%Y-%m-%d"),
            "period_end": data["trending_date"].max().strftime("%Y-%m-%d"),
            "records": int(len(data)),
            "unique_videos": int(data["video_id"].nunique()),
            "regions_count": int(data["region"].nunique()),
            "generated_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "regions": regions,
        "category_names": category_names,
        "categories": categories,
        "daily": daily,
        "top_videos": top_videos,
    }


def build_model(data: pd.DataFrame) -> dict:
    """Train a grouped holdout model and export coefficients for browser use."""
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    train_idx, test_idx = next(splitter.split(data, groups=data["video_id"]))
    train = data.iloc[train_idx]
    test = data.iloc[test_idx]

    preprocessor = ColumnTransformer([
        ("numeric", StandardScaler(), NUMERIC_FEATURES),
        ("category", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
    ])
    model = Pipeline([
        ("preprocessor", preprocessor),
        ("regressor", Ridge(alpha=10.0)),
    ])
    target_train = np.log1p(train["views"].to_numpy())
    target_test = np.log1p(test["views"].to_numpy())
    model.fit(train[FEATURE_COLUMNS], target_train)
    prediction = model.predict(test[FEATURE_COLUMNS])

    fitted = model.named_steps["preprocessor"]
    numeric_scaler = fitted.named_transformers_["numeric"]
    encoder = fitted.named_transformers_["category"]
    coefficients = model.named_steps["regressor"].coef_
    cursor = len(NUMERIC_FEATURES)

    numeric = {}
    for index, feature in enumerate(NUMERIC_FEATURES):
        numeric[feature] = {
            "mean": float(numeric_scaler.mean_[index]),
            "scale": float(numeric_scaler.scale_[index]),
            "coefficient": float(coefficients[index]),
        }

    categorical = {}
    for feature, values in zip(CATEGORICAL_FEATURES, encoder.categories_):
        categorical[feature] = {
            str(value): float(coefficients[cursor + index])
            for index, value in enumerate(values)
        }
        cursor += len(values)

    return {
        "model": "Ridge regression with standardized numeric and one-hot categorical features",
        "target": "log1p(views)",
        "scope": "Historical Kaggle videos that appeared in a trending snapshot; not a live or all-video forecast.",
        "split": "80/20 grouped by video_id so the same video does not appear in both train and test.",
        "training_rows": int(len(train)),
        "test_rows": int(len(test)),
        "metrics": {
            "r2_log_views": round(float(r2_score(target_test, prediction)), 4),
            "mae_log_views": round(float(mean_absolute_error(target_test, prediction)), 4),
        },
        "intercept": float(model.named_steps["regressor"].intercept_),
        "numeric": numeric,
        "categorical": categorical,
    }


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    data = load_data()
    analytics = build_analytics(data)
    model = build_model(data)
    for filename, payload in (("analytics.json", analytics), ("model.json", model)):
        output = ASSET_DIR / filename
        output.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        print(f"Wrote {output.relative_to(ROOT)} ({output.stat().st_size:,} bytes)")
    print(f"Model evaluation: {json.dumps(model['metrics'])}")


if __name__ == "__main__":
    main()
