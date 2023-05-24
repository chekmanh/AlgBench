from contextlib import redirect_stderr, redirect_stdout
import datetime
import io
import inspect
import traceback
import yaml
import typing

from .benchmark_db import BenchmarkDb
from .utils import Timer
from .fingerprint import fingerprint
from .db.json_serializer import to_json


class Benchmark:
    """
    This is the heart of the library. It allows to run, safe, and load
    a benchmark.

    The function `add` will run a configuration, if it is not
    already in the database. You can also split this into `check` and
    `run`. This may be advised if you want to distribute the execution.

    ```python
    benchmark = Benchmark("./test_benchmark")


    def f(x, _test=2, default="default"):
        print(x)  # here you would run your algorithm
        return {"r1": x, "r2": "test"}


    benchmark.add(f, 1, _test=None)
    benchmark.add(f, 2)
    benchmark.add(f, 3, _test=None)

    benchmark.compress()

    for entry in benchmark:
        print(entry["parameters"], entry["data"])
    ```

    The following functions are thread-safe:
    - exists
    - run
    - add
    - insert
    - front
    - __iter__

    Don't call any of the other functions while the benchmark is
    running. It could lead to data loss.
    """

    def __init__(self, path: str) -> None:
        """ """
        self._db = BenchmarkDb(path)

    def _get_arg_data(self, func, args, kwargs):
        sig = inspect.signature(func)
        func_args = {
            k: v.default
            for k, v in sig.parameters.items()
            if v.default is not inspect.Parameter.empty
        }
        func_args.update(sig.bind(*args, **kwargs).arguments)
        data = {
            "func": func.__name__,
            "args": {
                key: value
                for key, value in func_args.items()
                if not key.startswith("_")
            },
        }

        return fingerprint(data), to_json(data)

    def exists(self, func: typing.Callable, *args, **kwargs) -> bool:
        """
        Use this function to check if an entry already exist and thus
        does not have to be run again. If you want to have multiple
        samples, add a sample index argument.
        Caveat: This function may have false negatives. i.e., says that it
          does not exist despite it existing (only for fresh data).
        """
        fingp, _ = self._get_arg_data(func, args, kwargs)
        return self._db.contains_fingerprint(fingp)

    def run(self, func: typing.Callable, *args, **kwargs):
        """
        Will add the function call with the arguments
        to the benchmark.
        """
        fingp, arg_data = self._get_arg_data(func, args, kwargs)
        try:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout):
                with redirect_stderr(stderr):
                    timestamp = datetime.datetime.now().isoformat()
                    timer = Timer()
                    result = func(*args, **kwargs)
                    runtime = timer.time()
            self._db.add(
                arg_fingerprint=fingp,
                arg_data=arg_data,
                result={
                    "result": result,
                    "timestamp": timestamp,
                    "runtime": runtime,
                    "stdout": stdout.getvalue(),
                    "stderr": stderr.getvalue(),
                },
            )
            print(".", end="")
        except Exception as e:
            print()
            print("Exception while running benchmark.")
            print("=====================================")
            print(yaml.dump(arg_data))
            print("-------------------------------------")
            print("ERROR:", e, f"({type(e)})")
            print(traceback.format_exc())
            print("-------------------------------------")
            raise

    def add(self, func: typing.Callable, *args, **kwargs):
        """
        Will add the function call with the arguments
        to the benchmark if not yet contained.

        Combination of `check` and `run`.
        Will only call `run` if the arguments are not
        yet in the benchmark.
        """
        if not self.exists(func, *args, **kwargs):
            self.run(func, *args, **kwargs)

    def insert(self, entry: typing.Dict):
        """
        Insert a raw entry, as returned by `__iter__` or `front`.
        """
        self._db.insert(entry)

    def compress(self):
        """
        Compress the data of the benchmark to take less disk space.

        NOT THREAD-SAFE!
        """
        self._db.compress()

    def repair(self):
        """
        Repairs the benchmark in case it has some broken entries.

        NOT THREAD-SAFE!
        """
        self.delete_if(lambda data: False)

    def __iter__(self) -> typing.Generator[typing.Dict, None, None]:
        """
        Iterate over all entries in the benchmark.
        Use `front` to get a preview on how an entry looks like.
        """
        for entry in self._db:
            yield entry.copy()

    def delete(self):
        """
        Delete the benchmark.

        NOT THREAD-SAFE!
        """
        self._db.delete()

    def front(self) -> typing.Optional[typing.Dict]:
        """
        Return the first entry of the benchmark.
        Useful for checking its content.
        """
        return self._db.front()

    def delete_if(self, condition: typing.Callable[[typing.Dict], bool]):
        """
        Delete entries if a specific condition is met.
        This is currently inefficiently, as always a copy
        of the benchmark is created.
        Use `front` to get a preview on how an entry that is
        passed to the condition looks like.

        NOT THREAD-SAFE!
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdirname:
            benchmark_copy = Benchmark(tmpdirname)
            for entry in self:
                if not condition(entry):
                    benchmark_copy.insert(entry)
            self.delete()
            for entry in benchmark_copy:
                self.insert(entry)
            self.compress()
            benchmark_copy.delete()
