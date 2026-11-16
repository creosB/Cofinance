# State charts

The risky state machines, drawn from the code. `tests/test_job_statechart.py` fails if a diagram here no longer
matches the table it was drawn from, so review the picture, not the SQL.

## Chat job

Source: `NEXT` in `core/chat_store.py`. The store refuses every move not drawn here, so a late or repeated update
changes nothing. Only `complete` and `degraded` carry an answer, and only a running run can reach them. No end
state ever moves.

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> cancelled: stop honoured
    queued --> cancelling: user asks to stop
    queued --> error: run failed
    queued --> interrupted: server restarted
    queued --> running: worker starts
    running --> cancelled: stop honoured
    running --> cancelling: user asks to stop
    running --> complete: answer saved
    running --> degraded: answer saved, a step fell back
    running --> error: run failed
    running --> interrupted: server restarted
    cancelling --> cancelled: stop honoured
    cancelling --> error: run failed
    cancelling --> interrupted: server restarted
    cancelled --> [*]
    complete --> [*]
    degraded --> [*]
    error --> [*]
    interrupted --> [*]
```
