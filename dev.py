"""Project CLI for routine local work. Each command replaces an agent fact that used to be rediscovered by hand.

  python dev.py test            run the backend suite in the foreground (no-network guard on)
  python dev.py test --bg       same, detached, log in data/_logs/suite.log (survives a 60 s bridge timeout)
  python dev.py run --bg NAME -- python -m qualify.btc_crowd evaluate --root data/x
                                any long job, detached, log in data/_logs/NAME.log
  python dev.py status          every log in data/_logs: age, running/done/failed, last result
  python dev.py wait NAME [--max S]
                                block until a background job ends (default 40 s, fits one tool call even under load): exit 0 done, 1 failed, 2 running.
                                Use this instead of sleep-and-status loops; on 2, stop and check once later
  python dev.py news [--bg]     one Clock 1 collection over the official feeds (same path as the Collect button)
  python dev.py forward record|score [--bg]
                                weekly forward benchmark; record publishes docs/benchmarks/forward-ledger.jsonl
  python dev.py dashboard       restart http://127.0.0.1:8878 when it is idle
"""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / 'data' / '_logs'
SUITE = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.']
NAME = re.compile(r'^[a-z0-9_]{1,40}$')


def env():
    e = dict(os.environ)
    e.update(PYTHONPATH='.', PYTHONIOENCODING='utf-8')
    return e


def log_path(name):
    if not NAME.match(name):
        raise SystemExit('Log names use a-z, 0-9 and _ only.')
    LOGS.mkdir(parents=True, exist_ok=True)
    return LOGS / f'{name}.log'


def quote(arg):
    return '"' + arg + '"' if (' ' in arg or not arg) else arg


def detached_command(argv, log, run_id=''):
    """cmd line for a job that outlives the calling shell and writes everything to its log."""
    inner = ' '.join(quote(a) for a in argv)
    run = f'set DEV_RUN_ID={run_id}&& ' if run_id else ''
    return f'cmd /c cd /d {quote(str(ROOT))} && set PYTHONPATH=.&& set PYTHONIOENCODING=utf-8&& {run}{inner} > {quote(str(log))} 2>&1'


EXIT = 'dev.py exit '
# Every background job gets a fresh run id (sidecar <name>.run, first log line). wait/status trust an exit marker only
# in a log that carries the current id: three times on 11/17 the first `wait suite` after `test --bg` returned the
# PREVIOUS run's result; the cause was not reproduced, so the check makes any stale log harmless instead.
RUN = 'dev.py run '


def run_file(log):
    return Path(log).with_suffix('.run')


def expected_run(log):
    p = run_file(log)
    return p.read_text(encoding='ascii').strip() if p.exists() else None
PY = sys.executable if ' ' not in sys.executable else 'python'
WAIT_MAX = 280      # under the bridge's 300 s process limit
WAIT_DEFAULT = 40   # under the ~60 s a ClickUp tool call survives (measured 11/16); 50 s timed out on 11/17 while a model job loaded the CPU


def child(argv):
    """Run a background job and end its log with an exit marker, so 'finished' is a fact, not a guess from age."""
    ignore_console_breaks()
    print(f'{RUN}{os.environ.get("DEV_RUN_ID", "")}', flush=True)
    code = subprocess.call(argv, cwd=ROOT, env=env())
    print(f'{EXIT}{code}', flush=True)
    return code


def boot_time():
    """Epoch seconds of the last OS start, or None when unknown. A restart kills every job without an exit marker."""
    try:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
            return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000
        return time.time() - float(Path('/proc/uptime').read_text().split()[0])
    except Exception:
        return None


def job_state(text, mtime=None, boot=None, run=None):
    """(state, exit code): ('done', 0), ('failed', n), ('running', None) when the log has no exit marker yet,
    or ('lost', None) when it has none and was last written before the OS restarted (the job was killed).
    With `run`, a log that lacks that run's id line is someone else's output: its exit marker does not count."""
    marks = re.findall(r'^' + re.escape(EXIT) + r'(-?\d+)\s*$', text, flags=re.M)
    if run and not re.search(r'^' + re.escape(RUN + run) + r'\s*$', text, flags=re.M):
        marks = []
    if not marks:
        if mtime is not None and boot is not None and mtime < boot:
            return 'lost', None
        return 'running', None
    code = int(marks[-1])
    return ('done' if code == 0 else 'failed'), code


def wait(name, max_s=WAIT_DEFAULT, tick=2.0):
    """Block until a background job finishes or max_s passes. This is the loop guard for polling: one call, a hard cap,
    and a plain answer (exit 0 done, 1 failed, 2 still running) instead of repeated sleep-and-status calls."""
    printable_stdout()
    log, started, boot = log_path(name), time.monotonic(), boot_time()
    max_s = min(max_s, WAIT_MAX)
    while True:
        text = log.read_text(encoding='utf-8', errors='replace') if log.exists() else ''
        state, code = job_state(text, log.stat().st_mtime if log.exists() else None, boot, expected_run(log))
        if state == 'lost':
            print(f'{name} lost: no exit marker and the log predates the last OS restart. Rerun it (resumable jobs continue).')
            return 1
        if state != 'running':
            print(f'{name} {state}: {summarize(text)}')
            return 0 if code == 0 else 1
        if time.monotonic() - started >= max_s:
            print(f'{name} still running after {max_s:.0f} s. Stop polling; run "python dev.py wait {name}" once later.')
            return 2
        time.sleep(tick)


def start_detached(argv, log):
    import uuid
    run_id = uuid.uuid4().hex[:12]
    run_file(log).write_text(run_id, encoding='ascii')  # before the log, so wait never pairs a new id with an old log
    log.write_text(f'started {time.strftime("%Y-%m-%d %H:%M:%S")}: {" ".join(argv)}\n', encoding='utf-8')
    line = detached_command([PY, 'dev.py', 'child', '--', *argv], log, run_id)
    if os.name == 'nt':
        # WMI starts the process outside the bridge's process tree, so a request timeout cannot kill it.
        r = subprocess.run(['wmic', 'process', 'call', 'create', line], capture_output=True, text=True)
        if 'ReturnValue = 0' in r.stdout:
            return int(re.search(r'ProcessId\s*=\s*(\d+)', r.stdout).group(1))
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(line, shell=True, creationflags=flags, stdin=subprocess.DEVNULL, close_fds=True).pid
    else:
        return subprocess.Popen(['sh', '-c', line.replace('cmd /c ', '')], start_new_session=True, stdin=subprocess.DEVNULL).pid


def summarize(text):
    text = '\n'.join(l for l in text.splitlines() if not l.startswith(EXIT))
    ran = re.findall(r'Ran (\d+) tests? in ([\d.]+)s', text)
    result = re.findall(r'^(OK.*|FAILED.*)$', text, flags=re.M)
    if ran and result:
        return f'{result[-1]} ({ran[-1][0]} tests, {float(ran[-1][1]):.0f} s)'
    lines = [l for l in text.splitlines() if l.strip() and 'ScriptRunContext' not in l]
    return (lines[-1][:160] if lines else '(empty)')


def printable_stdout():
    """Logs hold arbitrary job output (vite prints a check mark); a cp1252 console or pipe must not crash the report."""
    try:
        sys.stdout.reconfigure(errors='replace')
    except AttributeError:  # a test's StringIO
        pass


def status():
    printable_stdout()
    if not LOGS.exists():
        print('No logs yet.')
        return
    boot = boot_time()
    for p in sorted(LOGS.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True):
        mtime = p.stat().st_mtime
        age = int(time.time() - mtime)
        text = p.read_text(encoding='utf-8', errors='replace')
        run = expected_run(p)
        state = job_state(text, mtime, boot, run)[0]
        if state == 'running' and run is None:
            # No run id: dev.py did not start it (a model server's own log, or a job from before run ids), so no
            # exit marker is coming. 11/17: dead llama-server logs were listed as "running" for 13 hours.
            state = 'untracked'
        print(f'{p.stem:22s} {age // 60:4d} min ago  {state:9s} {summarize(text)}')


FORWARD = {'record': ['record', '--root', 'data/forward_benchmark', '--publish', 'docs/benchmarks/forward-ledger.jsonl'],
           'score': ['score', '--root', 'data/forward_benchmark']}


def ignore_console_breaks():
    """The bridge broadcasts Ctrl+C to the console group on timeout; a sealed record must not die halfway."""
    import signal
    for name in ('SIGINT', 'SIGBREAK'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)


def forward(action):
    ignore_console_breaks()
    sys.argv = ['qualify.forward', *FORWARD[action]]
    from qualify.forward import main as forward_main
    forward_main()


def collect_news():
    import json
    import uuid
    ignore_console_breaks()
    from core.chat_service import ChatService
    svc = ChatService(ROOT)
    try:
        svc.collect_news(uuid.uuid4().hex)
        started = time.time()
        while svc.news_thread and svc.news_thread.is_alive() and time.time() - started < 900:
            time.sleep(2)
        snap = svc.news_snapshot()
        print(json.dumps({k: snap.get(k) for k in ('phase', 'stage', 'error')}))
    finally:
        getattr(svc, 'close', lambda: None)()


def dashboard_busy(state):
    return bool(state and (state.get('active_job') or state['news']['phase'] == 'running' or
                           state['models']['phase'] in ('downloading', 'verifying', 'qualifying', 'loading')))


def dashboard_state(timeout=5):
    import http.client
    import json
    try:
        c = http.client.HTTPConnection('127.0.0.1', 8878, timeout=timeout)
        c.request('GET', '/api/state', headers={'Host': '127.0.0.1:8878'})
        return json.loads(c.getresponse().read())
    except (OSError, ValueError):
        return None


def restart_dashboard():
    state = dashboard_state()
    if dashboard_busy(state):
        raise SystemExit('Dashboard is busy (run, collection or model operation); not restarting.')
    out = subprocess.run('netstat -ano', capture_output=True, text=True, shell=True).stdout
    pids = {l.split()[-1] for l in out.splitlines() if '127.0.0.1:8878' in l and 'LISTENING' in l}
    for pid in pids:
        subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], capture_output=True)
    from core.process_guard import cleanup_orphaned_servers
    cleaned = cleanup_orphaned_servers(ROOT)
    if cleaned:
        print('cleaned orphaned Cofinance servers:', cleaned)
    e = env()
    e.pop('COFINANCE_MEMORY', None)
    subprocess.Popen(['cmd.exe', '/k', 'run-dashboard.cmd'], cwd=ROOT, env=e, creationflags=subprocess.CREATE_NEW_CONSOLE)
    for _ in range(60):
        time.sleep(1)
        if dashboard_state(timeout=2) is not None:
            print('dashboard is up at http://127.0.0.1:8878; old pids', sorted(pids))
            return
    raise SystemExit('Dashboard did not answer within 60 s; check its console window.')


def features():
    import json
    data = json.loads((ROOT / 'docs' / 'feature-map.json').read_text(encoding='utf-8'))
    for f in data['features']:
        live = f'  live: {f["live"]}' if f.get('live') else ''
        print(f'{f["id"]:14s} {f["view"]:10s} {f["name"]}\n{"":25s}proof: {", ".join(f["proof"])}{live}')


def preflight_cmd(as_json=False, data_dir=None):
    import json
    from core.ops import run_preflight
    printable_stdout()
    report = run_preflight(ROOT, Path(data_dir) if data_dir else None)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for c in report['checks']:
            print(f'[{c["status"].upper():4s}] {c["id"]:24s} {c["detail"]}')
        print(f'preflight: {"OK" if report["ok"] else "FAILED"} ({report["pass_count"]} pass, {report["warn_count"]} warn, {report["fail_count"]} fail)')
    return 0 if report['ok'] else 1


def backup_cmd(dest=None, data_dir=None):
    import json
    from datetime import datetime, timezone
    from core.ops import backup_state
    printable_stdout()
    src = Path(data_dir) if data_dir else ROOT / 'data'
    if dest:
        out = Path(dest)
    else:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        out = src / '_backups' / f'backup_{stamp}'
    manifest = backup_state(src, out)
    print(json.dumps({'backup_dir': str(out), 'files': len(manifest['files']), 'format': manifest['format']}, indent=2))
    return 0


def restore_cmd(backup_dir, data_dir=None, dry_run=False, force=False):
    import json
    from core.ops import restore_state
    printable_stdout()
    target = Path(data_dir) if data_dir else ROOT / 'data'
    if not dry_run and target.resolve() == (ROOT / 'data').resolve() and dashboard_state(timeout=1) is not None:
        raise SystemExit('Dashboard is running at http://127.0.0.1:8878; stop it before restoring live data/.')
    result = restore_state(Path(backup_dir), target, dry_run=dry_run, force=force)
    print(json.dumps(result, indent=2))
    return 0


def outer_loop_cmd(traces='data/traces', registry_in=None, fail_on_mismatch=False):
    import json
    from qualify.outer_loop import replay_directory, replay_exit_code
    printable_stdout()
    report = replay_directory(
        ROOT / traces if not Path(traces).is_absolute() else Path(traces),
        registry_in=Path(registry_in) if registry_in else None,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return replay_exit_code(report, fail_on_mismatch=fail_on_mismatch)


def memory_eval_cmd(chat_db='data/chat_v2.db', traces='data/traces'):
    import json
    from qualify.memory_eval import evaluate_memory
    printable_stdout()
    cpath = ROOT / chat_db if not Path(chat_db).is_absolute() else Path(chat_db)
    tpath = ROOT / traces if not Path(traces).is_absolute() else Path(traces)
    report = evaluate_memory(cpath, tpath)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['synthetic_plumbing']['passed'] else 1


def spotcheck_cmd(action, corpus='data/news_corpus_20261108', overwrite=False):
    import json
    from qualify.label_spotcheck import prepare_spotcheck, score_spotcheck
    printable_stdout()
    cdir = ROOT / corpus if not Path(corpus).is_absolute() else Path(corpus)
    if action == 'prepare':
        print(json.dumps(prepare_spotcheck(cdir, overwrite=overwrite), indent=2))
        return 0
    print(json.dumps(score_spotcheck(cdir), indent=2))
    return 0


def guard_stats_cmd(traces='data/traces', mode='all', as_json=False, strict=False):
    import json
    from core.guard_telemetry import summarize_guard_directory
    printable_stdout()
    tpath = ROOT / traces if not Path(traces).is_absolute() else Path(traces)
    report = summarize_guard_directory(tpath, mode=mode)
    cov = report['coverage']
    strict_failed = strict and (
        cov['unreadable_files'] > 0 or cov['included_traces'] == 0 or cov['incomplete_traces'] > 0
    )
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if strict_failed else 0
    fpa = report['first_pass_acceptance_rate']
    fpa_str = f'{fpa * 100:.1f}%' if fpa is not None else 'n/a'
    w_share = report['wasted_ms_share']
    w_str = f'{w_share * 100:.1f}%' if w_share is not None else 'n/a'
    print(
        f'Guard telemetry ({cov["included_traces"]}/{cov["chat_traces"]} chat traces included; {cov["total_files"]} files, mode={report["mode_filter"]}): '
        f'pass-1 accepted {report["first_pass_accepted"]}/{report["narrated_checks"]} ({fpa_str}), '
        f'guard retries {report["guard_retries"]} (recovered {report["guard_retry_recovered"]}), '
        f'fallbacks {report["fallbacks"]}, '
        f'wasted passes {report["wasted_passes"]}/{report["total_narrator_passes"]} '
        f'({report["wasted_ms"]:.1f}/{report["total_narrator_ms"]:.1f} ms, {w_str})'
    )
    if cov['included_traces'] == 0:
        print('  status: not benchmarkable (0 eligible traces)')
    if cov["excluded_trace_integrity"]:
        excluded = ', '.join(f'{k}={v}' for k, v in cov["excluded_trace_integrity"].items())
        print(f'  excluded from rates: {excluded}')
    if report['failure_reasons']:
        reasons = ', '.join(f'{k}={v}' for k, v in report['failure_reasons'].items())
        print(f'  failure_reasons: {reasons}')
    if report['bypass_methods']:
        byp = ', '.join(f'{k}={v}' for k, v in report['bypass_methods'].items())
        print(f'  bypass_methods:  {byp}')
    for row in report['by_actor']:
        r_rate = f'{row["first_pass_acceptance_rate"] * 100:.1f}%' if row['first_pass_acceptance_rate'] is not None else 'n/a'
        print(
            f'  {row["actor"]:18s} pass1={row["first_pass_accepted"]}/{row["narrated_checks"]} ({r_rate}) '
            f'retries={row["guard_retries"]} fallbacks={row["fallbacks"]} '
            f'wasted={row["wasted_passes"]}/{row["total_passes"]} ({row["wasted_ms"]:.1f} ms)'
        )
    return 1 if strict_failed else 0


def showcase_preview_cmd():
    from uuid import uuid4

    build = subprocess.call(['npm', 'run', 'build'], cwd=ROOT / 'frontend', env=env(), shell=os.name == 'nt')
    if build:
        return build
    preview_env = env()
    preview_env.update(COFINANCE_PROVIDER_BROWSER_TESTS='1', COFINANCE_NETWORK_TESTS='1',
                       COFINANCE_SHOWCASE_DIR=str(ROOT / 'data' / ('_showcase_' + uuid4().hex[:12])))
    return subprocess.call([PY, '-m', 'unittest', 'tests.test_provider_workflow.TestProviderWorkflow.test_showcase_preview'],
                           cwd=ROOT, env=preview_env)


def verify_cmd(full=False, browser=True):
    printable_stdout()
    steps = [
        ('preflight', [PY, 'dev.py', 'preflight'], ROOT, env()),
        ('parity_self_check', [PY, '-m', 'qualify.parity_oracle', 'self-check'], ROOT, env()),
        (
            'backend_full' if full else 'backend_contracts',
            SUITE if full else [
                PY, '-m', 'unittest',
                'tests.test_system_integration',
                'tests.test_delete_conversations',
                'tests.test_invariants',
                'tests.test_feature_map',
                'tests.test_events_stream',
                'tests.test_trace',
                'tests.test_guard_telemetry',
                'tests.test_dev_cli',
                'tests.test_open_chat',
                'tests.test_research_acceptance',
                'tests.test_evidence_status',
                'tests.test_outer_loop',
                'tests.test_route_smoke',
                'tests.test_single_pass_data',
                'tests.test_concurrent_io',
            ],
            ROOT,
            env(),
        ),
        ('frontend_vitest', ['npm', 'test', '--', '--run'], ROOT / 'frontend', env()),
        ('frontend_build', ['npm', 'run', 'build'], ROOT / 'frontend', env()),
    ]
    if browser:
        benv = env()
        benv['COFINANCE_BROWSER_TESTS'] = '1'
        steps.append((
            'chrome_journeys',
            [PY, '-m', 'unittest', 'tests.test_chat_shell_browser', 'tests.test_run_map_browser'],
            ROOT,
            benv,
        ))
    for label, cmd, cwd, run_env in steps:
        print(f'=== verify: {label} ===', flush=True)
        code = subprocess.call(cmd, cwd=cwd, env=run_env, shell=(os.name == 'nt' and cmd[0] == 'npm'))
        if code != 0:
            print(f'verify FAILED at {label} (exit {code})', flush=True)
            return code
    print('verify: ALL GATES PASSED', flush=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='dev.py')
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('test')
    t.add_argument('--bg', action='store_true')
    t.add_argument('pattern', nargs='?', help='optional test module, e.g. tests.test_invariants')
    r = sub.add_parser('run')
    r.add_argument('--bg', action='store_true', required=True)
    r.add_argument('name')
    r.add_argument('argv', nargs=argparse.REMAINDER)
    sub.add_parser('status')
    n = sub.add_parser('news')
    n.add_argument('--bg', action='store_true')
    f = sub.add_parser('forward')
    f.add_argument('action', choices=sorted(FORWARD))
    f.add_argument('--bg', action='store_true')
    sub.add_parser('dashboard')
    sub.add_parser('features')
    pf = sub.add_parser('preflight')
    pf.add_argument('--json', action='store_true')
    pf.add_argument('--data-dir', default=None)
    bk = sub.add_parser('backup')
    bk.add_argument('--dest', default=None)
    bk.add_argument('--data-dir', default=None)
    rs = sub.add_parser('restore')
    rs.add_argument('backup_dir')
    rs.add_argument('--data-dir', default=None)
    rs.add_argument('--dry-run', action='store_true')
    rs.add_argument('--force', action='store_true')
    sub.add_parser('showcase-preview', help='Capture checked README previews with local models and Yahoo; never publish.')
    ol = sub.add_parser('outer-loop')
    ol.add_argument('--traces', default='data/traces')
    ol.add_argument('--registry-in', default=None)
    ol.add_argument('--fail-on-mismatch', action='store_true',
                    help='Fail on an unapproved diff, unverified source trace, or no replayable cases.')
    me = sub.add_parser('memory-eval')
    me.add_argument('--chat-db', default='data/chat_v2.db')
    me.add_argument('--traces', default='data/traces')
    sc = sub.add_parser('spotcheck')
    sc.add_argument('action', choices=('prepare', 'score'))
    sc.add_argument('--corpus', default='data/news_corpus_20261108')
    sc.add_argument('--overwrite', action='store_true')
    gs = sub.add_parser('guard-stats')
    gs.add_argument('--traces', default='data/traces')
    gs.add_argument('--mode', default='all', choices=('all', 'live', 'fixture', 'replay'))
    gs.add_argument('--json', action='store_true')
    gs.add_argument('--strict', action='store_true')
    vf = sub.add_parser('verify')
    vf.add_argument('--full', action='store_true')
    vf.add_argument('--no-browser', action='store_true')
    w = sub.add_parser('wait')
    w.add_argument('name')
    w.add_argument('--max', type=float, default=WAIT_DEFAULT)
    c = sub.add_parser('child')
    c.add_argument('argv', nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd == 'status':
        return status()
    if a.cmd == 'wait':
        raise SystemExit(wait(a.name, a.max))
    if a.cmd == 'child':
        raise SystemExit(child(a.argv[1:] if a.argv[:1] == ['--'] else a.argv))
    if a.cmd == 'features':
        return features()
    if a.cmd == 'preflight':
        raise SystemExit(preflight_cmd(as_json=a.json, data_dir=a.data_dir))
    if a.cmd == 'backup':
        raise SystemExit(backup_cmd(dest=a.dest, data_dir=a.data_dir))
    if a.cmd == 'restore':
        raise SystemExit(restore_cmd(a.backup_dir, data_dir=a.data_dir, dry_run=a.dry_run, force=a.force))
    if a.cmd == 'outer-loop':
        raise SystemExit(outer_loop_cmd(traces=a.traces, registry_in=a.registry_in, fail_on_mismatch=a.fail_on_mismatch))
    if a.cmd == 'memory-eval':
        raise SystemExit(memory_eval_cmd(chat_db=a.chat_db, traces=a.traces))
    if a.cmd == 'spotcheck':
        raise SystemExit(spotcheck_cmd(a.action, corpus=a.corpus, overwrite=a.overwrite))
    if a.cmd == 'guard-stats':
        raise SystemExit(guard_stats_cmd(traces=a.traces, mode=a.mode, as_json=a.json, strict=a.strict))
    if a.cmd == 'showcase-preview':
        raise SystemExit(showcase_preview_cmd())
    if a.cmd == 'verify':
        raise SystemExit(verify_cmd(full=a.full, browser=not a.no_browser))
    if a.cmd == 'dashboard':
        return restart_dashboard()
    if a.cmd in ('news', 'forward'):
        name = 'news_collect' if a.cmd == 'news' else f'forward_{a.action}'
        if a.bg:
            rest = [] if a.cmd == 'news' else [a.action]
            start_detached([sys.executable if ' ' not in sys.executable else 'python', 'dev.py', a.cmd, *rest], log_path(name))
            print(f'{name} started in the background: python dev.py status')
            return
        return collect_news() if a.cmd == 'news' else forward(a.action)
    if a.cmd == 'test':
        cmd = [sys.executable, '-m', 'unittest', a.pattern] if a.pattern else SUITE
        if a.bg:
            start_detached(cmd, log_path('suite'))
            print('suite started in the background: python dev.py status')
            return
        raise SystemExit(subprocess.call(cmd, cwd=ROOT, env=env()))
    job = [x for x in a.argv if x != '--']
    if not job:
        raise SystemExit('Give the command after --')
    start_detached(job, log_path(a.name))
    print(f'{a.name} started in the background: python dev.py status')


if __name__ == '__main__':
    main()
