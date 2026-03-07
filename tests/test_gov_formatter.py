from src.core.text_processor.gov_formatter import format_gov_numbers


def test_gov_formatter_cn_date_to_iso():
    assert format_gov_numbers("今天是2025年5月7日") == "今天是2025-05-07"
    assert format_gov_numbers("今天是2025年05月07号") == "今天是2025-05-07"


def test_gov_formatter_sep_date_to_iso():
    assert format_gov_numbers("日期2025/5/7") == "日期2025-05-07"
    assert format_gov_numbers("日期2025.05.07") == "日期2025-05-07"
    assert format_gov_numbers("日期2025-5-7") == "日期2025-05-07"


def test_gov_formatter_doc_no_brackets_normalize():
    assert format_gov_numbers("常政办发[2025] 12 号") == "常政办发〔2025〕12号"
    assert format_gov_numbers("苏政办发（2025）0012号") == "苏政办发〔2025〕12号"


def test_gov_formatter_money_and_percent_spacing_normalize():
    assert format_gov_numbers("总投资1.2 亿 元") == "总投资1.2亿元"
    assert format_gov_numbers("金额350 万元，完成率50 %") == "金额350万元，完成率50%"


def test_gov_formatter_item_code_normalize():
    assert format_gov_numbers("事项编码 320400 123456") == "事项编码：320400123456"


def test_gov_formatter_idempotent():
    s = "常政办发[2025] 12 号，日期2025年5月7日，总投资1.2 亿 元"
    once = format_gov_numbers(s)
    twice = format_gov_numbers(once)
    assert once == twice


def test_text_post_processor_applies_gov_formatter_after_itn():
    from src.core.text_processor.post_processor import PostProcessorSettings, TextPostProcessor

    p = TextPostProcessor(
        PostProcessorSettings(
            itn_enable=True,
            gov_format_enable=True,
            zh_convert_enable=False,
            punc_convert_enable=False,
        )
    )
    assert p.process("今天是二零二五年五月七日") == "今天是2025-05-07"


def test_text_post_processor_process_final_skips_punc_restore_but_formats_numbers():
    from src.core.text_processor.post_processor import PostProcessorSettings, TextPostProcessor

    p = TextPostProcessor(
        PostProcessorSettings(
            itn_enable=True,
            gov_format_enable=True,
            punc_restore_enable=True,  # should be ignored by process_final
            zh_convert_enable=False,
            punc_convert_enable=False,
        )
    )
    # NOTE: qj2bj will normalize Chinese comma "，" into "," by default.
    assert p.process_final("常政办发[2025] 12 号，日期二零二五年五月七日") == "常政办发〔2025〕12号,日期2025-05-07"
