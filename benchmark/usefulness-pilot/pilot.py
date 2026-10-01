"""Source-labelled experimental pilot; not agent-v1 and not Android runtime validation."""

def score(case, facts):
    ids = {fact['id'] for fact in facts}
    covered = sum(bool(ids.intersection(group)) for group in case['groups'])
    return {'covered': covered, 'total': len(case['groups']), 'complete': covered == len(case['groups'])}
