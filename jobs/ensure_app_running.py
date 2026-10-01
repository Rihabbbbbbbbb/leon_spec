"""Self-healing keepalive for the LEON Databricks App (leon-spec).

Runs as a scheduled Databricks job. Databricks Apps serverless compute
auto-stops after a period of inactivity; a stopped app serves
"App Not Available" (503) to users. This job checks the app and restarts
it when it is not RUNNING.

Auth: runs inside Databricks Runtime, so databricks.sdk.WorkspaceClient()
authenticates automatically as the job's run_as principal (the deploying
user, who has CAN_MANAGE on the app).
"""
import sys
import time

from databricks.sdk import WorkspaceClient

APP_NAME = "leon-spec"
RUNNING_TIMEOUT_SECONDS = 600   # max time to wait for app to reach RUNNING
POLL_INTERVAL_SECONDS = 10


def main() -> None:
    w = WorkspaceClient()

    app = w.apps.get(APP_NAME)
    compute = app.compute_status.state
    status = app.app_status.state
    print(f"initial: app={status} compute={compute}")

    if compute != "ACTIVE":
        print(f"compute is {compute} -> starting app ...")
        # `start` re-launches the last active deployment of the app.
        w.apps.start(APP_NAME)

    deadline = time.time() + RUNNING_TIMEOUT_SECONDS
    while time.time() < deadline:
        app = w.apps.get(APP_NAME)
        compute = app.compute_status.state
        status = app.app_status.state
        print(f"poll: app={status} compute={compute}")
        if status == "RUNNING" and compute == "ACTIVE":
            print("OK: leon-spec is RUNNING")
            return
        time.sleep(POLL_INTERVAL_SECONDS)

    print(
        f"WARNING: app did not reach RUNNING within {RUNNING_TIMEOUT_SECONDS}s "
        f"(last app={status} compute={compute})"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
