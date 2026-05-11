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


def test_split_questions_filters_numbered_exam_instructions_before_questions() -> None:
    text = """
    一、考试准备
    1. 考生须将姓名、学号填写在答题纸指定位置。
    2. 开考前请检查试卷页数，如有缺页立即报告监考教师。
    二、答题要求
    1. 所有答案必须写在答题纸上，写在试题册上无效。
    2. 不得携带手机、资料进入考场。
    三、选择题
    1. 下列哪一项是正确的 float 变量声明？
    A. float foo = 1;
    B. float foo = 1.0;
    2. 下列关于数组的说法正确的是？
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 2
    assert "考试准备" not in questions[0].content
    assert "答题要求" not in questions[0].content
    assert "考生须" not in questions[0].content
    assert questions[0].content.startswith("下列哪一项")
    assert "1.0" in questions[0].content


def test_split_questions_filters_unnumbered_exam_cover_text() -> None:
    text = """
    本套试题共 四 道大题，共 4 页，完卷时间 120 分钟。
    考试用品中除纸、笔、尺子外，可另带的用具有：计算器[ ] 字典[ ]
    以下各项由学生填写：任课教师： 年级专业： 学生姓名： 学号：
    考生注意事项：1、出示学生证和准考证于桌面左上角，以备查验。
    2、拿到试卷后清点并检查试卷页数，如有重页、空白页及印刷模糊等举手向监考教师示意调换试卷。
    3、答题前先将试题册及答题纸上的任课教师、专业、年级、学号、姓名填写完整。
    4、所有答案均需填写在答题纸上，答在试题册上无效。
    5、考生不得携带任何通讯工具进入考场。
    6、严格遵守考场纪律。
    一、单项选题（本大题共 10 小题，每小题 2分，共 20 分）
    1. 下列程序段的时间复杂度是( )
    A. O(logn) B. O(n)
    2. 用链表方式存储的队列，在进行插入运算时( )。
    """
    questions = split_questions(text, paper_id="A")

    assert len(questions) == 2
    assert questions[0].question_no == "1"
    assert questions[0].content.startswith("下列程序段")
    assert "本套试题" not in questions[0].content
    assert "考试用品" not in questions[0].content
    assert "严格遵守考场纪律" not in questions[0].content
    assert "单项选题" not in questions[0].content


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
