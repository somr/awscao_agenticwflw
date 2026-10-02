"""Stable publication identity independent of editable comment text and locations."""
import hashlib
import json


def finding_comment_marker(report: dict, finding_id: str) -> str:
    snapshot = report['snapshot']
    identity = [report['run_id'], snapshot['repository'], snapshot['pr_number'],
                snapshot['base_sha'], snapshot['head_sha'], finding_id]
    token = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return f'<!-- cao-source-finding:{token} -->'
