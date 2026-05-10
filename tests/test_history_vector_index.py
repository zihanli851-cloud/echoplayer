from app.models.schemas import Question
from app.services.comparator import compare_against_history_bank
from app.services.history_vector_index import (
    IndexedHistoryQuestions,
    build_or_load_history_vector_index,
)


def build_question(paper_id: str, content: str) -> Question:
    return Question(
        question_id=f"{paper_id}-1",
        paper_id=paper_id,
        question_no="1",
        order=1,
        content=content,
        raw_block=content,
    )


def test_history_vector_index_persists_and_reuses_cache(tmp_path) -> None:
    questions = [
        build_question("H1", "process thread operating system difference"),
    ]
    index_path = tmp_path / "history_index.json"

    first = build_or_load_history_vector_index(questions, index_path=index_path)
    second = build_or_load_history_vector_index(questions, index_path=index_path)

    assert index_path.exists()
    assert first.signature == second.signature
    assert len(second.records) == 1


def test_history_vector_index_rebuilds_when_source_label_changes(tmp_path) -> None:
    index_path = tmp_path / "history_index.json"
    old_question = build_question("H1", "same content")
    old_question.paper_label = "old-source"
    new_question = build_question("H1", "same content")
    new_question.paper_label = "new-source"

    old_index = build_or_load_history_vector_index([old_question], index_path=index_path)
    new_index = build_or_load_history_vector_index([new_question], index_path=index_path)

    assert old_index.signature != new_index.signature
    assert new_index.records[0]["question"]["paper_label"] == "new-source"


def test_compare_against_history_bank_uses_attached_vector_index(tmp_path) -> None:
    history_questions = [
        build_question("H1", "operating system thread and process difference"),
    ]
    index = build_or_load_history_vector_index(history_questions, index_path=tmp_path / "index.json")
    indexed_questions = IndexedHistoryQuestions(history_questions, vector_index=index)
    source_questions = [
        build_question("A", "process thread operating system difference"),
    ]

    matches = compare_against_history_bank(source_questions, indexed_questions, threshold=90)

    assert len(matches) == 1
    assert matches[0].match_id.startswith("history_bank-vector_index-")
    assert matches[0].target_question_id == "H1-1"
