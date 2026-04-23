from app.models.schemas import Question
from app.services.comparator import (
    classify_similarity,
    compare_against_history_bank,
    compare_cross_papers,
    compare_within_paper,
)


def build_question(
    paper_id: str,
    order: int,
    question_no: str,
    content: str,
) -> Question:
    return Question(
        question_id=f"{paper_id}-{order}",
        paper_id=paper_id,
        question_no=question_no,
        order=order,
        content=content,
        raw_block=content,
    )


def test_classify_similarity_levels() -> None:
    assert classify_similarity(96) == "高度重复"
    assert classify_similarity(90) == "疑似重复"
    assert classify_similarity(80) == "差异较大"


def test_compare_within_paper_detects_high_duplicate() -> None:
    questions = [
        build_question("A", 1, "1", "小明以 5m/s 的速度前进 10 秒，求路程。"),
        build_question("A", 2, "2", "小明以 5m/s 的速度前进 10 秒，求路程。"),
        build_question("A", 3, "3", "分析《岳阳楼记》的主旨。"),
    ]

    matches = compare_within_paper(questions, "within_paper_a")

    assert len(matches) == 1
    assert matches[0].level == "高度重复"
    assert matches[0].source_question_no == "1"
    assert matches[0].target_question_no == "2"


def test_compare_cross_papers_detects_suspected_duplicate() -> None:
    paper_a_questions = [
        build_question("A", 1, "1", "请分析传统文化传承与创新之间的关系。"),
    ]
    paper_b_questions = [
        build_question("B", 1, "1", "请分析传统文化传承和创新之间的关系。"),
    ]

    matches = compare_cross_papers(paper_a_questions, paper_b_questions)

    assert len(matches) == 1
    assert matches[0].comparison_type == "cross_paper"
    assert matches[0].level in {"高度重复", "疑似重复"}


def test_compare_against_history_bank_limits_top_matches_per_question() -> None:
    source_questions = [
        build_question("A", 1, "1", "请说明操作系统中进程与线程的主要区别。"),
    ]
    history_questions = [
        build_question("H1", 1, "1", "请说明操作系统中进程与线程的主要区别。"),
        build_question("H2", 1, "1", "请说明操作系统里进程和线程的主要区别。"),
        build_question("H3", 1, "1", "请分析数据库事务的 ACID 特性。"),
    ]

    history_questions[0].paper_label = "历史卷一"
    history_questions[1].paper_label = "历史卷二"
    history_questions[2].paper_label = "历史卷三"

    matches = compare_against_history_bank(
        source_questions,
        history_questions,
        top_k_per_question=1,
    )

    assert len(matches) == 1
    assert matches[0].comparison_type == "history_bank"
    assert matches[0].target_paper_label == "历史卷一"
