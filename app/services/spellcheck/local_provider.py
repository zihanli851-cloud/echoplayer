import re
from uuid import uuid4

from app.models.schemas import Question, SpellcheckIssue, UploadedPaper
from app.services.spellcheck.base import SpellcheckProvider


class LocalSpellcheckProvider(SpellcheckProvider):
    """Simple local spellcheck provider for MVP use."""

    provider_name = "local_spellcheck_provider"
    provider_label = "代码版错字检查"
    is_placeholder = False

    DEFAULT_TYPO_DICT = {
        "循序渐近": "循序渐进",
        "按步就班": "按部就班",
        "再接再励": "再接再厉",
        "迫不急待": "迫不及待",
        "惦定": "奠定",
        "必竟": "毕竟",
        "题纲": "提纲",
    }

    REPEATED_PUNCTUATION_PATTERN = re.compile(r"([，。！？；：,.!?;:])\1+")
    REPEATED_CHAR_PATTERN = re.compile(r"([\u4e00-\u9fffA-Za-z])\1+")
    BRACKET_PAIRS = {"（": "）", "(": ")", "【": "】", "《": "》", "“": "”", "‘": "’"}
    REVERSE_BRACKET_PAIRS = {value: key for key, value in BRACKET_PAIRS.items()}
    ALLOWED_DOUBLE_CHAR_WORDS = {
        "人人",
        "天天",
        "看看",
        "渐渐",
        "年年",
        "妈妈",
        "爸爸",
        "星星",
        "往往",
    }

    def __init__(self, typo_dict: dict[str, str] | None = None) -> None:
        self.typo_dict = typo_dict or self.DEFAULT_TYPO_DICT

    def check_questions(
        self,
        paper: UploadedPaper,
        questions: list[Question],
    ) -> list[SpellcheckIssue]:
        """Run typo, punctuation and duplicate-character checks over questions."""

        issues: list[SpellcheckIssue] = []
        for question in questions:
            issues.extend(self._check_typo_dictionary(paper, question))
            issues.extend(self._check_repeated_punctuation(paper, question))
            issues.extend(self._check_unbalanced_punctuation(paper, question))
            issues.extend(self._check_repeated_characters(paper, question))

        return sorted(
            issues,
            key=lambda item: (item.paper_id, item.question_no, item.start_index or 0),
        )

    def _build_issue(
        self,
        paper: UploadedPaper,
        question: Question,
        issue_type: str,
        issue_text: str,
        suggestion: str,
        start_index: int | None = None,
        end_index: int | None = None,
        confidence: float | None = None,
    ) -> SpellcheckIssue:
        """Create one standardized spellcheck issue."""

        return SpellcheckIssue(
            issue_id=uuid4().hex,
            paper_id=paper.paper_id,
            question_id=question.question_id,
            question_no=question.question_no,
            issue_type=issue_type,
            original_text=question.content,
            issue_text=issue_text,
            suggestion=suggestion,
            start_index=start_index,
            end_index=end_index,
            confidence=confidence,
        )

    def _check_typo_dictionary(
        self,
        paper: UploadedPaper,
        question: Question,
    ) -> list[SpellcheckIssue]:
        """Check known common typo phrases against a small local dictionary."""

        issues: list[SpellcheckIssue] = []
        for wrong_word, right_word in self.typo_dict.items():
            start_index = question.content.find(wrong_word)
            if start_index != -1:
                issues.append(
                    self._build_issue(
                        paper=paper,
                        question=question,
                        issue_type="常见错别字",
                        issue_text=wrong_word,
                        suggestion=right_word,
                        start_index=start_index,
                        end_index=start_index + len(wrong_word),
                        confidence=0.95,
                    )
                )
        return issues

    def _check_repeated_punctuation(
        self,
        paper: UploadedPaper,
        question: Question,
    ) -> list[SpellcheckIssue]:
        """Detect repeated punctuation such as '。。' or '！！'."""

        issues: list[SpellcheckIssue] = []
        for match in self.REPEATED_PUNCTUATION_PATTERN.finditer(question.content):
            issue_text = match.group(0)
            suggestion = issue_text[0]
            issues.append(
                self._build_issue(
                    paper=paper,
                    question=question,
                    issue_type="标点重复",
                    issue_text=issue_text,
                    suggestion=suggestion,
                    start_index=match.start(),
                    end_index=match.end(),
                    confidence=0.88,
                )
            )
        return issues

    def _check_unbalanced_punctuation(
        self,
        paper: UploadedPaper,
        question: Question,
    ) -> list[SpellcheckIssue]:
        """Detect obviously unbalanced brackets and quotation marks."""

        issues: list[SpellcheckIssue] = []
        stack: list[tuple[str, int]] = []

        for index, char in enumerate(question.content):
            if char in self.BRACKET_PAIRS:
                stack.append((char, index))
            elif char in self.REVERSE_BRACKET_PAIRS:
                if not stack:
                    issues.append(
                        self._build_issue(
                            paper=paper,
                            question=question,
                            issue_type="标点配对错误",
                            issue_text=char,
                            suggestion="补全或调整配对标点",
                            start_index=index,
                            end_index=index + 1,
                            confidence=0.8,
                        )
                    )
                    continue

                open_char, open_index = stack.pop()
                if self.BRACKET_PAIRS[open_char] != char:
                    issues.append(
                        self._build_issue(
                            paper=paper,
                            question=question,
                            issue_type="标点配对错误",
                            issue_text=question.content[open_index : index + 1],
                            suggestion="检查括号或引号是否成对出现",
                            start_index=open_index,
                            end_index=index + 1,
                            confidence=0.82,
                        )
                    )

        for open_char, open_index in stack:
            issues.append(
                self._build_issue(
                    paper=paper,
                    question=question,
                    issue_type="标点缺失",
                    issue_text=open_char,
                    suggestion=f"补全对应的 {self.BRACKET_PAIRS[open_char]}",
                    start_index=open_index,
                    end_index=open_index + 1,
                    confidence=0.8,
                )
            )

        return issues

    def _check_repeated_characters(
        self,
        paper: UploadedPaper,
        question: Question,
    ) -> list[SpellcheckIssue]:
        """Detect repeated characters such as '的的' or '学学习'."""

        issues: list[SpellcheckIssue] = []
        for match in self.REPEATED_CHAR_PATTERN.finditer(question.content):
            issue_text = match.group(0)
            if issue_text in self.ALLOWED_DOUBLE_CHAR_WORDS:
                continue

            suggestion = issue_text[0]
            issues.append(
                self._build_issue(
                    paper=paper,
                    question=question,
                    issue_type="重复字",
                    issue_text=issue_text,
                    suggestion=suggestion,
                    start_index=match.start(),
                    end_index=match.end(),
                    confidence=0.84,
                )
            )

        return issues
