"""Comparison projections for an explicitly authorized definition Save.

Never replace original GETs or their hashes with this projection. A Save may
regenerate a schedule job ID without changing when or what will run. All other
fields, including unrelated IDs, list order and missing/null distinctions stay
significant. Pre-Save observations must continue to compare their original IDs.
"""
import copy

from app.delivery_search.contracts import DeliveryRefused, canonical


def saved_definition(definition):
    value = copy.deepcopy(definition)
    triggers = value.get('triggers')
    if isinstance(triggers, list):
        for trigger in triggers:
            if not isinstance(trigger, dict) or trigger.get('triggerType') != 'schedule':
                continue
            schedules = trigger.get('schedules')
            if isinstance(schedules, list):
                for schedule in schedules:
                    if isinstance(schedule, dict):
                        schedule.pop('scheduleJobId', None)
    return value


def require_setup_restored(before, after, *, principals=None):
    """Shared full-readback setup restoration check for future owner helpers.

    Passing principals permits only the approved identity-continuity client IDs
    while Readers are removed. Omitting them requires the original clients.
    """
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    try:
        required = {'definition', 'variableGroups', 'groupPermission', 'groupChecks', 'unfinishedBuilds'} | {
            role + suffix for role in ('test', 'prod', 'verify') for suffix in ('', 'Permission', 'Checks')}
        if set(left) != required or set(right) != required:
            raise DeliveryRefused('checks')
        if (right['definition']['queueStatus'] != 'disabled'
                or right['unfinishedBuilds'] != {'count': 0, 'value': []}):
            raise DeliveryRefused('checks')
        for value in (left, right):
            value['definition'] = saved_definition(value['definition'])
            for key in ('_links', 'url', 'revision', 'createdDate'):
                value['definition'].pop(key, None)
            value['definition'].get('repository', {}).get('properties', {}).pop('lastUpdated', None)
            for role in ('test', 'prod', 'verify'):
                value[role].pop('modificationDate', None)
        if principals is not None:
            if set(principals) != {'test', 'prod', 'verify'}:
                raise DeliveryRefused('identity')
            for role in ('test', 'prod', 'verify'):
                left[role]['authorization']['parameters']['serviceprincipalid'] = principals[role]['client_id']
        if canonical(left) != canonical(right):
            raise DeliveryRefused('checks')
    except (KeyError, TypeError, AttributeError) as exc:
        raise DeliveryRefused('checks') from exc
