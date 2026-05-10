from app.services.question_splitter import normalize_formula_glyphs, split_questions


def test_split_questions_with_chinese_numerals() -> None:
    text = "一、第一题内容\n二、第二题内容\n三、第三题内容"
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 3
    assert questions[0].question_no == "一"
    assert questions[1].content == "第二题内容"
    assert questions[2].question_id == "A-3"


def test_split_questions_with_extended_chinese_number_punctuation() -> None:
    text = "一：第一题内容\n二）第二题内容\n三，第三题内容"
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 3
    assert [question.question_no for question in questions] == ["一", "二", "三"]


def test_split_questions_with_arabic_numbers() -> None:
    text = "1. 第一题\n2. 第二题\n3. 第三题"
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 3
    assert questions[0].question_no == "1"
    assert questions[2].content == "第三题"
    assert questions[0].split_confidence == 1.0
    assert questions[0].split_warning == ""


def test_split_questions_with_subquestion_markers() -> None:
    text = "（1）小题一\n（2）小题二"
    questions = split_questions(text, paper_id="B")

    assert len(questions) == 2
    assert questions[0].question_no == "（1）"
    assert questions[1].question_id == "B-2"


def test_split_questions_falls_back_to_single_question() -> None:
    text = "这是一个没有题号标记的短文材料。"
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 1
    assert questions[0].question_no == "1"
    assert "短文材料" in questions[0].content
    assert questions[0].split_confidence < 0.7
    assert "人工复核" in questions[0].split_warning


def test_split_questions_ignores_preamble_and_section_titles() -> None:
    text = """
    西南财经大学本科期末考试试题册（A）
    试题说明：
    1、考试类型：闭卷
    2、本套试题共 2 道大题，共 3 页。
    考生注意事项：
    1. 出示学生证
    2. 严格遵守考场纪律
    0
    一、问答题（每题10分，共20分）
    1. 第一题内容
    2. 第二题内容
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 2
    assert [question.question_no for question in questions] == ["1", "2"]
    assert questions[0].content == "第一题内容"


def test_split_questions_keeps_real_chinese_numbered_question_after_preamble() -> None:
    text = """
    试题说明：
    1、请将答案写在答题纸上
    2、考试结束后交卷
    一、阅读下列材料并回答问题。
    请结合材料分析人工智能的发展趋势。
    """
    questions = split_questions(text, paper_id="B")

    assert len(questions) == 1
    assert questions[0].question_no == "一"
    assert "人工智能" in questions[0].content


def test_split_questions_does_not_treat_decimal_numbers_as_question_markers() -> None:
    text = """
    一、编程题
    1. 阅读代码，回答问题。
    System.out.println(6.0);
    问：上面代码输出什么？
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 1
    assert "6.0" in questions[0].content


def test_split_questions_supports_section_heading_without_punctuation() -> None:
    text = """
    课程名称：数据结构
    试题说明：
    1、考试类型：闭卷
    一 单项选择题(每题2分，共4分)
    1. 第一题
    2. 第二题
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 2
    assert [question.question_no for question in questions] == ["1", "2"]


def test_split_questions_coalesces_fragmented_option_blocks() -> None:
    text = """
    一、选择题
    1. 下列命题公式等价的是 A. p→q B. ¬q→¬p
    2. C. p∨q D. p∧q
    3. 第二道完整题
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 2
    assert questions[0].question_no == "1"
    assert "C. p ∨ q" in questions[0].content
    assert "D. p ∧ q" in questions[0].content
    assert questions[1].question_no == "3"


def test_split_questions_does_not_merge_short_independent_questions() -> None:
    text = """
    一、判断题
    1. 是
    2. 否
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 2
    assert [question.content for question in questions] == ["是", "否"]


def test_normalize_formula_glyphs_repairs_common_private_use_symbols() -> None:
    text = "若 p\uf0aeq 且 A\uf0ceB，则 A\uf0c7B 非空。"

    normalized = normalize_formula_glyphs(text)

    assert "p → q" in normalized
    assert "A ∈ B" in normalized
    assert "A ∩ B" in normalized
