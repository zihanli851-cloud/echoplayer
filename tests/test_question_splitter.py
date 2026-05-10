from app.services.question_splitter import RuleQuestionSplitter, normalize_formula_glyphs, split_questions


def test_split_questions_with_chinese_numerals() -> None:
    text = "一、第一题内容\n二、第二题内容\n三、第三题内容"
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 3
    assert questions[0].question_no == "一"
    assert questions[1].content == "第二题内容"
    assert questions[2].question_id == "A-3"


def test_split_questions_with_arabic_numbers() -> None:
    text = "1. 第一题\n2. 第二题\n3. 第三题"
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 3
    assert questions[0].question_no == "1"
    assert questions[2].content == "第三题"


def test_split_questions_filters_exam_headers_before_first_question() -> None:
    text = """
    西南财经大学期末考试试卷
    计算机与人工智能学院
    课程名称：数据结构
    任课教师：黄老师
    姓名：张三
    学号：20250001
    考试说明：闭卷考试
    一、简答题
    1. 请说明栈和队列的区别。
    2. 请说明链表的优缺点。
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 2
    assert "西南财经大学" not in questions[0].content
    assert "课程名称" not in questions[0].content
    assert "姓名" not in questions[0].content
    assert questions[0].content == "请说明栈和队列的区别。"


def test_split_questions_falls_back_to_single_question() -> None:
    text = "这是一段没有明确题号的材料。"
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 1
    assert questions[0].question_no == "1"
    assert questions[0].split_confidence < 0.7
    assert "人工复核" in questions[0].split_warning


def test_major_question_mode_keeps_subquestions_inside_parent_question() -> None:
    text = """
    一、简答题
    1. 请说明操作系统进程与线程的区别。
    （1）从调度角度说明。
    （2）从资源占用角度说明。
    2. 请说明死锁的四个必要条件。
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 2
    assert "（1）从调度角度说明。" in questions[0].content
    assert "（2）从资源占用角度说明。" in questions[0].content


def test_subquestion_expand_mode_can_expand_subquestions() -> None:
    text = """
    一、简答题
    1. 请说明操作系统进程与线程的区别。
    （1）从调度角度说明。
    （2）从资源占用角度说明。
    """
    questions = RuleQuestionSplitter(split_mode="subquestion_expand_mode").split(text, "A")

    assert len(questions) == 3
    assert questions[1].question_no == "（1）"
    assert questions[2].question_no == "（2）"


def test_normalize_formula_glyphs_repairs_common_private_use_symbols() -> None:
    text = "若 p\uf03eq，则 A\uf02dB"

    normalized = normalize_formula_glyphs(text)

    assert ">" in normalized
    assert "-" in normalized
