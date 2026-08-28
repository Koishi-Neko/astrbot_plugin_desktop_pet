import pytest
from main import _strip_image_parts, DesktopPetBridge

def test_strip_image_parts_ignores_non_dicts():
    history = ["not a dict", 123, None]
    original = list(history)
    removed = _strip_image_parts(history)
    assert removed == 0
    assert history == original

def test_strip_image_parts_string_content():
    history = [{"content": "hello world"}, {"content": 123}]
    original = list(history)
    removed = _strip_image_parts(history)
    assert removed == 0
    assert history == original

def test_strip_image_parts_list_no_images():
    history = [{"content": [{"type": "text", "text": "hello"}]}]
    original_content = list(history[0]["content"])
    removed = _strip_image_parts(history)
    assert removed == 0
    assert history[0]["content"] == original_content

def test_strip_image_parts_list_with_images():
    history = [{"content": [
        {"type": "text", "text": "look:"},
        {"type": "image_url", "url": "http://example.com"},
        {"type": "image", "data": "abc"}
    ]}]
    removed = _strip_image_parts(history)
    assert removed == 2
    assert history[0]["content"] == [{"type": "text", "text": "look:"}]

def test_strip_image_parts_all_images():
    history = [{"content": [
        {"type": "image_url", "url": "http://example.com"},
        {"type": "image", "data": "abc"}
    ]}]
    removed = _strip_image_parts(history)
    assert removed == 2
    assert history[0]["content"] == "[图片]"

def test_strip_image_parts_empty_history():
    history = []
    removed = _strip_image_parts(history)
    assert removed == 0
    assert history == []

def test_split_jp_no_tag():
    zh, jp = DesktopPetBridge._split_jp("你好呀")
    assert zh == "你好呀"
    assert jp == ""

def test_split_jp_one_tag():
    zh, jp = DesktopPetBridge._split_jp(" 你好呀 【JP】 こんにちは ")
    assert zh == "你好呀"
    assert jp == "こんにちは"

def test_split_jp_multiple_tags():
    zh, jp = DesktopPetBridge._split_jp("第一句【jp】第二句【JP】第三句")
    assert zh == "第一句"
    assert jp == "第二句【JP】第三句"

def test_split_jp_empty_japanese():
    zh, jp = DesktopPetBridge._split_jp("中文部分【jp】")
    assert zh == "中文部分"
    assert jp == ""

def test_split_jp_surrounding_whitespace():
    zh, jp = DesktopPetBridge._split_jp("  前有空格  【jp】   后有空格   ")
    assert zh == "前有空格"
    assert jp == "后有空格"

def test_parse_blocklist_empty():
    assert DesktopPetBridge._parse_blocklist("") == []
    assert DesktopPetBridge._parse_blocklist(None) == []

def test_parse_blocklist_commas_and_whitespace():
    assert DesktopPetBridge._parse_blocklist("a,b,c") == ["a", "b", "c"]
    assert DesktopPetBridge._parse_blocklist("a，b，c") == ["a", "b", "c"]
    assert DesktopPetBridge._parse_blocklist("a b c") == ["a", "b", "c"]
    assert DesktopPetBridge._parse_blocklist("a , b ， c ") == ["a", "b", "c"]

def test_parse_blocklist_case_folding():
    assert DesktopPetBridge._parse_blocklist("Foo, BAR, BaZ") == ["foo", "bar", "baz"]

def test_parse_blocklist_extra_whitespaces_and_empty_parts():
    assert DesktopPetBridge._parse_blocklist("a,, , b") == ["a", "b"]
