from app.services.rsi_versions import (
    duplicate_current_groups,
    make_current,
    rsi_group_key,
)


def _doc(rsi_id, document_type, market="EU", current=True, product="ABPARA"):
    return {
        "rsi_id": rsi_id,
        "product_name": product,
        "document_type": document_type,
        "market": market,
        "is_current": current,
    }


def test_group_key_ignores_case_and_spacing():
    assert rsi_group_key(_doc(1, "SmPC", " eu ", product="abpara")) == rsi_group_key(
        _doc(2, "smpc", "EU", product="ABPARA ")
    )


def test_duplicate_current_groups_flags_only_repeated_current_documents():
    documents = [
        _doc(1, "Innovator Reference Safety Information"),
        _doc(2, "Innovator Reference Safety Information"),
        _doc(3, "SmPC"),
        _doc(4, "SmPC", current=False),
        _doc(5, "SmPC", market="Uganda"),
    ]

    groups = duplicate_current_groups(documents)

    assert len(groups) == 1
    assert groups[0]["label"] == "ABPARA · Innovator Reference Safety Information · EU"
    assert [d["rsi_id"] for d in groups[0]["documents"]] == [1, 2]


def test_make_current_supersedes_same_group_then_marks_document_current():
    calls = []

    class Cursor:
        rowcount = 2

        def execute(self, sql, params):
            calls.append((" ".join(sql.split()), params))

    superseded = make_current(Cursor(), _doc(7, "SmPC"))

    assert superseded == 2
    first_sql, first_params = calls[0]
    assert "SET is_current = FALSE" in first_sql
    assert "rsi_id <> %s" in first_sql
    assert first_params == (7, "ABPARA", "SmPC", "EU")
    assert "SET is_current = TRUE" in calls[1][0]
    assert calls[1][1] == (7,)
