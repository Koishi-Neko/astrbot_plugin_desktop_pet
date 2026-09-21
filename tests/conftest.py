import sys
from unittest.mock import MagicMock

def pytest_configure():
    astrbot_mock = MagicMock()
    sys.modules['astrbot'] = astrbot_mock
    sys.modules['astrbot.api'] = astrbot_mock.api
    sys.modules['astrbot.api.event'] = astrbot_mock.api.event
    sys.modules['astrbot.api.message_components'] = astrbot_mock.api.message_components
    sys.modules['astrbot.api.provider'] = astrbot_mock.api.provider
    sys.modules['astrbot.api.star'] = astrbot_mock.api.star
    sys.modules['astrbot.api.web'] = astrbot_mock.api.web

    # We also need to define Star for DesktopPetBridge to inherit from
    class MockStar:
        def __init__(self, context):
            self.context = context

    sys.modules['astrbot.api.star'].Star = MockStar
    sys.modules['astrbot.api.star'].Context = MagicMock()
    sys.modules['astrbot.api.event'].AstrMessageEvent = MagicMock()
    sys.modules['astrbot.api.event'].filter = MagicMock()
    sys.modules['astrbot.api.message_components'].Plain = MagicMock()
    sys.modules['astrbot.api.message_components'].Record = MagicMock()
    sys.modules['astrbot.api.provider'].ProviderRequest = MagicMock()
    sys.modules['astrbot.api.web'].error_response = MagicMock()
    sys.modules['astrbot.api.web'].request = MagicMock()
    sys.modules['astrbot.api'].logger = MagicMock()

    # main.py 从 astrbot.api.event.filter 导入 CustomFilter 作为基类，
    # 必须是真实类（MagicMock 实例不能被继承）
    import types
    event_filter_mod = types.ModuleType('astrbot.api.event.filter')

    class MockCustomFilter:
        def __init__(self, raise_error=True, **kwargs):
            self.raise_error = raise_error

    event_filter_mod.CustomFilter = MockCustomFilter
    sys.modules['astrbot.api.event.filter'] = event_filter_mod

    # 「记住 xxx」指令 handler 用 @filter.event_message_type(filter.EventMessageType.ALL)
    # 注册；改成透传装饰器以便测试直接调用 handler 本体（其余装饰器仍返回 MagicMock）
    class MockEventMessageType:
        GROUP_MESSAGE = "GROUP_MESSAGE"
        PRIVATE_MESSAGE = "PRIVATE_MESSAGE"
        OTHER_MESSAGE = "OTHER_MESSAGE"
        ALL = "ALL"

    _filter = sys.modules['astrbot.api.event'].filter
    _filter.EventMessageType = MockEventMessageType
    _filter.event_message_type = lambda *a, **k: (lambda f: f)
