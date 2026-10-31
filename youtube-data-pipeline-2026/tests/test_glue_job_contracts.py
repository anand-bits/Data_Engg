"""Static contract checks for Glue jobs, whose AWS runtime is not local to pytest."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_tree(relative_path):
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    return ast.parse(source), source


def calls_in(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def call_names(tree):
    names = set()
    for call in calls_in(tree):
        if isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
        elif isinstance(call.func, ast.Name):
            names.add(call.func.id)
    return names


def test_bronze_to_silver_job_compiles_and_enforces_transform_contract():
    tree, source = load_tree("glue/bronze_to_silver_stats.py")
    names = call_names(tree)
    assert {"filter", "withColumn", "writeFrame", "commit"}.issubset(names)
    assert "statistics.viewCount" in source
    assert "statistics__viewCount" in source
    assert 'partitionKeys=["region"]' in source
    assert 'coalesce(F.col(col_name), F.lit(0))' in source
    assert 'F.row_number().over(window)' in source


def test_silver_to_gold_job_builds_all_three_analytics_tables():
    tree, source = load_tree("glue/silver_to_gold_analytics.py")
    names = call_names(tree)
    assert {"groupBy", "agg", "join", "writeFrame", "commit"}.issubset(names)
    for table in ("trending_analytics", "channel_analytics", "category_analytics"):
        assert f'youtube/{table}/' in source
        assert f'catalogTableName="{table}"' in source
    assert 'how="left"' in source
    assert 'F.row_number().over(window_rank)' in source
    assert '"view_share_pct"' in source


def test_all_project_python_files_parse():
    for path in ROOT.rglob("*.py"):
        if "tests" not in path.parts:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
