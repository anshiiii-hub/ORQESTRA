import hashlib
import io
import zipfile

import pytest
from openpyxl import Workbook

from app.data_analysis import (
    MAX_ROWS,
    AnalysisError,
    analyze_dataset,
)


def workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    sheet.append(["product", "revenue", "formula"])
    sheet.append(["Frames", 10, "=1+1"])
    sheet.append(["Lenses", 20, "=SUM(B2:B3)"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_csv_profiling_matches_known_numeric_fixtures_and_emits_stable_outputs():
    source = b"item,sales,region\nA,10,west\nB,20,east\nC,30,west\n"
    result = analyze_dataset("quarter.csv", source)
    assert (result.row_count, result.column_count) == (3, 3)
    assert result.content_sha256 == hashlib.sha256(source).hexdigest()
    assert result.metadata["analysis_version"] == "1.0.0"
    revenue = result.profile["columns"][1]
    assert revenue["inferred_type"] == "numeric"
    assert revenue["statistics"] == {
        "count": 3,
        "mean": 20.0,
        "min": 10.0,
        "max": 30.0,
        "sum": 60.0,
    }
    assert result.normalized_csv == "item,sales,region\nA,10,west\nB,20,east\nC,30,west\n"
    assert result.chart_artifacts[0]["id"] == "numeric-means"
    assert "Mean by numeric column" in result.chart_artifacts[0]["svg"]


def test_formula_text_is_never_evaluated_and_export_is_safe_to_open():
    result = analyze_dataset("formulas.xlsx", workbook_bytes())
    assert result.row_count == 2
    assert result.metadata["formula_cells_treated_as_data"] == 2
    formula_profile = result.profile["columns"][2]
    assert formula_profile["inferred_type"] == "text"
    assert "'=1+1" in result.normalized_csv
    assert "'=SUM(B2:B3)" in result.normalized_csv
    assert result.profile["columns"][1]["statistics"]["sum"] == 30.0
    assert "<script" not in result.chart_artifacts[0]["svg"]


def test_negative_numeric_values_remain_numeric_in_safe_export():
    result = analyze_dataset("negative.csv", b"delta\n-2\n-1.5\n")
    assert result.normalized_csv == "delta\n-2\n-1.5\n"


def test_nonfinite_numeric_text_never_enters_json_statistics():
    result = analyze_dataset("nonfinite.csv", b"value\ninf\n1\n")
    assert result.profile["columns"][0]["inferred_type"] == "text"
    assert "statistics" not in result.profile["columns"][0]


def test_overflowing_numeric_aggregates_are_not_serialized_as_nonfinite_values():
    result = analyze_dataset("overflow.csv", b"value\n1e308\n1e308\n")
    assert result.profile["columns"][0]["inferred_type"] == "text"
    assert "statistics" not in result.profile["columns"][0]


def test_chart_labels_are_xml_escaped():
    result = analyze_dataset("labels.csv", b"<script>alert(1)</script>\n5\n")
    svg = result.chart_artifacts[0]["svg"]
    assert "&lt;script&gt;" in svg
    assert "<script>alert" not in svg


@pytest.mark.parametrize(
    ("filename", "source", "code"),
    [
        ("../secret.csv", b"a\n1\n", "filename_invalid"),
        ("unsafe.xlsm", b"PK\x03\x04", "file_type_unsupported"),
        ("empty.csv", b"", "file_empty"),
        ("binary.csv", b"a\n\xff", "csv_parse_failed"),
    ],
)
def test_invalid_files_have_safe_explanations(filename, source, code):
    with pytest.raises(AnalysisError) as error:
        analyze_dataset(filename, source)
    assert error.value.code == code


def test_oversize_and_excessive_rows_are_rejected():
    with pytest.raises(AnalysisError, match="5 MiB"):
        analyze_dataset("big.csv", b"x" * (5 * 1024 * 1024 + 1))
    source = ("value\n" + "1\n" * (MAX_ROWS + 1)).encode()
    with pytest.raises(AnalysisError) as error:
        analyze_dataset("rows.csv", source)
    assert error.value.code == "dataset_row_limit"


def test_csv_column_and_cell_limits_are_enforced():
    wide = (",".join(f"c{index}" for index in range(101)) + "\n").encode()
    with pytest.raises(AnalysisError) as columns:
        analyze_dataset("wide.csv", wide)
    assert columns.value.code == "csv_schema_invalid"
    cell = ("value\n" + "x" * 16_385 + "\n").encode()
    with pytest.raises(AnalysisError) as too_long:
        analyze_dataset("cell.csv", cell)
    assert too_long.value.code == "dataset_cell_too_large"


def test_invalid_zip_and_macro_archive_are_rejected():
    with pytest.raises(AnalysisError) as invalid:
        analyze_dataset("broken.xlsx", b"not a zip")
    assert invalid.value.code == "xlsx_invalid"

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("[Content_Types].xml", "<Types/>")
        output.writestr("xl/workbook.xml", "<workbook/>")
        output.writestr("xl/vbaProject.bin", b"macro")
    with pytest.raises(AnalysisError) as macro:
        analyze_dataset("macro.xlsx", archive.getvalue())
    assert macro.value.code == "macros_unsupported"


def test_highly_compressed_xlsx_member_is_rejected():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("[Content_Types].xml", "<Types/>")
        output.writestr("xl/workbook.xml", "<workbook/>")
        output.writestr("xl/worksheet/sheet1.xml", "A" * (1024 * 1024))
    with pytest.raises(AnalysisError) as error:
        analyze_dataset("compressed.xlsx", archive.getvalue())
    assert error.value.code == "xlsx_expanded_too_large"


def test_workspace_scoped_api_persists_profile_chart_export_and_enforces_roles(
    api_client, accounts
):
    owner = accounts("analysis-owner@example.com")
    viewer = accounts("analysis-viewer@example.com")
    outsider = accounts("analysis-outsider@example.com")
    workspace = owner.post("/api/v1/workspaces", json={"name": "Analytics"}).json()["id"]
    invite = owner.post(
        f"/api/v1/workspaces/{workspace}/members",
        json={"email": "analysis-viewer@example.com", "role": "viewer"},
    )
    assert invite.status_code == 200, invite.text

    imported = owner.post(
        f"/api/v1/data-analysis/import?workspace_id={workspace}&filename=fixture.csv",
        content=b"item,sales\nA,10\nB,20\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert imported.status_code == 201, imported.text
    summary = imported.json()
    dataset_id = summary["id"]
    assert summary["row_count"] == 2
    assert summary["profile"]["columns"][1]["statistics"]["mean"] == 15.0
    assert viewer.get(f"/api/v1/data-analysis/{dataset_id}").status_code == 200
    exported = viewer.get(f"/api/v1/data-analysis/{dataset_id}/export.csv")
    assert exported.status_code == 200
    assert exported.text == "item,sales\nA,10\nB,20\n"
    chart = viewer.get(f"/api/v1/data-analysis/{dataset_id}/charts/numeric-means.svg")
    assert chart.status_code == 200
    assert chart.headers["content-type"].startswith("image/svg+xml")
    assert outsider.get(f"/api/v1/data-analysis/{dataset_id}").status_code == 404
    denied = viewer.post(
        f"/api/v1/data-analysis/import?workspace_id={workspace}&filename=viewer.csv",
        content=b"a\n1\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert denied.status_code == 403


def test_api_rejects_bad_file_names_without_persisting(api_client, accounts):
    owner = accounts("analysis-invalid@example.com")
    workspace = owner.post("/api/v1/workspaces", json={"name": "Invalid"}).json()["id"]
    response = owner.post(
        f"/api/v1/data-analysis/import?workspace_id={workspace}&filename=../../nope.exe",
        content=b"x",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 422
    assert "simple name" in response.text
