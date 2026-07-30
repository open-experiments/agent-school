#!/usr/bin/env python3
"""Sync course walkthrough durations after (re)producing the mp4s.

Reads each course mp4 with ffprobe and rewrites:
  - docs/index.html      COURSES[].len values (card badge + walkthrough button)
  - docs/tests/site.test.js   expected-duration map
  - <course>/README.md   the "A narrated walkthrough (M:SS)" line

Run from the repo root after `python3 video/produce.py video/specs/<c>.json`:
  python3 video/sync-durations.py          # apply
  python3 video/sync-durations.py --check  # verify only (exit 1 on drift)
"""
import json, re, subprocess, sys

DIRS = {'101': '101-noc-assistant', '201': '201-rca-investigator', '202': '202-fraud-triage',
        '301': '301-closed-loop-netops', '302': '302-energy-optimizer'}

def mmss(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                          '-of', 'json', path], capture_output=True, text=True, check=True)
    sec = float(json.loads(out.stdout)['format']['duration'])
    return f"{int(sec // 60)}:{int(sec % 60):02d}"

def main():
    check = '--check' in sys.argv
    durs = {c: mmss(f"{d}/images/{d}.mp4") for c, d in DIRS.items()}
    print('measured:', durs)
    drift = False

    p = 'docs/index.html'; s = open(p).read()
    for c, v in durs.items():
        pat = re.compile(r'(\{id:"%s",dir:"[^"]+",title:"[^"]+",len:")([0-9:]+)(")' % c)
        m = pat.search(s)
        if not m: sys.exit(f'FATAL: course {c} len not found in {p}')
        if m.group(2) != v:
            drift = True
            print(f'{p}: {c} {m.group(2)} -> {v}')
            if not check: s = pat.sub(r'\g<1>%s\g<3>' % v, s)
    if not check: open(p, 'w').write(s)

    p = 'docs/tests/site.test.js'; s = open(p).read()
    for c, v in durs.items():
        pat = re.compile(r"('%s': '▶ )([0-9:]+)(')" % c)
        m = pat.search(s)
        if not m: sys.exit(f'FATAL: course {c} expectation not found in {p}')
        if m.group(2) != v:
            drift = True
            print(f'{p}: {c} {m.group(2)} -> {v}')
            if not check: s = pat.sub(r'\g<1>%s\g<3>' % v, s)
    # the 301 detail-view literal (F1 walkthrough button check)
    pat = re.compile(r"(walk\.includes\(')([0-9:]+)('\))")
    m = pat.search(s)
    if m and m.group(2) != durs['301']:
        drift = True
        print(f"{p}: 301 button literal {m.group(2)} -> {durs['301']}")
        if not check: s = pat.sub(r'\g<1>%s\g<3>' % durs['301'], s)
    if not check: open(p, 'w').write(s)

    for c, d in DIRS.items():
        p = f'{d}/README.md'; s = open(p).read()
        pat = re.compile(r'(A narrated walkthrough \()([0-9]+:[0-9]{2})(\))')
        m = pat.search(s)
        if not m: sys.exit(f'FATAL: walkthrough duration not found in {p}')
        if m.group(2) != durs[c]:
            drift = True
            print(f'{p}: {m.group(2)} -> {durs[c]}')
            if not check:
                s = pat.sub(r'\g<1>%s\g<3>' % durs[c], s, count=1)
                open(p, 'w').write(s)

    if check and drift: sys.exit('DRIFT: site/tests/READMEs out of sync with mp4 durations')
    print('check OK: site, tests, and READMEs match mp4 durations' if check else 'synced.')

if __name__ == '__main__':
    main()
