"""LL2 status labels; null results never imply a successful landing."""
import re
from i18n import tr

_STATUS_NAMES = {
    1: 'Go for Launch', 2: 'To Be Determined', 3: 'Launch Successful',
    4: 'Launch Failure', 5: 'On Hold', 6: 'Launch in Flight',
    7: 'Launch was a Partial Failure', 8: 'To Be Confirmed', 9: 'Payload Deployed',
}


def launch_status_text(value) -> str:
    if isinstance(value, dict):
        identifier = value.get('id')
        name = value.get('name')
        if type(identifier) is int and identifier in _STATUS_NAMES:
            return tr(f'launch_state.{identifier}')
    else:
        name = value
    if isinstance(name, str) and name.strip():
        for identifier, known in _STATUS_NAMES.items():
            if name.strip().casefold() == known.casefold():
                return tr(f'launch_state.{identifier}')
        return ' '.join(name.split())
    return tr('launch_state.unknown')


def recovery_states(text: str) -> list[str]:
    """Read saved exact result labels, ignoring stage identities and locations."""
    aliases = {'成功': 'success', '回收成功': 'success', 'successful': 'success',
               '失败': 'failure', '回收失败': 'failure', 'failed': 'failure',
               '部分失败': 'partial', '回收部分失败': 'partial', 'partial failure': 'partial',
               '结果待确认': 'pending', '回收待确认': 'pending', 'awaiting result': 'pending',
               '不尝试回收': 'none', '不回收': 'none', 'no landing attempt': 'none'}
    results = []
    for part in re.split(r'[;；\n]+', text):
        value = re.split(r'[(（]', part.rsplit(':', 1)[-1].rsplit('：', 1)[-1], maxsplit=1)[0]
        if value.strip():
            results.append(aliases.get(value.strip().casefold(), 'unknown'))
    return results


def recovery_text(item: dict) -> str:
    rocket = item.get('rocket')
    if not isinstance(rocket, dict):
        return ''
    rows = []
    for key, vehicle, label in [('launcher_stage', 'launcher', 'recovery_stage'),
                                ('spacecraft_stage', 'spacecraft', 'recovery_spacecraft')]:
        stages = rocket.get(key)
        if not isinstance(stages, list):
            continue
        for index, stage in enumerate(stages, 1):
            if not isinstance(stage, dict):
                continue
            craft = stage.get(vehicle)
            identity = (craft.get('serial_number') or craft.get('name')) if isinstance(craft, dict) else ''
            identity = identity if isinstance(identity, str) else ''
            if vehicle == 'spacecraft' or not identity.strip():
                identity = tr(label, number=index) + (' ' + identity if identity else '')
            landing = stage.get('landing')
            state = 'unknown'
            details = []
            if isinstance(landing, dict):
                attempt, success = landing.get('attempt'), landing.get('success')
                if attempt is False:
                    state = 'not_attempted'
                elif attempt is True:
                    state = 'success' if success is True else 'failure' if success is False else 'pending'
                for field in ('type', 'landing_location'):
                    value = landing.get(field)
                    if isinstance(value, dict):
                        text = value.get('abbrev') or value.get('name')
                        if isinstance(text, str) and text.strip():
                            details.append(' '.join(text.split()))
            result = tr('recovery_state.' + state)
            if details:
                result += ' (' + ' · '.join(details) + ')'
            rows.append(' '.join(identity.split()) + ': ' + result)
    text = '; '.join(rows)
    return text if any(state != 'unknown' for state in recovery_states(text)) else ''
