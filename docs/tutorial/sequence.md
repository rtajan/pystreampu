# Building and running a Sequence

In the previous pages, tasks were executed one by one, by hand. This is perfect to
explore StreamPU but a streaming application runs the same processing over and over.
A **Sequence** captures the graph of bound tasks and replays it as many times as needed,
on one or several threads.

## Describe the graph

We keep the example of the previous pages: a source generates 8 random bits and an
incrementer adds one to each of them.

```python
>>> import streampu as spu
>>> src = spu.source_random(8, dtype=spu.int64)
>>> inc = spu.incrementer(8, dtype=spu.int64)
>>> out_data, _ = src.generate()
>>> out = inc.increment(out_data)
```

Remember that calling a task does two things: it **binds** its sockets to the given
sockets and it **executes** the task once. After these lines, `src.generate` and
`inc.increment` are bound together: they form a graph.

!!! tip
    If you only want to describe the graph, without executing anything, call the
    tasks in a `with spu.bind_only():` block, or pass `auto_exec=False` to each call
    (see [Going further](going_further.md)).

## Build the Sequence

The simplest way to build a sequence is to give it the socket(s) you are interested in:

```python
>>> seq = spu.Sequence(out)
```

From `out`, StreamPU walks the bindings backwards until it finds the **first tasks**
of the graph (tasks without input sockets, such as `src.generate`). The sequence then
contains every task reachable from these first tasks.

Other ways to build a sequence:

| Call | Meaning |
|------|---------|
| `spu.Sequence(out)` | graph leading to the socket `out` |
| `spu.Sequence([s1, s2])` | graph leading to several sockets |
| `spu.Sequence(src.generate)` | graph starting from the task `src.generate` |
| `spu.Sequence(first_task, last_task)` | part of the graph between two tasks |

You can check the boundaries of a sequence with its `first_tasks` and `last_tasks`
properties. They hold one list of tasks per thread:

```python
>>> [t.name for t in seq.first_tasks[0]]
['generate']
>>> [t.name for t in seq.last_tasks[0]]
['increment']
```

## Describe the sequence in a `with` block

A sequence can also be described in a `with spu.sequence()` block. The tasks called in
the block make up the sequence, and StreamPU builds it at the end of the block:

```python
>>> src = spu.source_random(8, dtype=spu.int64)
>>> inc = spu.incrementer(8, dtype=spu.int64)
>>> with spu.sequence() as seq:
...     out_data, _ = src.generate()
...     out = inc.increment(out_data)
>>> seq.exec_seq()
```

Inside the block:

- the tasks are only bound, not executed: `Task.call_auto_exec` is `False` until the end
  of the block, and then gets its previous value back;
- StreamPU finds the first tasks (no input bound) and the last tasks (no consumer in the
  block) from the bindings. A task also joins the sequence when one of its sockets is
  bound in the block (`inc.increment["in"].bind(out_data)`), or when it is bound to
  another task with `Task.bind(...)`;
- a task without input (a source, a constant…) created before the block joins the
  sequence when a task of the block uses it. A task with inputs created before the block
  raises an error;
- the tasks out of the block that are bound to the tasks of the block (for instance by an
  earlier description) are left out of the sequence.

`spu.sequence()` takes the options of `spu.Sequence`: `n_threads`, `thread_pinning`,
`sequence_pinning_policy` and `tasks_inplace`. The object it returns passes every
attribute and method on to the sequence (`exec_seq()`, `exec_n_times(n)`, `n_frames`,
`get_cloned_modules(...)`…), and `seq.sequence` returns the `spu.Sequence` itself, for
instance to give it to a `spu.Set`.

## Execute the Sequence

A sequence offers several ways of being executed:

| Method | Behavior |
|--------|----------|
| `seq.exec_seq()` | executes the whole graph **once** |
| `seq.exec_n_times(n)` | executes the graph `n` times |
| `seq.exec()` | executes the graph until a module says it is done (for instance a source that reached the end of its file) |
| `seq.exec_step(tid)` | executes the next task only, for step-by-step debugging |

Let's run our sequence once and look at the sockets:

```python
>>> seq.exec_seq()
>>> out_data
socket([[0 1 1 0 0 1 0 1]], dtype=int64, name=out_data, task=Source_random.generate)
>>> out
socket([[1 2 2 1 1 2 1 2]], dtype=int64, name=out, task=Incrementer.increment)
```

Each execution generates new random bits, and `out` always holds `out_data` plus one.

To follow what happens during several executions, enable the `debug` mode of the tasks
(see [First steps](first_steps.md#debug-mode-activation)):

```python
>>> src.generate.debug = True
>>> inc.increment.debug = True
>>> seq.exec_n_times(3)
```

Each task now prints its sockets at each of the 3 executions.

!!! warning
    `seq.exec()` has no stop condition of its own. With a source that never ends, such
    as `source_random`, it runs forever. Call `spu.Signal_handler.init()` beforehand so
    that `Ctrl+C` stops the execution cleanly.

## A sequence replays the graph

A sequence replays the tasks **on the current content of the sockets**. Most of the time
this is what you want, but be careful with in-place operators (`+=`, `*=`, …): they
modify the data of the socket itself.

```python
>>> x = spu.array([1, 2, 3])
>>> x += 1     # executed now: x = [[2 3 4]]
>>> x += 1     # executed now: x = [[3 4 5]]
>>> x *= 2     # executed now: x = [[6 8 10]]
>>> seq = spu.Sequence(x)
>>> seq.exec_seq()
>>> x
```

The result is `[[16 20 24]]` and not `[[6 8 10]]`: the three operations were executed a
first time when they were written, then replayed by the sequence on `[[6 8 10]]`
(`6+1+1 = 8`, `8×2 = 16`, …).

To get the expected result, reset the data before running the sequence, for instance
through a NumPy view of the socket:

```python
>>> x = spu.array([1, 2, 3])
>>> x_view = x.numpy
>>> x += 1
>>> x += 1
>>> x *= 2
>>> seq = spu.Sequence(x)
>>> x_view[:] = [1, 2, 3]   # restore the initial values
>>> seq.exec_seq()
>>> x.numpy
array([[ 6,  8, 10]], dtype=int64)
```

## Process several frames at once

Socket data is 2D: one row per **frame**. By default a task processes one frame per
execution. Setting `n_frames` on the sequence makes each execution process several
frames, which reduces the overhead per frame:

```python
>>> seq = spu.Sequence(out)
>>> seq.n_frames = 4
>>> seq.exec_seq()
>>> out.numpy.shape
(4, 8)
```

## Inspect a sequence

Two tools help to understand and tune a sequence:

- **Export the graph** in the Graphviz DOT format, then render it, for instance with
  `dot -Tsvg seq.dot -o seq.svg`:

    ```python
    >>> seq.export_dot("seq.dot")
    ```

- **Display statistics** about the duration of each task. Statistics have to be enabled
  on the tasks before the execution:

    ```python
    >>> for task in seq.get_tasks_per_threads()[0]:
    ...     task.stats = True
    >>> seq.exec_n_times(1000)
    >>> seq.show_stats()
    ```

See the guides [Visualizing a graph](../guides/visualize.md) and
[Measuring performance](../guides/profiling.md) for more details.

## Next step

So far, the sequence runs on a single thread. The [next page](parallelism.md) shows how
to run it on several threads and how to split it into a pipeline.
