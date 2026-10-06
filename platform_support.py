"""Keep official QQ identities separate while retaining existing OneBot account keys."""
SUPPORTED_PLATFORMS = frozenset({'aiocqhttp', 'qq_official', 'qq_official_webhook'})
OFFICIAL_PLATFORMS = SUPPORTED_PLATFORMS - {'aiocqhttp'}


def owner_key(event):
    platform_id = str(event.get_platform_id())
    sender_id = str(event.get_sender_id())
    if event.get_platform_name() in OFFICIAL_PLATFORMS:
        platform = getattr(getattr(event, 'bot', None), 'platform', None)
        appid = str(getattr(platform, 'appid', '') or platform_id)
        return f'official:{platform_id}:{appid}:{sender_id}'
    return platform_id + ':' + sender_id


def card_hint(event, title='光遇'):
    if event.get_platform_name() in OFFICIAL_PLATFORMS:
        event.set_extra('qq_official_card', {'family': 'sky', 'title': title})
