import time
from datetime import timedelta

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Task
from ..task_cleanup import TASK_RETENTION, run_task_cleanup
from ..utils import ensure_aware, utcnow


def cleanup_expired_tasks(ignore_retention=False) -> int:
    """
    Delete tasks whose finished_at is older than their retention.
    Returns number of deleted tasks.
    """
    now = utcnow()
    deleted = 0

    with SessionLocal() as db:
        stmt = select(Task).where(Task.finished_at.is_not(None))
        tasks = db.scalars(stmt).all()

        for task in tasks:
            if ignore_retention:
                retention = timedelta(0)
            else:
                retention = TASK_RETENTION.get(task.status)
            if retention is None:
                # no retention rule => keep
                continue

            finished_at = ensure_aware(task.finished_at)
            if finished_at and finished_at < now - retention:
                run_task_cleanup(task)
                db.delete(task)
                deleted += 1

        db.commit()

    return deleted


def cleanup_everything() -> None:
    inp = input("Are you sure you want to delete all tasks? [N/y]")
    if inp.lower()[0] != "y":
        return

    deleted = cleanup_expired_tasks(ignore_retention=True)
    print(f"[cleanup] deleted {deleted} expired tasks")


def cleanup_once() -> None:
    deleted = cleanup_expired_tasks()
    print(f"[cleanup] deleted {deleted} expired tasks")


def cleanup_loop(interval_seconds: int = 60 * 60) -> None:
    """Run cleanup on a loop, sleeping between runs."""

    from .cleanup import cleanup_expired_tasks

    while True:
        try:
            deleted = cleanup_expired_tasks()
            print(f"[cleanup] deleted {deleted} expired tasks")
        except Exception as e:
            print(f"[cleanup] error: {e}")
        time.sleep(interval_seconds)


if __name__ == "__main__":

    import sys

    if len(sys.argv) < 2:
        sys.stderr.write("Not enough arguments: please specify 'once' or 'loop'\n")

    if len(sys.argv) < 2:
        sys.stderr.write("Too many arguments\n")

    match sys.argv[1]:
        case "everything":
            cleanup_everything()
        case "once":
            cleanup_once()
        case "loop":
            cleanup_loop()
        case _:
            sys.stderr.write(
                f"Invalid argument '{sys.argv[1]}':\n please specify 'once' or 'loop'\n"
            )
