# Going further with StreamPU in Python
Now that you are familiar with the basic concepts of StreamPU, including the `module`, `task`, and `socket` abstractions, we will show how the previous steps can be simplified.

## Build the modules
We keep the same example: suppose that we want to randomly generate `8` bits from a Bernoulli distribution and increment these bits.
As we have seen in the previous steps, we can build the modules as follows:
```python
>>> src = spu.source_random(8, dtype = spu.int64)
>>> src.generate.debug = True
>>> inc = spu.incrementer(8, dtype=spu.int64)
>>> inc.increment.debug = True
```

In the previous steps, we have seen how to connect the modules and execute the graph. However, we can simplify this process by "calling" a `task` directly as a method of the `module`.
Calling a task **binds** its sockets **and executes** it.

## Using a `task` as a method of the `module`
Schematically, we can call a `task` with 2 input sockets and 3 forward sockets as a method of the `module` as follows:
```python
>>> outputs = module.task(s0, s1, sf0, sf1, sf2)
```
where

- `s0` and `s1` are output or forward sockets of another module that will be bound to the two input sockets of `module.task`
- `sf0`, `sf1`, and `sf2` are three output or forward sockets bound to the corresponding forward sockets of `module.task`.
- `outputs` is a tuple of the output sockets of `module.task` (or a single socket if the task has only one output socket).

The arguments are bound to the input and forward sockets **in the order of their creation**, i.e. the order of `task.sockets` once the output sockets are removed. Use `spu.help(module.task)` to check this order.

Back to our first example, we want to call the `increment` with the output named `out_data` of the `src` module as input. Let's see how to do this.
First we need to get the output socket of the `src` module.
```python
>>> out_data = src.generate['out_data']
```
Then, we can call the `increment` task as a method of the `inc` module as follows:
```python
>>> inc["increment::in"]
socket([], dtype=int64, name=in, task=Incrementer.increment)
>>> out = inc.increment(out_data)
# Incrementer::increment(const int64 in[8], int64 out[8])
# {IN}  in  = [    0,     0,     0,     0,     0,     0,     0,     0]
# {OUT} out = [    1,     1,     1,     1,     1,     1,     1,     1]
# Returned status: [0 'SUCCESS']
```
As we can see in the above example, the input socket `increment::in` is empty at the beginning. This is because this socket is dangling and not connected to any output socket. After calling `inc.increment`, the input socket is automatically bound to the output socket `src.generate['out_data']` (named `out_data` in the example). After this step, both sockets share the same data. Since `src.generate` has not been executed yet, the output socket `out_data` is still filled with 0. However, calling `inc.increment` has also executed the task `increment`, indeed `out` is filled with 1.

To check that the binding step has been done correctly, we can execute both tasks again as in the first steps.
```python
>>> src.generate.exec()
# Source_random::generate(int64 out_data[8], uint32 out_count[1])
# {OUT} out_data  = [    0,     1,     1,     0,     1,     1,     1,     1]
# {OUT} out_count = [    8]
# Returned status: [0 'SUCCESS']
>>> inc.increment.exec()
# Incrementer::increment(const int64 in[8], int64 out[8])
# {IN}  in  = [    0,     1,     1,     0,     1,     1,     1,     1]
# {OUT} out = [    1,     2,     2,     1,     2,     2,     2,     2]
# Returned status: [0 'SUCCESS']
```
This time, the output of the `src.generate` task is filled with random bits and the output of the `inc.increment` task is filled with the incremented values.

## Binding sockets by name
Sockets can also be bound with keyword arguments named after the sockets of the task.
This is the only way to call a task with a socket named after a Python keyword, such as
`in`:
```python
>>> out = inc.increment(**{"in": out_data})
```

## Binding without executing
By default, calling a task executes it. To only bind the sockets, pass `auto_exec=False`:
```python
>>> out = inc.increment(out_data, auto_exec=False)
```
To describe a whole graph that will be executed later by a [Sequence](sequence.md), call
the tasks in a `with spu.bind_only()` block: no call executes its task, and the automatic
execution is restored at the end of the block, even if an exception is raised:
```python
>>> with spu.bind_only():
...     out_data, out_count = src.generate()
...     out = inc.increment(out_data)
```
The block only sets `spu.Task.call_auto_exec` to `False` and restores its previous value
afterwards; unlike `spu.sequence()`, it does not build any runtime.

## Calling a task with no input sockets
In the previous example, we have seen how to call a task with input sockets. However, we can also call a task with no input sockets. In this case, we can simply call the task as a method of the module without any arguments. For example, if we want to call the `generate` task of the `src` module, we can do it as follows:
```python
>>> out_data, out_count = src.generate()
```
As `generate` has two output sockets (besides `status`), the call returns a tuple.

!!! note
    The main difference between
    ```python
    >>> out_data = src.generate['out_data']
    ```
    and
    ```python
    >>> out_data, _ = src.generate()
    ```
    is that the first one retrieves a specific output socket from the task, while the second one executes the task before returning its output sockets.

!!! warning
    When calling a task, the output socket named `status` is not returned despite always being an output socket of the task.
    You can retrieve the status as follows:
    ```python
    >>> src.generate.status
    socket([[0]], dtype=int32, name=status, task=Source_random.generate)
    ```

## Comparing the two approaches
In the previous steps, we have seen two different approaches to build and execute this simple graph. The first approach is to build the modules, connect the sockets, and then execute the tasks as shown in the following code example.
```python
>>> src = spu.source_random(8, dtype = spu.int64)
>>> inc = spu.incrementer(8, dtype=spu.int64)
>>> inc['increment::in'] = src['generate::out_data']
>>> src['generate'].exec()
>>> inc['increment'].exec()
```

The second approach is to call the tasks as methods of the modules.
```python
>>> src = spu.source_random(8, dtype = spu.int64)
>>> inc = spu.incrementer(8, dtype=spu.int64)
>>> out_data = src.generate()[0]
>>> out = inc.increment(out_data)
```
Both approaches are valid and can be used depending on the situation.

In both cases, the tasks are bound together into a graph. The [next page](sequence.md)
shows how to capture this graph in a `Sequence` and execute it many times.
