import pytest
from main import _strip_image_parts, _strip_think_parts, DesktopPetBridge

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


def test_strip_think_parts_ignores_non_dicts():
    history = ["not a dict", 123, None]
    original = list(history)
    removed = _strip_think_parts(history)
    assert removed == 0
    assert history == original

def test_strip_think_parts_missing_key():
    history = [{"content": "hello"}, {"content": "world", "role": "user"}]
    original = list(history)
    removed = _strip_think_parts(history)
    assert removed == 0
    assert history == original

def test_strip_think_parts_removes_key():
    history = [
        {"content": "hello", "reasoning_content": "thinking..."},
        {"content": "world", "role": "assistant", "reasoning_content": "more thinking..."}
    ]
    removed = _strip_think_parts(history)
    assert removed == 2
    assert history == [
        {"content": "hello"},
        {"content": "world", "role": "assistant"}
    ]

def test_strip_think_parts_mixed():
    history = [
        "not dict",
        {"content": "hello"},
        {"content": "world", "reasoning_content": "thinking..."}
    ]
    removed = _strip_think_parts(history)
    assert removed == 1
    assert history == [
        "not dict",
        {"content": "hello"},
        {"content": "world"}
    ]

import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from datetime import datetime

def test_get_provider_stats_no_file(tmp_path):
    bridge = DesktopPetBridge(MagicMock())
    with patch("main.Path") as mock_path:
        mock_path_obj = MagicMock()
        mock_path_obj.resolve.return_value.parents = [None, None, tmp_path]
        mock_path.return_value = mock_path_obj

        # db does not exist
        res = bridge._get_provider_stats()
        assert res == {"has_data": False}

def test_get_provider_stats_no_table(tmp_path):
    bridge = DesktopPetBridge(MagicMock())
    db_file = tmp_path / "data_v4.db"
    con = sqlite3.connect(str(db_file))
    con.close()

    with patch("main.Path") as mock_path:
        mock_path_obj = MagicMock()
        mock_path_obj.resolve.return_value.parents = [None, None, tmp_path]
        mock_path_obj.__truediv__.return_value = db_file
        mock_path.return_value = mock_path_obj

        res = bridge._get_provider_stats()
        assert res == {"has_data": False}

def test_get_provider_stats_normal(tmp_path):
    bridge = DesktopPetBridge(MagicMock())
    db_file = tmp_path / "data_v4.db"
    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE provider_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME,
            token_input_other INTEGER,
            token_input_cached INTEGER,
            token_output INTEGER,
            time_to_first_token REAL,
            updated_at DATETIME,
            agent_type TEXT,
            status TEXT,
            umo TEXT,
            conversation_id TEXT,
            provider_id TEXT,
            provider_model TEXT,
            start_time REAL,
            end_time REAL
        )
    """)
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    cur.execute("INSERT INTO provider_stats (created_at, token_input_other, token_input_cached, token_output, time_to_first_token) VALUES (?, 10, 5, 20, 1.5)", (today_str,))
    cur.execute("INSERT INTO provider_stats (created_at, token_input_other, token_input_cached, token_output, time_to_first_token) VALUES (?, 30, 0, 40, 2.5)", ("2000-01-01 12:00:00.000000",))
    con.commit()
    con.close()

    with patch("main.Path") as mock_path:
        mock_path_obj = MagicMock()
        mock_path_obj.resolve.return_value.parents = [None, None, tmp_path]
        mock_path_obj.__truediv__.return_value = db_file
        mock_path.return_value = mock_path_obj

        res = bridge._get_provider_stats()
        assert res["has_data"] is True
        stats = res["stats"]

        assert stats["all_time"]["input"] == 40
        assert stats["all_time"]["cached"] == 5
        assert stats["all_time"]["output"] == 60
        assert stats["all_time"]["ttft_avg"] == 2.0

        assert stats["today"]["input"] == 10
        assert stats["today"]["cached"] == 5
        assert stats["today"]["output"] == 20
        assert stats["today"]["ttft_avg"] == 1.5

def test_get_provider_stats_missing_optional_column(tmp_path):
    bridge = DesktopPetBridge(MagicMock())
    db_file = tmp_path / "data_v4.db"
    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    # Table lacks token_input_cached and time_to_first_token
    cur.execute("""
        CREATE TABLE provider_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME,
            token_input_other INTEGER,
            token_output INTEGER
        )
    """)
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    cur.execute("INSERT INTO provider_stats (created_at, token_input_other, token_output) VALUES (?, 10, 20)", (today_str,))
    con.commit()
    con.close()

    with patch("main.Path") as mock_path:
        mock_path_obj = MagicMock()
        mock_path_obj.resolve.return_value.parents = [None, None, tmp_path]
        mock_path_obj.__truediv__.return_value = db_file
        mock_path.return_value = mock_path_obj

        res = bridge._get_provider_stats()
        assert res["has_data"] is True
        stats = res["stats"]

        assert stats["today"]["input"] == 10
        assert stats["today"]["cached"] == 0
        assert stats["today"]["output"] == 20
        assert stats["today"]["ttft_avg"] == 0.0
