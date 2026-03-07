import pytest
from src.core.hotword.corrector import PhonemeCorrector

@pytest.fixture
def corrector():
    c = PhonemeCorrector(threshold=0.8, similar_threshold=0.6)
    # Gov-meeting oriented hotwords (Changzhou / digital gov).
    c.update_hotwords("政企通\n我的常州\n常州市数据局\n苏采云\nAIGC")
    return c

def test_chinese_correction(corrector):
    """测试中文热词纠错"""
    result = corrector.correct("我们用政企同办理事项")
    assert "政企通" in result.text

def test_english_correction(corrector):
    """测试英文热词纠错"""
    result = corrector.correct("推进aigc治理能力提升")
    assert "AIGC" in result.text

def test_similar_phoneme_matching(corrector):
    """测试相似音素匹配（采/彩）"""
    result = corrector.correct("我们用苏彩云来做采购")
    assert "苏采云" in result.text

def test_no_false_positive(corrector):
    """测试不误纠正"""
    result = corrector.correct("今天天气不错")
    assert result.text == "今天天气不错"

def test_correction_result_structure(corrector):
    """测试返回结构"""
    result = corrector.correct("我们用政企同办理")
    assert hasattr(result, 'text')
    assert hasattr(result, 'matches')
    assert hasattr(result, 'similars')

def test_update_hotwords():
    """测试更新热词"""
    c = PhonemeCorrector()
    count = c.update_hotwords("测试\n热词")
    assert count == 2

def test_empty_input(corrector):
    """测试空输入"""
    result = corrector.correct("")
    assert result.text == ""


def test_hotword_alias_to_canonical():
    """别名命中时应替换为 canonical（支持 'a|b|c' 语法）"""
    c = PhonemeCorrector(threshold=0.8, similar_threshold=0.6)
    c.update_hotwords("政企通2.0|政企通二点零\n政企通|政企同")

    result = c.correct("我们用政企同办理")
    assert "政企通" in result.text

    result2 = c.correct("我们用政企通二点零办理")
    assert "政企通2.0" in result2.text


def test_hotword_auto_numeric_variants():
    """数字/小数热词应自动生成常见中文读法变体，提高召回率"""
    c = PhonemeCorrector(threshold=0.8, similar_threshold=0.6)
    c.update_hotwords("政企通2.0")

    # No explicit alias provided; should still match via auto variants.
    result = c.correct("我们用政企通二点零办理")
    assert "政企通2.0" in result.text
