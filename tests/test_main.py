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


def test_parse_keywords_empty():
    assert DesktopPetBridge._parse_keywords("") == []
    assert DesktopPetBridge._parse_keywords(None) == []


def test_parse_keywords_one_per_line():
    assert DesktopPetBridge._parse_keywords("看看屏幕\n我在干嘛\n") == ["看看屏幕", "我在干嘛"]


def test_parse_keywords_preserves_inner_spaces():
    # 行内空格必须保留（英文短语）；首尾空白裁剪；空行丢弃
    assert DesktopPetBridge._parse_keywords("look at my screen\n  what am i doing  \n\n") == [
        "look at my screen",
        "what am i doing",
    ]


def test_parse_keywords_crlf():
    assert DesktopPetBridge._parse_keywords("看看屏幕\r\n我在干嘛") == ["看看屏幕", "我在干嘛"]


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
from unittest.mock import patch, MagicMock
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

import types

def test_rewrite_pet_identity_happy_path():
    bridge = DesktopPetBridge(MagicMock(), {"master_name": "Jules", "master_qq": "12345"})
    req = types.SimpleNamespace()

    req.extra_user_content_parts = [
        types.SimpleNamespace(text="User ID: desktop_pet, Nickname: desktop_pet"),
        types.SimpleNamespace(text="desktop_pet is cool"),
        types.SimpleNamespace(text=None)
    ]
    req.prompt = "Hello User ID: desktop_pet, Nickname: desktop_pet"
    req.system_prompt = "System: User ID: desktop_pet, Nickname: desktop_pet"
    req.contexts = [
        {"content": "Context: User ID: desktop_pet, Nickname: desktop_pet"},
        {"content": [{"text": "Part: User ID: desktop_pet, Nickname: desktop_pet"}, {"text": "Just desktop_pet"}]}
    ]

    bridge._rewrite_pet_identity(req)

    assert req.extra_user_content_parts[0].text == "User ID: 12345, Nickname: Jules"
    assert req.extra_user_content_parts[1].text == "Jules is cool"
    assert req.extra_user_content_parts[2].text is None

    assert req.prompt == "Hello User ID: 12345, Nickname: Jules"
    assert req.system_prompt == "System: User ID: 12345, Nickname: Jules"
    assert req.contexts[0]["content"] == "Context: User ID: 12345, Nickname: Jules"
    assert req.contexts[1]["content"][0]["text"] == "Part: User ID: 12345, Nickname: Jules"
    assert req.contexts[1]["content"][1]["text"] == "Just Jules"

def test_rewrite_pet_identity_contexts():
    bridge = DesktopPetBridge(MagicMock(), {"master_name": "Jules", "master_qq": "12345"})
    req = types.SimpleNamespace()

    # Non-dict segments, non-str values
    req.contexts = [
        {"content": [{"not_text": "User ID: desktop_pet, Nickname: desktop_pet"}]},
        {"content": "User ID: desktop_pet, Nickname: desktop_pet"},
        "not a dict",
        {"content": [{"text": 123}]}, # non-str text
        {"content": 123},
        {"other": "User ID: desktop_pet, Nickname: desktop_pet"}
    ]

    bridge._rewrite_pet_identity(req)

    assert req.contexts[0]["content"][0]["not_text"] == "User ID: desktop_pet, Nickname: desktop_pet"
    assert req.contexts[1]["content"] == "User ID: 12345, Nickname: Jules"
    assert req.contexts[2] == "not a dict"
    assert req.contexts[3]["content"][0]["text"] == 123
    assert req.contexts[4]["content"] == 123
    assert req.contexts[5]["other"] == "User ID: desktop_pet, Nickname: desktop_pet"

def test_rewrite_pet_identity_no_false_positives():
    bridge = DesktopPetBridge(MagicMock(), {"master_name": "Jules", "master_qq": "12345"})
    req = types.SimpleNamespace()

    # Test identical return string instances
    original_str = "No session id here"
    req.extra_user_content_parts = [types.SimpleNamespace(text=original_str)]
    req.prompt = None
    req.system_prompt = 123
    # contexts is missing entirely

    bridge._rewrite_pet_identity(req)

    assert req.extra_user_content_parts[0].text is original_str
    assert req.prompt is None
    assert req.system_prompt == 123

def test_rewrite_pet_identity_fallbacks():
    # Empty config should use default fallbacks
    bridge = DesktopPetBridge(MagicMock(), {})
    req = types.SimpleNamespace()

    req.prompt = "User ID: desktop_pet, Nickname: desktop_pet"
    bridge._rewrite_pet_identity(req)
    assert req.prompt == "User ID: master, Nickname: 主人"

    # Custom session ID
    bridge = DesktopPetBridge(MagicMock(), {"master_name": "Jules", "master_qq": "12345", "pet_session_id": "custom_pet"})
    req = types.SimpleNamespace()

    req.prompt = "User ID: custom_pet, Nickname: custom_pet"
    bridge._rewrite_pet_identity(req)
    assert req.prompt == "User ID: 12345, Nickname: Jules"

def test_rewrite_pet_identity_pin_intended_behavior():
    # Document that bare substring hits of the session ID are replaced by the master name
    bridge = DesktopPetBridge(MagicMock(), {"master_name": "Jules", "master_qq": "12345"})
    req = types.SimpleNamespace()

    req.prompt = "I am talking to desktop_pet, who is my friend desktop_pet."
    bridge._rewrite_pet_identity(req)

    # Bare occurrences are replaced by aggressive string replacement
    assert req.prompt == "I am talking to Jules, who is my friend Jules."
