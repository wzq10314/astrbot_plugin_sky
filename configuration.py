DEFAULTS = {
    'candle_tokens': '{}',
    'admins': [], 'kevcore_api_key': '', 'ovoav_api_key': '', 't1qq_api_key': '',
    'height_provider': 'ovoav', 'api_cooldown': 30, 'height_daily_limit': 10,
    'wings_provider': 'ovoav', 'gifts_provider': 'ovoav',
    'request_timeout': 40, 'report_images': True, 'push_at_all': False,
    'daily_times': '01:00,06:00,12:00,18:00',
    'grandma_times': '07:55,09:55,11:55,15:55,17:55,19:55,21:55',
    'sacrifice_times': '00:00,04:00,08:00,12:00,16:00,20:00',
    'shard_times': '00:01',
    'shard_advance_reminder': True,
    'daily_text': '每日任务自动推送', 'grandma_text': '老奶奶干饭提醒：还有五分钟开始啦！',
    'sacrifice_text': '每周献祭已刷新！', 'shard_text': '碎石提醒',
    'daily_image': '', 'grandma_image': '', 'sacrifice_image': '', 'shard_image': '',
}


def load(config):
    import re
    result = dict(DEFAULTS)
    result.update({k: config[k] for k in DEFAULTS if k in config})
    for key, default in DEFAULTS.items():
        if type(default) is bool:
            result[key] = result[key] if type(result[key]) is bool else default
        elif type(default) is int:
            result[key] = max(1, min(600, int(result[key])))
        elif type(default) is str:
            result[key] = str(result[key]).strip()
    if result['height_provider'] not in ('ovoav', 'kevcore'):
        raise ValueError('height_provider must be ovoav or kevcore')
    for field, choices in [('wings_provider', ('ovoav', 'kevcore')), ('gifts_provider', ('ovoav', 't1qq'))]:
        if result[field] not in choices:
            raise ValueError(field + ' has an unsupported provider')
    for key in ('daily', 'grandma', 'sacrifice', 'shard'):
        for value in filter(None, result[key + '_times'].split(',')):
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value.strip()):
                raise ValueError(key + '_times must contain HH:MM values')
    result['admins'] = [str(x) for x in result['admins']]
    return result
