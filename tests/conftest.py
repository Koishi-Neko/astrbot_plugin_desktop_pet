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
        pass

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
