from app.models.schemas import Question
from app.services.comparator import (
    classify_similarity,
    compare_against_history_bank,
    compare_cross_papers,
    compare_within_paper,
    lightweight_vector_similarity,
    normalize_for_compare,
    strip_compare_boilerplate,
)


def build_question(
    paper_id: str,
    order: int,
    question_no: str,
    content: str,
    *,
    course: str = "",
) -> Question:
    return Question(
        question_id=f"{paper_id}-{order}",
        paper_id=paper_id,
        question_no=question_no,
        order=order,
        content=content,
        raw_block=content,
        course=course,
    )


def test_classify_similarity_levels() -> None:
    assert classify_similarity(96) == "高度重复"
    assert classify_similarity(90) == "疑似重复"
    assert classify_similarity(80) == "差异较大"
    assert classify_similarity(88, template_score_value=95) == "疑似原题"


def test_compare_within_paper_detects_high_duplicate() -> None:
    questions = [
        build_question("A", 1, "1", "小明以 5m/s 的速度前进 10 秒，求路程。"),
        build_question("A", 2, "2", "小明以 5m/s 的速度前进 10 秒，求路程。"),
        build_question("A", 3, "3", "分析《岳阳楼记》的主旨。"),
    ]

    matches = compare_within_paper(questions, "within_paper_a")

    assert len(matches) == 1
    assert matches[0].level == "高度重复"


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
    assert matches[0].level in {"高度重复", "疑似重复", "疑似原题"}


def test_compare_cross_papers_ignores_exam_cover_boilerplate() -> None:
    paper_a_questions = [
        build_question(
            "A",
            1,
            "1",
            "本套试题共 四 道大题，共 4 页，完卷时间 120 分钟。\n考生不得携带任何通讯工具进入考场。",
        ),
        build_question("A", 2, "2", "下列程序段的时间复杂度是( ) A. O(logn) B. O(n)"),
    ]
    paper_b_questions = [
        build_question(
            "B",
            1,
            "1",
            "本套试题共 四 道大题，共 3 页，完卷时间 120 分钟。\n考生不得携带任何通讯工具进入考场。",
        ),
        build_question("B", 2, "2", "二叉树中序遍历的访问顺序是( ) A. 根左右 B. 左根右"),
    ]

    matches = compare_cross_papers(paper_a_questions, paper_b_questions)

    assert matches == []
    assert strip_compare_boilerplate(paper_a_questions[0].content) == ""


def test_compare_boilerplate_filter_keeps_real_time_complexity_question() -> None:
    text = "下列程序段的时间复杂度是( ) A. O(logn) B. O(n)"

    assert strip_compare_boilerplate(text) == text
    assert normalize_for_compare(text)


def test_compare_against_history_bank_limits_top_matches_per_question() -> None:
    source_questions = [
        build_question("A", 1, "1", "请说明操作系统中进程与线程的主要区别。", course="操作系统"),
    ]
    history_questions = [
        build_question("H1", 1, "1", "请说明操作系统中进程与线程的主要区别。", course="操作系统"),
        build_question("H2", 1, "1", "请说明操作系统里进程和线程的主要区别。", course="操作系统"),
        build_question("H3", 1, "1", "请分析数据库事务的 ACID 特性。", course="数据库"),
    ]
    history_questions[0].paper_label = "历史卷一"
    history_questions[1].paper_label = "历史卷二"
    history_questions[2].paper_label = "历史卷三"

    matches = compare_against_history_bank(
        source_questions,
        history_questions,
        top_k_per_question=1,
        course_filter="操作系统",
    )

    assert len(matches) == 1
    assert matches[0].target_paper_label == "历史卷一"


def test_compare_against_history_bank_keeps_concrete_course_filter_strict() -> None:
    source_questions = [
        build_question("A", 1, "1", "请说明栈和队列的区别。", course="语文"),
    ]
    history_questions = [
        build_question("H1", 1, "1", "请说明栈和队列的区别。", course="数据结构"),
    ]

    matches = compare_against_history_bank(
        source_questions,
        history_questions,
        course_filter="语文",
    )

    assert matches == []


def test_compare_against_history_bank_uses_all_history_when_course_filter_is_empty() -> None:
    source_questions = [
        build_question("A", 1, "1", "请说明栈和队列的区别。", course=""),
    ]
    history_questions = [
        build_question("H1", 1, "1", "请说明栈和队列的区别。", course="数据结构"),
    ]

    matches = compare_against_history_bank(
        source_questions,
        history_questions,
        course_filter="",
    )

    assert len(matches) == 1
    assert matches[0].comparison_type == "history_bank"


def test_compare_against_history_bank_recognizes_same_source_question_by_template() -> None:
    source_questions = [
        build_question("A", 1, "1", "某汽车以 60km/h 行驶 3 小时，求路程。", course="物理"),
    ]
    history_questions = [
        build_question("H1", 1, "1", "某汽车以 80km/h 行驶 2 小时，求路程。", course="物理"),
    ]

    matches = compare_against_history_bank(
        source_questions,
        history_questions,
        threshold=85,
        course_filter="物理",
    )

    assert len(matches) == 1
    assert matches[0].template_score >= 90
    assert matches[0].is_same_source_question is True
    assert matches[0].level in {"疑似原题", "高度重复"}


def test_lightweight_vector_similarity_scores_reordered_overlap() -> None:
    score = lightweight_vector_similarity(
        "process thread operating system difference",
        "operating system thread and process difference",
    )

    assert score >= 90


def test_lightweight_vector_similarity_keeps_unrelated_chinese_low() -> None:
    score = lightweight_vector_similarity(
        "请简述春天的特点。",
        "计算长方形面积。",
    )

    assert score < 30


def test_image_placeholders_do_not_create_duplicate_matches() -> None:
    source_questions = [
        build_question("A", 1, "1", "[IMAGE page=1 index=1 bbox=0,0,100,100]"),
    ]
    history_questions = [
        build_question("H1", 1, "1", "[IMAGE page=1 index=1 bbox=0,0,100,100]"),
    ]

    matches = compare_against_history_bank(source_questions, history_questions)

    assert matches == []
    assert normalize_for_compare(source_questions[0].content) == ""


def test_ocr_marker_is_ignored_but_ocr_text_still_compares() -> None:
    source_questions = [
        build_question("A", 1, "1", "[IMAGE page=1 index=1 bbox=0,0,100,100]\n[OCR_TEXT]\n请简述春天的特点。"),
    ]
    history_questions = [
        build_question("H1", 1, "1", "请简述春天的特点。"),
    ]

    matches = compare_against_history_bank(source_questions, history_questions)

    assert len(matches) == 1
