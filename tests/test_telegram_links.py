from types import SimpleNamespace

from tg_vacancy_bot.telegram.links import get_event_message_link


def test_event_link_uses_chat_id_when_entity_is_not_cached() -> None:
    event = SimpleNamespace(
        chat=None,
        chat_id=-1003320156340,
        message=SimpleNamespace(id=114721),
    )

    assert get_event_message_link(event) == 'https://t.me/c/3320156340/114721'
