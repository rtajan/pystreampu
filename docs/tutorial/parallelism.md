# Running in parallel

StreamPU extracts two kinds of parallelism from a streaming application
([Cassagne et al., 2023](https://doi.org/10.1002/cpe.7820)):

- **spatial parallelism**: the whole sequence is duplicated on several threads, each
  thread processing its own frames;
- **temporal parallelism**: the graph is split into a **pipeline** of stages that run
  concurrently, like an assembly line.

Both are available from Python. The execution itself happens in C++ and the Python GIL
is released while a sequence or a pipeline runs.

## Duplicating a sequence on several threads

### Describe the graph

We build a chain of six incrementers between an `initializer` (which outputs constant
data) and a `finalizer` (which keeps the last data it received). Each incrementer
sleeps 5 µs per execution (`ns=5000`) to simulate some work.

```python
import time
import streampu as spu

data_length = 2048
n_threads = 4

initializer = spu.initializer(data_length, dtype=spu.uint8)
incs = [spu.incrementer(data_length, ns=5000, dtype=spu.uint8) for _ in range(6)]
finalizer = spu.finalizer(data_length, dtype=spu.uint8)

x = initializer.initialize()
y = x
for inc in incs:
    y = inc.increment(y)
finalizer.finalize(y)
```

### Build a multithreaded sequence

The second argument of `spu.Sequence` is the number of threads:

```python
seq = spu.Sequence(x.task, n_threads)
```

The same sequence can be described in a `with spu.sequence()` block (see
[Building a Sequence](sequence.md#describe-the-sequence-in-a-with-block)), in place of
the calls above:

```python
with spu.sequence(n_threads=n_threads) as seq:
    y = initializer.initialize()
    for inc in incs:
        y = inc.increment(y)
    finalizer.finalize(y)
```

When `n_threads > 1`, StreamPU **clones** every module of the graph so that each thread
works on its own copy. All the built-in modules used here can be cloned, as well as the
[`Stateless` modules written in Python](../guides/python_module.md).

!!! warning
    Some modules cannot be cloned, typically sources and sinks that read or write a
    file (`source_user_binary`, `sink_user_binary`…). They cannot be part of a
    multithreaded sequence: use a [pipeline](#splitting-the-graph-into-a-pipeline)
    to keep them on a single thread.

### Work with the clones

The modules you created are the ones of the first thread. To reach the copies used by
every thread, use `get_cloned_modules`. Here, each thread gets different initial data:

```python
for tid, ini in enumerate(seq.get_cloned_modules(initializer)):
    ini.data_init[0][:] = tid   # first (and only) frame of this thread
```

Then run the sequence and measure its duration:

```python
start = time.perf_counter()
seq.exec_n_times(1000)
print(f"Elapsed: {time.perf_counter() - start:.3f} s")

for tid, fin in enumerate(seq.get_cloned_modules(finalizer)):
    print(tid, fin.final_data[0][:4])   # each thread outputs tid + 6
```

The chain sleeps 6 × 5 µs per execution, so 1000 executions take about 30 ms on one
thread. With 4 threads, it ideally drops to 30 / 4 ≈ 7.5 ms. Try
different values of `n_threads` to see how the duration evolves on your machine
(`spu._ext.get_hardware_concurrency()` gives the number of hardware threads).

### Useful options

| Option | Effect |
|--------|--------|
| `seq.n_frames = 4` | each execution processes 4 frames per thread |
| `seq.no_copy_mode = True` | avoid data copies between tasks when possible |
| `spu.Sequence(..., thread_pinning=True)` | pin each thread to a core |

## Splitting the graph into a pipeline

A pipeline cuts the graph into **stages**. Each stage is a sequence running on its own
thread(s), and consecutive stages exchange frames through synchronization buffers. This
is the way to combine non-clonable modules (sources, sinks…) with a multithreaded
processing.

In the example below, `source_user_binary` reads the data from a file, six `relayer`
modules (which copy their input to their output after a short sleep) process it, and
`sink_user_binary` writes it to another file.

```python
import os
import streampu as spu

spu.Signal_handler.init()

data_length = 2048
n_threads = 4

with open("file.in", "wb") as f:          # some data to process
    f.write(os.urandom(data_length * 100))

source = spu.source_user_binary(data_length, "file.in", auto_reset=False, dtype=spu.uint8)
sink = spu.sink_user_binary(data_length, "file.out", dtype=spu.uint8)
rlys = [spu.relayer(data_length, ns=5000, dtype=spu.uint8) for _ in range(6)]
```

### Describe the pipeline stage by stage

The `with spu.pipeline()` block holds the pipeline. Inside it, each `with spu.stage()`
block describes one stage, in order. The tasks called in a stage belong to that stage:

```python
with spu.pipeline(buffer_size=16) as pipeline:
    with spu.stage():                     # stage 0: the source (1 thread)
        data, count = source.generate()

    with spu.stage(n_threads=n_threads):  # stage 1: the relayers
        for rly in rlys:
            data = rly.relay(data)

    with spu.stage():                     # stage 2: the sink (1 thread)
        sink.send_count(data, count)

pipeline.exec()
```

This pipeline has three stages: the source on one thread, the six relayers duplicated on
`n_threads` threads, and the sink on one thread. `pipeline.exec()` returns when the
source reaches the end of the file (`auto_reset=False`). The output file is then
identical to the input file.

Inside the `with spu.pipeline()` block:

- the tasks are only bound, not executed: `Task.call_auto_exec` is `False` until the end
  of the block, and then gets its previous value back. Otherwise, calling
  `source.generate()` would already consume the first frame of the file;
- StreamPU finds the first tasks, the last tasks and the excluded tasks of each stage from
  the bindings, including the bindings between two stages;
- a task belongs to a single stage. It joins a stage when it is called there, or when one
  of its sockets is bound there (`rly.relay["in"].bind(data)`);
- a task without input (a source, a constant…) can be created before the block. It then
  joins the stage of its first consumer;
- a stage can only depend on earlier stages. Binding a task to a task of a later stage
  raises an error.

At the end of the block, StreamPU builds the `spu.Pipeline`. The object returned by
`spu.pipeline()` then passes every attribute and method on to it (`exec()`,
`n_frames`, `export_dot(...)`, `show_stats()`, `get_modules(...)`, `stages`…).
`pipeline.pipeline` returns the `spu.Pipeline` itself, and `print(pipeline)` shows the
stages and the buffers between them.

#### Stage and buffer options

A buffer sits at the **input** of each stage except the first. Its options are given to
the stage that receives the frames. `spu.pipeline()` gives their default values:

| Option | Where | Effect |
|--------|-------|--------|
| `buffer_size` | `pipeline()` (default `1`), `stage()` | number of frames the input buffer of the stage can hold |
| `active_waiting` | `pipeline()` (default `False`), `stage()` | wait for the buffer in a loop (`True`: lower latency, but a core stays busy) or asleep (`False`) |
| `n_threads` | `stage()` (default `1`) | number of threads of the stage |
| `thread_pinning` | `stage()` (default `False`) | pin the threads of the stage (needs StreamPU built with hwloc) |
| `puids` | `stage()` | processing unit of each thread of the stage |

```python
with spu.pipeline(buffer_size=16) as pipeline:
    with spu.stage():
        data, count = source.generate()
    with spu.stage(n_threads=4):                 # input buffer: 16 frames, asleep
        data = rlys[0].relay(data)
    with spu.stage(buffer_size=1, active_waiting=True):
        sink.send_count(data, count)             # input buffer: 1 frame, in a loop
```

`buffer_size` and `active_waiting` are not allowed on the first stage, because it has no
input buffer.

#### Dependencies without data

`Task.bind(...)` makes a task wait for another one without exchanging any data. Inside a
`with spu.pipeline()` block, StreamPU takes these dependencies into account when it
splits the stages:

```python
with spu.stage(n_threads=4):
    a = rlys[0].relay(data)
    b = rlys[1].relay(data)
    rlys[1].relay.bind(rlys[0].relay)   # rlys[1].relay runs after rlys[0].relay
```

Out of the block, `Task.bind` is the usual method.

### Building the pipeline explicitly

`spu.Pipeline(...)` builds the same pipeline from explicit lists. The first tasks, the
last tasks and the excluded tasks of each stage then have to be given by hand. The graph
is described with the automatic execution disabled:

```python
with spu.bind_only():   # describe the graph without executing it
    data, count = source.generate()
    for rly in rlys:
        data = rly.relay(data)
    sink.send_count(data, count)

pipeline = spu.Pipeline(
    [source.generate],    # first tasks of the graph
    [sink.send_count],    # last tasks of the graph
    [                     # stages: [first tasks, last tasks, excluded tasks]
        [[source.generate], [source.generate], []],
        [[rlys[0].relay], [rlys[-1].relay], []],
        [[sink.send_count], [sink.send_count], []],
    ],
    [1, n_threads, 1],    # number of threads per stage
    [16, 16],             # size of the buffers between consecutive stages
    [False, False],       # active waiting between stages
)
pipeline.exec()
```

The lists of stages, numbers of threads and pinnings have one element per stage. The
buffer sizes and the active waitings have one element per pair of consecutive stages.

Like a sequence, a pipeline offers `n_frames`, `export_dot(...)`, `show_stats()` and
`get_modules(...)`. Its `stages` property gives access to the sequence of each stage.

## Python modules and the GIL

The built-in modules are written in C++ and run in parallel without any restriction.
Modules written in Python (see [Writing a module in Python](../guides/python_module.md))
execute Python code in each thread: with a regular Python interpreter, the GIL lets only
one of them run at a time. With a free-threaded Python (3.14t, run with `PYTHON_GIL=0`),
they can run truly in parallel. The example `examples/custom_python_module` compares
these configurations.
