"""Tests for RuleCorrector module"""
import pytest
import tempfile
from pathlib import Path

from src.core.hotword.rule_corrector import RuleCorrector


@pytest.fixture
def corrector():
    """Create a RuleCorrector with common rules"""
    c = RuleCorrector()
    c.update_rules("""
        毫安时 = mAh
        伏特 = V
        赫兹 = Hz
        摄氏度 = °C
    """)
    return c


class TestRuleCorrector:
    def test_initialization(self):
        """Test basic initialization"""
        c = RuleCorrector()
        assert len(c.patterns) == 0

    def test_update_rules(self, corrector):
        """Test updating rules"""
        assert len(corrector.patterns) == 4

    def test_update_rules_with_comments(self):
        """Test that comments are ignored"""
        c = RuleCorrector()
        count = c.update_rules("""
            # This is a comment
            毫安时 = mAh
            # Another comment
            伏特 = V
        """)
        assert count == 2

    def test_substitute_unit(self, corrector):
        """Test unit substitution"""
        result = corrector.substitute("这款手机有5000毫安时的电池")
        assert "5000mAh" in result

    def test_substitute_multiple(self, corrector):
        """Test multiple substitutions"""
        result = corrector.substitute("电压12伏特，频率50赫兹")
        assert "12V" in result
        assert "50Hz" in result

    def test_substitute_no_match(self, corrector):
        """Test text with no matching rules"""
        original = "今天天气不错"
        result = corrector.substitute(original)
        assert result == original

    def test_substitute_empty(self, corrector):
        """Test empty input"""
        assert corrector.substitute("") == ""
        assert corrector.substitute(None) == ""

    def test_regex_pattern(self):
        """Test regex pattern matching"""
        c = RuleCorrector()
        c.update_rules(r"(\d+)\s*度 = \1°")
        result = c.substitute("温度是25度")
        assert "25°" in result

    def test_substitute_with_info(self, corrector):
        """Test substitute with replacement info"""
        text, replacements = corrector.substitute_with_info("电池5000毫安时")
        assert "5000mAh" in text
        assert len(replacements) > 0
        assert replacements[0][0] == "毫安时"
        assert replacements[0][1] == "mAh"

    def test_load_rules_file(self):
        """Test loading rules from file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write("测试 = TEST\n")
            f.write("示例 = EXAMPLE\n")
            temp_path = Path(f.name)

        try:
            c = RuleCorrector()
            count = c.load_rules_file(str(temp_path))
            assert count == 2

            result = c.substitute("这是一个测试示例")
            assert "TEST" in result
            assert "EXAMPLE" in result
        finally:
            temp_path.unlink()

    def test_load_nonexistent_file(self):
        """Test loading from nonexistent file"""
        c = RuleCorrector()
        count = c.load_rules_file("nonexistent.txt")
        assert count == 0

    def test_repository_hot_rules_cover_gov_meeting_terms(self):
        """Repository default rules should cover representative gov-meeting strong replacements."""
        c = RuleCorrector()
        rules_path = Path(__file__).resolve().parent.parent / "data" / "hotwords" / "hot-rules.txt"
        count = c.load_rules_file(str(rules_path))
        assert count > 0

        cases = {
            "坚决防止数字面试工程": "面子工程",
            "推进人工智能家政应用": "人工智能+政务",
            "我们通过政企同办理": "政企通",
            "统一入口是我的常舟": "我的常州",
            "政策支持免申既享": "免申即享",
            "材料已进入电子征兆库": "电子证照库",
            "登录依赖统一身份认正": "统一身份认证",
            "依托数据共享交互平台推进": "数据共享交换平台",
            "项目通过苏彩云采购": "苏采云",
        }

        for original, expected in cases.items():
            result = c.substitute(original)
            assert expected in result

    def test_repository_hot_rules_cover_ai_gov_meeting_terms(self):
        """Repository default rules should cover conservative AI+gov meeting strong replacements."""
        c = RuleCorrector()
        rules_path = Path(__file__).resolve().parent.parent / "data" / "hotwords" / "hot-rules.txt"
        count = c.load_rules_file(str(rules_path))
        assert count > 0

        cases = {
            "推进AI加政务建设": "AI+政务",
            "推进人工智能加政务应用": "人工智能+政务",
            "当前存在政务大模形风险": "政务大模型",
            "需要防止算法备安遗漏": "算法备案",
            "要避免模型幻税问题": "模型幻觉",
            "要防止鱼料中毒风险": "语料中毒",
            "要避免算法偏建": "算法偏见",
        }

        for original, expected in cases.items():
            result = c.substitute(original)
            assert expected in result

    def test_repository_hot_rules_cover_ai_gov_meeting_fixed_phrases(self):
        """Repository default rules should cover fixed AI+gov meeting phrase misrecognitions."""
        c = RuleCorrector()
        rules_path = Path(__file__).resolve().parent.parent / "data" / "hotwords" / "hot-rules.txt"
        count = c.load_rules_file(str(rules_path))
        assert count > 0

        cases = {
            "第一次提出了人工智能加政府": "人工智能+政务",
            "根据国国办要求推进": "国办",
            "不要做夸张性的宣传": "夸大性宣传",
            "不要做发展性的宣传": "夸大性宣传",
            "必须部署在政府万嘛": "政府外网",
        }

        for original, expected in cases.items():
            result = c.substitute(original)
            assert expected in result

    def test_repository_hot_rules_cover_ai_gov_meeting_platform_terms(self):
        """Repository default rules should cover conservative AI platform / term misrecognitions."""
        c = RuleCorrector()
        rules_path = Path(__file__).resolve().parent.parent / "data" / "hotwords" / "hot-rules.txt"
        count = c.load_rules_file(str(rules_path))
        assert count > 0

        cases = {
            "推进生成式人工只能治理": "生成式人工智能",
            "系统提出人工智能加增方案": "人工智能+政务",
            "系统提出人工智能加政方案": "人工智能+政务",
            "现场参观AI自助服务体验中心": "智能政务AI自助服务体验中心",
        }

        for original, expected in cases.items():
            result = c.substitute(original)
            assert expected in result

    def test_repository_hot_rules_cover_ai_gov_meeting_governance_terms(self):
        """Repository default rules should cover conservative governance-term misrecognitions."""
        c = RuleCorrector()
        rules_path = Path(__file__).resolve().parent.parent / "data" / "hotwords" / "hot-rules.txt"
        count = c.load_rules_file(str(rules_path))
        assert count > 0

        cases = {
            "还要避免产生模型患水": "模型灌水",
            "还要避免产生模型灌者": "模型灌水",
            "不要再出现模型不导": "模型误导",
            "要防止这个出现鱼料不毒": "语料中毒",
            "要防止这个出现鱼料过毒": "语料中毒",
        }

        for original, expected in cases.items():
            result = c.substitute(original)
            assert expected in result

    def test_repository_hot_rules_cover_gov_meeting_fixed_sentence_terms(self):
        """Repository default rules should cover low-risk fixed sentence-level gov meeting corrections."""
        c = RuleCorrector()
        rules_path = Path(__file__).resolve().parent.parent / "data" / "hotwords" / "hot-rules.txt"
        count = c.load_rules_file(str(rules_path))
        assert count > 0

        cases = {
            "政务服务中的电子工程": "政务服务中的面子工程",
            "政务服务中的面试工程": "政务服务中的面子工程",
            "国务院这个文员总的": "国务院这个文件总的",
            "国国办这个文案里面": "国办这个文件里面",
            "国国办这个文件里面": "国办这个文件里面",
        }

        for original, expected in cases.items():
            result = c.substitute(original)
            assert expected in result

    def test_invalid_regex(self):
        """Test handling of invalid regex patterns"""
        c = RuleCorrector()
        c.update_rules(r"[invalid = replacement")  # Invalid regex
        # Should not raise, just skip the invalid pattern
        result = c.substitute("some text")
        assert result == "some text"
