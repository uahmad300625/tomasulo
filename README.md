# Tomasulo Simulator with Multiple Issue

A cycle-level simulator of Tomasulo's algorithm with a reorder buffer
(ROB), register renaming (RAT), speculative execution with a 1-bit branch
predictor and an 8-entry BTB, and a load/store queue with store-to-load
forwarding. It issues **1 to 4 instructions per cycle, in order**.

It started from the provided single-issue Python simulator
(`tomasulo-python`, first commit of this repository). The report
([REPORT.md](REPORT.md) / [report.pdf](report.pdf)) describes the changes
and the results.

## Requirements

Python 3.6 or newer. No other packages.

## How to run

```bash
cd code
python main.py                                       # runs code.in
python main.py ../tests/test4_loop.txt               # any input file
python main.py ../tests/test4_loop.txt -w 4          # override the issue width (1-4)
python main.py ../tests/test1_independent.txt --cdb 2   # optional: more than one CDB
```

Run every test at issue widths 1, 2, 3 and 4 and check the results:

```bash
python run_tests.py          # from the repository root
```

This writes each run's output to `results/<test>_w<width>.txt` and the
summary tables to `results/summary.md` and
`results/summary_cdb_equals_width.md`.

## Input file format

The input file is case-insensitive and commas are optional. Lines starting
with `#` or `//` are ignored, so the table header line is skipped.

```
                # of rs     Cycles in EX    Cycles in Mem   # of FUs
Integer adder     2             1                             1
FP adder          3             3                             1
FP multiplier     2             20                            1
Load/store unit   3             1               4             1

ROB entries = 128
Issue width = 2          (1-4, default 1)
CDB = 1                  (optional, default 1)
R1=10, R2=20, F2=30.1
Mem[4]=1, Mem[8]=2, Mem[12]=3.4

Add.d F1, F2, F3
Ld F4, 8(R1)
Bne R2, R3, -3
```

For the load/store unit, `# of rs` is the number of load/store queue
entries. Registers and memory start at zero unless they're set in the
file. R0 is always 0.

Supported instructions: `Ld`, `Sd`, `Beq`, `Bne`, `Add`, `Addi`, `Sub`,
`Add.d`, `Sub.d`, `Mult.d`. A branch offset counts instructions:
`Bne Rs, Rt, offset` branches to `PC + 4 + (offset << 2)`.

## Output

- **Instruction status table:** the cycle of ISSUE, EX, MEM, WB and COMMIT
  for every committed instruction. For a store, MEM is the memory write,
  which happens at commit.
- **Register values:** R0–R31 and F0–F31.
- **Non-zero memory values.**
- **Summary:** total cycles, IPC, branch mispredictions, and the squashed
  wrong-path instructions.

## Repository layout

```
code/
  main.py          main loop, command line
  init.py          data structures, input file parser
  issue.py         ISSUE: in-order multi-issue, renaming, branch prediction
  exe.py           EX: functional units, address calculation, branch resolution
  branch.py        BTB / 1-bit predictor update, misprediction recovery (new)
  mem.py           MEM: load/store queue, forwarding, memory access
  wb.py            WB: CDB broadcast
  commit.py        COMMIT: in-order multi-commit, stores write memory
  print_status.py  output format
  reference.py     in-order functional simulator used to check the results (new)
  code.in          default input
tests/             test inputs (test0 ... test7)
results/           output of every test at widths 1-4 (made by run_tests.py)
run_tests.py       runs and checks all tests
REPORT.md, report.pdf
data_structure.txt
```
