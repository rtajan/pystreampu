# Measuring performance

This guide shows how to measure the time spent in each task of a sequence and how to
report values while the sequence runs.

## Overall duration

The simplest measure is the wall-clock time of the execution:

```python
import time

start = time.perf_counter()
seq.exec_n_times(1000)
print(f"{(time.perf_counter() - start) * 1e3:.1f} ms")
```

## Statistics per task

Each task can record statistics about its executions. They have to be enabled
**before** the execution, with the `stats` property of the tasks:

```python
import streampu as spu

src = spu.source_random(8, dtype=spu.int64)
inc = spu.incrementer(8, dtype=spu.int64)
out_data, _ = src.generate()
out = inc.increment(out_data)
seq = spu.Sequence(out)

for task in seq.get_tasks_per_threads()[0]:
    task.stats = True

seq.exec_n_times(1000)
seq.show_stats()
```

`show_stats` prints one line per task (columns shortened here):

```text
#        MODULE NAME |         TASK NAME | REP | ORDER |   TIMER ||    CALLS |     TIME |   PERC || ...
# -------------------|-------------------|-----|-------|---------||----------|----------|--------||
#             Source |          generate | yes |     0 |       * ||     1001 |     0.00 |  60.28 || ...
#        Incrementer |         increment | yes |     1 |       * ||     1001 |     0.00 |  39.72 || ...
# -------------------|-------------------|-----|-------|---------||----------|----------|--------||
#              TOTAL |                 * | yes |     * |       * ||     1001 |     0.00 | 100.00 || ...
```

The full table also gives the throughput and the latency of each task (average,
minimum and maximum). Here `generate` and `increment` were called 1001 times: once
when the graph was described, then 1000 times by the sequence.

The statistics are also available from Python, for each task:

| Attribute | Meaning |
|-----------|---------|
| `task.n_calls` | number of executions |
| `task.total_duration` | total time spent in the task (s) |
| `task.avg_duration`, `task.min_duration`, `task.max_duration` | average, minimum and maximum duration of an execution (s) |
| `task.reset()` | reset the statistics |

!!! note
    With a multithreaded sequence, each thread has its own copies of the tasks:
    enable the statistics on all of them.

    ```python
    for tasks in seq.get_tasks_per_threads():
        for task in tasks:
            task.stats = True
    ```

    For a [pipeline](../tutorial/parallelism.md#splitting-the-graph-into-a-pipeline),
    `pipeline.show_stats()` prints one table per stage.

!!! warning
    Collecting statistics has a small cost. Disable them when measuring the overall
    performance.

These statistics give averages per task. For the distribution of the latency of the
whole sequence or pipeline (percentiles, jitter, worst cases), see
[Real-time statistics](realtime.md).

## Reporting values with probes

**Probes** are modules that observe the graph during its execution: the value of a
socket, the number of executions, the elapsed time… Each probe fills a column of a
**reporter**, and a **terminal** displays the reporters.

In the following example, a counter is incremented at each execution of the sequence
and its value is reported, together with the execution number and the elapsed time.

```python
import streampu as spu

with spu.bind_only():                    # describe the graph without executing it
    count = spu.array([0], dtype=spu.int32)
    count += 1                           # incremented at each execution

    reporter = spu.Reporter_probe("Counter", "A simple example")
    prb_exec = spu.Probe_occurrence("Exec.", reporter)
    prb_count = spu.probe_value(1, "Count", reporter, spu.int32)
    prb_time = spu.Probe_time("Time", reporter)
    prb_time.set_col_unit("(s)")

    prb_count(count)                     # the value probe reads the socket
    prb_exec()                           # the other probes have no input socket:
    prb_exec.probe.bind(count)           # bind their task to the graph so that they
    prb_time()                           # are part of the sequence
    prb_time.probe.bind(count)

terminal = spu.Terminal_dump([reporter])
seq = spu.Sequence(count)

terminal.legend()
seq.exec_n_times(5, terminal)
terminal.report()
```

The terminal prints the title of the reporter, the name and unit of each column, then
one line per execution.

The appearance of a column is set on its probe: `set_col_unit`, `set_col_prec`
(precision), `set_col_size` (width) and `set_col_fmtflags`, for instance
`prb.set_col_fmtflags(spu.FormatFlags.fixed)`.

Available probes:

| Probe | Reports |
|-------|---------|
| `spu.probe_value(size, name, reporter, dtype)` | the values of a socket |
| `spu.Probe_occurrence(name, reporter)` | the number of executions |
| `spu.Probe_time(name, reporter)` | the elapsed time |
| `spu.Probe_timestamp(name, reporter)` | a timestamp |
| `spu.Probe_throughput(data_size, name, reporter)` | a throughput |
| `spu.Probe_latency(name, reporter)` | a latency |

Their behavior is the one of the corresponding C++ probes, described in the
[StreamPU documentation](https://aff3ct.github.io/streampu).
