#!/usr/bin/env python3
"""
skill_lifecycle.py — NIGHTLY adapter for skill-router/manage.py

Single-target skill archive/recall with JSON output and NIGHTLY-compatible exit codes.
Exit 0 = action taken, 1 = no candidate, 3 = safety rejected.

Usage:
  python3 skill_lifecycle.py --propose              # JSON: best archive/recall candidate
  python3 skill_lifecycle.py --apply --name X --action archive|recall --source claude|agents
  python3 skill_lifecycle.py --revert --name X --action archive|recall --source claude|agents
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser('~/.agents/skills/skill-router/scripts'))))
from manage import (
    ALWAYS_KEEP, SKILL_DIRS, cmd_status,
    get_session_files, list_skills, load_protected, move_skill, scan_usage,
)

MIN_ACTIVE_SKILLS = 5
ARCHIVE_DAYS = 30
RECALL_DAYS = 14
RECALL_MIN_HITS = 3


def propose():
    """Find the single best archive or recall candidate. Prints JSON to stdout."""
    files = get_session_files(ARCHIVE_DAYS)
    usage = scan_usage(files)
    protected = load_protected() | ALWAYS_KEEP

    # Collect archive candidates: active skills with 0 usage in window
    archive_candidates = []
    for source in ('claude', 'agents'):
        for name in list_skills(source, 'active'):
            if name in protected:
                continue
            if usage.get(name, 0) == 0:
                skill_path = SKILL_DIRS[source]['active'] / name
                size = sum(f.stat().st_size for f in skill_path.rglob('*') if f.is_file())
                archive_candidates.append({
                    'name': name, 'source': source, 'action': 'archive',
                    'hits': 0, 'days': ARCHIVE_DAYS, 'size_bytes': size,
                })

    # Collect recall candidates: archived skills with usage signals
    recall_files = get_session_files(RECALL_DAYS)
    recall_usage = scan_usage(recall_files)
    all_active = set()
    for source in ('claude', 'agents'):
        all_active.update(list_skills(source, 'active'))

    recall_candidates = []
    for source in ('claude', 'agents'):
        for name in list_skills(source, 'archive'):
            if name in all_active:
                continue
            hits = recall_usage.get(name, 0)
            if hits >= RECALL_MIN_HITS:
                recall_candidates.append({
                    'name': name, 'source': source, 'action': 'recall',
                    'hits': hits, 'days': RECALL_DAYS, 'size_bytes': 0,
                })

    # Pick best: recall with most hits first, then archive with largest size (most token savings)
    recall_candidates.sort(key=lambda x: x['hits'], reverse=True)
    archive_candidates.sort(key=lambda x: x['size_bytes'], reverse=True)

    pick = None
    if recall_candidates:
        pick = recall_candidates[0]
    elif archive_candidates:
        pick = archive_candidates[0]

    if not pick:
        print(json.dumps({'status': 'no_candidate', 'archive_pool': 0, 'recall_pool': 0}))
        sys.exit(1)

    pick['status'] = 'proposed'
    pick['archive_pool'] = len(archive_candidates)
    pick['recall_pool'] = len(recall_candidates)
    print(json.dumps(pick))
    sys.exit(0)


def apply_action(name: str, action: str, source: str):
    """Execute a single archive or recall. Exit 0=ok, 3=rejected."""
    protected = load_protected() | ALWAYS_KEEP

    if name in protected:
        print(json.dumps({'status': 'rejected', 'reason': 'protected'}))
        sys.exit(3)

    if action == 'archive':
        # Safety: don't reduce active count below minimum
        active_count = sum(len(list_skills(s, 'active')) for s in ('claude', 'agents'))
        if active_count <= MIN_ACTIVE_SKILLS:
            print(json.dumps({'status': 'rejected', 'reason': f'active_count={active_count} <= {MIN_ACTIVE_SKILLS}'}))
            sys.exit(3)
        ok = move_skill(name, source, 'active', 'archive')
    elif action == 'recall':
        ok = move_skill(name, source, 'archive', 'active')
    else:
        print(json.dumps({'status': 'rejected', 'reason': f'unknown action: {action}'}))
        sys.exit(3)

    if not ok:
        print(json.dumps({'status': 'rejected', 'reason': 'move_skill failed (not found or symlink)'}))
        sys.exit(3)

    print(json.dumps({'status': 'applied', 'name': name, 'action': action, 'source': source}))
    sys.exit(0)


def revert_action(name: str, action: str, source: str):
    """Undo a previous apply. archive→recall back, recall→archive back."""
    if action == 'archive':
        ok = move_skill(name, source, 'archive', 'active')
    elif action == 'recall':
        ok = move_skill(name, source, 'active', 'archive')
    else:
        print(json.dumps({'status': 'error', 'reason': f'unknown action: {action}'}))
        sys.exit(1)

    if not ok:
        print(json.dumps({'status': 'error', 'reason': 'revert move_skill failed'}))
        sys.exit(1)

    print(json.dumps({'status': 'reverted', 'name': name, 'action': action, 'source': source}))
    sys.exit(0)


def main():
    ap = argparse.ArgumentParser(description='NIGHTLY skill lifecycle adapter')
    ap.add_argument('--propose', action='store_true', help='Find best archive/recall candidate')
    ap.add_argument('--apply', action='store_true', help='Execute archive or recall')
    ap.add_argument('--revert', action='store_true', help='Undo a previous apply')
    ap.add_argument('--name', help='Skill name')
    ap.add_argument('--action', choices=['archive', 'recall'], help='archive or recall')
    ap.add_argument('--source', choices=['claude', 'agents'], help='Skill source directory')
    args = ap.parse_args()

    if args.propose:
        propose()
    elif args.apply:
        if not all([args.name, args.action, args.source]):
            ap.error('--apply requires --name, --action, --source')
        apply_action(args.name, args.action, args.source)
    elif args.revert:
        if not all([args.name, args.action, args.source]):
            ap.error('--revert requires --name, --action, --source')
        revert_action(args.name, args.action, args.source)
    else:
        ap.error('One of --propose, --apply, --revert required')


if __name__ == '__main__':
    main()
