"""OhCamel Quant's job system (compute plan Lane B; contracts II.2-II.3).

``jobs.sqlite`` (WAL) holds the queue, the artifact ledger and the
scheduler's record. The worker process (``python -m ohcamel_quant worker``)
claims one job at a time when admission control allows, runs it in a spawned
child process, and publishes an immutable artifact under
``{data_dir}/artifacts/<kind>/<id>/``.

Nothing in this package imports numpy or pandas at module level except the
handlers, which only the child process imports: the worker's parent process
stays small inside its 1280 MiB ceiling.
"""
