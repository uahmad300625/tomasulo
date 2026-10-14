"""
Runs every test in tests/ with issue width 1, 2, 3 and 4 and checks it.

For every run:
  1. Functional check: final registers and memory, and the sequence of
     committed instructions, must match a simple in-order reference
     simulator (code/reference.py).
  2. Timing checks (rules the pipeline must never break):
       - in-order issue, at most W instructions issued per cycle
       - in-order commit, at most W instructions committed per cycle
       - at most one result per CDB per cycle
       - EX starts after ISSUE and after the producers' WB
       - load MEM after its EX, WB after EX/MEM, COMMIT after WB
       - memory port (single, non-pipelined) never used by two accesses
       - after a misprediction resolved in cycle n, the next correct-path
         instruction is issued in cycle n+2 or later
       - ROB / reservation station limits respected

The output of every run is written to results/<test>_w<W>.txt and a
summary table to results/summary.md.

usage:  python run_tests.py
"""
import glob
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'code'))

from init import parse_input, BRANCHES          # noqa: E402
from main import simulate                        # noqa: E402
from reference import run_reference              # noqa: E402
from print_status import report, total_cycles    # noqa: E402


def close(a, b):
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def functional_errors(cpu, cfg, program):
    errs = []
    R, F, M, trace = run_reference(cfg, program)
    for i in range(32):
        if not close(cpu.reg_int[i], R[i]):
            errs.append('R%d = %s, expected %s' % (i, cpu.reg_int[i], R[i]))
        if not close(cpu.reg_fp[i], F[i]):
            errs.append('F%d = %s, expected %s' % (i, cpu.reg_fp[i], F[i]))
    for i in range(64):
        if not close(cpu.memory[i], M[i]):
            errs.append('Mem[%d] = %s, expected %s' % (4 * i, cpu.memory[i], M[i]))
    got = [e.pc for e in cpu.committed]
    if got != trace:
        errs.append('committed instruction sequence differs from reference')
    return errs


def timing_errors(cpu):
    cfg = cpu.cfg
    W = cfg.issue_width
    errs = []
    entries = sorted(cpu.all_entries, key=lambda e: e.seq)
    committed = cpu.committed

    # issue: in order, <= W per cycle
    for a, b in zip(entries, entries[1:]):
        if b.issue < a.issue:
            errs.append('out-of-order issue: %s' % b.ins.text)
    for cyc, n in Counter(e.issue for e in entries).items():
        if n > W:
            errs.append('%d instructions issued in cycle %d' % (n, cyc))
    # commit: in order, <= W per cycle, one store per cycle
    for a, b in zip(committed, committed[1:]):
        if b.commit < a.commit or b.seq < a.seq:
            errs.append('out-of-order commit: %s' % b.ins.text)
    for cyc, n in Counter(e.commit for e in committed).items():
        if n > W:
            errs.append('%d instructions committed in cycle %d' % (n, cyc))
    # CDB
    for cyc, n in Counter(e.wb for e in entries if e.wb is not None).items():
        if n > cfg.num_cdb:
            errs.append('%d broadcasts in cycle %d' % (n, cyc))

    for e in committed:
        op = e.ins.op
        t = e.ins.text
        if e.ex_start is None or e.ex_start <= e.issue:
            errs.append('%s: EX not after ISSUE' % t)
        for p in e.producers:
            if p.wb is None:
                continue
            if op == 'Sd' and p.dest == e.ins.src2:
                if e.commit <= p.wb:
                    errs.append('%s: store committed before its data was broadcast' % t)
            elif e.ex_start <= p.wb:
                errs.append('%s: EX started before operand from %s was broadcast'
                            % (t, p.ins.text))
        if op == 'Ld':
            if e.mem_start is None or e.mem_start <= e.ex_end:
                errs.append('%s: MEM not after EX' % t)
            if e.wb is None or e.wb <= e.mem_end:
                errs.append('%s: WB not after MEM' % t)
        elif op not in BRANCHES and op != 'Sd':
            if e.wb is None or e.wb <= e.ex_end:
                errs.append('%s: WB not after EX' % t)
        if op in BRANCHES:
            if e.commit <= e.ex_end:
                errs.append('%s: branch committed before resolution' % t)
        elif op != 'Sd' and e.commit <= e.wb:
            errs.append('%s: COMMIT not after WB' % t)

    # memory port: real accesses (loads that were not forwarded, and stores)
    busy = Counter()
    for e in entries:
        if e.ins.op in ('Ld', 'Sd') and e.mem_start is not None and not e.forwarded:
            for cyc in range(e.mem_start, e.mem_end + 1):
                busy[cyc] += 1
    if any(n > 1 for n in busy.values()):
        errs.append('memory port used by two accesses in the same cycle')

    # misprediction penalty
    for b in committed:
        if b.ins.op in BRANCHES and b.mispredicted:
            for e in entries:
                if e.seq > b.seq and not e.squashed and e.issue < b.ex_end + 2:
                    errs.append('%s issued too early after misprediction of %s'
                                % (e.ins.text, b.ins.text))

    # ROB occupancy (committed instructions only, so this is a lower bound)
    last = total_cycles(cpu)
    for cyc in range(1, last + 1):
        live = sum(1 for e in committed if e.issue <= cyc < e.commit)
        if live > cfg.rob_size:
            errs.append('ROB over capacity in cycle %d' % cyc)
            break
    # unpipelined integer adder: at most #FUs ops in EX in any cycle
    use = Counter()
    for e in entries:
        if e.ins.op in ('Add', 'Addi', 'Sub', 'Beq', 'Bne') and e.ex_start is not None:
            for cyc in range(e.ex_start, e.ex_end + 1):
                use[cyc] += 1
    if any(n > cfg.fus['int'] for n in use.values()):
        errs.append('integer adder over-subscribed')
    return errs


def main():
    out_dir = os.path.join(HERE, 'results')
    os.makedirs(out_dir, exist_ok=True)
    tests = sorted(glob.glob(os.path.join(HERE, 'tests', '*.txt')))
    rows = []
    all_ok = True
    for path in tests:
        name = os.path.splitext(os.path.basename(path))[0]
        row = [name]
        for w in (1, 2, 3, 4):
            cfg, program = parse_input(path)
            cfg.issue_width = w
            cpu = simulate(cfg, program)
            errs = functional_errors(cpu, cfg, program) + timing_errors(cpu)
            with open(os.path.join(out_dir, '%s_w%d.txt' % (name, w)), 'w') as f:
                f.write(report(cpu) + '\n')
            cycles = total_cycles(cpu)
            n = len(cpu.committed)
            status = 'PASS' if not errs else 'FAIL'
            all_ok &= not errs
            print('%-26s width %d: %4d cycles  IPC %.2f  mispredictions %d  %s'
                  % (name, w, cycles, n / cycles, cpu.mispredictions, status))
            for err in errs[:10]:
                print('      ' + err)
            row.append('%d / %.2f' % (cycles, n / cycles))
        rows.append(row)

    with open(os.path.join(out_dir, 'summary.md'), 'w') as f:
        f.write('| Test | W=1 cycles / IPC | W=2 | W=3 | W=4 |\n')
        f.write('|---|---|---|---|---|\n')
        for row in rows:
            f.write('| ' + ' | '.join(row) + ' |\n')

    # Extra experiment (not required by the handout): with a single CDB at
    # most one result is written back per cycle, which caps IPC at ~1. Here
    # the number of CDBs is set equal to the issue width.
    print('\nExperiment: number of CDBs = issue width')
    rows = []
    for path in tests:
        name = os.path.splitext(os.path.basename(path))[0]
        row = [name]
        for w in (1, 2, 3, 4):
            cfg, program = parse_input(path)
            cfg.issue_width = w
            cfg.num_cdb = w
            cpu = simulate(cfg, program)
            errs = functional_errors(cpu, cfg, program) + timing_errors(cpu)
            all_ok &= not errs
            cycles = total_cycles(cpu)
            n = len(cpu.committed)
            print('%-26s width %d, %d CDBs: %4d cycles  IPC %.2f  %s'
                  % (name, w, w, cycles, n / cycles, 'PASS' if not errs else 'FAIL'))
            for err in errs[:10]:
                print('      ' + err)
            if w == 4:
                with open(os.path.join(out_dir, '%s_w4_cdb4.txt' % name), 'w') as f:
                    f.write(report(cpu) + '\n')
            row.append('%d / %.2f' % (cycles, n / cycles))
        rows.append(row)
    with open(os.path.join(out_dir, 'summary_cdb_equals_width.md'), 'w') as f:
        f.write('| Test | W=1, 1 CDB | W=2, 2 CDBs | W=3, 3 CDBs | W=4, 4 CDBs |\n')
        f.write('|---|---|---|---|---|\n')
        for row in rows:
            f.write('| ' + ' | '.join(row) + ' |\n')
    print('\nALL TESTS PASSED' if all_ok else '\nSOME TESTS FAILED')
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
